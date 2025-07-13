import json
from typing import Optional

from pydantic import Field
import httpx

import settings
from app.functions.base import OpenAIFunction, OpenAIFunctionParams


# todoist = TodoistAPIAsync(settings.TODOIST_TOKEN)


class N8NAgentCallParams(OpenAIFunctionParams):
    chatInput: str = Field(..., description="prompt for specified LLM agent")
    session_id: str = Field(None, description="session id, can be empty for the first call to this agent")


class CallN8NAgent(OpenAIFunction):
    PARAMS_SCHEMA = N8NAgentCallParams

    async def run(self, params: N8NAgentCallParams) -> Optional[str]:
        try:
            async with httpx.AsyncClient(base_url=settings.OBSIDIAN_ECHO_BASE_URL) as client:
                n8n_authorization = {"Authorization": f"Bearer {settings.N8N_TOKEN}"}
                if params.session_id is None:
                    params.session_id = str(uuid.uuid4())
                n8n_payload = {
                    "sessionId": params.session_id,
                    "action": "sendMessage",
                    "chatInput": params.chatInput,
                }
                # TODO: parametrize
                resp = await client.post("/webhook/ff0ec143-0189-4365-967a-7a7c249a8424/chat", json=n8n_payload, headers=n8n_authorization)
                if resp.status_code != 200:
                    raise Exception(f'Agent call failed with code {resp.status_code}')
                output = resp.json()['output']
                result = {
                    "session_id": params.session_id,
                    "output": output,
                }
                return json.dumps(result)
        except Exception as e:
            return f"Failed to add task: {str(e)}"

    @classmethod
    def get_name(cls) -> str:
        return "call_n8n_agent_web_search"

    @classmethod
    def get_description(cls) -> str:
        # TODO: parametrize
        description = "This agent is used to search for information on the web. It can use search engines and fetch info from urls."
        return "Delegates task to n8n agent. Description of agent: " + description

    # @classmethod
    # def get_system_prompt_addition(cls) -> Optional[str]:
    #     return "You have agents to do some tasks for you. You can call them and ask them to do something."
