from datetime import datetime
from typing import List, Dict, Optional
from db import db
from models import Bill
from dotenv import load_dotenv
import os
import json
import hashlib
from openai import OpenAI
from models import AgentResult


def _compute_next_due_from(start_date: datetime, period: Optional[str], interval_count: int = 1) -> Optional[datetime]:
    if not start_date:
        return None
    now = datetime.utcnow()
    cur = start_date
    max_iterations = 1200
    i = 0
    if period == 'one-time' or not period:
        return cur if cur >= now else None
    while cur < now and i < max_iterations:
        if period == 'monthly':
            month = cur.month - 1 + interval_count
            year = cur.year + month // 12
            month = month % 12 + 1
            day = min(cur.day, 28)
            try:
                cur = cur.replace(year=year, month=month, day=day)
            except Exception:
                cur = cur.replace(day=1)
                if month == 12:
                    cur = cur.replace(year=year + 1, month=1)
                else:
                    cur = cur.replace(month=month)
        elif period == 'yearly':
            try:
                cur = cur.replace(year=cur.year + interval_count)
            except Exception:
                cur = cur.replace(month=cur.month, day=min(cur.day, 28), year=cur.year + interval_count)
        else:
            return None
        i += 1
    return cur if cur >= now else None


def get_upcoming_reminders(user_id: int, days: int = 7) -> List[Dict]:
    """Return upcoming bill reminders for the next `days` days for the user.

    Only bills with a non-null `next_due` are considered. The returned list
    contains simple dicts suitable for rendering in the notifications page.
    """
    now = datetime.utcnow()
    end = now
    try:
        from datetime import timedelta
        end = now + timedelta(days=days)
    except Exception:
        pass

    bills = Bill.query.filter(Bill.user_id == user_id, Bill.active == True, Bill.next_due != None).all()
    out = []
    for b in bills:
        try:
            nd = b.next_due
            if not nd:
                # try to compute from last_paid
                nd = _compute_next_due_from(b.last_paid or b.created_at, getattr(b, 'period', None), getattr(b, 'interval_count', 1))
            if not nd:
                continue
            if nd >= now and nd <= end:
                out.append({
                    'id': b.id,
                    'title': b.name,
                    'body': b.description or '',
                    'created_at': (b.created_at.isoformat() if b.created_at else None),
                    'type': 'reminder',
                    'meta': {'due_date': nd.isoformat(), 'amount_cents': b.amount_cents}
                })
        except Exception:
            continue
    # sort by due date
    out.sort(key=lambda x: x.get('meta', {}).get('due_date') or '')
    return out


def mark_bill_paid(user_id: int, bill_id: int, paid_at: Optional[datetime] = None) -> Dict:
    """Mark the bill as paid: update last_paid and compute next_due.

    Returns a dict with 'ok' and 'next_due' iso string when successful.
    """
    if paid_at is None:
        paid_at = datetime.utcnow()
    b = Bill.query.filter_by(id=bill_id, user_id=user_id).first()
    if not b:
        return {'ok': False, 'error': 'bill not found'}
    try:
        b.last_paid = paid_at
        # compute next_due based on period/interval_count if available
        period = getattr(b, 'period', None) or getattr(b, 'schedule_type', None)
        interval = getattr(b, 'interval_count', 1)
        nd = _compute_next_due_from(paid_at, period, interval)
        b.next_due = nd
        db.session.add(b)
        db.session.commit()
        return {'ok': True, 'next_due': nd.isoformat() if nd else None}
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return {'ok': False, 'error': str(e)}


def _compute_signature_for_bills(bills: List[Dict]) -> str:
    """Create a stable signature string for a list of upcoming bills."""
    # Only include id, due, amount_cents -- these determine summary stability
    stable = []
    for b in bills:
        md = b.get('meta') or {}
        stable.append({'id': b.get('id'), 'due': md.get('due_date'), 'amount_cents': md.get('amount_cents')})
    # sort for determinism
    stable = sorted(stable, key=lambda x: (x.get('due') or '', x.get('id')))
    s = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def _local_rich_summary(bills: List[Dict]) -> Dict:
    """Produce a deterministic, informative summary (no AI) for upcoming bills.

    Returns a dict with a short `message` and a `details` object containing
    per-bill rows and aggregates. This can be cached and shown without calling AI.
    """
    now = datetime.utcnow()
    if not bills:
        return {'message': 'No bills due in the next 7 days.', 'details': {'count': 0, 'total_rupees': 0.0, 'bills': []}}

    rows = []
    total_cents = 0
    nearest = None
    largest = None
    for b in bills:
        md = b.get('meta') or {}
        due_iso = md.get('due_date')
        try:
            due_dt = datetime.fromisoformat(due_iso)
        except Exception:
            due_dt = None
        amount_cents = int(md.get('amount_cents') or 0)
        total_cents += amount_cents
        days_until = None
        if due_dt:
            days_until = (due_dt - now).days
        row = {
            'id': b.get('id'),
            'name': b.get('title') or b.get('name'),
            'due_date': due_iso,
            'days_until': days_until,
            'amount_rupees': round(amount_cents / 100.0, 2),
            'raw_amount_cents': amount_cents,
            'description': b.get('body') or ''
        }
        rows.append(row)
        if nearest is None or (row['days_until'] is not None and (nearest['days_until'] is None or row['days_until'] < nearest['days_until'])):
            nearest = row
        if largest is None or row['raw_amount_cents'] > largest['raw_amount_cents']:
            largest = row

    count = len(rows)
    total_rupees = round(total_cents / 100.0, 2)

    short_message = f"{count} bill{'s' if count!=1 else ''} due in the next 7 days totaling ₹{total_rupees}."
    if nearest:
        nd = nearest['due_date'] or 'soon'
        short_message += f" Nearest: {nearest['name']} on {nd} (in {nearest['days_until']} days)."

    details = {
        'count': count,
        'total_rupees': total_rupees,
        'nearest': nearest,
        'largest': largest,
        'bills': rows
    }
    return {'message': short_message, 'details': details}


