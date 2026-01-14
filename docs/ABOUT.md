# FinAssist - Project Overview

## What is FinAssist?

FinAssist is an AI-powered personal finance assistant that helps you manage your money, track expenses, pay bills on time, and get intelligent financial insights. It combines a clean, user-friendly interface with powerful AI analysis to help you understand and optimize your spending.


## Core Features

### 1. Dashboard
View monthly expenses vs income at a glance. Get quick statistics on where your money goes, see latest transactions summary, and access AI-powered financial insights about your spending patterns.

### 2. Add Data
Log expenses and income with categories. Set up recurring bills for monthly, yearly, or one-time payments. Tag and categorize transactions for better tracking and organization.

### 3. Transactions
View complete spending and income history. Filter by category, date, or transaction type. Edit or delete transactions as needed. Track bill payment history over time.

### 4. Chat Assistant
Ask AI anything about your finances. Get instant answers without navigating to separate pages. Your 30-day chat history is stored both locally on your device and on the server for continuity. The AI maintains conversation context for intelligent responses.

### 5. Bill Reminders
Set up notifications for upcoming bill payments. Mark bills as paid with one click. Get alerts for overdue bills. See recommended payment dates and avoid late payments.

### 6. Agents Center
Three specialized AI agents analyze your finances:
- Insights Agent: Analyzes spending patterns and suggests ways to save money
- Forecast Agent: Predicts next month's expenses based on your historical data
- Reminder Agent: Manages all bill notifications and payment tracking


## Tech Stack

### Frontend
- **HTML5 & Vanilla JavaScript** - Core interface and interactivity
- **Tailwind CSS** - Responsive, modern styling
- **marked.js** - Markdown rendering for AI responses
- **DOMPurify** - HTML sanitization for security
- **LocalStorage** - 30-day chat history persistence on device
- **Full Page Navigation** - Clean state on every page change

### Backend
- **Flask** - Lightweight Python web framework
- **SQLAlchemy ORM** - Secure database operations with parameterized queries
- **JSON APIs** - All endpoints return structured data
- **Session Authentication** - Secure password hashing with bcrypt
- **APScheduler** - Background tasks for automation

### Database
- **PostgreSQL (Supabase)** - Hosted relational database
- **7 Tables** - Users, Transactions, Bills, Categories, ChatLogs, ChatCache, AgentResults
- **Amounts in Cents** - Prevents floating-point errors
- **UTC Timestamps** - Consistent timezone handling
- **Parameterized Queries** - SQL injection prevention

### AI and Language Model
- **Groq API (Free Tier)** - Access to Llama 3.3 70B model
- **Rate Limits** - 25 req/min, 14,000 req/day
- **Semantic Caching** - Reduces API calls for similar questions (80%+ match)
- **Real Financial Context** - Data from your database for personalized responses


## How It Works

### Architecture Overview

The system is organized in layers. The user interacts with the frontend in their browser. The frontend communicates with the Flask backend server. The backend uses SQLAlchemy ORM to query the PostgreSQL database. When AI analysis is needed, the backend calls the Groq API and receives intelligent responses.

### User Journey: Asking a Chat Question

When a user asks the chat assistant a question:

1. The user types a question and submits it
2. The frontend displays a "Thinking..." placeholder (not saved to history)
3. The frontend sends the question to the backend's /api/chat endpoint
4. The backend checks if we've exceeded rate limits (25 per minute, 14,000 per day)
5. The backend gathers financial context from the database (expenses, income, bills, categories)
6. The backend checks if a similar question exists in the semantic cache (over 80% word match)
7. If found in cache, the cached answer is returned immediately, skipping the API call
8. If not cached, the backend calls the Groq API with the question and financial context
9. Llama 3.3 analyzes the data and generates an intelligent response
10. The backend saves both the question and response to the ChatLog database
11. The response is added to the semantic cache for future similar questions
12. The frontend receives the response and replaces the "Thinking..." with the actual answer
13. The response is rendered with markdown formatting and HTML sanitization
14. The response is saved to browser's LocalStorage with a 30-day expiry
15. The user sees the answer immediately and it persists across page refreshes

### User Journey: Marking a Bill as Paid

When a user marks a bill as paid from the Notifications page:

1. The frontend sends a request to mark the bill as paid
2. The backend updates the Bill record with the current timestamp
3. The backend calculates when this bill is due next based on its schedule
4. The Reminder Agent runs automatically to gather current bill status
5. The agent queries all user bills and calculates days until payment for each
6. The agent categorizes bills as overdue, due today, coming soon, or upcoming
7. The agent generates bill-specific advice for each bill type
8. The agent formats everything into a readable summary with markdown
9. The frontend displays the updated bill list and the summary
10. The new bill status is saved to the database for future reference


## Navigation

The application uses a sidebar with links for Dashboard, Add Data, Transactions, Chat, Notifications, and Agents Center. When you click a sidebar item:

- Click is intercepted by JavaScript
- Active link is highlighted
- Full page refresh is performed
- Fresh data is fetched from server

**Benefits of Full Page Refresh:**
- Clean state (no stale data)
- Fresh data always from server
- Reliable behavior across browsers
- Simpler debugging


## Authentication

### User Registration
- Provide email and password
- Password is securely hashed (never plain text)
- Email must be unique and valid format
- New User record created in database
- Account immediately available for login

### User Login
- Enter email and password
- System retrieves hashed password from database
- Submitted password compared against hash
- Secure session created on successful match
- Session allows access without re-entering credentials

### Protected Routes
- All financial pages require active session
- Unauthenticated users redirected to login
- Current user retrieved from session for each request
- User ID filtering ensures data isolation
- No user can access another user's data


## Key Features Explained

### Semantic Caching
- Caches AI responses to questions
- Compares new questions using word similarity matching
- If 80%+ similar to cached question, returns cached answer instantly
- Reduces API calls and saves money on free tier
- Improves response speed for repeat questions

### Rate Limiting
- Tracks API calls per minute and per day
- Limits: 25 requests per minute, 14,000 per day
- Checks before each API call if limits would be exceeded
- Fails gracefully with explanation if limit reached
- Prevents account blocking from exceeding free tier

### Financial Context Gathering
- Gathers real user data before calling AI
- Includes: recent transactions, category totals, monthly summary, upcoming bills
- Sends context to AI for personalized responses
- Ensures advice is based on actual spending, not generic tips
- Updates dynamically as you add transactions

### Chat History Management
- All conversations saved to database for long-term persistence
- AI loads previous messages to understand conversation flow
- LocalStorage caches last 30 days on your device
- Users can manually clear all history anytime
- Data persists across page refreshes and sessions

## Performance and Security

### Security
- **Parameterized Queries** - Prevent SQL injection attacks
- **Password Hashing** - Strong bcrypt algorithms, never plain text
- **HTML Sanitization** - DOMPurify prevents XSS attacks
- **Session Security** - Secure cookies with appropriate expiry
- **HTTPS Encryption** - All database connections encrypted
- **Data Isolation** - Users can only see their own financial data

### Performance
- **Semantic Caching** - Reduces API calls for similar questions
- **Rate Limiting** - Prevents service degradation and API blocking
- **Database Indexing** - Optimized queries on frequently accessed columns
- **Connection Pooling** - Reuse database connections across requests
- **LocalStorage Caching** - Instant chat history loading on client
- **Concurrent Users** - System handles multiple users simultaneously