import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# Exchange rate: 1 USD = 83 INR (approximate, adjust as needed)
USD_TO_INR = 83


def get_stock_price(ticker: str) -> dict:
    """Get current stock price."""
    t = yf.Ticker(ticker)
    hist = t.history(period="1d")
    if hist.empty:
        return {"ticker": ticker, "price": None}
    return {"ticker": ticker, "price": round(float(hist['Close'].iloc[-1]), 2)}


def get_detailed_stock_analysis(ticker: str) -> dict:
    """
    Get comprehensive stock analysis including:
    - Current price and company info
    - Historical performance (1M, 3M, 6M, 1Y)
    - Volatility metrics
    - Key fundamentals
    - Analyst recommendations
    """
    try:
        stock = yf.Ticker(ticker)
        
        # Basic info
        info = stock.info
        hist_1y = stock.history(period="1y")
        hist_3m = stock.history(period="3mo")
        hist_1m = stock.history(period="1mo")
        
        if hist_1y.empty:
            return {"ticker": ticker, "error": "No data available"}
        
        current_price = float(hist_1y['Close'].iloc[-1])
        
        # Calculate performance
        def calc_return(hist, period_name):
            if len(hist) < 2:
                return None
            start_price = float(hist['Close'].iloc[0])
            end_price = float(hist['Close'].iloc[-1])
            return round(((end_price - start_price) / start_price) * 100, 2)
        
        returns = {
            "1_month": calc_return(hist_1m, "1M") if len(hist_1m) > 1 else None,
            "3_month": calc_return(hist_3m, "3M") if len(hist_3m) > 1 else None,
            "1_year": calc_return(hist_1y, "1Y") if len(hist_1y) > 1 else None
        }
        
        # Volatility (standard deviation of daily returns)
        daily_returns = hist_1y['Close'].pct_change().dropna()
        volatility = round(daily_returns.std() * 100, 2) if len(daily_returns) > 0 else None
        
        # 52-week high/low
        high_52w = round(float(hist_1y['High'].max()), 2)
        low_52w = round(float(hist_1y['Low'].min()), 2)
        
        # Distance from 52w high
        distance_from_high = round(((current_price - high_52w) / high_52w) * 100, 2)
        
        # Company fundamentals
        company_name = info.get('longName', ticker)
        sector = info.get('sector', 'N/A')
        industry = info.get('industry', 'N/A')
        market_cap = info.get('marketCap', None)
        
        # Format market cap
        if market_cap:
            market_cap_inr = market_cap * USD_TO_INR
            if market_cap_inr >= 1e12:
                market_cap_str = f"₹{market_cap_inr/1e12:.2f}T"
            elif market_cap_inr >= 1e9:
                market_cap_str = f"₹{market_cap_inr/1e9:.2f}B"
            elif market_cap_inr >= 1e6:
                market_cap_str = f"₹{market_cap_inr/1e6:.2f}M"
            else:
                market_cap_str = f"₹{market_cap_inr:,.0f}"
        else:
            market_cap_str = "N/A"
        
        # Valuation metrics
        pe_ratio = info.get('trailingPE', None)
        if pe_ratio:
            pe_ratio = round(pe_ratio, 2)
        
        forward_pe = info.get('forwardPE', None)
        if forward_pe:
            forward_pe = round(forward_pe, 2)
        
        # Profitability
        profit_margin = info.get('profitMargins', None)
        if profit_margin:
            profit_margin = round(profit_margin * 100, 2)
        
        # Growth
        revenue_growth = info.get('revenueGrowth', None)
        if revenue_growth:
            revenue_growth = round(revenue_growth * 100, 2)
        
        earnings_growth = info.get('earningsGrowth', None)
        if earnings_growth:
            earnings_growth = round(earnings_growth * 100, 2)
        
        # Dividend
        dividend_yield = info.get('dividendYield', None)
        if dividend_yield:
            dividend_yield = round(dividend_yield * 100, 2)
        
        # Analyst recommendations
        recommendations = stock.recommendations
        recent_recommendation = None
        if recommendations is not None and not recommendations.empty:
            # Get most recent recommendation
            recent = recommendations.tail(5)
            if not recent.empty:
                rec_summary = recent['To Grade'].value_counts().to_dict()
                recent_recommendation = rec_summary
        
        # Target price
        target_mean = info.get('targetMeanPrice', None)
        target_high = info.get('targetHighPrice', None)
        target_low = info.get('targetLowPrice', None)
        
        upside_potential = None
        if target_mean and current_price:
            upside_potential = round(((target_mean - current_price) / current_price) * 100, 2)
        
        return {
            "ticker": ticker,
            "company_name": company_name,
            "current_price": round(current_price, 2),
            "sector": sector,
            "industry": industry,
            "market_cap": market_cap_str,
            
            # Performance
            "returns": returns,
            "volatility_pct": volatility,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "distance_from_high_pct": distance_from_high,
            
            # Fundamentals
            "pe_ratio": pe_ratio,
            "forward_pe": forward_pe,
            "profit_margin_pct": profit_margin,
            "revenue_growth_pct": revenue_growth,
            "earnings_growth_pct": earnings_growth,
            "dividend_yield_pct": dividend_yield,
            
            # Analyst data
            "analyst_target_mean": round(target_mean, 2) if target_mean else None,
            "analyst_target_high": round(target_high, 2) if target_high else None,
            "analyst_target_low": round(target_low, 2) if target_low else None,
            "upside_potential_pct": upside_potential,
            "recent_recommendations": recent_recommendation
        }
        
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def summarize_portfolio(portfolio: dict) -> dict:
    """Basic portfolio summary."""
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