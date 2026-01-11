from dotenv import load_dotenv
load_dotenv()
import os  
import json
import hashlib
from typing import Any, Dict
from openai import OpenAI

from db import db
from sqlalchemy import text
from models import Transaction, Category, AgentResult
from collections import Counter
from datetime import datetime, timedelta
from openai import OpenAI


def gather_insight_context(user_id: str) -> Dict[str, Any]:
    # recent transactions and category totals
    txns = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.occurred_at.desc()).limit(100).all()
    # only include transactions that have a valid category to avoid showing a fallback label
    categories = db.session.execute(text("""
        SELECT c.name as category, SUM(t.amount_cents) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = :uid
        GROUP BY c.name
        ORDER BY total DESC
        LIMIT 10
    """), {"uid": user_id}).fetchall()

    tx_out = []
    for t in txns:
        tx_out.append({
            'id': t.id,
            'txn_type': t.txn_type,
            'amount_cents': int(t.amount_cents or 0),
            'amount_rupees': round(int(t.amount_cents or 0) / 100.0, 2),
            'occurred_at': t.occurred_at.isoformat() if t.occurred_at else None,
            'category_id': t.category_id,
            'description': t.description
        })

    cat_out = [{'category': r[0], 'amount_cents': int(r[1] or 0), 'amount_rupees': round(int(r[1] or 0) / 100.0, 2)} for r in categories]

    return {'transactions': tx_out, 'category_totals': cat_out}


def build_insight_prompt(context: Dict[str, Any]) -> str:
    # ask the model to return a JSON object with specific fields
    system = (
        "You are FinAssist Insights agent. All amounts provided are in Indian Rupees (INR). Numeric amounts in the context are given as `amount_cents` (paise); convert to rupees by dividing by 100 and use the `₹` symbol when returning results.\n"
        "Analyze the provided transactions and category totals and return a JSON object with the following keys:\n"
        "- top_spending_entity (string)  // prefer a merchant/payee/description, not a generic category name\n"
        "- top_spending_amount_cents (number)\n"
        "- week_over_week_change (string, e.g. '+6%')\n- biggest_expense {amount_cents, label, occurred_at}\n- behavior_pattern (short string)\n- recommendation (short string)\n\n"
        "Return ONLY valid JSON. Do not include any explanatory text. When possible, include rupee-converted fields (e.g. top_spending_amount_rupees) in the JSON."
    )
    payload = {'system': system, 'context': context}
    return json.dumps(payload, default=str)


