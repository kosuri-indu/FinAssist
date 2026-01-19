import yfinance as yf
import requests




def get_stock_price(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    hist = t.history(period="1d")
    if hist.empty:
        return {"ticker": ticker, "price": None}
    return {"ticker": ticker, "price": round(float(hist['Close'].iloc[-1]), 2)}




def summarize_portfolio(portfolio: dict) -> dict:
    total = 0
    holdings = []
    for t, q in portfolio.items():
        p = get_stock_price(t)
        if p["price"] is None:
            continue
        value = p["price"] * q
        total += value
        holdings.append({"ticker": t, "quantity": q, "price": p["price"], "value": round(value, 2)})
    return {"total_value": round(total, 2), "holdings": holdings}