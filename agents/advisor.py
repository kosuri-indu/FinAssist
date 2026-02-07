from agents.llm import call_llm
from agents.market_data import get_stock_price
import yfinance as yf


def get_stock_details(ticker: str, quantity: int) -> dict:
    """Get essential stock details for analysis."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist_1y = stock.history(period="1y")
        hist_3m = stock.history(period="3mo")
        
        # Get current price - try multiple methods
        current_price = None
        if not hist_1y.empty:
            current_price = float(hist_1y['Close'].iloc[-1])
        elif info.get('currentPrice'):
            current_price = float(info.get('currentPrice'))
        elif info.get('regularMarketPrice'):
            current_price = float(info.get('regularMarketPrice'))
        
        if not current_price:
            return {"ticker": ticker, "error": "No price data available"}
        
        # Calculate returns
        returns = {}
        if len(hist_3m) > 1:
            ret_3m = ((hist_3m['Close'].iloc[-1] - hist_3m['Close'].iloc[0]) / hist_3m['Close'].iloc[0]) * 100
            returns['3_month'] = round(ret_3m, 2)
        
        if len(hist_1y) > 1:
            ret_1y = ((hist_1y['Close'].iloc[-1] - hist_1y['Close'].iloc[0]) / hist_1y['Close'].iloc[0]) * 100
            returns['1_year'] = round(ret_1y, 2)
        
        # 52-week range
        high_52w = round(float(hist_1y['High'].max()), 2) if not hist_1y.empty else None
        low_52w = round(float(hist_1y['Low'].min()), 2) if not hist_1y.empty else None
        
        return {
            "ticker": ticker,
            "quantity": quantity,
            "company_name": info.get('longName', ticker),
            "current_price": round(current_price, 2),
            "value": round(current_price * quantity, 2),
            "sector": info.get('sector', 'N/A'),
            "industry": info.get('industry', 'N/A'),
            "returns": returns,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "pe_ratio": round(info.get('trailingPE', 0), 2) if info.get('trailingPE') else None,
            "market_cap": info.get('marketCap', None),
            "dividend_yield": round(info.get('dividendYield', 0) * 100, 2) if info.get('dividendYield') else None,
            "beta": round(info.get('beta', 0), 2) if info.get('beta') else None,
            "target_price": round(info.get('targetMeanPrice', 0), 2) if info.get('targetMeanPrice') else None,
        }
    except Exception as e:
        print(f"Error fetching details for {ticker}: {e}")
        # Fallback to basic price
        price_info = get_stock_price(ticker)
        if price_info.get('price'):
            return {
                "ticker": ticker,
                "quantity": quantity,
                "company_name": ticker,
                "current_price": price_info['price'],
                "value": price_info['price'] * quantity,
                "sector": "N/A",
                "returns": {},
                "error": f"Limited data: {str(e)}"
            }
        return {"ticker": ticker, "error": str(e)}


def build_advice(extracted: dict) -> str:
    """
    Build comprehensive investment advice based on extracted user intent.
    Uses real market data with detailed analysis.
    """
    
    portfolio = extracted.get("portfolio", {})
    intent = extracted.get("intent", [])
    goal = extracted.get("goal", "").strip()
    question = extracted.get("question", "").strip()

    # Check if portfolio exists
    has_portfolio = bool(portfolio and any(qty > 0 for qty in portfolio.values()))

    if not has_portfolio:
        # No portfolio - provide general advice
        main_query = question or goal or "general investment advice"
        prompt = f"""You are a professional investment advisor. The user asks: "{main_query}"

Provide comprehensive investment advice covering:
1. Understanding their query/goal
2. General investment principles
3. Portfolio construction recommendations
4. Risk management
5. Next steps

Be specific, actionable, and professional."""
        
        try:
            return call_llm(prompt)
        except Exception as e:
            return f"I apologize, but I encountered an error: {str(e)}"

    # Fetch detailed stock data
    print("\n" + "="*60)
    print("Fetching real-time market data for your portfolio...")
    print("="*60 + "\n")
    
    stock_details = []
    total_value = 0
    
    for ticker, qty in portfolio.items():
        print(f"Fetching data for {ticker}...")
        details = get_stock_details(ticker, qty)
        
        if "error" not in details:
            stock_details.append(details)
            total_value += details.get('value', 0)
            print(f"✓ {ticker}: ${details.get('current_price'):.2f} x {qty} = ${details.get('value'):.2f}")
        else:
            print(f"✗ {ticker}: {details.get('error')}")
            stock_details.append(details)
    
    print(f"\nTotal Portfolio Value: ${total_value:.2f}")
    print("="*60 + "\n")
    
    # Build detailed portfolio context
    portfolio_context = f"""PORTFOLIO OVERVIEW:
