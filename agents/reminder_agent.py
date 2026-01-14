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
from .chat_agent_v2 import groq_limiter
import re


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

def _get_bill_specific_advice(bill_name: str, amount_rupees: float) -> str:
    """Return specific financial advice for a bill based on its name and amount."""
    bill_lower = bill_name.lower()
    
    # Category-specific advice
    if any(word in bill_lower for word in ['electric', 'power', 'electricity']):
        return "Shift heavy appliance usage to off-peak hours (typically 9 PM - 6 AM) to reduce consumption."
    elif any(word in bill_lower for word in ['internet', 'broadband', 'wifi', 'mobile', 'phone']):
        return "Review your plan annually; bundled services often offer better rates."
    elif any(word in bill_lower for word in ['insurance']):
        return "Review coverage needs yearly to avoid overpaying for unused benefits."
    elif any(word in bill_lower for word in ['water', 'gas']):
        return "Install flow restrictors and check for leaks to reduce consumption."
    elif any(word in bill_lower for word in ['rent', 'mortgage']):
        return "Set a recurring reminder 5 days before due date to avoid late payment penalties."
    elif any(word in bill_lower for word in ['credit', 'loan']):
        return "Paying on or before the due date improves your credit score over time."
    
    # Amount-based advice
    if amount_rupees > 5000:
        return "For high-value bills, maintain at least 2× the bill amount in reserves to handle emergencies."
    elif amount_rupees < 500:
        return "Small bills add up—track them to find savings opportunities."
    
    # Default advice
    return "Set a reminder a few days before the due date to ensure timely payment."

