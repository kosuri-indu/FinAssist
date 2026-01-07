"""Demo runner for investment agents.

This script shows how to:
- Build and call the LangChain-backed investment agent (uses `agents.langchain_tools.build_investment_agent_llm`).
- Build and run the LangGraph investor graph (`agents.langgraph_investor_graph.build_graph`).

Set `OPENAI_API_KEY` in your environment before running.
"""
import os
import json
from agents.langchain_tools import build_investment_agent_llm
from agents.langgraph_investor_graph import build_graph


def demo_langchain_agent():
    print('\n=== LangChain Investment Agent Demo ===')
    try:
        agent = build_investment_agent_llm()
    except Exception as e:
        print('Could not build LangChain agent:', e)
        return

    query = "What's the current stock price of AAPL and any short recommendation?"
    try:
        # different LangChain versions expose different interfaces; try call/ run
        if hasattr(agent, 'run'):
            out = agent.run(query)
        else:
            out = agent.invoke(query)
        print('Agent output:\n', out)
    except Exception as e:
        print('Agent invocation failed:', e)


def demo_langgraph_graph():
    print('\n=== LangGraph Investor Graph Demo ===')
    try:
        g = build_graph()
    except Exception as e:
        print('Could not build langgraph graph:', e)
        return

    test_input = {
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

    try:
        out = g.invoke(test_input)
        print('\nProfile summary:\n', json.dumps(out.get('profile'), indent=2))
        print('\nProposal (truncated):\n', str(out.get('proposal'))[:1000])
    except Exception as e:
        print('Graph invoke failed:', e)


if __name__ == '__main__':
    # Quick environment check
    if not os.environ.get('OPENAI_API_KEY'):
        print('Warning: OPENAI_API_KEY not set; the demos will likely fail.')

    demo_langchain_agent()
    demo_langgraph_graph()
