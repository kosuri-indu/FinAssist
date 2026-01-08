from dotenv import load_dotenv
import os
import json
from typing import Dict, Any
import yfinance as yf
from agents.llm_gemini import call_gemini

load_dotenv()

def get_stock_history(ticker: str, period: str = "6mo") -> list:
    """Return historical OHLC data as a list of dicts."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        return hist.reset_index().to_dict(orient='records')
    except Exception as e:
        return [{"error": str(e)}]


def summarize_portfolio(portfolio: Dict[str, float]) -> Dict[str, Any]:
    """Given a mapping ticker -> quantity, return price info and totals."""
    rows = []
    total_value = 0.0
    for ticker, qty in (portfolio or {}).items():
        try:
            t = yf.Ticker(ticker)
            # Gather detailed yfinance objects (info, fast_info, and recent history)
            info = getattr(t, 'info', {}) or {}
            fast_info = getattr(t, 'fast_info', {}) or {}
            try:
                hist_recent = t.history(period='5d').reset_index().to_dict(orient='records')
            except Exception:
                hist_recent = []

            # Print the full yfinance response to the terminal for debugging/inspection.
            try:
                print(f"Full yfinance response for {ticker}:\n{json.dumps({'info': info, 'fast_info': fast_info, 'history_recent': hist_recent}, default=str, indent=2)}")
            except Exception:
                # If printing fails, don't let it block the summary.
                print(f"(Could not pretty-print yfinance response for {ticker})")

            # Prefer fast_info lastPrice when available for the latest price
            price = fast_info.get('lastPrice') or info.get('regularMarketPrice') or info.get('previousClose') or 0.0
            value = (price or 0.0) * float(qty)
            rows.append({'ticker': ticker, 'price': price, 'qty': qty, 'value': value})
            total_value += value
        except Exception as e:
            rows.append({'ticker': ticker, 'error': str(e)})
    return {'rows': rows, 'total_value': total_value}


def investment_advice(portfolio: Dict[str, float], user_goal: str | None = None, max_tokens: int = 300) -> str:
    """Build a prompt with portfolio summary and call the LLM for advice.

    Returns text (LLM raw output)."""
    summary = summarize_portfolio(portfolio)
    prompt = (
        f"You are a financial advisor. Portfolio summary (JSON): {json.dumps(summary, default=str)}\n"
        f"User goal: {user_goal or 'None'}\n"
        "Provide: 1) brief risk assessment (low/med/high) unless the goal mentions the level of risk; 2) three practical suggestions; 3) short action plan. Return the responce in less than 400 words."
    )
    # Delegate to the LLM caller but pass an explicit max_tokens to limit response length.
    resp = call_gemini(prompt, model=os.environ.get('INVESTMENT_GEMINI_MODEL'), max_tokens=max_tokens)
    return resp


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Run investment_advice demo and print LLM response')
    # parser.add_argument('--max-tokens', type=int, default=300, help='Max tokens for LLM response')
    parser.add_argument('--portfolio', nargs='*', default=None,
                        help='Ticker:qty pairs, e.g. AAPL:2 MSFT:1')
    parser.add_argument('--goal', type=str, default=None, help='User goal (e.g. Buy an SUV worth 2,500,000 INR within 2 years)')
    args = parser.parse_args()

    # Helper to parse portfolio input
    def parse_portfolio_list(items: list) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for item in (items or []):
            if not item:
                continue
            parts = [p.strip() for p in item.replace(',', ' ').split() if p.strip()]
            for p in parts:
                if ':' in p:
                    t, q = p.split(':', 1)
                    try:
                        out[t.strip().upper()] = float(q)
                    except Exception:
                        out[t.strip().upper()] = 1.0
                else:
                    out[p.strip().upper()] = 1.0
        return out

    portfolio: Dict[str, float] = parse_portfolio_list(args.portfolio)
    goal = args.goal

    # If neither portfolio nor goal provided, ask interactively
    if not portfolio and not goal:
        print('No portfolio or goal provided.')
        while True:
            choice = input("Would you like to provide a 'goal' or a 'portfolio'? (type 'goal' or 'portfolio', or 'quit' to exit): ").strip().lower()
            if choice == 'quit':
                print('Exiting.')
                raise SystemExit(0)
            if choice == 'goal':
                goal = input('Enter your goal (e.g. Buy an SUV worth 2,500,000 INR within 2 years): ').strip()
                more = input('Would you like to provide your current portfolio as well? (y/n): ').strip().lower()
                if more.startswith('y'):
                    ptext = input('Enter tickers as TICKER:QTY separated by space or comma (e.g. AAPL:2 MSFT:1): ').strip()
                    portfolio = parse_portfolio_list([ptext])
                break
            if choice == 'portfolio':
                ptext = input('Enter tickers as TICKER:QTY separated by space or comma (e.g. AAPL:2 MSFT:1): ').strip()
                portfolio = parse_portfolio_list([ptext])
                more = input('Would you like to provide a goal as well? (y/n): ').strip().lower()
                if more.startswith('y'):
                    goal = input('Enter your goal (e.g. Buy an SUV worth 2,500,000 INR within 2 years): ').strip()
                break
            print("Please answer 'goal', 'portfolio', or 'quit'.")

    # If a goal was given but portfolio is empty, optionally prompt for portfolio
    if goal and not portfolio:
        more = input('You provided a goal but no portfolio. Would you like to provide your current portfolio? (y/n): ').strip().lower()
        if more.startswith('y'):
            ptext = input('Enter tickers as TICKER:QTY separated by space or comma (e.g. AAPL:2 MSFT:1): ').strip()
            portfolio = parse_portfolio_list([ptext])

    print('Running investment_advice with portfolio:', portfolio)
    print('User goal:', goal)
    resp = investment_advice(portfolio, user_goal=goal)
    print('\n=== Final LLM response ===\n')
    print(resp)
