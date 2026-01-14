"""
Enhanced Chat Agent v2 - Production Grade
- Better system prompts
- Response formatting
- Semantic caching
- Rate limiting
- Chat history management
"""

import os
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
from openai import OpenAI
import hashlib
import difflib
from collections import deque
import threading

from db import db
from models import Transaction, Bill, Category, ChatLog, AgentResult
from sqlalchemy.sql import text

load_dotenv()

# ============================================================================
# RATE LIMITER FOR GROQ API (Free tier limits)
# ============================================================================

class GroqRateLimiter:
    def __init__(self):
        self.requests_per_minute = deque()
        self.requests_per_day = deque()
        self.lock = threading.Lock()
        
        # Groq free tier limits (conservative values)
        self.RPM_LIMIT = 25  # 30 allowed, using 25 for safety margin
        self.RPD_LIMIT = 14000  # 14,400 allowed, using 14,000 for safety
    
    def check_limits(self):
        """Check if we can make a request without exceeding rate limits."""
        with self.lock:
            now = datetime.utcnow()
            
            # Clean up old entries (older than 1 minute)
            while self.requests_per_minute and now - self.requests_per_minute[0] > timedelta(minutes=1):
                self.requests_per_minute.popleft()
            
            # Clean up old entries (older than 24 hours)
            while self.requests_per_day and now - self.requests_per_day[0] > timedelta(days=1):
                self.requests_per_day.popleft()
            
            # Check limits
            if len(self.requests_per_minute) >= self.RPM_LIMIT:
                return False, f"Rate limit: {self.RPM_LIMIT} requests per minute exceeded. Please wait a moment."
            
            if len(self.requests_per_day) >= self.RPD_LIMIT:
                return False, f"Daily limit: {self.RPD_LIMIT} requests per day exceeded. Please try again tomorrow."
            
            return True, "OK"
    
    def record_request(self):
        """Record that a request was made."""
        with self.lock:
            now = datetime.utcnow()
            self.requests_per_minute.append(now)
            self.requests_per_day.append(now)

# Global rate limiter instance
groq_limiter = GroqRateLimiter()

# ============================================================================
# SEMANTIC SIMILARITY CACHING (Simple Version - No embeddings needed)
# ============================================================================

class SimpleSemanticCache:
    """Simple word-based semantic caching. No ML required!"""
    
    @staticmethod
    def normalize_question(text: str) -> set:
        """Convert question to word set for comparison"""
        # Remove common words, convert to lowercase
        stop_words = {'what', 'is', 'the', 'a', 'an', 'my', 'i', 'me', 'can', 'could', 
                     'would', 'should', 'how', 'when', 'where', 'why', 'how much', 'how many'}
        words = set(text.lower().split())
        return words - stop_words
    
    @staticmethod
    def similarity_score(q1: str, q2: str) -> float:
        """Calculate similarity between two questions (0-1)"""
        words1 = SimpleSemanticCache.normalize_question(q1)
        words2 = SimpleSemanticCache.normalize_question(q2)
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        jaccard = intersection / union if union > 0 else 0
        
        return jaccard
    
    @staticmethod
    def find_cached_answer(user_id: str, question: str, min_similarity: float = 0.70) -> Optional[str]:
        """Find similar cached answer"""
        try:
            # Get recent cache entries (last 7 days)
            cutoff = datetime.utcnow() - timedelta(days=7)
            cached = db.session.execute(
                text("""
                    SELECT question_text, answer_text, similarity_score 
                    FROM chat_cache 
                    WHERE user_id = :uid AND cached_at > :cutoff
                    ORDER BY hit_count DESC
                    LIMIT 50
                """),
                {"uid": user_id, "cutoff": cutoff}
            ).fetchall()
            
            best_match = None
            best_score = 0
            
            for cached_question, cached_answer, stored_score in cached:
                score = SimpleSemanticCache.similarity_score(question, cached_question)
                
                if score > best_score and score >= min_similarity:
                    best_score = score
                    best_match = (cached_answer, score)
            
            return best_match
        except Exception:
            return None
    
    @staticmethod
    def cache_answer(user_id: str, question: str, answer: str, similarity: float = 1.0):
        """Store question-answer pair in cache"""
        try:
            from models import ChatCache
            cache_entry = ChatCache(
                user_id=user_id,
                question_text=question,
                answer_text=answer,
                similarity_score=similarity,
                cached_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(days=7)
            )
            db.session.add(cache_entry)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except:
                pass


# ============================================================================
# IMPROVED SYSTEM PROMPTS
# ============================================================================

