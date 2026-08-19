from typing import Optional

from pydantic import Field

import settings
from app.functions.base import OpenAIFunction, OpenAIFunctionParams
from app.sandbox.client import SandboxClient, SandboxError


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + '\n...[truncated]'


# --- bash_exec ---

class BashExecParams(OpenAIFunctionParams):
    command: str = Field(..., description="bash command to execute")
    timeout: int = Field(settings.SANDBOX_BASH_TIMEOUT_DEFAULT, description="timeout in seconds")


class BashExec(OpenAIFunction):
    PARAMS_SCHEMA = BashExecParams

    async def run(self, params: BashExecParams) -> Optional[str]:
        try:
            result = await SandboxClient().exec(self.user.telegram_id, params.command, params.timeout)
        except SandboxError as e:
            return f"Error: {e}"
        parts = [f"exit_code: {result['exit_code']}", f"cwd: {result['cwd']}"]
        if result['stdout']:
            parts.append(f"stdout:\n{_truncate(result['stdout'], settings.SANDBOX_MAX_OUTPUT_CHARS)}")
        if result['stderr']:
            parts.append(f"stderr:\n{_truncate(result['stderr'], settings.SANDBOX_MAX_OUTPUT_CHARS)}")
        return '\n'.join(parts)

    @classmethod
    def get_name(cls) -> str:
        return 'bash_exec'

    @classmethod
    def get_description(cls) -> str:
        return ("Execute a bash command in your personal workspace. Each call is stateless — "
                "a fresh shell is spawned every time (cwd resets to the workspace, environment "
                "variables do not persist). Chain dependent commands with && or write a script.")

    @classmethod
    def get_system_prompt_addition(cls) -> Optional[str]:
        return (
            "You have a personal bash workspace (the cwd of every bash_exec call). "
            "Files the user sends via Telegram are saved to the workspace root. "
            "Write only inside the workspace; /workspace/public_skills is shared and "
            "read-only, but you can read and run anything in it. "
            "Use send_file_to_chat to deliver files to the user."
        )

    @classmethod
    def get_status_message(cls) -> str:
        return 'Running bash command...'


# --- read_file ---

class ReadFileParams(OpenAIFunctionParams):
    path: str = Field(..., description="file path, relative to your workspace")
    limit: int = Field(0, description="return only the first N lines (0 = whole file)")


class ReadFile(OpenAIFunction):
    PARAMS_SCHEMA = ReadFileParams

    async def run(self, params: ReadFileParams) -> Optional[str]:
        try:
            result = await SandboxClient().read_file(self.user.telegram_id, params.path, params.limit)
        except SandboxError as e:
            return f"Error: {e}"
        return _truncate(result['content'], settings.SANDBOX_MAX_OUTPUT_CHARS)

    @classmethod
    def get_name(cls) -> str:
        return 'read_file'

    @classmethod
    def get_description(cls) -> str:
        return ("Read the contents of a text file in your workspace, or a shared file under "
                "/workspace/public_skills given by absolute path. "
                "Use `limit` to return only the first N lines.")

    @classmethod
    def get_status_message(cls) -> str:
        return 'Reading file...'


# --- write_file ---

class WriteFileParams(OpenAIFunctionParams):
    path: str = Field(..., description="file path, relative to your workspace")
    content: str = Field(..., description="content to write")


class WriteFile(OpenAIFunction):
    PARAMS_SCHEMA = WriteFileParams

    async def run(self, params: WriteFileParams) -> Optional[str]:
        try:
            result = await SandboxClient().write_file(self.user.telegram_id, params.path, params.content)
        except SandboxError as e:
            return f"Error: {e}"
        return f"Written {result['size']} bytes to {params.path}"

    @classmethod
    def get_name(cls) -> str:
        return 'write_file'

    @classmethod
    def get_description(cls) -> str:
        return "Write content to a file in your workspace, creating parent directories as needed."

    @classmethod
    def get_status_message(cls) -> str:
        return 'Writing file...'


# --- edit_file ---

class EditFileParams(OpenAIFunctionParams):
    path: str = Field(..., description="file path, relative to your workspace")
    old_text: str = Field(..., description="exact text to replace, must occur exactly once in the file")
    new_text: str = Field(..., description="replacement text")


class EditFile(OpenAIFunction):
    PARAMS_SCHEMA = EditFileParams

    async def run(self, params: EditFileParams) -> Optional[str]:
        try:
            await SandboxClient().edit_file(
                self.user.telegram_id, params.path, params.old_text, params.new_text
            )
        except SandboxError as e:
            return f"Error: {e}"
        return f"Edited {params.path}: 1 replacement made"

    @classmethod
    def get_name(cls) -> str:
        return 'edit_file'

    @classmethod
    def get_description(cls) -> str:
        return ("Replace an exact occurrence of old_text with new_text in a workspace file. "
                "old_text must appear exactly once; otherwise an error is returned.")

    @classmethod
    def get_status_message(cls) -> str:
        return 'Editing file...'


# --- send_file_to_chat ---

class SendFileToChatParams(OpenAIFunctionParams):
    path: str = Field(..., description="workspace file path to send to the user")
    caption: Optional[str] = Field(None, description="optional caption for the document")


class SendFileToChat(OpenAIFunction):
    PARAMS_SCHEMA = SendFileToChatParams

    async def run(self, params: SendFileToChatParams) -> Optional[str]:
        max_bytes = settings.SANDBOX_SEND_FILE_MAX_MB * 1024 * 1024
        try:
            data, filename = await SandboxClient().download_file(
                self.user.telegram_id, params.path, max_bytes=max_bytes
            )
        except SandboxError as e:
            return f"Error: {e}"
        # telegram caption limit
        caption = params.caption[:1024] if params.caption else None
        # bind the tool response to the sent document so a reply to it continues this dialog branch
        self.result_tg_message_id = await self.side_effects.send_document(data, filename, caption)
        return f"File {filename} ({len(data)} bytes) sent to chat."

    @classmethod
    def get_name(cls) -> str:
        return 'send_file_to_chat'

    @classmethod
    def get_description(cls) -> str:
        return "Send a file from your workspace to the user as a Telegram document."

    @classmethod
    def get_status_message(cls) -> str:
        return 'Sending file...'


SANDBOX_TOOLS = [BashExec, ReadFile, WriteFile, EditFile, SendFileToChat]