def run_reminder_agent_for_user(user_id: int, force_ai: bool = False) -> Dict:
    """Return a cached local summary when possible, otherwise optionally call AI.

    - Computes a signature for the upcoming bills and looks for an existing
      AgentResult with the same signature. If found and younger than TTL, returns it.
    - By default, this uses a deterministic local summary (no AI). If `force_ai`
      is True and there is no cached result, the OpenAI call will be attempted.
    """
    load_dotenv()

    bills = get_upcoming_reminders(user_id, days=7)
    sig = _compute_signature_for_bills(bills)

    # TTL in hours for cached results (default 24h)
    try:
        ttl_hours = int(os.environ.get('REMINDER_AGENT_CACHE_TTL_HOURS', '24'))
    except Exception:
        ttl_hours = 24

    # Try to find an existing AgentResult with same signature
    try:
        existing = AgentResult.query.filter_by(user_id=user_id, agent_name='reminder_agent_v1', input_text=sig).order_by(AgentResult.created_at.desc()).first()
    except Exception:
        existing = None

    now = datetime.utcnow()
    if existing and existing.created_at:
        age_hours = (now - existing.created_at).total_seconds() / 3600.0
        if age_hours <= ttl_hours and not force_ai:
            # return cached result
            return existing.result_json or {'message': 'No summary available.'}

    # Produce a deterministic local summary and persist it.
    parsed = _local_rich_summary(bills)

    # Persist the AgentResult with the signature stored in input_text
    try:
        ar = AgentResult(user_id=user_id, agent_name='reminder_agent_v1', input_text=sig, result_json=parsed)
        db.session.add(ar)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    # If caller explicitly asked for an AI-enhanced summary and we have an API key, call OpenAI
    if force_ai:
        api_key = os.environ.get('OPENAI_API_KEY')
        model = os.environ.get('REMINDER_AGENT_AI_MODEL', 'gpt-4o-mini')
        if not api_key:
            # already persisted local summary; return it with a note
            parsed['note'] = 'OPENAI_API_KEY not set; returning local summary.'
            return parsed

        # Build context for AI (compact)
        context = {'upcoming_bills': []}
        for b in bills:
            md = b.get('meta') or {}
            cents = int(md.get('amount_cents') or 0)
            context['upcoming_bills'].append({'id': b.get('id'), 'name': b.get('title') or b.get('name'), 'due': md.get('due_date'), 'amount_cents': cents, 'amount_rupees': round(cents/100.0, 2)})

        system_msg = (
            "You are FinAssist, a concise and helpful financial assistant. All amounts in the context are Indian Rupees (INR). Numeric amounts are supplied as `amount_cents` (paise); convert to rupees by dividing by 100 and use the `₹` symbol when presenting amounts. "
            "Given the upcoming bills, produce a short (1-3 sentence) actionable summary and one quick suggestion. Return plain text only."
        )
        user_prompt = f"Here are the upcoming bills in JSON (amounts include both `amount_cents` and `amount_rupees`):\n{json.dumps(context, default=str)}\n\nProvide a short friendly summary (plain text, 1-3 sentences) and one short suggestion."

        try:
            client = OpenAI(api_key=api_key)
            resp = client.chat.completions.create(model=model, messages=[
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_prompt}
            ], max_tokens=200, temperature=0.2)
            assistant = resp.choices[0].message.content.strip()
            # merge AI message into parsed
            parsed['ai_message'] = assistant

            # persist updated result
            try:
                ar2 = AgentResult(user_id=user_id, agent_name='reminder_agent_v1', input_text=sig, result_json=parsed)
                db.session.add(ar2)
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
        except Exception as e:
            parsed['ai_error'] = str(e)

    return parsed
