import uuid
from datetime import datetime
from db import db
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Boolean, JSON

def generate_uuid():
    return str(uuid.uuid4())
class User(db.Model):
    __tablename__ = 'users'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'email': self.email, 'created_at': self.created_at.isoformat()}

class Category(db.Model):
    __tablename__ = 'categories'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'description': self.description}


class Bill(db.Model):
    __tablename__ = 'bills'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(10), nullable=False, default='INR')
    reminder_text = Column(Text, nullable=True)
    schedule_type = Column(String(32), nullable=True)
    interval_count = Column(Integer, nullable=False, default=1)
    interval_unit = Column(String(16), nullable=False, default='months')
    active = Column(Boolean, nullable=False, default=True)
    last_paid = Column(DateTime, nullable=True)
    next_due = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'description': self.description,
            'amount_cents': self.amount_cents,
            'currency': self.currency,
            'reminder_text': self.reminder_text,
            'schedule_type': self.schedule_type,
            'interval_count': self.interval_count,
            'interval_unit': self.interval_unit,
            'active': self.active,
            'last_paid': self.last_paid.isoformat() if self.last_paid else None,
            'next_due': self.next_due.isoformat() if self.next_due else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'created_at': self.created_at.isoformat()
        }


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False)
    bill_id = Column(String(36), ForeignKey('bills.id'), nullable=True)
    category_id = Column(String(36), ForeignKey('categories.id'), nullable=True)
    txn_type = Column(String(32), nullable=False)  # e.g., 'expense' or 'income'
    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(10), nullable=False, default='INR')
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    description = Column(Text, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'bill_id': self.bill_id,
            'category_id': self.category_id,
            'txn_type': self.txn_type,
            'amount_cents': self.amount_cents,
            'currency': self.currency,
            'occurred_at': self.occurred_at.isoformat() if self.occurred_at else None,
            'description': self.description,
            'meta': self.meta,
            'created_at': self.created_at.isoformat()
        }


class AgentResult(db.Model):
    __tablename__ = 'agent_results'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=True)
    agent_name = Column(String(255), nullable=True)
    input_text = Column(Text, nullable=True)
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'agent_name': self.agent_name, 'result_json': self.result_json, 'created_at': self.created_at.isoformat()}


class ChatLog(db.Model):
    __tablename__ = 'chat_logs'
    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=True)
    role = Column(String(32), nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'user_id': self.user_id, 'role': self.role, 'content': self.content, 'meta': self.meta, 'created_at': self.created_at.isoformat()}

