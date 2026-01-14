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
from .chat_agent_v2 import groq_limiter


def gather_insight_context(user_id: str) -> Dict[str, Any]:
    # recent transactions and category totals - ONLY EXPENSES, NOT INCOME
    txns = Transaction.query.filter_by(user_id=user_id, txn_type='expense').order_by(Transaction.occurred_at.desc()).limit(100).all()
    # only include transactions that have a valid category to avoid showing a fallback label
    # also filter to expenses only to exclude salary and other income
    categories = db.session.execute(text("""
        SELECT c.name as category, SUM(t.amount_cents) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = :uid AND t.txn_type = 'expense'
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
    # Simpler, clearer prompt for JSON output
    txns = context.get('transactions', [])
    cats = context.get('category_totals', [])
    
    txns_summary = json.dumps(txns[:20], default=str)  # First 20 transactions
    cats_summary = json.dumps(cats, default=str)
    
    prompt = f"""Analyze this spending data and return ONLY a valid JSON object (no extra text before or after):

Transactions (last 20): {txns_summary}

Categories: {cats_summary}

Return this JSON structure exactly:
{{
  "top_spending_category": "category name",
  "top_spending_amount_rupees": number,
  "behavior_pattern": "2-3 sentences about spending habits",
  "spending_health": "GOOD or OKAY or CONCERNING",
  "week_over_week_change_percent": "+5% or -3%",
  "key_observation": "one key insight",
  "top_3_categories": [
    {{"category": "name", "amount_rupees": number, "percentage_of_total": "XX%"}}
  ],
  "recommendation": "specific actionable advice with amounts"
}}

IMPORTANT: Return ONLY the JSON object. No markdown, no explanation, no code blocks. Just valid JSON."""
    
    return prompt


def _compute_insight_signature(context: Dict[str, Any]) -> str:
    """Compute a stable signature for insight context to enable caching."""
    sig_obj = {
        'top_cats': sorted([{ 'category': c.get('category'), 'amount_cents': c.get('amount_cents', 0)} for c in context.get('category_totals', [])], key=lambda x: (x['category'] or '')),
        'recent_tx_ids': [t.get('id') for t in context.get('transactions', [])[:50]]
    }
    s = json.dumps(sig_obj, sort_keys=True, default=str)
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def run_insights_agent_for_user(user_id: str, force_ai: bool = True) -> Dict[str, Any]:
    """Return AI-powered insights for spending patterns.
    
    Always calls AI to provide fresh, detailed analysis of spending behavior.
    """
    load_dotenv()
    api_key = os.environ.get('GROQ_API_KEY')
    model = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')

    context = gather_insight_context(user_id)
    
    # Always generate fresh insights via AI
    if not api_key:
        return {'error': 'GROQ_API_KEY not configured', 'message': 'AI insights unavailable'}
    
    # Check rate limits
    can_proceed, limit_msg = groq_limiter.check_limits()
    if not can_proceed:
        return {'error': limit_msg, 'message': 'Rate limit reached'}
    
    groq_limiter.record_request()
    
    # Call AI for comprehensive insights
    try:
        prompt = build_insight_prompt(context)
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        response = client.chat.completions.create(model=model, messages=[{'role':'user','content': prompt}], max_tokens=1500, temperature=0.0)
        assistant = response.choices[0].message.content
        
        # Try to extract JSON from response (may have extra text, markdown blocks, etc)
        try:
            ai_insights = json.loads(assistant)
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
                    ai_insights = json.loads(cleaned[start:end+1])
                except json.JSONDecodeError:
                    # Return raw response with error
                    ai_insights = {'error': 'JSON parsing failed', 'raw_response': assistant[:300]}
            else:
                ai_insights = {'error': 'No JSON found in response', 'raw_response': assistant[:300]}

        # persist AI result
        try:
            sig = _compute_insight_signature(context)
            ar = AgentResult(user_id=user_id, agent_name='insight_agent_v1', input_text=sig, result_json=ai_insights)
            db.session.add(ar)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

        return ai_insights
    except Exception as e:
        return {'error': f'AI analysis failed: {str(e)}', 'message': 'Could not generate insights'}