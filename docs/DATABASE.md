# Database Documentation

## Overview

FinAssist uses a PostgreSQL database (hosted on Supabase) to store all user data, transactions, bills, chat messages, and AI analysis results. All database tables are managed using SQLAlchemy ORM, which translates Python objects into secure database operations. Data persists indefinitely unless the user deletes it manually. Monetary amounts are stored in cents to avoid floating-point precision errors. All timestamps are in UTC timezone for consistency.


## Database Schema

### 1. User Table

**Purpose**: Stores user account information and authentication credentials.

**Columns**:
- user_id: Unique identifier (auto-incrementing integer)
- email: User's email address (unique, used for login)
- password_hash: Securely hashed password (never stored in plain text)
- created_at: Account creation timestamp

**Usage**: Every user must have a record in this table. All other tables reference the user_id to ensure users only see their own data.

**Relationships**: One user has many transactions, bills, chat logs, and agent results.


### 2. Category Table

**Purpose**: Defines expense categories for organizing transactions.

**Columns**:
- category_id: Unique identifier (auto-incrementing integer)
- user_id: Foreign key to User (one category per user per type)
- category_name: Human-readable name (e.g., "Groceries", "Rent", "Utilities")
- category_type: Type of category (income, expense, or saving)
- created_at: When this category was created

**Usage**: Users create custom categories to organize their transactions. The system provides default categories but users can add more.

**Relationships**: One category can have many transactions. Each category belongs to one user.

---

### 3. Transaction Table

**Purpose**: Records every money movement (income or expense).

**Columns**:
- transaction_id: Unique identifier (auto-incrementing integer)
- user_id: Foreign key to User
- category_id: Foreign key to Category
- amount_cents: Money amount in cents (not dollars to avoid decimal errors)
- transaction_type: Either "income" or "expense"
- description: Optional note about the transaction
- transaction_date: Date when the transaction occurred
- created_at: When the transaction was logged

**Usage**: This is the core table for tracking money. Every expense or income is recorded here. Users can add multiple transactions per day. All amounts are in cents (so $10.50 is stored as 1050).

**Relationships**: Many transactions belong to one user and one category. Transactions can be deleted individually but not modified after creation (maintains audit trail).

---

### 4. Bill Table

**Purpose**: Tracks recurring and upcoming bills with payment status.

**Columns**:
- bill_id: Unique identifier (auto-incrementing integer)
- user_id: Foreign key to User
- bill_name: Name of the bill (e.g., "Electricity", "Internet", "Rent")
- amount_cents: Bill amount in cents
- frequency: Recurrence pattern (one_time, monthly, yearly)
- next_due: Calculated date when this bill is next due
- is_paid: Boolean indicating if current bill is marked as paid
- last_paid: Timestamp when user last marked this bill as paid
- created_at: When this bill was created

**Usage**: Bills are set up by the user to track recurring payments. The system calculates when each bill is next due based on frequency and last payment date. Users mark bills as paid when they complete payment. The Reminder Agent uses this table to generate notifications.

**Relationships**: Many bills belong to one user. Bills appear on the Notifications page for the user.

---

### 5. ChatLog Table

**Purpose**: Stores user questions and AI responses for conversation history.

**Columns**:
- chat_id: Unique identifier (auto-incrementing integer)
- user_id: Foreign key to User
- user_message: The question asked by the user
- assistant_message: The response provided by the AI
- message_type: Either "user" or "assistant" to distinguish sender
- created_at: Timestamp when the message was created

**Usage**: Every chat message (both questions and answers) is saved here for permanent history and context. Users can see their complete chat history. The Chat page loads these messages to display conversation. The AI loads previous messages from this table to understand conversation context. Users can delete their entire chat history, which clears this table.

**Relationships**: Many chat logs belong to one user. All chat messages for a user are linked by user_id.

---

### 6. ChatCache Table

**Purpose**: Stores AI responses indexed by question similarity for faster repeated questions.

**Columns**:
- cache_id: Unique identifier (auto-incrementing integer)
- user_id: Foreign key to User
- user_message: The question asked
- assistant_response: The cached AI response
- token_count: Number of tokens in the cached response (for cache size management)
- created_at: When this cache entry was created

**Usage**: When a user asks a question, the system searches this table for similar questions. If a match is found (over 80% word similarity), the cached response is returned immediately without calling the Groq API. This reduces API calls and saves time. Cache entries are treated independently per user (no sharing across users). Cache is saved for any AI response that is not system-generated. Users clearing chat history also clears this cache.

**Relationships**: Many cache entries belong to one user. Cache entries are automatically created and deleted based on chat activity.

---

### 7. AgentResult Table

**Purpose**: Stores results from AI agent runs for tracking and auditing.

**Columns**:
- result_id: Unique identifier (auto-incrementing integer)
- user_id: Foreign key to User
- agent_name: Name of the agent that ran (Chat, Insights, Forecast, or Reminder)
- result_data: The complete output or analysis from the agent
- created_at: Timestamp when the agent ran

**Usage**: Every time an agent analyzes user data and produces results, a record is created here. The Insights Agent stores analysis about spending patterns. The Forecast Agent stores expense predictions. The Reminder Agent stores bill summaries. This table provides an audit trail of all agent activities and can be used to track changes over time. Users do not directly interact with this table; it's for backend tracking.

**Relationships**: Many agent results belong to one user. Results are immutable (not edited after creation).

---

## Data Relationships

### User-Centric Design

All tables are connected through the user_id field. This ensures:

