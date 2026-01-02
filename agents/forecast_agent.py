from dotenv import load_dotenv
load_dotenv()
import os
import json
import hashlib
from typing import Any, Dict, List
from openai import OpenAI

from db import db
from sqlalchemy import text
from models import Transaction, AgentResult
from datetime import datetime


def gather_forecast_context(user_id: str) -> Dict[str, Any]:
    # monthly totals for last 6 months
    sql = """
        SELECT to_char(date_trunc('month', occurred_at), 'YYYY-MM') as ym,
               SUM(CASE WHEN txn_type = 'income' THEN amount_cents ELSE 0 END) as income_cents,
               SUM(CASE WHEN txn_type = 'expense' THEN amount_cents ELSE 0 END) as expense_cents
        FROM transactions
        WHERE user_id = :uid
        GROUP BY ym
        ORDER BY ym DESC
        LIMIT 6
    """
    try:
        rows = db.session.execute(text(sql), {"uid": user_id}).fetchall()
    except Exception:
        # fallback for sqlite or other dialects
        alt_sql = sql.replace("to_char(date_trunc('month', occurred_at), 'YYYY-MM')", "strftime('%Y-%m', occurred_at)")
        rows = db.session.execute(text(alt_sql), {"uid": user_id}).fetchall()

    months = []
    for r in rows:
        months.append({'month': r[0], 'income_cents': int(r[1] or 0), 'income_rupees': round(int(r[1] or 0) / 100.0, 2), 'expense_cents': int(r[2] or 0), 'expense_rupees': round(int(r[2] or 0) / 100.0, 2)})

    # also compute per-category expense sums for last two months (if available)
    category_history = {}
    try:
        if months and len(months) >= 1:
            last_month = months[0]['month']
            # compute previous month label if exists
            prev_month = months[1]['month'] if len(months) > 1 else None
            # query per-category sums for those months
            if prev_month:
                sql_cat = """
                    SELECT c.name as category, to_char(date_trunc('month', t.occurred_at), 'YYYY-MM') as ym, SUM(t.amount_cents) as expense_cents
                    FROM transactions t
                    JOIN categories c ON t.category_id = c.id
                    WHERE t.user_id = :uid AND t.txn_type = 'expense' AND to_char(date_trunc('month', t.occurred_at), 'YYYY-MM') IN (:m1, :m2)
                    GROUP BY c.name, ym
                """
                try:
                    rows_cat = db.session.execute(text(sql_cat), {'uid': user_id, 'm1': last_month, 'm2': prev_month}).fetchall()
                except Exception:
                    alt_sql = sql_cat.replace("to_char(date_trunc('month', t.occurred_at), 'YYYY-MM')", "strftime('%Y-%m', t.occurred_at)")
                    rows_cat = db.session.execute(text(alt_sql), {'uid': user_id, 'm1': last_month, 'm2': prev_month}).fetchall()

                for r in rows_cat:
                    cat = r[0]
                    ym = r[1]
                    amt = int(r[2] or 0)
                    if ym not in category_history:
                        category_history[ym] = {}
                    category_history[ym][cat] = amt
    except Exception:
        category_history = {}

    return {'monthly_history': months, 'category_history': category_history}


def build_forecast_prompt(context: Dict[str, Any]) -> str:
    system = (
        "You are FinAssist Forecast agent. All amounts are Indian Rupees (INR). Numeric values in the context are in paise (stored as `amount_cents`). Convert to rupees for human-readable output and include rupee values and the `₹` symbol when appropriate.\n"
        "Given monthly income/expense history, produce a JSON object with:\n"
        "- predicted_total_expense_cents (number)\n- predicted_total_income_cents (number)\n- expected_net_cents (number)\n- predicted_total_expense_rupees (number)\n- predicted_total_income_rupees (number)\n- expected_net_rupees (number)\n- narrative (short string)\n\nReturn ONLY JSON."
    )
    payload = {'system': system, 'context': context}
    return json.dumps(payload, default=str)


