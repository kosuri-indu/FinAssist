# AI Agents Documentation

FinAssist has four specialized AI agents that analyze your financial data and provide intelligent insights. Each agent has a specific purpose and runs either on-demand or automatically.

---

## Overview of All Agents

| Agent | Purpose | Trigger | Frequency |
|-------|---------|---------|-----------|
| Chat Agent | Answer financial questions in real-time | User asks in chat | On-demand |
| Insights Agent | Analyze spending patterns and suggest improvements | User clicks on Agents page | On-demand |
| Forecast Agent | Predict next month's expenses | User clicks on Agents page | On-demand |
| Reminder Agent | Manage bill notifications and payment tracking | Automatic on page load | Every page load |


## Chat Agent

### Purpose

The Chat Agent allows you to ask natural language questions about your finances and get instant, personalized answers. It understands your financial context and provides advice based on your actual spending data.

### What It Does

- Receives your question
- Gathers your financial context (recent transactions, bills, categories, balances)
- Checks if a similar question was asked before (semantic caching)
- If found in cache, returns the cached answer immediately
- If not cached, calls the Groq API (Llama 3.3 70B model)
- Llama analyzes your data and generates a personalized response
- Saves the response to the database and cache
- Displays the response with markdown formatting

### How to Use

Type a question in the chat box and press Enter. Examples of questions:
- "How much did I spend last month?"
- "What's my biggest expense category?"
- "How can I save more money?"
- "What bills are coming up?"
- "Compare my spending this month vs last month"

### Rate Limiting

The system enforces rate limits to work within the Groq free tier:
- Maximum 25 requests per minute
- Maximum 14,000 requests per day
- If you exceed limits, the system tells you when you can try again

### Semantic Caching

Similar questions (over 80% word similarity) return cached answers instantly without calling the API. This:
- Makes the application faster
- Reduces API usage
- Saves money on the free tier
- Maintains conversation context

### Financial Context Gathering

Before calling the AI, the system prepares your financial context:
- Recent transactions (last 30 days)
- Total income and expenses for the month
- Spending breakdown by category
- Upcoming bills and due dates
- Current account balance
- Payment history

This context is sent to the AI so it can provide accurate, personalized advice based on your actual data.

### How Responses are Saved

- Your question is saved to ChatLog table
- The AI's response is saved to ChatLog table
- The response is also saved to ChatCache table for faster future retrieval
- Both saved to browser LocalStorage (30-day expiry)
- Dual storage ensures persistence across refreshes and navigation

---

## Insights Agent

### Purpose

The Insights Agent analyzes your complete spending history to identify patterns, find savings opportunities, and provide personalized financial recommendations.

### What It Does

- Analyzes your entire transaction history
- Identifies your top spending categories
- Calculates average spending per category
- Finds spending trends (increasing, decreasing, stable)
- Identifies categories with high spending relative to your average
- Generates specific savings recommendations
- Calculates potential monthly savings
- Formats results as a detailed report with explanations
- Saves the analysis to the AgentResult table for auditing

### How to Use

Click the "Run Insights Analysis" button on the Agents page. The analysis will show:
- Your biggest spending categories
- Monthly averages by category
- Spending trends over time
- Specific recommendations for each category
- Total potential savings if you follow recommendations

### Sample Recommendations

The agent might suggest:
- "Reduce food spending by preparing meals at home instead of eating out"
- "Review your subscription services - you may be paying for unused services"
- "Transport costs are increasing - consider carpooling or public transit"
- "Entertainment spending is 40% above your average - consider setting a budget"

### Data Analysis Process

1. Queries all your transactions from the database
2. Groups transactions by category
3. Calculates totals and averages
4. Identifies outliers and trends
5. Calls Groq API to generate recommendations
6. Formats the response in markdown for readability
7. Saves everything to AgentResult table

### Output

The analysis provides:
- Total spending by category (ranked)
- Monthly average per category
- Categories with high or low spending
- Specific, actionable recommendations
- Estimated savings potential
- Explanation of each recommendation

---

## Forecast Agent

### Purpose

The Forecast Agent predicts your expected expenses for the next month based on your historical spending patterns. This helps with budgeting and financial planning.

### What It Does

- Analyzes your spending history (at least 30 days of data)
- Calculates average spending per category
- Identifies spending trends (up, down, or stable)
- Accounts for recurring bills that are due next month
- Predicts total expenses for the next month
- Provides confidence level for the prediction
- Explains which categories are driving the prediction
- Saves the forecast to the AgentResult table

### How to Use

Click the "Run Expense Forecast" button on the Agents page. The forecast will show:
- Predicted total expenses for next month
- Confidence level (0-100%)
- Breakdown by category
- Explanation of trends

### Prediction Factors