SYSTEM_PROMPT_V2 = """
You are FinAssist, a friendly financial assistant helping users understand and manage their money better.

## Your Job
- Answer questions about their transactions, spending, and bills
- Help them understand their financial patterns
- Give practical advice when asked
- Think deeply about their data to provide insights

## Key Rules
✓ ONLY use data from the context provided - never invent transactions or amounts
✓ Think step-by-step about the data to answer questions accurately
✓ All amounts are in Indian Rupees (₹) - always use the ₹ symbol
✓ Keep answers natural and conversational (not templated)
✓ Use clear formatting for readability
✓ If you don't have information, just say so

## How to Answer
- Be direct and friendly - like talking to a helpful friend
- NO "Answer First:", "Key Insight:", "Actionable Advice:" labels - just answer naturally
- Use good spacing and formatting for readability
- Include examples from their data when relevant
- If they ask about forecasting, trends, or patterns - analyze their data and share what you find
- Feel free to give advice about savings, budgeting, or spending if appropriate

## For Different Question Types
- **Spending questions**: Analyze their transactions and explain patterns
- **Budget/forecasting questions**: Look at their historical data and project trends
- **Bill/reminder questions**: Reference their upcoming or recent bills
- **Advice questions**: Think about their situation and give practical suggestions
- **General finance questions**: Answer helpfully based on their data when relevant

## Formatting Tips
- Use **bold** for ALL money amounts (₹50,000), key numbers, and important findings
- Use **bold** for recommendations or advice
- Use **bold** for category names when discussing spending (e.g., **Dining Out** was ₹2,400)
- Use bullet points or numbered lists when helpful
- Add line breaks between sections for readability
- Keep paragraphs short and easy to scan

## When to Use Bold
✓ All rupee amounts: **₹50,000**, **₹2,400**
✓ Key findings: **Your highest spending was Fuel**, **You saved ₹10,000 this month**
✓ Category names: **Healthcare**, **Groceries**, **Entertainment**
✓ Important advice: **Try to reduce dining expenses by 20%**, **Build an emergency fund**
✓ Trends or patterns: **Your spending is increasing**, **This is 30% higher than last month**
✓ Action items: **Set a budget of ₹5,000 for Groceries**, **Pay this bill by 15th January**

## Important Notes
- All monetary amounts in the context are in Indian Rupees (INR)
- amount_cents in the data is stored as paise (divide by 100 to get rupees)
- Example: 50000 paise = ₹500
- Always be honest - if the data doesn't support something, say so
"""


def prepare_chat_context_v2(user_id: str) -> Dict[str, Any]:
    """Gather comprehensive chat context including all recent months"""
    
    # Get transactions from last 6 months (180 days) to include past months like November
    cutoff = datetime.utcnow() - timedelta(days=180)
    transactions = Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.occurred_at >= cutoff
    ).order_by(Transaction.occurred_at.desc()).limit(100).all()
    
    # Get spending by category (last 6 months for complete picture)
    category_rows = db.session.execute(text("""
        SELECT c.name as category, SUM(t.amount_cents) as total
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        WHERE t.user_id = :uid AND t.txn_type = 'expense'
            AND t.occurred_at >= :cutoff
        GROUP BY c.name
        ORDER BY total DESC
        LIMIT 10
    """), {"uid": user_id, "cutoff": cutoff.isoformat()}).fetchall()
    
    # Get monthly summary
    today = datetime.utcnow()
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    income_cents = db.session.query(
        db.func.coalesce(db.func.sum(Transaction.amount_cents), 0)
    ).filter(
        Transaction.user_id == user_id,
        Transaction.txn_type == 'income',
        Transaction.occurred_at >= month_start
    ).scalar() or 0
    
    expense_cents = db.session.query(
        db.func.coalesce(db.func.sum(Transaction.amount_cents), 0)
    ).filter(
        Transaction.user_id == user_id,
        Transaction.txn_type == 'expense',
        Transaction.occurred_at >= month_start
    ).scalar() or 0
    
    # Format output
    tx_out = []
    for t in transactions:
        tx_out.append({
            'date': t.occurred_at.strftime('%Y-%m-%d'),
            'type': t.txn_type,
            'amount': f"₹{int(t.amount_cents)/100:.2f}",
            'description': t.description[:100]
        })
    
    cat_out = []
    for row in category_rows:
        cat_out.append({
            'category': row[0],
            'amount': f"₹{int(row[1])/100:.2f}"
        })
    
    # Upcoming bills
    bills = Bill.query.filter(
        Bill.user_id == user_id,
        Bill.active == True,
        Bill.next_due != None
    ).order_by(Bill.next_due).limit(5).all()
    
    bills_out = []
    for b in bills:
        days_until = (b.next_due - today).days
        bills_out.append({
            'name': b.name,
            'amount': f"₹{int(b.amount_cents)/100:.2f}",
            'due_in_days': days_until,
            'due_date': b.next_due.strftime('%Y-%m-%d')
        })
    
    return {
        'summary': {
            'month': today.strftime('%B %Y'),
            'total_income': f"₹{int(income_cents)/100:.2f}",
            'total_expense': f"₹{int(expense_cents)/100:.2f}",
            'net': f"₹{int(income_cents - expense_cents)/100:.2f}"
        },
        'recent_transactions': tx_out,
        'spending_by_category': cat_out,
        'upcoming_bills': bills_out
    }