def _local_rich_summary(bills: List[Dict]) -> Dict:
    """Produce a deterministic, informative summary (no AI) for upcoming bills.

    Returns a dict with a short `message` and a `details` object containing
    per-bill rows and aggregates. This can be cached and shown without calling AI.
    """
    now = datetime.utcnow()
    if not bills:
        return {
            'message': 'No bills due in the next 7 days. You\'re all clear!', 
            'details': {
                'count': 0, 
                'total_rupees': 0.0, 
                'bills': [],
                'overdue_count': 0,
                'summary': 'All clear! No upcoming bills.'
            }
        }

    rows = []
    total_cents = 0
    nearest = None
    largest = None
    overdue_count = 0
    due_today_count = 0
    
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
            if days_until < 0:
                overdue_count += 1
            elif days_until == 0:
                due_today_count += 1
        
        # Determine priority/urgency
        if days_until is not None:
            if days_until < 0:
                priority = '🔴 OVERDUE'
            elif days_until == 0:
                priority = '🔴 DUE TODAY'
            elif days_until <= 2:
                priority = '🟠 URGENT'
            elif days_until <= 5:
                priority = '🟡 SOON'
            else:
                priority = '🟢 NORMAL'
        else:
            priority = '🟢 NORMAL'
        
        row = {
            'id': b.get('id'),
            'name': b.get('title') or b.get('name'),
            'due_date': due_iso,
            'days_until': days_until,
            'amount_rupees': round(amount_cents / 100.0, 2),
            'raw_amount_cents': amount_cents,
            'description': b.get('body') or '',
            'priority': priority
        }
        rows.append(row)
        if nearest is None or (row['days_until'] is not None and (nearest['days_until'] is None or row['days_until'] < nearest['days_until'])):
            nearest = row
        if largest is None or row['raw_amount_cents'] > largest['raw_amount_cents']:
            largest = row

    count = len(rows)
    total_rupees = round(total_cents / 100.0, 2)

    # Build structured message with sections
    sections = []
    
    # 1. Overview section
    if overdue_count > 0:
        sections.append(f"**URGENT:** {overdue_count} overdue bill{'s' if overdue_count != 1 else ''} totaling **₹{total_rupees:,.0f}** need immediate attention. Late fees typically range from 2-5% of the bill amount, and unpaid bills can affect credit scores and service continuity.")
        if due_today_count > 0:
            sections.append(f"Additionally, {due_today_count} bill{'s' if due_today_count != 1 else ''} {'are' if due_today_count != 1 else 'is'} due today.")
    elif due_today_count > 0:
        sections.append(f"**Today's Payments:** {due_today_count} bill{'s' if due_today_count != 1 else ''} totaling **₹{total_rupees:,.0f}** {'are' if due_today_count != 1 else 'is'} due today. Process payment{'s' if due_today_count != 1 else ''} before 11:59 PM to maintain on-time status.")
        if count > due_today_count:
            sections.append(f"{count - due_today_count} additional bill{'s' if (count - due_today_count) != 1 else ''} coming up this week.")
    else:
        schedule_desc = "light payment week" if count <= 3 else ("manageable schedule" if count <= 5 else "busy payment week")
        sections.append(f"**Payment Overview:** {count} bill{'s' if count != 1 else ''} worth **₹{total_rupees:,.0f}** due in the next 7 days. You have a {schedule_desc}.")
    
    # 2. Priority bill with personalized advice
    if nearest:
        if nearest['days_until'] is not None:
            if nearest['days_until'] < 0:
                days_text = f"{abs(nearest['days_until'])} day{'s' if abs(nearest['days_until']) != 1 else ''} overdue"
                sections.append(f"**Priority:** {nearest['name']} ({days_text}) — ₹{nearest['amount_rupees']:,.0f}. {_get_bill_specific_advice(nearest['name'], nearest['amount_rupees'])}")
            elif nearest['days_until'] == 0:
                sections.append(f"**Due Today:** {nearest['name']} — ₹{nearest['amount_rupees']:,.0f}. {_get_bill_specific_advice(nearest['name'], nearest['amount_rupees'])}")
            elif nearest['days_until'] <= 2:
                sections.append(f"**Coming Soon:** {nearest['name']} in {nearest['days_until']} day{'s' if nearest['days_until'] != 1 else ''} — ₹{nearest['amount_rupees']:,.0f}. {_get_bill_specific_advice(nearest['name'], nearest['amount_rupees'])}")
            else:
                sections.append(f"**Next Payment:** {nearest['name']} in {nearest['days_until']} days — ₹{nearest['amount_rupees']:,.0f}. {_get_bill_specific_advice(nearest['name'], nearest['amount_rupees'])}")
        else:
            sections.append(f"**Next Payment:** {nearest['name']} — ₹{nearest['amount_rupees']:,.0f}. {_get_bill_specific_advice(nearest['name'], nearest['amount_rupees'])}")
    
    # 3. Largest expense (if different from priority)
    if largest and largest != nearest:
        sections.append(f"**Largest Expense:** {largest['name']} at ₹{largest['amount_rupees']:,.0f}. {_get_bill_specific_advice(largest['name'], largest['amount_rupees'])}")
    
    # 4. Financial strategy tip
    if total_rupees > 20000:
        sections.append("**Payment Strategy:** With high-value bills totaling over ₹20,000, ensure sufficient account balance 2-3 days in advance. Consider setting up automatic payments or linking to a dedicated bill-payment account to avoid missed payments.")
    elif total_rupees > 10000:
        sections.append("**Payment Strategy:** Set up automatic payments for recurring bills to streamline cash flow. Most banks offer auto-debit facilities with SMS alerts for each payment.")
    elif count >= 5:
        sections.append("**Payment Strategy:** With multiple bills this week, consider consolidating payment dates. Many service providers allow billing cycle adjustments, which can simplify your payment schedule.")
    else:
        sections.append("**Payment Health:** You're managing bills well! Continue monitoring upcoming payments and maintaining this discipline. Consider setting calendar reminders 3 days before due dates.")

    short_message = "\n\n".join(sections)

    details = {
        'count': count,
        'total_rupees': total_rupees,
        'nearest': nearest,
        'largest': largest,
        'overdue_count': overdue_count,
        'due_today_count': due_today_count,
        'bills': sorted(rows, key=lambda x: x['days_until'] if x['days_until'] is not None else 999),
        'summary': f"Total ₹{total_rupees} due across {count} bills"
    }
    return {'message': short_message, 'details': details}


