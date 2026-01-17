import os
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are a professional investment advisor.

Rules:
- NEVER hallucinate prices
- NEVER guess numbers
- ALWAYS rely on provided market data
- Be structured and detailed

Output format:

Final Answer:
1. Understanding Your Situation
2. Current Portfolio Analysis
3. Goal Feasibility Analysis
4. Strategy Options (Low / Moderate / High Risk)
5. Recommended Action Plan
6. Risks and Reality Check
"""

def call_llm(prompt: str) -> str:
    r = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        timeout=30   # <<< THIS IS THE CRITICAL FIX
    )
    return r.choices[0].message.content
