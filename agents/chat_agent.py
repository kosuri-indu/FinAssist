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
    # Last 20 transactions
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.occurred_at.desc()).limit(20).all()

    # Category totals (join category names where available)
    sql = text("""
        SELECT COALESCE(c.name, 'Uncategorized') as category, SUM(t.amount_cents) as total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = :uid
        GROUP BY category
    """)
    category_rows = db.session.execute(sql, {"uid": user_id}).fetchall()
    category_totals = [{"category": r[0], "amount_cents": int(r[1] or 0)} for r in category_rows]

    # Monthly totals (simple summary per month: income vs expense) - last 6 months
    # Use DB-specific SQL: SQLite uses strftime, Postgres uses to_char(date_trunc(...),'YYYY-MM')
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
        # Assume Postgres-compatible SQL
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

    # Upcoming bills
    bills = Bill.query.filter_by(user_id=user_id).order_by(Bill.next_due.asc().nulls_last()).limit(5).all()

    # Latest agent results (insight & forecast)
    insight = AgentResult.query.filter_by(user_id=user_id, agent_name="insight_agent_v1").order_by(AgentResult.created_at.desc()).first()
    forecast = AgentResult.query.filter_by(user_id=user_id, agent_name="forecast_agent_v1").order_by(AgentResult.created_at.desc()).first()

    def fmt(cents):
        try:
            c = int(cents)
        except Exception:
            return cents
        rupees = c / 100.0
        return f"₹{rupees:,.2f}"

    # Map transactions and bills to include a user-friendly display amount
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
        """
    )

    ctx_json = json.dumps(context, indent=2, default=str)
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "system", "content": f"Context (JSON):\n{ctx_json}"}
    ]

    if prior_chat:
        # prior_chat expected as list of {'role': 'user'|'assistant', 'content': '...'} in chronological order
        for m in prior_chat:
            messages.append({"role": m.get('role', 'user'), "content": m.get('content', '')})

    # finally add the current user message
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

    # prepare context from DB
    try:
        context = prepare_chat_context(user_id)
    except Exception as e:
        return f'Error gathering context: {e}'

    # fetch recent chat history (last 8 messages) so short follow-ups have context
    try:
        prior = ChatLog.query.filter_by(user_id=user_id).order_by(ChatLog.created_at.desc()).limit(8).all()
        prior = list(reversed([{"role": p.role, "content": p.content} for p in prior]))
    except Exception:
        prior = None

    messages = build_chat_prompt(message, context, prior_chat=prior)

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(model=model, messages=messages, max_tokens=500, temperature=0.0)
        assistant = response.choices[0].message.content
        return assistant.strip()
    except Exception as e:
        return f'OpenAI request failed: {e}'
