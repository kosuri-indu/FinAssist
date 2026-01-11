from dotenv import load_dotenv
import os
import json
from typing import Dict, Any, List, Optional
import yfinance as yf
from agents.llm_gemini import call_gemini
import re

load_dotenv()


# ---------- Market Data Layer (NO LLM HERE) ----------

def get_stock_history(ticker: str, period: str = "6mo") -> list:
    """Return historical OHLC data as a list of dicts (compatible with previous API)."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        return hist.reset_index().to_dict(orient='records')
    except Exception as e:
        return [{"error": str(e)}]


def get_stock_price(ticker: str) -> str:
    """Get the current/latest stock price for a ticker. Returns formatted string with current price.
    
    This function is optimized for simple price queries and returns a clear, concise response.
    """
    try:
        # Basic ticker sanitization (inline to avoid dependency order issues)
        ticker_clean = str(ticker).strip().upper()
        ticker_clean = ticker_clean.strip('\"\'{}[]()')
        if ticker_clean.startswith('$'):
            ticker_clean = ticker_clean[1:]
        ticker_clean = re.sub(r'[^A-Z0-9\.\-]', '', ticker_clean)
        
        if not ticker_clean or len(ticker_clean) > 10:
            return f"Invalid ticker symbol: {ticker}"
        
        t = yf.Ticker(ticker_clean)
        # Get today's data (or most recent trading day)
        todays_data = t.history(period='1d')
        if todays_data.empty:
            # Fallback to 5 days if today has no data (weekend/holiday)
            todays_data = t.history(period='5d')
            if todays_data.empty:
                return f"Unable to fetch price data for {ticker_clean}. The ticker may be invalid or not trading."
        
        latest = todays_data.iloc[-1]
        current_price = float(latest['Close'])
        
        # Get company info
        try:
            info = t.info
            company_name = info.get('longName') or info.get('shortName') or ticker_clean
            currency = info.get('currency', 'USD')
        except Exception:
            company_name = ticker_clean
            currency = 'USD'
        
        price_date = latest.name.strftime('%Y-%m-%d') if hasattr(latest.name, 'strftime') else str(latest.name)
        
        return f"Current stock price of {company_name} ({ticker_clean}): {currency} {current_price:.2f} (as of {price_date})"
    except Exception as e:
        return f"Error fetching stock price for {ticker}: {str(e)}"


# --- Helpers: sanitize and validate tickers ---
TICKER_RE = re.compile(r'^[A-Z0-9\.\-]{1,10}$')

def sanitize_ticker(t: Optional[str]) -> str:
    if not t:
        return ""
    s = str(t).strip().upper()
    # remove surrounding quotes/braces and leading dollar signs
    s = s.strip('\"\'{}[]()')
    if s.startswith('$'):
        s = s[1:]
    # drop any characters that are not alphanumeric, dot or dash
    s = re.sub(r'[^A-Z0-9\.\-]', '', s)
    return s


def is_valid_ticker(s: str) -> bool:
    return bool(s) and bool(TICKER_RE.match(s))


def analyze_stock(ticker: str, qty: float) -> Dict[str, Any]:
    """Analyze stock trend, volatility, and value using yfinance only."""
    try:
        # sanitize and validate ticker before contacting yfinance
        ticker_clean = sanitize_ticker(ticker)
        if not is_valid_ticker(ticker_clean):
            return {"ticker": ticker, "error": f"Invalid ticker format: {ticker!s}"}
        t = yf.Ticker(ticker_clean)
        hist = t.history(period="6mo")

        if hist.empty:
            return {"ticker": ticker, "error": "No historical data available"}

        start_price = hist["Close"].iloc[0]
        end_price = hist["Close"].iloc[-1]
        pct_change = ((end_price - start_price) / start_price) * 100

        volatility = hist["Close"].pct_change().std() * (252 ** 0.5)

        trend = "bullish" if pct_change > 5 else "bearish" if pct_change < -5 else "sideways"
        risk = "high" if volatility > 0.4 else "medium" if volatility > 0.2 else "low"

        return {
            "ticker": ticker_clean,
            "current_price": round(float(end_price), 2),
            "6m_return_pct": round(float(pct_change), 2),
            "volatility": round(float(volatility), 4),
            "trend": trend,
            "risk": risk,
            "holding_qty": qty,
            "holding_value": round(float(end_price) * float(qty), 2)
        }

    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def summarize_portfolio(portfolio: Dict[str, float]) -> Dict[str, Any]:
    # Accept multiple input forms for compatibility with LangChain tools:
    # - dict of ticker->qty
    # - JSON string representing such a dict
    # - simple string like 'AAPL:2 MSFT:1' or 'AAPL,MSFT'
    analysis: List[Dict[str, Any]] = []
    total_value = 0.0

    def _normalize(inp) -> Dict[str, float]:
        if inp is None:
            return {}
        if isinstance(inp, dict):
            out = {}
            for k, v in inp.items():
                tk = sanitize_ticker(k)
                if not is_valid_ticker(tk):
                    continue
                try:
                    out[tk] = float(v)
                except Exception:
                    out[tk] = 1.0
            return out
        if isinstance(inp, str):
            # try JSON first
            try:
                parsed = json.loads(inp)
                if isinstance(parsed, dict):
                    return {str(k).upper(): float(v) for k, v in parsed.items()}
            except Exception:
                pass
            # fallback: parse 'AAPL:2 MSFT:1' or 'AAPL,MSFT'
            out: Dict[str, float] = {}
            parts = [p.strip() for p in inp.replace(',', ' ').split() if p.strip()]
            for p in parts:
                if ':' in p:
                    t, q = p.split(':', 1)
                    tk = sanitize_ticker(t)
                    if not is_valid_ticker(tk):
                        continue
                    try:
                        out[tk] = float(q)
                    except Exception:
                        out[tk] = 1.0
                else:
                    tk = sanitize_ticker(p)
                    if not is_valid_ticker(tk):
                        continue
                    out[tk] = 1.0
            return out
        # Unknown type -> try to cast to dict
        try:
            return {str(k).upper(): float(v) for k, v in dict(inp).items()}
        except Exception:
            raise TypeError('Unsupported portfolio format')

    norm_portfolio = _normalize(portfolio)

    for ticker, qty in norm_portfolio.items():
        stock_analysis = analyze_stock(ticker, qty)
        analysis.append(stock_analysis)
        total_value += stock_analysis.get("holding_value", 0) or 0

    return {"portfolio_analysis": analysis, "total_value": round(total_value, 2)}


# ---------- Goal Clarification Layer ----------

def get_clarification_questions(user_profile: Dict[str, Any]) -> List[str]:
    questions: List[str] = []

    if not user_profile.get("risk_tolerance"):
        questions.append("What is your risk tolerance? (low / medium / high)")

    if not user_profile.get("time_horizon"):
        questions.append("What is your investment time horizon?")

    if not user_profile.get("investment_amount"):
        questions.append("How much amount can you invest monthly or as a lump sum?")

    if not user_profile.get("investment_goals"):
        questions.append("Please describe your investment goals (e.g., target amount and timeframe)")

    return questions


# ---------- Prompt Builder ----------

def build_prompt(user_profile: Dict[str, Any], market_data: Dict[str, Any]) -> str:
    return f"""
