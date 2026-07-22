"""HTTP client for the Tavily API (https://docs.tavily.com).

Used by the web search / scraper sub-agents. API key comes from
settings.TAVILY_API_KEY and is sent as a Bearer token.
"""

from typing import List, Optional

import httpx

import settings


class TavilyError(Exception):
    pass


def _extract_error(response: httpx.Response) -> str:
    try:
        data = response.json()
        return data.get('detail', {}).get('error') or data.get('error') or response.text
    except Exception:
        return response.text or f'HTTP {response.status_code}'


class TavilyClient:
    BASE_URL = 'https://api.tavily.com'

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.TAVILY_API_KEY
        self.base_url = (base_url or self.BASE_URL).rstrip('/')

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self.api_key}'}

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=settings.WEB_AGENT_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f'{self.base_url}{path}',
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as e:
            raise TavilyError(f'Tavily unavailable: {e}')
        if response.status_code != 200:
            raise TavilyError(_extract_error(response))
        return response.json()

    async def search(self, query: str, max_results: int = 5) -> dict:
        """Returns {'results': [{'title', 'url', 'content', 'score'}, ...], ...}"""
        return await self._post('/search', {
            'query': query,
            'max_results': max_results,
            'search_depth': 'basic',
        })

    async def extract(self, urls: List[str]) -> dict:
        """Returns {'results': [{'url', 'raw_content'}, ...], 'failed_results': [...]}"""
        return await self._post('/extract', {'urls': urls})
