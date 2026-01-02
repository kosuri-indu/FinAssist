from dotenv import load_dotenv
from dotenv import load_dotenv
import os
import json
from typing import Dict, Any
from openai import OpenAI

from db import db
from models import Transaction, Bill, AgentResult, ChatLog
from sqlalchemy.sql import text

load_dotenv()

def prepare_chat_context(user_id: str) -> Dict[str, Any]:
    """Gather DB context for the chat agent and return a serializable dict.
    This follows the structure specified by the user instructions.
    """
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.occurred_at.desc()).limit(20).all()

    sql = text("""
        SELECT c.name as category, SUM(t.amount_cents) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = :uid
        GROUP BY c.name
    """)
    category_rows = db.session.execute(sql, {"uid": user_id}).fetchall()
    category_totals = [{"category": r[0], "amount_cents": int(r[1] or 0)} for r in category_rows]

    dialect = getattr(db.engine, 'name', None) or db.session.bind.dialect.name
    if dialect and 'sqlite' in dialect:
        sql_monthly = text("""
            SELECT strftime('%Y-%m', occurred_at) as ym,
                   SUM(CASE WHEN txn_type = 'income' THEN amount_cents ELSE 0 END) as income_cents,
                   SUM(CASE WHEN txn_type = 'expense' THEN amount_cents ELSE 0 END) as expense_cents
            FROM transactions
            WHERE user_id = :uid
            GROUP BY ym
            ORDER BY ym DESC
            LIMIT 6
        """)
    else:
        sql_monthly = text("""
            SELECT to_char(date_trunc('month', occurred_at), 'YYYY-MM') as ym,
                   SUM(CASE WHEN txn_type = 'income' THEN amount_cents ELSE 0 END) as income_cents,
                   SUM(CASE WHEN txn_type = 'expense' THEN amount_cents ELSE 0 END) as expense_cents
            FROM transactions
            WHERE user_id = :uid
            GROUP BY ym
            ORDER BY ym DESC
            LIMIT 6
        """)
    monthly_rows = db.session.execute(sql_monthly, {"uid": user_id}).fetchall()
    monthly_totals = []
    for r in monthly_rows:
        monthly_totals.append({"month": r[0], "income_cents": int(r[1] or 0), "expense_cents": int(r[2] or 0)})

    bills = Bill.query.filter_by(user_id=user_id).order_by(Bill.next_due.asc().nulls_last()).limit(5).all()

    insight = AgentResult.query.filter_by(user_id=user_id, agent_name="insight_agent_v1").order_by(AgentResult.created_at.desc()).first()
    forecast = AgentResult.query.filter_by(user_id=user_id, agent_name="forecast_agent_v1").order_by(AgentResult.created_at.desc()).first()

    def fmt(cents):
        try:
            c = int(cents)
        except Exception:
            return cents
        rupees = c / 100.0
        return f"₹{rupees:,.2f}"

    tx_out = []
    for t in transactions:
        td = t.to_dict()
        td['amount'] = float(td.get('amount_cents') or 0) / 100.0
        td['amount_display'] = fmt(td.get('amount_cents'))
        tx_out.append(td)

    cat_out = []
    for c in category_totals:
        cat_out.append({"category": c['category'] if isinstance(c, dict) else c[0], "amount_cents": int(c['amount_cents'] if isinstance(c, dict) and 'amount_cents' in c else c[1]), "amount_display": fmt(c[1] if not (isinstance(c, dict) and 'amount_cents' in c) else c['amount_cents'])})

    monthly_out = []
    for m in monthly_totals:
        monthly_out.append({"month": m.get('month'), "income_cents": m.get('income_cents'), "income_display": fmt(m.get('income_cents')), "expense_cents": m.get('expense_cents'), "expense_display": fmt(m.get('expense_cents'))})

    bills_out = []
    for b in bills:
        bd = b.to_dict()
        bd['amount'] = float(bd.get('amount_cents') or 0) / 100.0
        bd['amount_display'] = fmt(bd.get('amount_cents'))
        bills_out.append(bd)

    context = {
        "transactions": tx_out,
        "category_totals": cat_out,
        "monthly_totals": monthly_out,
        "upcoming_bills": bills_out,
        "insight_summary": (insight.result_json if insight else None),
        "forecast_summary": (forecast.result_json if forecast else None)
    }
    return context


