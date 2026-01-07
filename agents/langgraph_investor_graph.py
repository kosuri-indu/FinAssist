from langgraph.graph import StateGraph, END
from typing import TypedDict, Literal, Dict, Any
from agents.llm_gemini import call_gemini
from agents.investment_agent import get_stock_history, summarize_portfolio
import os


class InvestorState(TypedDict):
    age: int
    income: float
    net_worth: float
    profession: str
    horizon: str
    anticipated_retirement_age: int
    risk: str
    goal: str
    scope: Literal["basic", "comprehensive"]
    marital_status: str
    children: int

    # outputs
    profile: str
    research_plan: str
    market_data: str
    macro_analysis: str
    portfolio: str
    proposal: str


def _llm_invoke(prompt: str) -> str:
    try:
        return call_gemini(prompt, model=os.environ.get('GENAI_MODEL') or None)
    except Exception as e:
        return f"LLM error: {e}"


def analyze_investor_profile(state: InvestorState) -> InvestorState:
    prompt = f"""
    Analyze this investor profile and produce a concise summary suitable for building an investment plan:\n
    - Age: {state['age']}\n    - Income: ${state['income']}\n    - Net Worth: ${state['net_worth']}\n+    - Profession: {state['profession']}\n    - Marital Status: {state['marital_status']}, Children: {state['children']}\n    - Investment Horizon: {state['horizon']} (Anticipated retirement age: {state['anticipated_retirement_age']})\n    - Risk Tolerance: {state['risk']}\n    - Goal: {state['goal']}\n+    Provide a short profile paragraph (3-6 sentences) describing financial outlook and high-level allocation guidance.
    """
    state['profile'] = _llm_invoke(prompt)
    return state


def plan_research(state: InvestorState) -> InvestorState:
    prompt = f"""
    Based on this investor profile:\n{state['profile']}\n
    Suggest 4 focused research areas and 3 data sources (tickers or macro indicators) to consult.
    """
    state['research_plan'] = _llm_invoke(prompt)
    return state


def route_based_on_scope(state: InvestorState) -> list[str]:
    if state.get('scope') == 'comprehensive':
        return ['fetch_market_data', 'analyze_macro']
    return ['build_portfolio']


def fetch_market_data(state: InvestorState) -> Dict[str, Any]:
    # Simple market fetch: pull last 6 months price history for top tickers mentioned in research_plan
    tickers = []
    # naive ticker extraction: split words and keep uppercase tokens 1-5 chars
    for token in (state.get('research_plan') or '').split():
        if token.isupper() and 1 < len(token) <= 5:
            tickers.append(token)
    tickers = list(dict.fromkeys(tickers))[:5]
    result = {}
    for t in tickers:
        result[t] = get_stock_history(t, period='6mo')
    # also add a short LLM summary of market conditions
    summary = _llm_invoke('Summarize current market conditions affecting retirement-focused portfolios in 2-4 sentences.')
    return {'market_data': {'tickers': tickers, 'history': result, 'llm_summary': summary}}


def analyze_macro(state: InvestorState) -> Dict[str, Any]:
    summary = _llm_invoke('Analyze current macroeconomic trends (inflation, rates, growth) and their likely impact on a moderate-risk retirement portfolio in 3-5 bullets.')
    return {'macro_analysis': summary}


def build_portfolio(state: InvestorState) -> InvestorState:
    prompt = f"""
    Using the following inputs:\n
    Profile: {state.get('profile')}\n    Research Plan: {state.get('research_plan')}\n    Market Data: {state.get('market_data')}\n    Macro Analysis: {state.get('macro_analysis')}\n
    Recommend a portfolio allocation (ETF/ticker and percent) for a {state.get('risk')} risk investor with a {state.get('horizon')} horizon.
    Provide 5 lines: ticker, percent, rationale.
    """
    state['portfolio'] = _llm_invoke(prompt)
    return state


def generate_proposal(state: InvestorState) -> InvestorState:
    prompt = f"""
    Compile a final investment proposal covering:\n
    - Profile and financial outlook\n    - Research plan\n    - Key market and macro points\n    - Portfolio allocation and a short action plan (5 steps)\n
    Profile: {state.get('profile')}\n    Research: {state.get('research_plan')}\n    Market Data: {state.get('market_data')}\n    Macro: {state.get('macro_analysis')}\n    Portfolio: {state.get('portfolio')}\n
    Present clearly and concisely.
    """
    state['proposal'] = _llm_invoke(prompt)
    return state


def build_graph() -> StateGraph:
    g = StateGraph(InvestorState)
    g.add_node('analyze_profile', analyze_investor_profile)
    g.add_node('plan_research', plan_research)
    g.add_node('fetch_market_data', fetch_market_data)
    g.add_node('analyze_macro', analyze_macro)
    g.add_node('build_portfolio', build_portfolio)
    g.add_node('generate_proposal', generate_proposal)

    g.set_entry_point('analyze_profile')
    g.add_edge('analyze_profile', 'plan_research')
    g.add_conditional_edges('plan_research', route_based_on_scope, ['fetch_market_data', 'analyze_macro', 'build_portfolio'])
    g.add_edge(['fetch_market_data', 'analyze_macro'], 'build_portfolio')
    g.add_edge('build_portfolio', 'generate_proposal')
    g.add_edge('generate_proposal', END)
    return g


if __name__ == '__main__':
    # Simple demo when run directly
    base = {
        'age': 45,
        'income': 140000,
        'net_worth': 500000,
        'profession': 'Senior Business Analyst',
        'horizon': 'long-term',
        'anticipated_retirement_age': 66,
        'risk': 'moderate',
        'goal': 'retirement',
        'marital_status': 'Married',
        'children': 2,
        'scope': 'comprehensive',
        'profile': '',
        'research_plan': '',
        'market_data': '',
        'macro_analysis': '',
        'portfolio': '',
        'proposal': '',
    }
    graph = build_graph()
    out = graph.invoke(base)
    print('Profile:\n', out.get('profile'))
    print('\nProposal:\n', out.get('proposal'))