def _compute_insight_signature(context: Dict[str, Any]) -> str:
    """Compute a stable signature for insight context to enable caching."""
    sig_obj = {
        'top_cats': sorted([{ 'category': c.get('category'), 'amount_cents': c.get('amount_cents', 0)} for c in context.get('category_totals', [])], key=lambda x: (x['category'] or '')),
        'recent_tx_ids': [t.get('id') for t in context.get('transactions', [])[:50]]
    }
    s = json.dumps(sig_obj, sort_keys=True, default=str)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def _local_insights(context: Dict[str, Any]) -> Dict[str, Any]:
    """Produce deterministic, DB-only insights without calling AI.

    Returns a dict matching the expected insight keys.
    """
    txns = context.get('transactions', [])
    cats = context.get('category_totals', [])

    # top category (kept for backward compatibility) and top spending entity
    top_cat = None
    top_amount_cents = 0
    if cats:
        top = max(cats, key=lambda c: c.get('amount_cents', 0))
        top_cat = top.get('category')
        top_amount_cents = int(top.get('amount_cents', 0))

    # top spending entity: choose most frequent expense description (merchant/payee)
    top_entity = None
    top_entity_amount = 0
    expense_labels = [ (t.get('description') or '').strip() for t in txns if t.get('txn_type') == 'expense' and (t.get('description') or '').strip() ]
    if expense_labels:
        ent_counter = Counter([lab.lower() for lab in expense_labels])
        most_common = ent_counter.most_common(1)
        if most_common:
            candidate = most_common[0][0]
            # calculate total amount for transactions matching this description
            total_cents = sum(int(t.get('amount_cents',0)) for t in txns if (t.get('description') or '').strip().lower() == candidate and t.get('txn_type') == 'expense')
            top_entity = candidate
            top_entity_amount = int(total_cents)

    # week-over-week: compare sum of expenses in last 7 days vs previous 7 days
    now = datetime.utcnow()
    def sum_window(start, end):
        s = 0
        for t in txns:
            try:
                dt = datetime.fromisoformat(t.get('occurred_at')) if t.get('occurred_at') else None
            except Exception:
                dt = None
            if not dt:
                continue
            if dt >= start and dt < end and t.get('txn_type') == 'expense':
                s += int(t.get('amount_cents', 0))
        return s

    this_week_start = now - timedelta(days=7)
    prev_week_start = now - timedelta(days=14)
    this_week_sum = sum_window(this_week_start, now)
    prev_week_sum = sum_window(prev_week_start, this_week_start)
    if prev_week_sum > 0:
        wow = f"{round(((this_week_sum - prev_week_sum) / prev_week_sum) * 100)}%"
    elif this_week_sum > 0:
        wow = "+100%"
    else:
        wow = "0%"

    # biggest expense
    expenses = [t for t in txns if t.get('txn_type') == 'expense']
    biggest = None
    lowest = None
    if expenses:
        big = max(expenses, key=lambda t: int(t.get('amount_cents', 0)))
        biggest = {'amount_cents': int(big.get('amount_cents', 0)), 'label': (big.get('description') or '')[:120], 'occurred_at': big.get('occurred_at')}
        # lowest non-zero expense
        nonzero = [e for e in expenses if int(e.get('amount_cents', 0)) > 0]
        if nonzero:
            low = min(nonzero, key=lambda t: int(t.get('amount_cents', 0)))
            lowest = {'amount_cents': int(low.get('amount_cents', 0)), 'label': (low.get('description') or '')[:120], 'occurred_at': low.get('occurred_at')}

    # behavior pattern (naive): detect frequent descriptions
    labels = [ (t.get('description') or '').strip().lower() for t in txns if t.get('occurred_at') ]
    counter = Counter(labels)
    common = [(lab,c) for lab,c in counter.items() if lab and c >= 3]
    if common:
        pattern = f"Recurring: {common[0][0][:40]} appears {common[0][1]} times"
    else:
        pattern = 'No clear recurring pattern detected.'

    recommendation = 'Review large expenses and consider trimming discretionary spend.'
    if biggest and biggest.get('amount_cents',0) > 50000*100:
        recommendation = 'Consider reviewing the largest recent expense for possible savings.'

    out = {
        'top_category': top_cat,
        'top_amount_cents': int(top_amount_cents),
        'top_amount_rupees': round(int(top_amount_cents)/100.0,2),
        'top_spending_entity': top_entity,
        'top_spending_amount_cents': int(top_entity_amount),
        'top_spending_amount_rupees': round(int(top_entity_amount)/100.0,2),
        'week_over_week_change': wow,
        'biggest_expense': biggest,
        'lowest_expense': lowest,
        'behavior_pattern': pattern,
        'recommendation': recommendation
    }
    return out


