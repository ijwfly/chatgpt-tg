import json
from contextlib import asynccontextmanager
from typing import Optional

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

import settings
from app.functions.base import OpenAIFunction


@asynccontextmanager
async def _streamable_http_transport(server_url: str, headers: Optional[dict[str, str]]):
    """Streamable HTTP transport whose httpx2 client carries the custom headers.

    The SDK does not close a user-supplied http_client, so it is owned (and closed) here. Timeouts mirror the
    SDK defaults: short connect timeout, long read timeout for SSE streams.
    """
    async with httpx2.AsyncClient(
        headers=headers or None,
        timeout=httpx2.Timeout(30.0, read=float(settings.MCP_TOOL_CALL_TIMEOUT)),
        follow_redirects=True,
    ) as http_client:
        async with streamable_http_client(server_url, http_client=http_client) as streams:
            yield streams


def make_mcp_client(server_url: str, headers: Optional[dict[str, str]] = None) -> Client:
    """High-level MCP client (mcp 2.x) for a streamable HTTP server; handshake happens on `async with`."""
    transport = _streamable_http_transport(server_url, headers)
    return Client(transport, read_timeout_seconds=float(settings.MCP_TOOL_CALL_TIMEOUT))


class MCPFunction(OpenAIFunction):
    def __init__(
        self,
        mcp_server_url: str,
        name: str,
        description: str,
        schema: dict,
        headers: Optional[dict[str, str]] = None
    ):
        self.mcp_server_url = mcp_server_url
        self.name = name
        self.description = description
        self.schema = schema
        self.headers = headers

        self.user = None
        self.db = None
        self.context_manager = None
        self.side_effects = None
        self.tool_call_id = None

    def __call__(self, user, db, context_manager, side_effects, tool_call_id: str = None):
        self.user = user
        self.db = db
        self.context_manager = context_manager
        self.side_effects = side_effects
        self.tool_call_id = tool_call_id
        return self

    def _client(self) -> Client:
        return make_mcp_client(self.mcp_server_url, self.headers)

    async def run(self, params: dict) -> Optional[str]:
        # TODO: Each invocation creates a new connection to the MCP server; sessions could be reused.
        try:
            async with self._client() as client:
                result = await client.call_tool(self.name, arguments=params)
                if result is None or not result.content:
                    return None
                text = result.content[0].text
                if result.is_error:
                    return f"Error calling MCP tool: {text}"
                return text
        except MCPError as e:
            # mcp 2.x raises on tool errors instead of returning is_error=True
            return f"Error calling MCP tool: {e.message}"
        except Exception as e:
            return f"Error calling MCP tool: {e}"

    async def run_dict_args(self, params: dict):
        return await self.run(params)

    async def run_str_args(self, params: str):
        try:
            params_dict = json.loads(params)
            return await self.run(params_dict)
        except json.JSONDecodeError as e:
            return f"JSON parsing error: {e}"

    def get_description(self) -> str:
        return self.description

    def get_name(self) -> str:
        return self.name

    def get_params_schema(self) -> dict:
        return self.schema

    def get_system_prompt_addition(self) -> Optional[str]:
        return None

    def get_status_message(self) -> str:
        humanized = self.name.replace('_', ' ').replace('-', ' ').strip()
        return f'Running {humanized}...'


class MCPFunctionManager:
    def __init__(self, server_url: str, headers: Optional[dict[str, str]] = None):
        self.server_url = server_url
        self.headers = headers

    def _client(self) -> Client:
        return make_mcp_client(self.server_url, self.headers)

    async def get_tools(self):
        # TODO: Each invocation creates a new connection to the MCP server; sessions could be reused.
        result = []
        async with self._client() as client:
            tools = await client.list_tools()
            for tool in tools.tools:
                result.append(MCPFunction(
                    self.server_url,
                    tool.name,
                    tool.description,
                    tool.input_schema,
                    self.headers
                ))
        return result
