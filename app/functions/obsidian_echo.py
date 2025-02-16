from typing import Optional

import httpx
from pydantic import Field

import settings
from app.functions.base import OpenAIFunction, OpenAIFunctionParams


class ObsidianEchoParams(OpenAIFunctionParams):
    title: str = Field(..., description="title of the note")
    content: str = Field(..., description="content of the note in markdown")

class CreateObsidianNote(OpenAIFunction):
    PARAMS_SCHEMA = ObsidianEchoParams

    async def run(self, params: ObsidianEchoParams) -> Optional[str]:
        try:
            async with httpx.AsyncClient(base_url=settings.OBSIDIAN_ECHO_BASE_URL) as client:
                vault_headers = {"Authorization": f"Bearer {settings.OBSIDIAN_ECHO_VAULT_TOKEN}"}
                note_payload = {
                    "external_id": params.title,
                    "title": params.title,
                    "content": params.content,
                }
                await client.post("/api/notes", json=note_payload, headers=vault_headers)
                return f"Added Obsidian note: {params.title}"
        except Exception as e:
            return f"Failed to add task: {str(e)}"

    @classmethod
    def get_name(cls) -> str:
        return "create_obsidian_note"

    @classmethod
    def get_description(cls) -> str:
        return "Creates a note in Obsidian notebook"

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return "You can create notes in Obsidian notebook using the `create_obsidian_note` function if user asks you to do so."
