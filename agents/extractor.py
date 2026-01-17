from agents.llm import call_llm
import json
import re


def extract_user_intent(user_input: str) -> dict:
    """
    Extract portfolio, intent, goal, and question from user input.
    Uses LLM to intelligently parse the message.
    """
    
    prompt = f"""Extract investment information from this message and return ONLY valid JSON.

User message: "{user_input}"

Return JSON with these exact keys:
{{
  "portfolio": {{}},  // dict of ticker->quantity, e.g. {{"AAPL": 10, "GOOGL": 5}}
  "intent": [],  // list from: ["portfolio_analysis", "price_query", "goal_analysis", "education", "general_advice"]
  "goal": "",  // investment goal string like "save for retirement in 10 years"
  "question": ""  // main question/query
}}

Rules:
- Convert company names to tickers (Apple->AAPL, Amazon->AMZN, Google->GOOGL, Microsoft->MSFT, Tesla->TSLA, AST SpaceMobile->ASTS, etc.)
- Extract quantities mentioned (e.g., "10 ASTS" -> {{"ASTS": 10}})
- If no portfolio: use empty dict {{}}
- If no goal: use empty string
- question should capture the main query
- Return ONLY the JSON object, no other text"""

    try:
        raw_response = call_llm(prompt)
        
        # Clean response - remove markdown code blocks if present
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            # Remove ```json and ``` markers
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)
        
        parsed = json.loads(cleaned)
        
        # Validate structure
        result = {
            "portfolio": parsed.get("portfolio", {}),
            "intent": parsed.get("intent", []),
            "goal": parsed.get("goal", ""),
            "question": parsed.get("question", user_input)
        }
        
        # Ensure portfolio values are integers
        if result["portfolio"]:
            result["portfolio"] = {
                k: int(v) for k, v in result["portfolio"].items() if v
            }
        
        return result
        
    except Exception as e:
        print(f"Extraction error: {e}")
        # Fallback: basic extraction
        return {
            "portfolio": {},
            "intent": ["general_advice"],
            "goal": "",
            "question": user_input
        }