You are a financial planning assistant.

STRICT RULES:
- Use ONLY the provided market data.
- Do NOT invent stock prices, returns, or instruments.
- Do NOT name specific mutual funds or SIP products; suggest categories only.
- If data is insufficient, ask clarification questions instead of assuming.
- IMPORTANT: You must follow the Thought/Action/Action Input/Observation format. 
- When you have the final answer, you MUST prefix it with 'Final Answer:'

User Financial Profile:
{json.dumps(user_profile, indent=2)}

Market Data (from yfinance):
{json.dumps(market_data, indent=2)}

Tasks:
1. Analyze portfolio risk and diversification.
2. Check alignment with investment goals.
3. Suggest data-backed investment strategies (stocks, SIPs, MF categories).
4. Highlight risks and trade-offs.
5. Provide a concise action plan.

Constraints:
- Under 400 words
- Clear headings
- Do NOT provide legal or tax disclaimers; keep practical guidance.
"""


# ---------- Main Agent ----------

def investment_advice(
    portfolio: Dict[str, float],
    user_profile: Dict[str, Any],
    max_tokens: int = 300,
    show_raw: bool = False,
) -> str:

    clarification_questions = get_clarification_questions(user_profile)
    if clarification_questions:
        return (
            "To provide accurate advice, I need a bit more information:\n"
            + "\n".join(f"- {q}" for q in clarification_questions)
        )

    market_summary = summarize_portfolio(portfolio)
    prompt = build_prompt(user_profile, market_summary)

    llm_text = call_gemini(
        prompt=prompt,
        # max_tokens=max_tokens,
        temperature=0.2
    )

    if show_raw:
        try:
            print('\n--- Raw LLM Response ---\n')
            print(llm_text)
            print('\n--- End Raw LLM Response ---\n')
        except Exception:
            pass

    return llm_text


# ---------- CLI Runner ----------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Run investment_advice demo and print LLM response')
    parser.add_argument('--portfolio', nargs='*', default=None, help='Ticker:qty pairs, e.g. AAPL:2 MSFT:1')
    parser.add_argument('--age', type=int, help='Age')
    parser.add_argument('--occupation', type=str, help='Occupation')
    parser.add_argument('--monthly_income', type=float, help='Monthly income')
    parser.add_argument('--monthly_expenses', type=float, help='Monthly expenses')
    parser.add_argument('--current_savings', type=float, help='Current savings')
    parser.add_argument('--investment_goals', type=str, help='Investment goals description')
    parser.add_argument('--risk_tolerance', type=str, help='Risk tolerance (low/medium/high)')
    parser.add_argument('--time_horizon', type=str, help='Time horizon (e.g., 2 years)')
    parser.add_argument('--investment_type', type=str, help='Preferred investment type')
    parser.add_argument('--investment_amount', type=float, help='Amount available for investment')
    parser.add_argument('--other_investments', type=str, help='Other investments')
    parser.add_argument('--expected_returns', type=str, help='Expected returns')
    parser.add_argument('--max-tokens', type=int, default=300, help='Max tokens for LLM response')
    args = parser.parse_args()

    def parse_portfolio_list(items: Optional[list]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for item in (items or []):
            if not item:
                continue
            parts = [p.strip() for p in item.replace(',', ' ').split() if p.strip()]
            for p in parts:
                if ':' in p:
                    t, q = p.split(':', 1)
                    tk = sanitize_ticker(t)
                    if not is_valid_ticker(tk):
                        continue
                    try:
                        out[tk] = float(q)
                    except Exception:
                        out[tk] = 1.0
                else:
                    tk = sanitize_ticker(p)
                    if not is_valid_ticker(tk):
                        continue
                    out[tk] = 1.0
        return out

    portfolio = parse_portfolio_list(args.portfolio)

    user_profile: Dict[str, Any] = {
        "age": args.age,
        "occupation": args.occupation,
        "monthly_income": args.monthly_income,
        "monthly_expenses": args.monthly_expenses,
        "current_savings": args.current_savings,
        "investment_goals": args.investment_goals,
        "risk_tolerance": args.risk_tolerance,
        "time_horizon": args.time_horizon,
        "preferred_investment_type": args.investment_type,
        "investment_amount": args.investment_amount,
        "other_investments": args.other_investments,
        "expected_returns": args.expected_returns,
    }

    # If required fields missing, ask interactively
    clar_qs = get_clarification_questions(user_profile)
    if clar_qs:
        print('Some profile fields are missing; will ask interactively.')
        for q in clar_qs:
            ans = input(q + ' ') or ''
            if 'risk tolerance' in q.lower():
                user_profile['risk_tolerance'] = ans
            elif 'time horizon' in q.lower():
                user_profile['time_horizon'] = ans
            elif 'amount' in q.lower():
                try:
                    user_profile['investment_amount'] = float(ans.replace(',', '').strip())
                except Exception:
                    user_profile['investment_amount'] = None
            elif 'investment goals' in q.lower():
                user_profile['investment_goals'] = ans

    # If still missing everything, prompt for goal/portfolio
    if not portfolio and not user_profile.get('investment_goals'):
        print('No portfolio or investment goals provided.')
        goal = input('Enter your investment goal (e.g., Buy an SUV worth 2,500,000 INR in 2 years): ').strip()
        user_profile['investment_goals'] = goal

    print('Running investment_advice with portfolio:', portfolio)
    print('User profile:', {k: v for k, v in user_profile.items() if v is not None})

    response = investment_advice(portfolio, user_profile, max_tokens=args.max_tokens)
    # If the agent asks clarification questions, print them and exit
    if isinstance(response, str) and response.strip().startswith('To provide accurate advice'):
        print('\n=== Clarification Questions ===\n')
        print(response)
    else:
        print('\n=== Investment Advice ===\n')
        print(response)