def run_insights_agent_for_user(user_id: str, force_ai: bool = False) -> Dict[str, Any]:
    """Return cached deterministic insights when possible; optionally call AI when forced.

    Caches results in `AgentResult` using a signature stored in `input_text`.
    """
    load_dotenv()
    api_key = os.environ.get('OPENAI_API_KEY')
    model = os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini')

    context = gather_insight_context(user_id)
    sig = _compute_insight_signature(context)

    try:
        ttl_hours = int(os.environ.get('INSIGHT_AGENT_CACHE_TTL_HOURS', '12'))
    except Exception:
        ttl_hours = 12

    try:
        existing = AgentResult.query.filter_by(user_id=user_id, agent_name='insight_agent_v1', input_text=sig).order_by(AgentResult.created_at.desc()).first()
    except Exception:
        existing = None

    now = datetime.utcnow()
    if existing and existing.created_at:
        age_hours = (now - existing.created_at).total_seconds() / 3600.0
        if age_hours <= ttl_hours and not force_ai:
            return existing.result_json or {'error': 'no result'}

    # produce deterministic local insights
    parsed = _local_insights(context)

    # persist deterministic result
    try:
        ar = AgentResult(user_id=user_id, agent_name='insight_agent_v1', input_text=sig, result_json=parsed)
        db.session.add(ar)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    # If forced and API key present, call AI for an enhanced JSON result
    if force_ai:
        if not api_key:
            parsed['note'] = 'OPENAI_API_KEY not set; returning local insights.'
            return parsed
        # First: AI-assisted categorization pass for uncategorized transactions
        try:
            # collect uncategorized/orphaned transactions
            uncats = [t for t in context.get('transactions', []) if (not t.get('category_id'))]
            if uncats:
                # provide category list to the model
                cat_rows = db.session.execute(text("SELECT id, name FROM categories WHERE user_id = :uid"), {"uid": user_id}).fetchall()
                available_cats = [r[1] for r in cat_rows]

                # build instruction: map each transaction id -> category name from available list or 'Uncategorized'
                mapping_prompt = {
                    'instruction': 'Map each transaction to one of the provided category names based only on the transaction description. Return ONLY valid JSON mapping txn_id -> category_name. Use exact category names from the list. If unsure, return an empty string for that txn id (do not invent new category names).',
                    'available_categories': available_cats,
                    'transactions': [{ 'id': t.get('id'), 'description': t.get('description') or '' } for t in uncats]
                }

                client = OpenAI(api_key=api_key)
                mprompt = f"Instruction:\n{json.dumps(mapping_prompt, ensure_ascii=False)}"
                resp = client.chat.completions.create(model=model, messages=[{'role':'system','content': mprompt}], max_tokens=800, temperature=0.0)
                ai_map_raw = resp.choices[0].message.content
                try:
                    ai_map = json.loads(ai_map_raw)
                except Exception:
                    # If AI returned text, try to extract a JSON object inside
                    try:
                        start = ai_map_raw.find('{')
                        ai_map = json.loads(ai_map_raw[start:]) if start != -1 else {}
                    except Exception:
                        ai_map = {}

                # persist suggested mapping in AgentResult for auditing
                try:
                    ar_map = AgentResult(user_id=user_id, agent_name='insight_agent_v1', input_text=sig + '|categorization', result_json={'suggested_mapping': ai_map})
                    db.session.add(ar_map)
                    db.session.commit()
                except Exception:
                    try:
                        db.session.rollback()
                    except Exception:
                        pass

                # apply mapping to DB if model returned mappings and mapping is non-empty
                if isinstance(ai_map, dict) and ai_map:
                    applied = 0
                    for txid, cat_name in ai_map.items():
                        try:
                            if not cat_name:
                                continue
                            # find or create category matching cat_name (case-insensitive)
                            cat = Category.query.filter(Category.user_id == user_id, db.func.lower(Category.name) == cat_name.lower()).first()
                            if not cat:
                                cat = Category(user_id=user_id, name=cat_name)
                                db.session.add(cat)
                                db.session.commit()
                            # update transaction if it's still uncategorized or orphaned
                            t = Transaction.query.filter_by(id=txid, user_id=user_id).first()
                            if t and (not t.category_id or not Category.query.get(t.category_id)):
                                t.category_id = cat.id
                                db.session.add(t)
                                db.session.commit()
                                applied += 1
                        except Exception:
                            try:
                                db.session.rollback()
                            except Exception:
                                pass
                    parsed['categorization_applied'] = applied
        except Exception as e:
            parsed['categorization_error'] = str(e)

        # Now call the AI for enhanced insights using updated context
        try:
            # refresh context after possible categorization
            context = gather_insight_context(user_id)
            prompt = build_insight_prompt(context)
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(model=model, messages=[{'role':'system','content':prompt}], max_tokens=800, temperature=0.0)
            assistant = response.choices[0].message.content
            try:
                ai_parsed = json.loads(assistant)
            except Exception:
                ai_parsed = {'raw': assistant}

            merged = parsed.copy()
            if isinstance(ai_parsed, dict):
                merged.update(ai_parsed)
            else:
                merged['ai_raw'] = ai_parsed

            # persist AI-updated result
            try:
                ar2 = AgentResult(user_id=user_id, agent_name='insight_agent_v1', input_text=sig, result_json=merged)
                db.session.add(ar2)
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass
            return merged
        except Exception as e:
            parsed['ai_error'] = str(e)

    return parsed
