import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, jsonify
from dotenv import load_dotenv
from db import init_db, db
from werkzeug.security import generate_password_hash, check_password_hash
from models import User, Bill, Category, Transaction, ChatLog, AgentResult
from datetime import datetime, timedelta
import csv
import io
import json
from sqlalchemy import inspect, text, create_engine
from sqlalchemy.exc import OperationalError
from threading import Thread
from agents.chat_agent_v2 import run_chat_agent_v2, groq_limiter
from agents.insights_agent import run_insights_agent_for_user
from agents.forecast_agent import run_forecast_agent_for_user
from agents.reminder_agent import get_upcoming_reminders, mark_bill_paid, run_reminder_agent_for_user

def _compute_next_due_from(start_date, period, interval_count=1):
    if not start_date:
        return None
    now = datetime.utcnow()
    try:
        cur = start_date
    except Exception:
        return None
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

load_dotenv()
app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret')


def get_current_user():
    """Return the logged-in User or None.

    This function is made resilient to transient DB connection closures by
    retrying once after rolling back the session when an OperationalError occurs.
    """
    user_id = session.get('user_id')
    if not user_id:
        return None
    try:
        return User.query.get(user_id)
    except OperationalError as oe:
        # transient DB issue: rollback and retry once
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            return User.query.get(user_id)
        except Exception:
            return None
    except Exception:
        return None


@app.route('/')
def index():
    return render_template('auth_ui.html')