def _compute_forecast_signature(context: Dict[str, Any]) -> str:
    s = json.dumps(context.get('monthly_history', []), sort_keys=True, default=str)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def run_forecast_agent_for_user(user_id: str, force_ai: bool = False) -> Dict[str, Any]:
    load_dotenv()
    api_key = os.environ.get('OPENAI_API_KEY')
    model = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')

    context = gather_forecast_context(user_id)
    # simple heuristic: average last 3 months if data available
    months = context.get('monthly_history', [])
    if months and len(months) >= 1:
        take = months[:3]
        avg_exp = sum(m['expense_cents'] for m in take) // len(take)
        avg_inc = sum(m['income_cents'] for m in take) // len(take)
        heuristic = {
            'predicted_total_expense_cents': int(avg_exp),
            'predicted_total_income_cents': int(avg_inc),
            'expected_net_cents': int(avg_inc - avg_exp),
            'narrative': 'Heuristic forecast based on average of recent months.'
        }
    else:
        heuristic = {'error': 'not enough history', 'predicted_total_expense_cents': 0, 'predicted_total_income_cents': 0, 'expected_net_cents': 0}

    # compute category shifts if category_history present
    cat_shifts = []
    try:
        ch = context.get('category_history', {})
        # expect keys like 'YYYY-MM'
        keys = sorted([k for k in ch.keys()], reverse=True)
        if keys and len(keys) >= 2:
            last = ch.get(keys[0], {})
            prev = ch.get(keys[1], {})
            # collect all categories
            cats = set(list(last.keys()) + list(prev.keys()))
            shifts = []
            for c in cats:
                last_amt = last.get(c, 0)
                prev_amt = prev.get(c, 0)
                if prev_amt > 0:
                    pct = round(((last_amt - prev_amt) / prev_amt) * 100, 1)
                elif last_amt > 0:
                    pct = None
                else:
                    pct = 0.0
                shifts.append({'category': c, 'prev_cents': int(prev_amt), 'last_cents': int(last_amt), 'change_pct': pct})
            # top increases and decreases
            incs = sorted([s for s in shifts if s.get('change_pct') is not None], key=lambda x: x['change_pct'], reverse=True)[:5]
            decs = sorted([s for s in shifts if s.get('change_pct') is not None], key=lambda x: x['change_pct'])[:5]
            cat_shifts = {'month': keys[0], 'prev_month': keys[1], 'increases': incs, 'decreases': decs}
    except Exception:
        cat_shifts = []

    heuristic['category_shifts'] = cat_shifts

    # Ask model for a short JSON forecast too (but fallback to heuristic)
    # caching by signature to avoid repeated AI calls
    sig = _compute_forecast_signature(context)
    try:
        ttl_hours = int(os.environ.get('FORECAST_AGENT_CACHE_TTL_HOURS', '24'))
    except Exception:
        ttl_hours = 24

    try:
        existing = AgentResult.query.filter_by(user_id=user_id, agent_name='forecast_agent_v1', input_text=sig).order_by(AgentResult.created_at.desc()).first()
    except Exception:
        existing = None

    from datetime import datetime
    now = datetime.utcnow()
    if existing and existing.created_at:
        age_hours = (now - existing.created_at).total_seconds() / 3600.0
        if age_hours <= ttl_hours and not force_ai:
            return existing.result_json or heuristic

    # persist heuristic result (as default cached value)
    try:
        ar = AgentResult(user_id=user_id, agent_name='forecast_agent_v1', input_text=sig, result_json=heuristic)
        db.session.add(ar)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    # If forcing AI and we have a key, call model; otherwise return heuristic
    if force_ai:
        if not api_key:
            heuristic['note'] = 'OPENAI_API_KEY not set; returning heuristic forecast.'
            return heuristic

        prompt = build_forecast_prompt(context)
        try:
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(model=model, messages=[{'role':'system','content':prompt}], max_tokens=400, temperature=0.0)
            assistant = response.choices[0].message.content
            try:
                parsed = json.loads(assistant)
            except Exception:
                parsed = heuristic

            try:
                # ensure category_shifts included in AI result where possible
                if isinstance(parsed, dict) and 'category_shifts' not in parsed:
                    parsed['category_shifts'] = heuristic.get('category_shifts')
                ar2 = AgentResult(user_id=user_id, agent_name='forecast_agent_v1', input_text=sig, result_json=parsed)
                db.session.add(ar2)
                db.session.commit()
            except Exception:
                try:
                    db.session.rollback()
                except Exception:
                    pass

            return parsed
        except Exception:
            return heuristic

    return heuristic
