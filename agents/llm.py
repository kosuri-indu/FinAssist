import os
import time
import requests
from openai import OpenAI

# OpenAI client (fallback only)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Track which provider was used
last_provider = None

SYSTEM_PROMPT = """
You are a senior investment advisor and equity analyst with 15+ years of experience.

Your expertise includes:
- Fundamental analysis and company valuation
- Technical analysis and market trends
- Portfolio construction and risk management
- Sector and industry analysis
- Growth projections based on financials

Communication style:
- Professional but accessible
- Data-driven with specific numbers and metrics
- Detailed explanations of WHY, not just WHAT
- Compare stocks to industry benchmarks
- Provide context for all recommendations

Critical rules:
- NEVER hallucinate prices, metrics, or data
- ALWAYS use the exact data provided
- Explain financial metrics in context (e.g., "P/E of 25 is high for this sector")
- Give specific timeframes for projections
- Acknowledge uncertainty where appropriate
- Compare current valuation to historical ranges

When analyzing stocks:
1. Explain the business and why it matters
2. Interpret the performance data (not just repeat it)
3. Contextualize valuation metrics
4. Discuss growth drivers and headwinds
5. Give probability-weighted scenarios
6. Recommend specific actions with reasoning

Be thorough, insightful, and actionable - not generic.
"""


def call_llm(prompt: str) -> str:
    """
    Call LLM with automatic Groq/OpenAI selection.
    
    Priority:
    1. Use Groq if GROQ_API_KEY is set
    2. Fall back to OpenAI if Groq fails or key not set
    """
    global last_provider
    
    groq_key = os.environ.get('GROQ_API_KEY')
    
    # Try Groq first if key is available
    if groq_key:
        try:
            url = 'https://api.groq.com/openai/v1/chat/completions'
            
            payload = {
                'model': os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile'),
                'messages': [
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.2,
                'max_tokens': 2000
            }
            
            headers = {
                'Authorization': f'Bearer {groq_key}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            last_provider = 'groq'
            
            # Extract response text
            if 'choices' in data and len(data['choices']) > 0:
                choice = data['choices'][0]
                if 'message' in choice and 'content' in choice['message']:
                    return choice['message']['content']
            
            # Fallback if structure is different
            return str(data)
            
        except Exception as e:
            print(f"Groq API failed: {e}, falling back to OpenAI...")
            # Fall through to OpenAI
    
    # OpenAI fallback
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            timeout=30
        )
        last_provider = 'openai'
        return r.choices[0].message.content
    except Exception as e:
        return f"Error: Both Groq and OpenAI failed. {str(e)}"