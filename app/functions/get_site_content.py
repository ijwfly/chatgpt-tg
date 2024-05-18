import logging

import httpx
import trafilatura
from pydantic import Field

from app.functions.base import OpenAIFunction, OpenAIFunctionParams

logger = logging.getLogger(__name__)


class GetSiteContentParams(OpenAIFunctionParams):
    url: str = Field(..., description='url of the site to get text from')


class GetSiteContent(OpenAIFunction):
    PARAMS_SCHEMA = GetSiteContentParams

    @staticmethod
    async def fetch_content(url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:92.0) Gecko/20100101 Firefox/92.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0"
        }

        async with httpx.AsyncClient(headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()  # Проверка на успешный статус ответа
            return response.text

    async def run(self, params: GetSiteContentParams):
        try:
            downloaded = await self.fetch_content(params.url)
        except Exception as e:
            logger.error(f"Error while downloading site content: {e}")
            return f"Error: can't download this site. Maybe the site is not available or the URL is incorrect. Error {e}"

        contents = trafilatura.extract(downloaded, favor_precision=True, deduplicate=True)
        if not contents:
            return "Error: can't read contents of this site. Maybe there is some protection or the site is empty."
        return contents

    @classmethod
    def get_name(cls) -> str:
        return "get_site_content"

    @classmethod
    def get_description(cls) -> str:
        return "Get text content from a site by url"
