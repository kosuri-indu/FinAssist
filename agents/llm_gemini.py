from dotenv import load_dotenv
import os
from typing import Optional

load_dotenv()

# Simple OpenAI-backed LLM caller. We keep the function name `call_gemini` so existing
# code that imports it doesn't need changes; it will use OpenAI behind the scenes.
DEFAULT_MODEL = os.environ.get('OPENAI_MODEL') or 'gpt-4o-mini'


def call_gemini(prompt: str, model: Optional[str] = None, max_tokens: int = 512, temperature: float = 0.0) -> str:
    """Call an LLM and return the assistant text.

    This implementation uses the OpenAI Python client. Set `OPENAI_API_KEY` in your
    environment (or via a .env file) to use it. The function intentionally does not
    print or return any secret values.
    """
    oa_key = os.environ.get('OPENAI_API_KEY')
    if not oa_key:
        raise RuntimeError('OPENAI_API_KEY is not set in the environment')

    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError('OpenAI Python package is not installed') from e

    client = OpenAI(api_key=oa_key)
    model_to_use = model or DEFAULT_MODEL

    # Use the chat completions API available in the OpenAI client wrapper.
    resp = client.chat.completions.create(
        model=model_to_use,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    )

    # Extract text in a way that matches the client's response shape.
    try:
        return resp.choices[0].message.content.strip()
    except Exception:
        # Fall back to a best-effort string cast
        return str(resp)

