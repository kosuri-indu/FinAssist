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
from .chat_agent_v2 import groq_limiter


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
    # Simpler, clearer prompt for JSON output
    months = context.get('monthly_history', [])
    cat_history = context.get('category_history', {})
    
    months_summary = json.dumps(months, default=str)
    
    prompt = f"""Analyze this spending history and return ONLY a valid JSON object (no extra text before or after):

Monthly History (last 6 months): {months_summary}

Return this JSON structure exactly:
{{
  "predicted_expense_rupees": number,
  "predicted_income_rupees": number,
  "expected_net_rupees": number,
  "confidence": "HIGH or MEDIUM or LOW",
  "forecast_explanation": "2-3 sentences explaining the forecast",
  "category_predictions": [
    {{"category": "name", "predicted_rupees": number, "trend": "UP or DOWN or STABLE"}}
  ],
  "biggest_risk": "what could go wrong",
  "savings_opportunity": "specific way to save money with ₹ amount",
  "monthly_summary": {{
    "avg_last_3_months_expense_rupees": number,
    "trend_direction": "INCREASING or STABLE or DECREASING",
    "total_months_analyzed": number
  }}
}}

IMPORTANT: Return ONLY the JSON object. No markdown, no explanation, no code blocks. Just valid JSON."""
    
    return prompt


def _compute_forecast_signature(context: Dict[str, Any]) -> str:
    s = json.dumps(context.get('monthly_history', []), sort_keys=True, default=str)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def run_forecast_agent_for_user(user_id: str, force_ai: bool = True) -> Dict[str, Any]:
    """Return AI-powered financial forecast for next month.
    
    Always calls AI to provide detailed spending predictions and insights.
    """
    load_dotenv()
    api_key = os.environ.get('GROQ_API_KEY')
    model = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')

    context = gather_forecast_context(user_id)
    
    # Always generate fresh forecast via AI
    if not api_key:
        return {'error': 'GROQ_API_KEY not configured', 'message': 'AI forecast unavailable'}
    
    # Check rate limits
    can_proceed, limit_msg = groq_limiter.check_limits()
    if not can_proceed:
        return {'error': limit_msg, 'message': 'Rate limit reached'}
    
    groq_limiter.record_request()    
    # Call AI for comprehensive forecast
    try:
        prompt = build_forecast_prompt(context)
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        response = client.chat.completions.create(model=model, messages=[{'role':'user','content': prompt}], max_tokens=2000, temperature=0.0)
        assistant = response.choices[0].message.content
        
        # Try to extract JSON from response (may have extra text, markdown blocks, etc)
        try:
            ai_forecast = json.loads(assistant)
        except json.JSONDecodeError:
            # Remove markdown code blocks if present
            cleaned = assistant
            if '```json' in cleaned:
                start = cleaned.find('{', cleaned.find('```json'))
                end = cleaned.rfind('}')
            elif '```' in cleaned:
                start = cleaned.find('{', cleaned.find('```'))
                end = cleaned.rfind('}')
            else:
                start = cleaned.find('{')
                end = cleaned.rfind('}')
            
            if start != -1 and end != -1:
                try:
                    ai_forecast = json.loads(cleaned[start:end+1])
                except json.JSONDecodeError:
                    # Return raw response with error
                    ai_forecast = {'error': 'JSON parsing failed', 'raw_response': assistant[:300]}
            else:
                ai_forecast = {'error': 'No JSON found in response', 'raw_response': assistant[:300]}

        # persist AI result
        try:
            sig = _compute_forecast_signature(context)
            ar = AgentResult(user_id=user_id, agent_name='forecast_agent_v1', input_text=sig, result_json=ai_forecast)
            db.session.add(ar)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

        return ai_forecast
    except Exception as e:
        return {'error': f'AI forecast failed: {str(e)}', 'message': 'Could not generate forecast'}