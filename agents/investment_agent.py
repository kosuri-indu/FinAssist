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
            info = t.info if hasattr(t, 'info') else {}
            price = info.get('regularMarketPrice') or info.get('previousClose') or 0.0
            value = (price or 0.0) * float(qty)
            rows.append({'ticker': ticker, 'price': price, 'qty': qty, 'value': value})
            total_value += value
        except Exception as e:
            rows.append({'ticker': ticker, 'error': str(e)})
    return {'rows': rows, 'total_value': total_value}


def investment_advice(portfolio: Dict[str, float], user_goal: str | None = None) -> str:
    """Build a prompt with portfolio summary and call the LLM for advice.

    Returns text (LLM raw output)."""
    summary = summarize_portfolio(portfolio)
    prompt = (
        f"You are a financial advisor. Portfolio summary (JSON): {json.dumps(summary, default=str)}\n"
        f"User goal: {user_goal or 'None'}\n"
        "Provide: 1) brief risk assessment (low/med/high); 2) three practical suggestions; 3) short action plan. Return JSON when possible."
    )
    resp = call_gemini(prompt, model=os.environ.get('INVESTMENT_GEMINI_MODEL'))
    return resp