- Data Isolation: Users only see their own data
- Multi-Tenancy: Many users can use the system simultaneously
- Security: No user can accidentally access another user's information
- Scalability: Adding new users doesn't affect existing data

### Transaction to Category Relationship

Every transaction is linked to exactly one category. This enables:

- Categorization: Users organize expenses by type
- Filtering: Show only expenses in a specific category
- Analysis: Calculate totals by category
- Insights: Identify spending patterns by category type

### Bill Tracking

Bills are independent entities but are related to transactions:

- Bills forecast future expenses
- Transactions record actual payments
- Payment history shows which bills have been paid
- Bill amounts can differ from transaction amounts

### Chat Continuity

ChatLog and ChatCache work together:

- ChatLog stores permanent history
- ChatCache optimizes repeated questions
- Together they enable context-aware AI responses
- Clearing history clears both tables

---

## Data Relationships Diagram

```
User (1)
  ├── (M) Transactions
  │    └── (M) Categories
  ├── (M) Bills
  ├── (M) ChatLogs
  ├── (M) ChatCache
  └── (M) AgentResults
```

The User is the central entity. All other tables connect to users through user_id foreign keys.

---

## Data Flow Patterns

### Adding a Transaction

1. User submits a new transaction with category
2. System creates a new Transaction record with user_id, category_id, amount, type, and date
3. Transaction appears immediately on Dashboard and Transactions page
4. Category totals are recalculated for the month
5. If Forecast Agent has run, it notes the new expense for future predictions

### Asking a Chat Question

1. User submits a question in Chat
2. System checks ChatCache for similar questions (80% word match)
3. If found, cached response is returned immediately
4. If not found, system gathers context (recent transactions, bills, categories)
5. Groq API is called with the question and context
6. New ChatLog record is created with user_message and assistant_message
7. New ChatCache record is created with the response
8. User sees the response appear in the chat interface

### Setting Up a Bill

1. User creates a new bill with name, amount, and frequency
2. System calculates next_due date based on frequency (today for monthly bills)
3. Bill record is saved to the database
4. Bill appears on Notifications page
5. When Reminder Agent runs, it includes this bill in the summary

### Marking a Bill as Paid

1. User clicks "Mark as Paid" on the Notifications page
2. System updates the Bill's is_paid flag and last_paid timestamp
3. System recalculates next_due for the next billing period
4. A Transaction record is optionally created if user confirms "Log Payment"
5. Reminder Agent updates its summary on the next run
6. Bill appears as paid on the Notifications page until the next due date

---

## Data Constraints and Rules

### User Data Isolation

- Every query that accesses user-specific data filters by user_id
- No two users can see each other's transactions, bills, or chat messages
- This is enforced at the ORM level and database level

### Monetary Precision

- All monetary amounts are stored in cents (integers)
- This prevents floating-point arithmetic errors
- When displaying to users, amounts are divided by 100 for display

### Timestamps

- All timestamps are stored in UTC timezone
- System converts local time to UTC when storing
- System converts UTC back to local when displaying
- This prevents timezone-related bugs

### Chat History Expiration

- Chat messages are stored indefinitely in the database
- However, LocalStorage on the client side expires after 30 days
- This is just for client-side caching, not database deletion
- Users can manually delete chat history at any time

### Category Constraints

- Users cannot create duplicate category names
- Categories can be used by multiple transactions
- Users cannot delete categories with existing transactions
- Default categories are created for each user automatically

---

## Performance Considerations

### Database Indexes

The system creates indexes on:

- user_id: Because nearly every query filters by user
- transaction_date: For date-based filtering on Transactions page
- next_due: For finding bills due soon
- created_at: For sorting by date

These indexes make queries much faster by avoiding full table scans.

### Query Optimization

- Recent transactions are queried with date filters to limit results
- Bill queries use indexes on next_due for efficient retrieval
- ChatLog queries are limited to recent messages unless user scrolls
- Category queries use user_id and category_name indexes

### Connection Pooling

The application maintains a pool of database connections that are reused. This is more efficient than creating a new connection for every request.

---

## Backup and Data Safety

### Supabase Automatic Backups

PostgreSQL databases on Supabase are automatically backed up. If data is accidentally deleted or corrupted, backups can be restored.

### User Data Deletion

When a user deletes their account, all associated records should be deleted:

- User record is deleted
- All Transactions for this user are deleted
- All Bills for this user are deleted
- All ChatLog messages for this user are deleted
- All ChatCache entries for this user are deleted
- All AgentResult records for this user are deleted

This ensures complete data removal and privacy.

---

## Monitoring and Maintenance

### Backup Strategy

Regular backups are maintained automatically by Supabase. These should be tested periodically for recovery capability.

### Database Growth

Over time, databases grow as users add transactions and messages. Monitor:

- Total database size
- ChatLog table size (largest over time due to unlimited history)
- Index efficiency

### Query Performance

Monitor slow queries to identify optimization opportunities. The system logs query execution times.

### User Isolation Verification

Periodically verify that:

- No user can query another user's data
- All queries include user_id filtering
- Foreign keys are correctly enforced

---

## Summary

The database schema is designed around the user as the central entity. All data flows through user-specific records with proper relationships. This ensures security (users can only see their data), performance (indexes on frequently queried columns), and consistency (proper relationships prevent orphaned data). The system uses industry-standard practices like parameterized queries to prevent SQL injection, transactions to ensure data consistency, and foreign keys to maintain referential integrity.