def build_chat_prompt(user_message: str, context: Dict[str, Any], prior_chat: list | None = None):
    """Build the exact system + context + user messages per user's template.
    Optionally include prior chat messages (list of dicts with 'role' and 'content') to allow follow-ups.
    """
    system_msg = (
        """
You are FinAssist, a financial assistant that answers questions using ONLY the data provided.
You must follow these rules strictly:

1. Never invent transactions, bills, or numbers.
2. Never guess information that is not provided in the context.
3. If you don't have enough information, reply:
   "I don't have enough information to answer that."
4. Keep answers short, clear, and friendly.
5. Do not perform actions or create new transactions. You are read-only.
6. Base all answers strictly on the context given below.
Use the context to answer user questions clearly.

Additionally, when appropriate given the user's question and the provided context, produce concise, practical financial guidance in three areas:
- Savings plan: suggest an achievable short-term plan (1-12 months) to build an emergency buffer, expressed as a weekly/monthly target and a simple priority order (e.g., reduce X, move Y to savings). Base any numeric targets strictly on amounts available in the context; if the context lacks sufficient data, state that and offer sensible percentage-based guidance (e.g., "aim for 5-10% of net income") without inventing exact rupee amounts.
- Budget plan: provide a simple monthly budget split (percentages per category or high-level buckets: essentials, savings, discretionary) calibrated to the user's recent income/expense data in the context. If income is missing, present percentage ranges and explain assumptions.
- Low-risk investment suggestions: list 2–3 low-risk options (e.g., fixed deposits, high-quality government bonds, short-term debt funds) with short notes on time horizon and liquidity. Do NOT claim returns that are not in the context; instead give qualitative guidance (e.g., "low expected returns, high capital preservation"). Mention inflation sensitivity briefly and recommend time horizon.

Always be explicit about assumptions and show where you drew numbers from the provided context. If you must give ranges because exact values are missing, label them clearly (e.g., "estimate, based on last 3 months' average"). Never fabricate exact transaction-level data.

Important: All monetary amounts in the context are in Indian Rupees (INR). Numeric amounts are provided in paise (stored as `amount_cents`). When presenting amounts to the user, convert to rupees (divide by 100) and use the `₹` symbol. Be explicit that amounts are in ₹.
        """
    )

    ctx_json = json.dumps(context, indent=2, default=str)
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "system", "content": f"Context (JSON):\n{ctx_json}"}
    ]

    if prior_chat:
        for m in prior_chat:
            messages.append({"role": m.get('role', 'user'), "content": m.get('content', '')})

    messages.append({"role": "user", "content": user_message})
    return messages


def run_chat_agent(user_id: str, message: str) -> str:
    """Gather context from DB, build the prompt, call OpenAI, and return assistant text.
    Returns readable error strings on failure. No DB writes (besides optional chat logging handled by the caller).
    """
    load_dotenv()
    api_key = os.environ.get('OPENAI_API_KEY')
    model = "gpt-4o-mini"
    if not api_key:
        return 'OPENAI_API_KEY not set. Please add it to your .env file.'

    try:
        context = prepare_chat_context(user_id)
    except Exception as e:
        return f'Error gathering context: {e}'

    try:
        prior = ChatLog.query.filter_by(user_id=user_id).order_by(ChatLog.created_at.desc()).limit(8).all()
        prior = list(reversed([{"role": p.role, "content": p.content} for p in prior]))
    except Exception:
        prior = None

    messages = build_chat_prompt(message, context, prior_chat=prior)

    try:
        # Add a small retry/backoff loop to handle transient 429 rate-limit errors
        import time
        client = OpenAI(api_key=api_key)
        attempts = 3
        backoff = 1.0
        last_err = None
        for attempt in range(1, attempts + 1):
            try:
                response = client.chat.completions.create(model=model, messages=messages, max_tokens=500, temperature=0.0)
                assistant = response.choices[0].message.content
                return assistant.strip()
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                # if rate-limited or transient server error, wait and retry
                if '429' in msg or 'rate limit' in msg or 'too many' in msg or 'server error' in msg:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                # otherwise don't retry
                return f'OpenAI request failed: {e}'
        # exhausted retries
        return f'OpenAI request failed after retries: {last_err}'
    except Exception as e:
        return f'OpenAI setup failed: {e}'
