from dotenv import load_dotenv
import os
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any

load_dotenv()

def fetch_url_text(url: str, timeout: int = 10) -> Dict[str, Any]:
    """Fetch a URL and return basic metadata and extracted text."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        # naive extraction: join top-level paragraphs
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')]
        text = '\n\n'.join(paragraphs[:50])
        title = soup.title.string if soup.title else None
        return {'url': url, 'status_code': resp.status_code, 'title': title, 'text': text}
    except Exception as e:
        return {'url': url, 'error': str(e)}


def post_action(url: str, data: Dict[str, Any], timeout: int = 10) -> Dict[str, Any]:
    """Perform a POST action; used for web-agent actions (careful!)."""
    try:
        resp = requests.post(url, json=data, timeout=timeout)
        resp.raise_for_status()
        return {'url': url, 'status_code': resp.status_code, 'response': resp.text}
    except Exception as e:
        return {'url': url, 'error': str(e)}
