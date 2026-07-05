import logging
from tavily import TavilyClient
from src.config import TAVILY_KEY

logger = logging.getLogger(__name__)
tavily = TavilyClient(api_key=TAVILY_KEY)

def needs_search(text):
    return True

def web_search(query):
    try:
        result = tavily.search(query=query, max_results=3)
        texts = [r['content'] for r in result['results']]
        return "\n\n".join(texts[:3])
    except Exception as e:
        logger.error(f"Search error: {e}")
        return ""