"""Web agent tools: web_search_agent and web_scraper_agent.

The two public tools each run an isolated LLM sub-agent (see
app/runtime/web_agent_runner.py) equipped with low-level Tavily tools
(tavily_search / tavily_extract). The sub-agent returns a synthesized
answer with source links, which becomes the tool result for the main LLM.
"""

from typing import List, Optional

from pydantic import Field

import settings
from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from app.runtime.web_agent_runner import run_web_agent
from app.web.tavily_client import TavilyClient, TavilyError


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, {len(text) - limit} chars omitted)"


# --- Internal tools, registered only inside the web sub-agents ---

class TavilySearchParams(OpenAIFunctionParams):
    query: str = Field(..., description="search query")
    max_results: int = Field(5, description="number of results to return (1-10)")


class TavilySearch(OpenAIFunction):
    PARAMS_SCHEMA = TavilySearchParams

    async def run(self, params: TavilySearchParams) -> Optional[str]:
        try:
            response = await TavilyClient().search(params.query, max_results=params.max_results)
        except TavilyError as e:
            return f"Error: {e}"
        results = response.get('results') or []
        if not results:
            return "No results found."
        lines = []
        for i, result in enumerate(results, 1):
            lines.append(f"{i}. {result.get('title', '(no title)')}\n"
                         f"   URL: {result.get('url', '')}\n"
                         f"   {result.get('content', '')}")
        return "\n".join(lines)

    @classmethod
    def get_name(cls) -> str:
        return "tavily_search"

    @classmethod
    def get_description(cls) -> str:
        return "Search the web. Returns a list of results with title, URL and a content snippet."


class TavilyExtractParams(OpenAIFunctionParams):
    urls: List[str] = Field(..., description="URLs to extract content from (1-3 per call)")


class TavilyExtract(OpenAIFunction):
    PARAMS_SCHEMA = TavilyExtractParams

    async def run(self, params: TavilyExtractParams) -> Optional[str]:
        try:
            response = await TavilyClient().extract(params.urls)
        except TavilyError as e:
            return f"Error: {e}"
        parts = []
        for result in response.get('results') or []:
            content = _truncate(result.get('raw_content') or '', settings.WEB_AGENT_EXTRACT_MAX_CHARS)
            parts.append(f"URL: {result.get('url', '')}\n{content}")
        for failed in response.get('failed_results') or []:
            url = failed.get('url', failed) if isinstance(failed, dict) else failed
            parts.append(f"URL: {url}\nFailed to extract content.")
        if not parts:
            return "No content extracted."
        return "\n\n---\n\n".join(parts)

    @classmethod
    def get_name(cls) -> str:
        return "tavily_extract"

    @classmethod
    def get_description(cls) -> str:
        return "Extract the full page content from the given URLs."


# --- Public tools, exposed to the main LLM ---

WEB_SEARCH_AGENT_SYSTEM_PROMPT = """You are a web search agent. Your job is to answer the given task using web search, quickly and efficiently.

Reasoning structure for every task:
1. Decide: is this a simple query (a single fact or current value, e.g. "USD exchange rate today") or a complex one (requires digging into the topic, e.g. "find the best recipes")?
2. Formulate the search query. Prefer searching in English — results are usually better — unless the task is tied to a specific language or region.
3. Simple query: one tavily_search, answer directly from the snippets.
   Complex query: tavily_search, then tavily_extract on the most promising URLs to read full pages.
4. Synthesize everything you found into a complete answer.

Be efficient — aim to finish within 2-3 turns:
- Batch tool calls: issue multiple tavily_search calls in one turn if you need several angles, and extract up to 3 URLs in a single tavily_extract call.
- Search again only if the results are truly unusable.

Your final answer must:
- Be a synthesis of what you found, in the same language as the task.
- Include a "Sources:" list with the URLs you actually used.
- Say explicitly if you could not find reliable information."""


class WebSearchAgentParams(OpenAIFunctionParams):
    query: str = Field(..., description="natural language search query, include all relevant context")


class WebSearchAgent(OpenAIFunction):
    PARAMS_SCHEMA = WebSearchAgentParams
    STATUS_DETAIL_PARAM = 'query'

    async def run(self, params: WebSearchAgentParams) -> Optional[str]:
        return await run_web_agent(
            self.user, self.db, self.context_manager, self.side_effects,
            system_prompt=WEB_SEARCH_AGENT_SYSTEM_PROMPT,
            task=params.query,
            tool_classes=[TavilySearch, TavilyExtract],
        )

    @classmethod
    def get_name(cls) -> str:
        return "web_search_agent"

    @classmethod
    def get_description(cls) -> str:
        return ("Search the web for up-to-date information. Use for anything requiring current data, "
                "news, or facts you're unsure about. Returns a synthesized answer with source links.")

    @classmethod
    def get_status_message(cls) -> str:
        return 'Searching the web...'


WEB_SCRAPER_AGENT_SYSTEM_PROMPT = """You are a web scraper agent. You are given a URL and optionally a task describing what to extract from it.

Work method:
1. Extract the page content with tavily_extract.
2. If the task requires it and the page links to other pages you need, you may extract those too (at most a few).
3. Return the requested information. Usually a single tavily_extract call is enough; aim to finish in 1-2 turns.

Your final answer must:
- Contain the extracted information or summary, in the same language as the task (or the page language if no task is given).
- If a task is given, focus strictly on it; otherwise provide a general summary of the page.
- Always mention the source URL.
- Say explicitly if the page could not be extracted."""


class WebScraperAgentParams(OpenAIFunctionParams):
    url: str = Field(..., description="URL of the page to extract content from")
    task: Optional[str] = Field(None, description="what exactly to extract or answer from the page; omit for a general summary")


class WebScraperAgent(OpenAIFunction):
    PARAMS_SCHEMA = WebScraperAgentParams
    STATUS_DETAIL_PARAM = 'url'

    async def run(self, params: WebScraperAgentParams) -> Optional[str]:
        task = f"URL: {params.url}"
        if params.task:
            task += f"\nTask: {params.task}"
        else:
            task += "\nTask: provide a general summary of the page content."
        return await run_web_agent(
            self.user, self.db, self.context_manager, self.side_effects,
            system_prompt=WEB_SCRAPER_AGENT_SYSTEM_PROMPT,
            task=task,
            tool_classes=[TavilyExtract],
        )

    @classmethod
    def get_name(cls) -> str:
        return "web_scraper_agent"

    @classmethod
    def get_description(cls) -> str:
        return ("Extract and summarize content from a specific URL. Use when the user provides a link "
                "or when you need the contents of a known page.")

    @classmethod
    def get_status_message(cls) -> str:
        return 'Reading web page...'


WEB_AGENT_TOOLS = [WebSearchAgent, WebScraperAgent]
