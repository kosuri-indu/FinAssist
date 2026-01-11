from dotenv import load_dotenv
import os
from typing import Callable, List, Any

load_dotenv()

from agents.web_agent import fetch_url_text, post_action
from agents.investment_agent import get_stock_history, get_stock_price, summarize_portfolio, investment_advice

try:
    from langchain.tools import Tool
except Exception:
    # Lightweight shim if langchain isn't installed or API differs.
    from dataclasses import dataclass

    @dataclass
    class Tool:
        name: str
        func: Callable
        description: str


def make_tools() -> List[Tool]:
    """Return a list of LangChain-compatible Tool objects wrapping our helpers."""
    tools = []
    tools.append(Tool(name='fetch_url_text', func=fetch_url_text, description='Fetch a URL and return extracted text and metadata'))
    tools.append(Tool(name='post_action', func=post_action, description='POST JSON to a URL and return response'))
    tools.append(Tool(
        name='get_stock_price',
        func=get_stock_price,
        description='Get the CURRENT/LATEST stock price for a ticker. Use this for simple price queries like "what is the price of MSFT" or "stock price of Apple". '
                    'Input: A single ticker symbol (e.g. "AAPL", "MSFT", "GOOGL", "TSLA"). '
                    'Returns a formatted string with the ACTUAL current price (e.g., "Current stock price of Microsoft Corporation (MSFT): USD 450.23 (as of 2024-01-15)"). '
                    'CRITICAL: When you use this tool, you MUST copy the EXACT price from the tool output. Do NOT use placeholders like $XXX.XX or [price]. '
                    'ALWAYS use this tool for simple price queries - do NOT provide analysis, just copy and return the exact price from the tool output.'
    ))
    tools.append(Tool(
        name='get_stock_history', 
        func=get_stock_history, 
        description='Get historical OHLC (Open, High, Low, Close) data for a ticker over a period. Use this for historical analysis, trends, or performance over time. '
                    'Input: A single ticker symbol (e.g. "AAPL", "MSFT", "GOOGL"). '
                    'Returns historical data including dates and prices. Use this when you need price history, not just current price.'
    ))
    tools.append(Tool(name='summarize_portfolio', func=summarize_portfolio, description='Summarize portfolio positions (ticker->qty). Input: dict of ticker->quantity or JSON string.'))
    tools.append(Tool(name='investment_advice', func=investment_advice, description='Get investment advice for a portfolio and goal'))
    return tools


def build_investment_agent_llm():
    """Build and return a LangChain agent executor backed by Gemini via call_gemini.

    Returns an agent executor if LangChain is installed. If LangChain isn't
    available, raises RuntimeError so callers can fallback.
    """
    try:
        from langchain.llms.base import LLM
        from langchain.agents import initialize_agent, AgentType
        from langchain.agents import Tool as LCTool
    except Exception as e:
        raise RuntimeError('LangChain not available: ' + str(e))

    # Small LLM wrapper that delegates to our call_gemini function
    from agents.llm_gemini import call_gemini

    class GeminiLLM(LLM):
        # LangChain LLM classes are pydantic models; declare fields as annotations
        model: str | None = None
        temperature: float = 0.0

        @property
        def _llm_type(self) -> str:
            return 'gemini'

        def _call(self, prompt: str, stop=None) -> str:
            # call_gemini raises RuntimeError on failure
            return call_gemini(prompt, model=self.model, temperature=self.temperature)

        @property
        def _identifying_params(self):
            return {'model': self.model, 'temperature': self.temperature}

    # Build LangChain Tool wrappers
    lc_tools = []
    for t in make_tools():
        try:
            lc_tools.append(LCTool(name=t.name, func=t.func, description=t.description))
        except Exception:
            # fallback to minimal Tool dataclass shim
            lc_tools.append(t)

    llm = GeminiLLM(model=os.environ.get('GENAI_MODEL') or os.environ.get('GENAI_MODEL') or None, temperature=float(os.environ.get('GENAI_TEMP') or 0.0))

    agent = initialize_agent(lc_tools, llm, agent=AgentType.CHAT_ZERO_SHOT_REACT_DESCRIPTION, verbose=False, handle_parsing_errors=True)
    return agent