The forecast considers:
- Average spending per category from the last 3 months
- Spending trends (if you're spending more or less each month)
- Seasonal patterns if enough data exists
- Upcoming recurring bills
- One-time expenses from recent history

### Confidence Levels

Higher confidence means the prediction is more reliable:
- 80%+: Strong historical pattern, reliable prediction
- 60-80%: Moderate variation, reasonable prediction
- Below 60%: High variation or insufficient data, use with caution

### Use Cases

Use forecasts to:
- Plan your monthly budget
- Determine how much income you need
- Set savings goals
- Identify months with higher expected expenses
- Plan for upcoming large expenses

---

## Reminder Agent

### Purpose

The Reminder Agent manages all your bill notifications, payment tracking, and due date alerts. It automatically runs on every page load to keep your bill status current.

### What It Does

- Queries all your active bills from the database
- Calculates days until payment for each bill
- Categorizes bills into four groups (Overdue, Due Today, Coming Soon, Upcoming)
- Generates category-specific payment advice for each bill
- Formats everything into a readable summary with markdown
- Saves the summary to the AgentResult table
- Displays the summary on the Notifications page

### Automatic Operation

The Reminder Agent runs automatically every time you load a page:
- Bill status is always current
- You never miss an overdue bill
- Get warnings for bills due soon
- Payment reminders are always fresh

### Bill Status Categories

**Overdue Bills**
- Past their due date
- Action required: Pay immediately

**Due Today**
- Due within the next 24 hours
- Action required: Pay today

**Coming Soon**
- Due within 7 days
- Action recommended: Make a note to pay soon

**Upcoming**
- Due more than 7 days away
- For information: No action needed yet

### Category-Specific Advice

The agent provides tailored advice based on the bill type:

**Electricity Bills**
- "Shift usage to off-peak hours if available"
- "Check for any unusual spikes in consumption"

**Internet/Mobile Bills**
- "Review your current plan annually"
- "Consider bundling services for discounts"

**Insurance Bills**
- "Review coverage yearly to ensure it's adequate"
- "Shop around for better rates periodically"

**Water/Gas Bills**
- "Check for leaks that might waste water"
- "Monitor usage for unusual patterns"

**Rent/Mortgage**
- "Set a reminder 5 days before the due date"
- "Maintain 2 months of rent in emergency reserves"

**High Bills (over 5000)**
- "Maintain 2x the bill amount in emergency reserves"
- "Consider negotiating rates or switching providers"

**Small Bills (under 500)**
- "Track these carefully - small amounts add up"
- "Look for opportunities to cancel unused services"

### How to Use

The Notifications page automatically displays the bill summary generated by the Reminder Agent. You can:

- See all bills organized by status (overdue, due today, coming soon, upcoming)
- Click "Mark as Paid" on any bill to update its status
- Optionally log the payment as a transaction
- View the payment advice for each bill
- Plan ahead based on upcoming bills

### Payment Tracking

When you mark a bill as paid:
- System updates the last_paid timestamp
- System calculates the next due date based on bill frequency
- Reminder Agent runs again and updates the summary
- Bill moves to the appropriate category (or disappears if one-time)

### Data Update Frequency

The agent's information is current because it:
- Runs on every page load (always fresh)
- Reads directly from the database (not cached)
- Updates bill status as soon as you mark bills as paid
- Never shows stale information

---

## How Agents Work Together

The agents work in concert:

- **Chat Agent** provides immediate answers to specific questions
- **Insights Agent** identifies patterns and opportunities
- **Forecast Agent** helps predict future expenses
- **Reminder Agent** keeps you on track with bill payments

Together, they provide comprehensive financial intelligence and automation.

### Data Flow

All agents:
- Access your real financial data from the database
- Respect your privacy (no data is shared between users)
- Save their results to the AgentResult table for auditing
- Return formatted responses with explanations and advice

---

## AI Model Details

### Groq API and Llama 3.3 70B

All agents use the Groq API to access the Llama 3.3 70B language model:

**Llama 3.3 70B**
- State-of-the-art large language model
- Trained on diverse financial and general knowledge
- Fast response times (excellent for real-time chat)
- Good context understanding

**Groq API (Free Tier)**
- Rate limit: 25 requests per minute
- Daily limit: 14,000 requests per day
- Excellent for side projects and learning
- Ideal for FinAssist's use case

### Why We Use Groq

- **Cost-Effective**: Free tier covers normal usage
- **Speed**: Groq's inference engine is very fast
- **No Quotas**: Unlike some providers, no hidden limits
- **Reliability**: Consistent performance and uptime

---

## Privacy and Security

### Data Handling

- Your financial data is only sent to Groq when you ask a question
- Data is not used to train models
- Each user's data is isolated (no cross-user data sharing)
- Sensitive information (passwords, account numbers) is never sent

### Caching Privacy

- Cached responses are stored per-user
- You cannot see other users' cached responses
- Your cache expires after a period of inactivity
- You can clear your cache anytime via "Clear History"

---

## Troubleshooting

### Chat Not Working

If the chat agent doesn't respond:
- Check your internet connection
- Verify you haven't exceeded rate limits (25 req/min, 14k/day)
- Try asking a simpler question first
- Clear your browser cache and try again

### Forecast Showing Low Confidence

The forecast confidence is low when:
- You have less than 30 days of transaction history
- Your spending varies significantly month-to-month
- You just started using FinAssist
- You made one-time large purchases

Wait for more data to accumulate for better predictions.

### Missing Insights or Recommendations

Insights need:
- At least 2-3 weeks of transaction data
- Multiple categories with spending
- Enough data to identify patterns
- At least some recurring expenses

### Bills Not Showing in Reminders

Check that:
- The bill is marked as "active"
- The bill has a next_due date in the future
- You haven't manually deleted the bill
- The bill belongs to your account (not shared)

---

## Summary

The four agents work together to provide comprehensive financial intelligence:

- **Chat Agent**: Ask questions, get instant answers
- **Insights Agent**: Understand patterns, find savings
- **Forecast Agent**: Predict future spending, plan ahead
- **Reminder Agent**: Never miss a bill payment

All agents respect your privacy, work within rate limits, and save their analyses for your records.
