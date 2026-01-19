from agents.llm import call_llm
from agents.market_data import get_stock_price
import json


def build_advice(extracted: dict) -> str:
    """
    Build comprehensive investment advice based on extracted user intent.
    Fast processing with parallel market data fetching.
    """
    
    portfolio = extracted.get("portfolio", {})
    intent = extracted.get("intent", [])
    goal = extracted.get("goal", "").strip()
    question = extracted.get("question", "").strip()

    # Step 1: Check if portfolio exists
    has_portfolio = bool(portfolio and any(qty > 0 for qty in portfolio.values()))

    # Step 2: Fetch portfolio data ONLY if needed (faster)
    portfolio_context = ""
    if has_portfolio:
        holdings_text = []
        total_value = 0
        
        for ticker, qty in portfolio.items():
            try:
                price_info = get_stock_price(ticker)
                if price_info["price"]:
                    value = price_info["price"] * qty
                    total_value += value
                    holdings_text.append(
                        f"  - {ticker}: {qty} shares @ ₹{price_info['price']:.2f} = ₹{value:.2f}"
                    )
                else:
                    holdings_text.append(f"  - {ticker}: {qty} shares (price unavailable)")
            except Exception as e:
                holdings_text.append(f"  - {ticker}: {qty} shares (error fetching price)")
        
        portfolio_context = f"""
CURRENT PORTFOLIO:
Total Value: ₹{total_value:.2f}
Holdings ({len(portfolio)} positions):
{chr(10).join(holdings_text)}
"""

    # Step 3: Build main query
    main_query = question or goal or "general investment advice"

    # Step 4: Construct context-aware prompt
    prompt_parts = []
    
    if has_portfolio:
        prompt_parts.append(portfolio_context)
    
    prompt_parts.append(f"USER QUERY: {main_query}")
    
    if goal and goal != main_query:
        prompt_parts.append(f"GOAL: {goal}")
    
    if intent:
        prompt_parts.append(f"Detected Intent: {', '.join(intent)}")

    full_prompt = f"""You are a professional investment advisor. Provide comprehensive, actionable advice.

CONTEXT:
{chr(10).join(prompt_parts)}

INSTRUCTIONS:
1. {"Analyze the portfolio above and " if has_portfolio else ""}address the user's query/goal
2. Use the REAL market data provided (never make up prices)
3. Provide structured, specific recommendations
4. Include risk assessment and realistic expectations
5. Be concise but thorough

RESPONSE STRUCTURE:
{"• Portfolio Analysis" if has_portfolio else ""}
• Understanding Your Query/Goal
• Specific Recommendations
• Risk Considerations
• Next Steps

Provide your response now:"""

    try:
        response = call_llm(full_prompt)
        return response
    except Exception as e:
        error_msg = f"I apologize, but I encountered an error: {str(e)}"
        if has_portfolio:
            error_msg += f"\n\nYour portfolio summary:\n{portfolio_context}"
        return error_msg