Total Value: ${total_value:.2f}
Number of Holdings: {len(portfolio)}

HOLDINGS DETAIL:
"""
    
    for details in stock_details:
        if "error" in details:
            portfolio_context += f"\n- {details['ticker']}: {details.get('quantity', 0)} shares (data unavailable)\n"
            continue
        
        ticker = details['ticker']
        company = details['company_name']
        qty = details['quantity']
        price = details['current_price']
        value = details['value']
        sector = details.get('sector', 'N/A')
        
        portfolio_context += f"\n{ticker} ({company})\n"
        portfolio_context += f"  Current Price: ${price:.2f} per share\n"
        portfolio_context += f"  Your Position: {qty} shares valued at ${value:.2f}\n"
        portfolio_context += f"  Sector: {sector}\n"
        
        # Performance
        returns = details.get('returns', {})
        if returns.get('3_month'):
            portfolio_context += f"  3-Month Return: {returns['3_month']:+.2f}%\n"
        if returns.get('1_year'):
            portfolio_context += f"  1-Year Return: {returns['1_year']:+.2f}%\n"
        
        # 52-week range
        if details.get('high_52w') and details.get('low_52w'):
            portfolio_context += f"  52-Week Range: ${details['low_52w']} - ${details['high_52w']}\n"
        
        # Fundamentals
        if details.get('pe_ratio'):
            portfolio_context += f"  P/E Ratio: {details['pe_ratio']}\n"
        if details.get('dividend_yield'):
            portfolio_context += f"  Dividend Yield: {details['dividend_yield']}%\n"
        if details.get('beta'):
            portfolio_context += f"  Beta: {details['beta']}\n"
        if details.get('target_price'):
            upside = ((details['target_price'] - price) / price) * 100
            portfolio_context += f"  Analyst Target Price: ${details['target_price']} ({upside:+.2f}% potential)\n"
        
        # Market cap
        if details.get('market_cap'):
            mcap = details['market_cap']
            if mcap >= 1e12:
                mcap_str = f"${mcap/1e12:.2f}T"
            elif mcap >= 1e9:
                mcap_str = f"${mcap/1e9:.2f}B"
            else:
                mcap_str = f"${mcap/1e6:.2f}M"
            portfolio_context += f"  Market Cap: {mcap_str}\n"

    # Build main query
    main_query = question or goal or "Analyze my portfolio and provide recommendations"

    # Construct professional prompt
    full_prompt = f"""You are a senior investment advisor. Analyze this portfolio and provide detailed, professional advice.

{portfolio_context}

USER QUERY: {main_query}
{f"INVESTMENT GOAL: {goal}" if goal and goal != main_query else ""}

Provide a comprehensive analysis following this EXACT structure:

📊 Your Personalized Investment Advice

• Portfolio Analysis
[Summarize total value, list each holding with current price and valuation. Use the EXACT prices provided above.]

• Understanding Your Query/Goal
[Address their specific question/goal directly]

• Detailed Stock Analysis
[For EACH stock:
 - Brief company description and sector
 - Current valuation context (is P/E high/low for sector?)
 - Recent performance interpretation (explain what the returns mean)
 - Analyst outlook (use target price if provided)
 - Specific recommendation: BUY/HOLD/SELL with clear reasoning]

• Risk Considerations
[Identify specific risks:
 - Sector concentration
 - Individual stock risks
 - Market risks
 - Diversification issues]

• Specific Recommendations
[Concrete actions with reasoning:
 - Should they hold each stock? Why?
 - Should they buy more or sell? At what price?
 - Should they diversify? Into what?]

• Next Steps
[Prioritized action items with timeline]

CRITICAL RULES:
- Use the EXACT prices and data provided above
- NEVER make up numbers or prices
- Be specific and actionable (not generic advice)
- Explain WHY for every recommendation
- Compare to sector benchmarks when discussing P/E, returns, etc.
- Give specific price targets or thresholds for actions
- Discuss growth prospects based on the data

Provide your comprehensive analysis now:"""

    try:
        response = call_llm(full_prompt)
        return response
    except Exception as e:
        error_msg = f"I apologize, but I encountered an error generating advice: {str(e)}\n\n"
        error_msg += f"However, here's your portfolio summary:\n{portfolio_context}"
        return error_msg