def build_chat_prompt_v2(user_message: str, context: Dict[str, Any], chat_history: List = None) -> List[Dict]:
    """Build optimized prompt with context"""
    
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT_V2
        },
        {
            "role": "system",
            "content": f"CURRENT DATA:\n{json.dumps(context, indent=2, default=str)}"
        }
    ]
    
    # Add chat history (last 5 exchanges)
    if chat_history:
        for msg in chat_history[-10:]:  # Last 10 messages (5 exchanges)
            messages.append({
                "role": msg.get('role', 'user'),
                "content": msg.get('content', '')
            })
    
    # Add current question
    messages.append({
        "role": "user",
        "content": user_message
    })
    
    return messages


def run_chat_agent_v2(user_id: str, message: str) -> Dict[str, Any]:
    """
    Enhanced chat agent with:
    - Semantic caching
    - Better prompts
    - Response formatting
    - Rate limiting
    """
    
    load_dotenv()
    api_key = os.environ.get('GROQ_API_KEY')
    
    if not api_key:
        return {
            'success': False,
            'reply': '❌ API key not configured. Please set GROQ_API_KEY in .env',
            'cached': False
        }
    
    # ===== STEP 1: Check cache =====
    cache_result = SimpleSemanticCache.find_cached_answer(user_id, message, min_similarity=0.75)
    if cache_result:
        cached_answer, similarity = cache_result
        
        # Log cache hit
        try:
            chat_log = ChatLog(
                user_id=user_id,
                role='assistant',
                content=cached_answer
            )
            db.session.add(chat_log)
            db.session.commit()
        except:
            db.session.rollback()
        
        return {
            'success': True,
            'reply': cached_answer,
            'cached': True,
            'similarity': f"{similarity:.1%}"
        }
    
    # ===== STEP 2: Prepare context =====
    try:
        context = prepare_chat_context_v2(user_id)
    except Exception as e:
        return {
            'success': False,
            'reply': f'Error gathering financial data: {str(e)}',
            'cached': False
        }
    
    # ===== STEP 3: Get chat history =====
    try:
        history = ChatLog.query.filter_by(user_id=user_id).order_by(
            ChatLog.created_at.desc()
        ).limit(10).all()
        history = list(reversed([{"role": h.role, "content": h.content} for h in history]))
    except:
        history = None
    
    # ===== STEP 4: Build prompt =====
    messages = build_chat_prompt_v2(message, context, history)
    
    # ===== STEP 5: Call Groq API =====
    try:
        can_proceed, limit_msg = groq_limiter.check_limits()
        if not can_proceed:
            return {
                'success': False,
                'reply': limit_msg,
                'cached': False
            }
        
        groq_limiter.record_request()
        
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )
        
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1200,
            temperature=0.3  # Lower temp for more consistent output
        )
        
        assistant_reply = response.choices[0].message.content.strip()
        
    except Exception as e:
        return {
            'success': False,
            'reply': f'❌ API Error: {str(e)}',
            'cached': False
        }
    
    # ===== STEP 6: Store in database =====
    try:
        # Store user message
        user_log = ChatLog(user_id=user_id, role='user', content=message)
        db.session.add(user_log)
        
        # Store assistant reply
        asst_log = ChatLog(user_id=user_id, role='assistant', content=assistant_reply)
        db.session.add(asst_log)
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    
    # ===== STEP 7: Cache the answer =====
    SimpleSemanticCache.cache_answer(user_id, message, assistant_reply)
    
    # ===== STEP 8: Return result =====
    return {
        'success': True,
        'reply': assistant_reply,
        'cached': False,
        'source': 'groq'
    }