def run_reminder_agent_for_user(user_id: int, force_ai: bool = False) -> Dict:
    """Return a fresh summary for upcoming bills.

    Always computes a fresh local summary to ensure accurate bill totals.
    If `force_ai` is True, will attempt to call AI for enhanced analysis.
    """
    load_dotenv()

    bills = get_upcoming_reminders(user_id, days=7)
    
    # Always produce a fresh deterministic local summary
    parsed = _local_rich_summary(bills)

    # If caller explicitly asked for an AI-enhanced summary and we have an API key, call Groq
    if force_ai:
        api_key = os.environ.get('GROQ_API_KEY')
        model = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')
        if not api_key:
            parsed['note'] = 'GROQ_API_KEY not set; returning local summary.'
            return parsed
        
        # Check rate limits
        can_proceed, limit_msg = groq_limiter.check_limits()
        if not can_proceed:
            parsed['note'] = limit_msg
            return parsed
        
        groq_limiter.record_request()

        # Build context for AI (comprehensive)
        context = {'upcoming_bills': []}
        for b in bills:
            md = b.get('meta') or {}
            cents = int(md.get('amount_cents') or 0)
            context['upcoming_bills'].append({
                'id': b.get('id'),
                'name': b.get('title') or b.get('name'),
                'due': md.get('due_date'),
                'amount_cents': cents,
                'amount_rupees': round(cents/100.0, 2),
                'description': b.get('body', '')
            })

        system_msg = (
            """You are FinAssist Bill Agent - an intelligent bill management assistant.
Your job is to help users manage their bills smartly and avoid missed payments.

## Your Responsibilities
1. Analyze upcoming bills and identify payment priorities
2. Warn about bills due soon (next 3 days)
3. Suggest payment optimization strategies
4. Provide actionable reminders

## Response Format
Return a JSON object with:
{
  "summary": "2-3 sentence friendly overview of upcoming bills",
  
  "priority_bills": [
    {
      "name": "bill name",
      "due_in_days": number,
      "amount_rupees": number,
      "action": "PAY_SOON|PAY_TODAY|UPCOMING"
    }
  ],
  
  "critical_alerts": ["Alert 1", "Alert 2"],  // Bills due in next 3 days
  
  "total_amount_due": "₹X in the next 7 days",
  
  "smart_suggestions": [
    "Specific, actionable suggestion 1",
    "Specific, actionable suggestion 2"
  ],
  
  "payment_plan": "Brief overview of recommended payment schedule"
}

## Important Rules
- All amounts in context are in paise (amount_cents). Convert: divide by 100 for rupees
- Use ₹ symbol in all amounts
- Identify bills due in NEXT 3 DAYS as critical
- Sort by due date (nearest first)
- Be specific and actionable - show rupee amounts
- Consider which bills are highest priority (rent, utilities, essentials)
- Suggest payment dates/order based on due dates

## Tone
- Helpful and non-judgmental
- Clear and specific
- Focused on helping avoid missed payments
- Encouraging about financial responsibility

Return ONLY valid JSON. No explanatory text before or after."""
        )
        user_prompt = f"Here are the upcoming bills (amounts in both paise and rupees):\n{json.dumps(context, default=str)}\n\nAnalyze these bills and provide smart management suggestions in the JSON format specified."

        try:
            client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': system_msg},
                    {'role': 'user', 'content': user_prompt}
                ],
                max_tokens=1000,
                temperature=0.3
            )
            response_text = resp.choices[0].message.content.strip()
            
            # Parse the JSON response
            try:
                ai_analysis = json.loads(response_text)
                # Merge AI analysis into parsed result
                parsed['ai_analysis'] = ai_analysis
                parsed['message'] = ai_analysis.get('summary', parsed.get('message', ''))
                parsed['has_critical_alerts'] = len(ai_analysis.get('critical_alerts', [])) > 0
            except json.JSONDecodeError:
                # If response isn't JSON, store as text
                parsed['ai_message'] = response_text
                parsed['ai_error'] = 'Response was not valid JSON'
        except Exception as e:
            parsed['ai_error'] = str(e)

    # persist reminder result
    try:
        sig = f"reminder_summary_{user_id}_{datetime.utcnow().isoformat()}"
        ar = AgentResult(user_id=user_id, agent_name='reminder_agent_v1', input_text=sig, result_json=parsed)
        db.session.add(ar)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    return parsed