@app.route('/signup', methods=['POST'])
def signup():
    email = request.form.get('signupEmail')
    password = request.form.get('signupPassword')
    if not email or not password:
        flash('Email and password are required.', 'error')
        return redirect(url_for('index'))
    existing = User.query.filter_by(email=email).first()
    if existing:
        flash('Email already registered. Please log in.', 'error')
        return redirect(url_for('index'))
    user = User(email=email, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    session['user_id'] = user.id
    flash('Account created. Welcome!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('loginEmail')
    password = request.form.get('loginPassword')
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('No account found with this email. Please create an account first.', 'error')
        return redirect(url_for('index'))
    if not check_password_hash(user.password_hash, password):
        flash('Invalid password. Please try again.', 'error')
        return redirect(url_for('index'))
    session['user_id'] = user.id
    flash('Logged in successfully.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    return render_template('dashboard.html')


@app.route('/_debug/whoami')
def _debug_whoami():
    """Return session user id and whether the User exists (debug helper)."""
    uid = session.get('user_id')
    user = None
    try:
        if uid:
            user = User.query.get(uid)
    except Exception as e:
        return jsonify({'session_user_id': uid, 'error': str(e)})
    return jsonify({'session_user_id': uid, 'user_exists': bool(user), 'user': user.to_dict() if user else None})


@app.route('/chat')
def chat():
    # Use chat template
    return render_template('chat.html')


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """Enhanced chat endpoint with semantic caching and rate limiting.
    Returns: {success, reply, cached, source, similarity, error}
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required', 'success': False}), 401

    data = request.get_json(silent=True) or {}
    message = data.get('message') if isinstance(data, dict) else None
    if not message:
        return jsonify({'error': 'no message provided', 'success': False}), 400

    # Check rate limits before processing
    can_proceed, limit_msg = groq_limiter.check_limits()
    if not can_proceed:
        return jsonify({
            'error': f'Rate limit: {limit_msg}',
            'success': False
        }), 429

    try:
        # Save user message
        ul = ChatLog(user_id=user.id, role='user', content=message)
        db.session.add(ul)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Failed to save user message: {e}")

    try:
        # Call enhanced chat agent with caching
        result = run_chat_agent_v2(user.id, message)
        
        # Extract response details
        reply = result.get('reply', 'No response generated')
        is_cached = result.get('cached', False)
        similarity = result.get('similarity', '')
        
        # Save assistant message
        al = ChatLog(user_id=user.id, role='assistant', content=reply)
        db.session.add(al)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'reply': reply,
            'cached': is_cached,
            'source': result.get('source', 'api'),
            'similarity': similarity
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Chat agent error: {e}")
        return jsonify({
            'error': str(e),
            'success': False
        }), 500


@app.route('/api/chat/history')
def chat_history():
    """Fetch user's chat history (last 24 hours visible in UI, 7 days in cache)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    
    try:
        # Get messages from last 24 hours for display
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=24)
        
        messages = ChatLog.query.filter(
            ChatLog.user_id == user.id,
            ChatLog.created_at >= cutoff
        ).order_by(ChatLog.created_at.asc()).all()
        
        return jsonify({
            'success': True,
            'messages': [
                {
                    'role': m.role,
                    'content': m.content,
                    'timestamp': m.created_at.isoformat() if m.created_at else None
                }
                for m in messages
            ]
        })
    except Exception as e:
        app.logger.error(f"Failed to fetch chat history: {e}")
        return jsonify({'error': str(e), 'success': False}), 500


@app.route('/api/chat/clear', methods=['POST'])
def clear_chat_history():
    """Clear all chat history for the current user from database."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    
    try:
        # Delete all ChatLog records for this user
        ChatLog.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        app.logger.info(f"Cleared chat history for user {user.id}")
        return jsonify({'success': True, 'message': 'Chat history cleared'}), 200
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Failed to clear chat history: {e}")
        return jsonify({'error': str(e), 'success': False}), 500


# Chat agent endpoints removed — agents disabled in this workspace

@app.route('/bills', methods=['GET'])
def bills():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    bills = Bill.query.filter_by(user_id=user.id).order_by(Bill.next_due.asc().nulls_last()).all()
    now = datetime.utcnow()
    for b in bills:
        try:
            nd = getattr(b, 'next_due', None)
            if not nd or (isinstance(nd, datetime) and nd < now):
                base = getattr(b, 'last_paid', None) or getattr(b, 'created_at', None)
                computed = None
                if base:
                    try:
                        computed = _compute_next_due_from(base, getattr(b, 'period', None), interval_count=1)
                    except Exception:
                        computed = None
                if computed:
                    b.next_due = computed
        except Exception:
            continue
    return render_template('transaction.html', bills=bills)


@app.route('/add')
def add_data():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    return render_template('add_data.html')


@app.route('/transactions')
def transactions_page():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    bills_list = Bill.query.filter_by(user_id=user.id).order_by(Bill.next_due.asc().nulls_last()).all()
    transactions = Transaction.query.filter_by(user_id=user.id).order_by(Transaction.occurred_at.desc()).all()
    # build a lightweight view-friendly list to avoid template attribute errors
    transactions_view = []
    for t in transactions:
        try:
            cat = None
            if t.category_id:
                cat = Category.query.get(t.category_id)
            transactions_view.append({
                'id': t.id,
                'amount_cents': t.amount_cents,
                'amount': t.amount_cents / 100.0,
                'txn_type': t.txn_type,
                'description': t.description,
                'category_name': cat.name if cat else None,
                'occurred_at': t.occurred_at,
                'meta': t.meta
            })
        except Exception:
            transactions_view.append({
                'id': t.id,
                'amount_cents': t.amount_cents,
                'amount': t.amount_cents / 100.0,
                'txn_type': t.txn_type,
                'description': t.description,
                'category_name': None,
                'occurred_at': t.occurred_at,
                'meta': t.meta
            })
    # build list of unique category names for the filter dropdown
    category_names = sorted({tv['category_name'] for tv in transactions_view if tv.get('category_name')})
    return render_template('transaction.html', bills=bills_list, transactions=transactions_view, categories=category_names)


@app.route('/notifications')
def notifications_page():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    # get upcoming reminders for next 7 days
    notes = get_upcoming_reminders(user.id, days=7)
    # transform to template-friendly structure
    notifs = []
    for n in notes:
        meta = n.get('meta', {})
        amt = None
        if meta.get('amount_cents') is not None:
            try:
                amt = round((meta.get('amount_cents') or 0)/100.0, 2)
            except Exception:
                amt = None
        body = n.get('body') or ''
        # format due_date (ISO -> friendly)
        due_iso = meta.get('due_date')
        try:
            if due_iso:
                from datetime import datetime
                dd = datetime.fromisoformat(due_iso)
                due_str = dd.strftime('%a, %b %d • %I:%M %p')
                body = (body + '\nDue: ' + due_str) if body else ('Due: ' + due_str)
        except Exception:
            body = (body + '\nDue: ' + (due_iso or '')) if body else ('Due: ' + (due_iso or ''))
        if amt is not None:
            body = (body + f' • ₹{amt:.2f}')

        # format created_at for display
        created = n.get('created_at')
        created_str = None
        try:
            if created:
                from datetime import datetime
                cd = datetime.fromisoformat(created)
                created_str = cd.strftime('%a, %b %d • %I:%M %p')
        except Exception:
            created_str = created

        notifs.append({'id': n.get('id'), 'title': n.get('title'), 'body': body, 'created_at': created_str, 'type': 'reminder'})
    
    # Run reminder agent to get fresh AI summary
    agent_summary = None
    try:
        result = run_reminder_agent_for_user(user.id, force_ai=False)
        if result and isinstance(result, dict):
            agent_summary = result
        else:
            agent_summary = None
    except Exception as e:
        print(f"Error running reminder agent: {e}")
        agent_summary = None

    return render_template('notifications.html', notifications=notifs, agent_summary=agent_summary)


@app.route('/notifications/<bill_id>/mark_done', methods=['POST'])
def notifications_mark_done(bill_id):
    user = get_current_user()
    if not user:
        return jsonify({'ok': False, 'error': 'authentication required'}), 401
    res = mark_bill_paid(user.id, bill_id)
    return jsonify(res)


@app.route('/api/transactions/recent')
def api_transactions_recent():
    """Return last 5 transactions for current user as JSON."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    try:
        txns = Transaction.query.filter_by(user_id=user.id).order_by(Transaction.occurred_at.desc()).limit(5).all()
        out = []
        for t in txns:
            cat = None
            if t.category_id:
                c = Category.query.get(t.category_id)
                cat = c.name if c else None
            out.append({
                'id': t.id,
                'txn_type': t.txn_type,
                'category': cat,
                'amount_cents': t.amount_cents,
                'amount': round((t.amount_cents or 0)/100,2),
                'occurred_at': t.occurred_at.isoformat() if t.occurred_at else None,
                'description': t.description,
                'meta': t.meta
            })
        return jsonify({'transactions': out})
    except OperationalError as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': 'database error', 'details': str(e)}), 500


@app.route('/api/reminders')
def api_reminders():
    """Return upcoming bills with AI summary and details for dashboard display."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    try:
        # Get reminder data with optional AI enhancement
        force_ai = request.args.get('force_ai', 'false').lower() == 'true'
        result = run_reminder_agent_for_user(user.id, force_ai=force_ai)
        return jsonify(result)
    except OperationalError as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': 'database error', 'details': str(e)}), 500
    except Exception as e:
        return jsonify({'error': 'failed to fetch reminders', 'details': str(e)}), 500


@app.route('/api/agents/insights/run', methods=['POST'])
@app.route('/api/agents/insights/run', methods=['POST'])
def api_agents_insights_run():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    try:
        res = run_insights_agent_for_user(user.id, force_ai=True)
        return jsonify({'result': res})
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': f'Insights agent run failed: {e}'}), 500


@app.route('/api/agents/reminders/run', methods=['POST'])
def api_agents_reminders_run():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    data = request.get_json(silent=True) or {}
    force = bool(data.get('force_ai') or data.get('force'))
    res = run_reminder_agent_for_user(user.id, force_ai=force)
    return jsonify({'result': res})


@app.route('/api/agents/reminders')
def api_agents_reminders_get():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    ar = AgentResult.query.filter_by(user_id=user.id, agent_name='reminder_agent_v1').order_by(AgentResult.created_at.desc()).first()
    return jsonify({'result': ar.result_json if ar else None})


@app.route('/api/agents/insights')
def api_agents_insights_get():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    ar = AgentResult.query.filter_by(user_id=user.id, agent_name='insight_agent_v1').order_by(AgentResult.created_at.desc()).first()
    return jsonify({'result': ar.result_json if ar else None})


@app.route('/api/agents/forecast/run', methods=['POST'])
def api_agents_forecast_run():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    try:
        res = run_forecast_agent_for_user(user.id, force_ai=True)
        return jsonify({'result': res})
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': f'Forecast agent run failed: {e}'}), 500


@app.route('/api/agents/forecast')
def api_agents_forecast_get():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    ar = AgentResult.query.filter_by(user_id=user.id, agent_name='forecast_agent_v1').order_by(AgentResult.created_at.desc()).first()
    return jsonify({'result': ar.result_json if ar else None})


@app.route('/api/dashboard/summary')
def api_dashboard_summary():
    """Return monthly totals and simple utilization for the current user.

    Response shape:
    {
      monthly: { income: int, expense: int, net: int },
      upcoming_bills: [ {id,name,next_due,amount_cents} ... ],
      utilization: { essentials_pct, discretionary_pct },
      trend: { points: [daily_expenses...] }
    }
    """
    user = get_current_user()
    if not user:
        print("API: No user found")
        return jsonify({'error': 'authentication required'}), 401
    try:
        now = datetime.utcnow()
        start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # sum incomes and expenses for current month
        # sums are stored in cents in the DB; convert to rupees for the frontend
        incomes_cents = db.session.query(db.func.coalesce(db.func.sum(Transaction.amount_cents), 0)).filter(Transaction.user_id == user.id, Transaction.txn_type == 'income', Transaction.occurred_at >= start_month).scalar() or 0
        expenses_cents = db.session.query(db.func.coalesce(db.func.sum(Transaction.amount_cents), 0)).filter(Transaction.user_id == user.id, Transaction.txn_type == 'expense', Transaction.occurred_at >= start_month).scalar() or 0
        incomes_cents = int(incomes_cents)
        expenses_cents = int(expenses_cents)
        incomes = round(incomes_cents / 100.0, 2)
        expenses = round(expenses_cents / 100.0, 2)
        
        print(f"API: User {user.id} - Incomes: ₹{incomes}, Expenses: ₹{expenses}")

        # upcoming bills in this month or next 7 days
        upcoming = []
        bills = Bill.query.filter_by(user_id=user.id, active=True).all()
        for b in bills:
            if not b.next_due:
                continue
            nd = b.next_due
            days_left = (nd.date() - now.date()).days
            # include if in current month
            if nd.year == now.year and nd.month == now.month:
                upcoming.append({'id': b.id, 'name': b.name, 'next_due': nd.isoformat(), 'amount_cents': b.amount_cents, 'amount': round((b.amount_cents or 0)/100.0,2)})
            # also include if within next 7 days
            elif 0 <= days_left <= 7:
                upcoming.append({'id': b.id, 'name': b.name, 'next_due': nd.isoformat(), 'amount_cents': b.amount_cents})

        # simple utilization mock: essentials = 60% of expenses, discretionary 40% (placeholder)
        util = {'essentials_pct': 60, 'discretionary_pct': 40}
        
        # Calculate spending trend: last 7 days of daily expenses
        trend_points = []
        for day_offset in range(6, -1, -1):  # 6 days ago to today
            day_start = (now - timedelta(days=day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = (now - timedelta(days=day_offset)).replace(hour=23, minute=59, second=59, microsecond=999999)
            day_expense = db.session.query(db.func.coalesce(db.func.sum(Transaction.amount_cents), 0)).filter(
                Transaction.user_id == user.id,
                Transaction.txn_type == 'expense',
                Transaction.occurred_at >= day_start,
                Transaction.occurred_at <= day_end
            ).scalar() or 0
            daily_rupees = round(int(day_expense) / 100.0, 2)
            trend_points.append(max(daily_rupees, 0))  # Ensure non-negative

        # return rupee values for easier display in the frontend
        response = {
            'monthly': {'income': incomes, 'expense': expenses, 'net': round((incomes - expenses),2)},
            'upcoming_bills': upcoming,
            'utilization': util,
            'trend': {'points': trend_points}
        }
        print(f"API: Returning response: {response}")
        return jsonify(response)
    except OperationalError as e:
        print(f"API: OperationalError - {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': 'database error', 'details': str(e)}), 500
    except Exception as e:
        print(f"API: Unexpected error - {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'server error', 'details': str(e)}), 500


@app.route('/api/notifications/json')
def api_notifications_json():
    """Produce a simple notifications JSON derived from the bills table (no Notification model required).

    Returns: { notifications: [ {id,title,type,meta,created_at} ] }
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    try:
        now = datetime.utcnow()
        notes = []
        bills = Bill.query.filter_by(user_id=user.id, active=True).all()
        for b in bills:
            if not b.next_due:
                continue
            nd = b.next_due
            days_left = (nd.date() - now.date()).days
            meta = {'bill_id': b.id, 'due_date': nd.isoformat(), 'amount_cents': b.amount_cents}
            if days_left < 0:
                notes.append({'id': b.id, 'title': f"{b.name}", 'type': 'bill_overdue', 'meta': meta, 'created_at': b.created_at.isoformat() if b.created_at else None})
            elif days_left == 0:
                notes.append({'id': b.id, 'title': f"{b.name}", 'type': 'bill_due_today', 'meta': meta, 'created_at': b.created_at.isoformat() if b.created_at else None})
            elif 1 <= days_left <= 7:
                notes.append({'id': b.id, 'title': f"{b.name}", 'type': 'bill_upcoming_week', 'meta': meta, 'created_at': b.created_at.isoformat() if b.created_at else None})
            elif nd.year == now.year and nd.month == now.month:
                notes.append({'id': b.id, 'title': f"{b.name}", 'type': 'bill_upcoming_month', 'meta': meta, 'created_at': b.created_at.isoformat() if b.created_at else None})

        return jsonify({'notifications': notes})
    except OperationalError as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({'error': 'database error', 'details': str(e)}), 500


@app.route('/transactions/<txn_id>/delete', methods=['POST'])
def delete_transaction(txn_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    txn = Transaction.query.filter_by(id=txn_id, user_id=user.id).first()
    if not txn:
        flash('Transaction not found.', 'error')
        return redirect(url_for('transactions_page'))
    db.session.delete(txn)
    db.session.commit()
    flash('Transaction deleted.', 'success')
    return redirect(url_for('transactions_page'))


@app.route('/api/transactions')
def api_transactions():
    """Return JSON with bills and transactions for the current user (debug / useful for frontend checks)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'authentication required'}), 401
    bills = Bill.query.filter_by(user_id=user.id).order_by(Bill.next_due.asc().nulls_last()).all()
    txns = Transaction.query.filter_by(user_id=user.id).order_by(Transaction.occurred_at.desc()).all()
    bills_out = []
    for b in bills:
        bills_out.append({'id': b.id, 'name': b.name, 'description': b.description, 'amount_cents': b.amount_cents, 'next_due': b.next_due.isoformat() if b.next_due else None})
    txns_out = []
    for t in txns:
        txns_out.append({'id': t.id, 'txn_type': t.txn_type, 'amount_cents': t.amount_cents, 'description': t.description, 'category_id': t.category_id, 'occurred_at': t.occurred_at.isoformat() if t.occurred_at else None, 'meta': t.meta})
    return jsonify({'bills': bills_out, 'transactions': txns_out})


def get_or_create_category(user, name):
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    try:
        cat = None
        if user:
            cat = Category.query.filter_by(user_id=user.id, name=name).first()
        if not cat:
            cat = Category.query.filter_by(user_id=None, name=name).first()
        if cat:
            return cat
        cat = Category(user_id=user.id if user else None, name=name)
        db.session.add(cat)
        db.session.commit()
        return cat
    except Exception:
        db.session.rollback()
        return None


@app.route('/transactions/create', methods=['POST'])
def create_transaction():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    txn_type = request.form.get('txn_type') or request.form.get('type') or 'expense'
    amount = request.form.get('amount')
    try:
        amount_cents = int(float(amount) * 100)
    except Exception:
        amount_cents = 0
    occurred_at = request.form.get('occurred_at')
    occurred = None
    if occurred_at:
        try:
            occurred = datetime.fromisoformat(occurred_at)
        except Exception:
            occurred = datetime.utcnow()
    else:
        occurred = datetime.utcnow()
    description = request.form.get('description')
    category_name = request.form.get('category')
    category = get_or_create_category(user, category_name) if category_name else None
    meta = {}
    payment_mode = request.form.get('payment_mode')
    if payment_mode:
        meta['payment_mode'] = payment_mode
    source = request.form.get('source')
    if source:
        meta['source'] = source
    txn = Transaction(user_id=user.id, txn_type=txn_type, amount_cents=amount_cents, occurred_at=occurred, description=description, currency='INR', category_id=(category.id if category else None), meta=meta)
    try:
        db.session.add(txn)
        db.session.commit()
        flash('Transaction added.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error adding transaction: ' + str(e), 'error')
    return redirect(url_for('add_data'))


@app.route('/transactions/quick', methods=['POST'])
def transaction_quick():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    text = request.form.get('text', '').strip()
    if not text:
        flash('Enter quick text.', 'error')
        return redirect(url_for('add_data'))
    
    # Enhanced parsing: handle multiple amounts and detect income vs expense
    import re
    
    # Detect transaction type from keywords
    text_lower = text.lower()
    is_income = any(word in text_lower for word in ['received', 'earned', 'salary', 'income', 'got', 'paid to me', 'refund', 'bonus', 'gift'])
    is_expense = any(word in text_lower for word in ['paid', 'spent', 'cost', 'expense', 'bought', 'charged', 'deducted'])
    
    # Extract ALL amounts (handle both with and without commas)
    amounts = re.findall(r'(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{2})?', text)
    
    if not amounts:
        flash('No amount found in text. Please include a number.', 'error')
        return redirect(url_for('add_data'))
    
    # Convert amounts by removing commas
    parsed_amounts = []
    for amt_str in amounts:
        try:
            # Remove commas and convert to float, then to cents
            amt_float = float(amt_str.replace(',', ''))
            amt_cents = int(amt_float * 100)
            parsed_amounts.append(amt_cents)
        except Exception:
            continue
    
    if not parsed_amounts:
        flash('Could not parse amounts.', 'error')
        return redirect(url_for('add_data'))
    
    # Determine transaction type
    if is_income and not is_expense:
        txn_type = 'income'
    elif is_expense and not is_income:
        txn_type = 'expense'
    else:
        # Default to expense if ambiguous
        txn_type = 'expense'
    
    # Process each amount found
    for amount_cents in parsed_amounts:
        try:
            category = None
            try:
                candidates = Category.query.filter((Category.user_id == user.id) | (Category.user_id == None)).all()
                lt = text.lower()
                for c in candidates:
                    if c.name and c.name.lower() in lt:
                        category = c
                        break
            except Exception:
                category = None
            
            txn = Transaction(
                user_id=user.id, 
                txn_type=txn_type, 
                amount_cents=amount_cents, 
                occurred_at=datetime.utcnow(), 
                description=text, 
                currency='INR', 
                category_id=(category.id if category else None), 
                meta={'source': 'quick_add', 'parsed_from': 'multi_amount'}
            )
            db.session.add(txn)
        except Exception as e:
            continue
    
    try:
        db.session.commit()
        if len(parsed_amounts) == 1:
            flash(f'Transaction added (₹{parsed_amounts[0]/100:.2f}).', 'success')
        else:
            flash(f'Added {len(parsed_amounts)} transactions from text.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error adding transactions: ' + str(e), 'error')
    return redirect(url_for('add_data'))


@app.route('/_seed_demo')
def seed_demo():
    """Development-only: seed demo user, categories, bills, and transactions."""
    # only allow in debug mode to avoid accidental production use
    if not app.debug:
        return jsonify({'error': 'seeding only allowed in debug mode'}), 403

    demo_email = 'demo@billbot.local'
    demo = User.query.filter_by(email=demo_email).first()
    if not demo:
        demo = User(email=demo_email, password_hash=generate_password_hash('demo'))
        db.session.add(demo)
        db.session.commit()

    # avoid reseeding if enough data exists
    existing_txn_count = Transaction.query.filter_by(user_id=demo.id).count()
    existing_bills = Bill.query.filter_by(user_id=demo.id).count()
    if existing_txn_count >= 20 and existing_bills >= 10:
        return jsonify({'status': 'already seeded', 'transactions': existing_txn_count, 'bills': existing_bills})

    # categories
    cat_names = ['Groceries','Dining Out','Transport','Fuel','Rent','Utilities - Electricity','Internet','Phone','Education','Healthcare','Insurance','Entertainment','Clothing','Home Improvement','Subscriptions']
    categories = {}
    for name in cat_names:
        c = get_or_create_category(demo, name)
        if c:
            categories[name] = c

    # create 10 representative bills (monthly or yearly)
    from datetime import timedelta
    now = datetime.utcnow()
    bills_data = [
        {'name':'Monthly Rent','amount':25000,'interval_unit':'months','interval_count':1},
        {'name':'Electricity','amount':3500,'interval_unit':'months','interval_count':1},
        {'name':'Water','amount':400,'interval_unit':'months','interval_count':1},
        {'name':'Internet','amount':999,'interval_unit':'months','interval_count':1},
        {'name':'Mobile Phone','amount':699,'interval_unit':'months','interval_count':1},
        {'name':'Gym Membership','amount':1200,'interval_unit':'months','interval_count':1},
        {'name':'House Insurance','amount':6000,'interval_unit':'year','interval_count':1},
        {'name':'Streaming Subscriptions','amount':499,'interval_unit':'months','interval_count':1},
        {'name':'Property Tax','amount':8000,'interval_unit':'year','interval_count':1},
        {'name':'Car Loan','amount':8000,'interval_unit':'months','interval_count':1},
    ]

    created_bills = []
    for b in bills_data:
        amount_cents = int(b['amount'] * 100)
        # set last_paid one month ago for recurring monthly bills
        last_paid = now - timedelta(days=30)
        bill = Bill(user_id=demo.id, name=b['name'], description=f"Auto-seeded {b['name']}", amount_cents=amount_cents, currency='INR', reminder_text=None, schedule_type=b.get('schedule_type'), interval_count=b.get('interval_count',1), interval_unit=b.get('interval_unit','months'), active=True, last_paid=last_paid, next_due=None, due_date=None)
        db.session.add(bill)
        created_bills.append(bill)
    db.session.commit()

    # create 20 transactions (10 expenses, 10 incomes) distributed over recent 60 days
    import random
    txn_categories = list(categories.values())
    incomes = [
        {'desc':'Salary - Acme Corp','amount':50000},
        {'desc':'Spouse Salary - HomeTech','amount':42000},
        {'desc':'Freelance Project','amount':8000},
        {'desc':'Interest Income','amount':200},
        {'desc':'Gift Received','amount':1500},
        {'desc':'Rental Income','amount':8000},
        {'desc':'Bonus','amount':6000},
        {'desc':'Investment Dividend','amount':1200},
        {'desc':'Selling Old Items','amount':900},
        {'desc':'Tax Refund','amount':3000},
    ]
    expenses = [
        {'desc':'Grocery shopping at BigMart','amount':5200,'cat':'Groceries'},
        {'desc':'Dinner out with family','amount':1800,'cat':'Dining Out'},
        {'desc':'Monthly fuel topup','amount':3000,'cat':'Fuel'},
        {'desc':'Bus/Train passes','amount':600,'cat':'Transport'},
        {'desc':'Movie night','amount':800,'cat':'Entertainment'},
        {'desc':'Clothes shopping','amount':2400,'cat':'Clothing'},
        {'desc':'Phone bill payment','amount':699,'cat':'Phone'},
        {'desc':'Internet bill','amount':999,'cat':'Internet'},
        {'desc':'Medicine and clinic visit','amount':1200,'cat':'Healthcare'},
        {'desc':'Household supplies','amount':950,'cat':'Home Improvement'},
    ]

    # spread dates
    txns_created = []
    for i in range(10):
        inc = incomes[i]
        amt = int(inc['amount']*100)
        days_ago = random.randint(1,60)
        occurred = now - timedelta(days=days_ago)
        txn = Transaction(user_id=demo.id, txn_type='income', amount_cents=amt, occurred_at=occurred, description=inc['desc'], currency='INR', meta={'source':'seed'})
        db.session.add(txn)
        txns_created.append(txn)
    for i in range(10):
        exp = expenses[i]
        amt = int(exp['amount']*100)
        days_ago = random.randint(1,60)
        occurred = now - timedelta(days=days_ago)
        cat = categories.get(exp.get('cat'))
        txn = Transaction(user_id=demo.id, txn_type='expense', amount_cents=amt, occurred_at=occurred, description=exp['desc'], currency='INR', category_id=(cat.id if cat else None), meta={'source':'seed'})
        db.session.add(txn)
        txns_created.append(txn)

    db.session.commit()

    return jsonify({'status':'seeded','user':demo.email,'bills':len(created_bills),'transactions':len(txns_created)})


@app.route('/agents')
def agents_center():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))

    # gather last-run timestamps for agents to display accurately
    def fmt_timesince(dt):
        if not dt:
            return 'never'
        try:
            from datetime import datetime
            diff = datetime.utcnow() - dt
            seconds = int(diff.total_seconds())
            if seconds < 60:
                return 'just now'
            if seconds < 3600:
                mins = seconds // 60
                return f"{mins} minute{'s' if mins!=1 else ''} ago"
            if seconds < 86400:
                hrs = seconds // 3600
                return f"{hrs} hour{'s' if hrs!=1 else ''} ago"
            days = seconds // 86400
            return f"{days} day{'s' if days!=1 else ''} ago"
        except Exception:
            return str(dt)

    # agent result names used elsewhere in the code
    try:
        last_insight = AgentResult.query.filter_by(user_id=user.id, agent_name='insight_agent_v1').order_by(AgentResult.created_at.desc()).first()
        last_forecast = AgentResult.query.filter_by(user_id=user.id, agent_name='forecast_agent_v1').order_by(AgentResult.created_at.desc()).first()
        last_reminder = AgentResult.query.filter_by(user_id=user.id, agent_name='reminder_agent_v1').order_by(AgentResult.created_at.desc()).first()
    except Exception:
        last_insight = last_forecast = last_reminder = None

    # chat: use recent chat logs as "last run" indicator if no AgentResult exists
    try:
        last_chat_log = ChatLog.query.filter_by(user_id=user.id).order_by(ChatLog.created_at.desc()).first()
    except Exception:
        last_chat_log = None

    last_runs = {
        'insights': fmt_timesince(last_insight.created_at) if last_insight else 'never',
        'forecast': fmt_timesince(last_forecast.created_at) if last_forecast else 'never',
        'reminder': fmt_timesince(last_reminder.created_at) if last_reminder else 'never',
        'chat': fmt_timesince(last_chat_log.created_at) if last_chat_log else 'never'
    }

    return render_template('agents.html', last_runs=last_runs)

@app.route('/bills/create', methods=['POST'])
def create_bill():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    name = request.form.get('name')
    description = request.form.get('description')
    tag = request.form.get('tag')
    payment_mode = request.form.get('payment_mode')
    amount = request.form.get('amount')
    period = request.form.get('period')
    first_payment_date = request.form.get('first_payment_date')
    try:
        amount_cents = int(float(amount) * 100)
    except Exception:
        amount_cents = 0
    last_paid = None
    next_due = None
    if first_payment_date:
        try:
            last_paid = datetime.fromisoformat(first_payment_date)
            next_due = _compute_next_due_from(last_paid, period, interval_count=1)
        except Exception:
            last_paid = None
    bill = Bill(user_id=user.id, name=name, description=description, tag=tag, payment_mode=payment_mode, amount_cents=amount_cents, period=period, last_paid=last_paid, next_due=next_due, due_date=next_due)
    db.session.add(bill)
    db.session.commit()
    flash('Bill created.', 'success')
    return redirect(url_for('bills'))

@app.route('/bills/<bill_id>/edit', methods=['POST'])
def edit_bill(bill_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    bill = Bill.query.filter_by(id=bill_id, user_id=user.id).first()
    if not bill:
        flash('Bill not found.', 'error')
        return redirect(url_for('bills'))
    name = request.form.get('name')
    description = request.form.get('description')
    tag = request.form.get('tag')
    payment_mode = request.form.get('payment_mode')
    amount = request.form.get('amount')
    period = request.form.get('period')
    first_payment_date = request.form.get('first_payment_date')
    try:
        amount_cents = int(float(amount) * 100)
    except Exception:
        amount_cents = 0
    last_paid = bill.last_paid
    next_due = bill.next_due
    if first_payment_date:
        try:
            last_paid = datetime.fromisoformat(first_payment_date)
            next_due = _compute_next_due_from(last_paid, period, interval_count=1)
        except Exception:
            pass
    bill.name = name
    bill.description = description
    bill.tag = tag
    bill.payment_mode = payment_mode
    bill.amount_cents = amount_cents
    bill.period = period
    bill.last_paid = last_paid
    bill.next_due = next_due
    bill.due_date = next_due
    db.session.commit()
    flash('Bill updated.', 'success')
    return redirect(url_for('bills'))

@app.route('/bills/<bill_id>/delete', methods=['POST'])
def delete_bill(bill_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    bill = Bill.query.filter_by(id=bill_id, user_id=user.id).first()
    if not bill:
        flash('Bill not found.', 'error')
        return redirect(url_for('bills'))
    db.session.delete(bill)
    db.session.commit()
    flash('Bill deleted.', 'success')
    return redirect(url_for('bills'))

@app.route('/notifications')
def notifications():
    user = get_current_user()
    if not user:
        return redirect(url_for('index'))
    return render_template('notifications.html')

if __name__ == '__main__':

    def safe_startup():
        # Decide which DB URI to use by testing the configured DATABASE_URL first
        configured = os.environ.get('DATABASE_URL') or 'sqlite:///dev.db'
        final_uri = configured
        try:
            test_engine = create_engine(configured)
            with test_engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            print(f'Using database: {configured}')
        except OperationalError as e:
            print('Database connection failed:', e)
            if configured.startswith('sqlite'):
                print('SQLite database configured but connection failed. Exiting.')
                raise
            final_uri = 'sqlite:///dev_fallback.db'
            print(f'Falling back to local SQLite DB at {final_uri}')

        # Configure and initialize the Flask-SQLAlchemy extension once
        app.config['SQLALCHEMY_DATABASE_URI'] = final_uri
        init_db(app)

        with app.app_context():
            try:
                db.create_all()
            except Exception as e:
                print('Error during db.create_all():', e)
    safe_startup()
    app.run(debug=True, host='127.0.0.1', port=5000)
