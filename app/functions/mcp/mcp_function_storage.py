import json
from typing import Optional

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

from app.functions.base import OpenAIFunction


class MCPFunction(OpenAIFunction):
    def __init__(self, mcp_server_url: str, name: str, description: str, schema: dict):
        self.mcp_server_url = mcp_server_url
        self.name = name
        self.description = description
        self.schema = schema

        self.user = None
        self.db = None
        self.context_manager = None
        self.message = None
        self.tool_call_id = None

    def __call__(self, user, db, context_manager, message, tool_call_id: str = None):
        self.user = user
        self.db = db
        self.context_manager = context_manager
        self.message = message
        self.tool_call_id = tool_call_id
        return self

    async def run(self, params: dict) -> Optional[str]:
        # TODO: very naive implementation, refactor with session manager
        async with streamablehttp_client(self.mcp_server_url) as (
                read_stream,
                write_stream,
                _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(self.name, arguments=params)
                if result is not None and hasattr(result, 'content') and len(result.content):
                    return result.content[0].text
                else:
                    return None

    async def run_dict_args(self, params: dict):
        return await self.run(params)

    async def run_str_args(self, params: str):
        params = json.loads(params)
        return await self.run(params)

    def get_description(self) -> str:
        return self.description

    def get_name(self) -> str:
        return self.name

    def get_params_schema(self) -> dict:
        return self.schema

    def get_system_prompt_addition(self) -> Optional[str]:
        return None



class MCPFunctionManager:
    def __init__(self, server_url: str):
        self.server_url = server_url

    async def get_tools(self):
        # TODO: very naive implementation, refactor with session manager
        result = []
        async with streamablehttp_client(self.server_url) as (
                read_stream,
                write_stream,
                _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                for tool in tools.tools:
                    result.append(MCPFunction(self.server_url, tool.name, tool.description, tool.inputSchema))
        return result
