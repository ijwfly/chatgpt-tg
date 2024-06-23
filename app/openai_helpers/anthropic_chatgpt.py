import base64
import json
from typing import List, Any, Optional, Callable, Union

import httpx
import pydantic
from async_lru import alru_cache

import settings
from app.bot.utils import get_image_proxy_url
from app.openai_helpers.chatgpt import DialogMessage, CompletionUsage, FunctionCall, ToolCall
from app.openai_helpers.function_storage import FunctionStorage

from app.openai_helpers.llm_client_factory import LLMClientFactory


OPENAI_TO_ANTHROPIC_ROLE_MAPPING = {
    'user': 'user',
    'tool': 'user',
    'assistant': 'assistant',
}


@alru_cache(maxsize=32)
async def get_image_base64(image_url: str) -> str:
    async with httpx.AsyncClient() as client:
        image_name = image_url.split('/')[-1]
        url = f'{get_image_proxy_url()}/{image_name}'
        response = await client.get(url)
        response.raise_for_status()
        return base64.b64encode(response.content).decode()


class AnthropicImageContent(pydantic.BaseModel):
    type: str
    media_type: str
    data: str


class AnthropicToolUseResult(pydantic.BaseModel):
    type: str  # always "text"
    text: str


class AnthropicContentPart(pydantic.BaseModel):
    type: str
    # text
    text: Optional[str] = None
    # image
    source: Optional[AnthropicImageContent] = None
    # tool_use
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[dict] = None
    # tool_result
    tool_use_id: Optional[str] = None
    is_error: Optional[bool] = None
    content: List[Optional[AnthropicToolUseResult]] = None


class AnthropicDialogMessage(pydantic.BaseModel):
    role: Optional[str] = None
    content: Union[Optional[str], Optional[List[AnthropicContentPart]]] = None

    @classmethod
    async def from_dialog_message(cls, dialog_message):
        content = []
        if dialog_message.function_call:
            raise ValueError('Function calls are not supported by Anthropic API. Use only tool calls.')
        elif dialog_message.tool_calls:
            for tool_call in dialog_message.tool_calls:
                content.append(AnthropicContentPart(type='tool_use', id=tool_call.id, name=tool_call.function.name, input=json.loads(tool_call.function.arguments)))
        elif dialog_message.role == 'tool':
            content.append(AnthropicContentPart(type='tool_result', tool_use_id=dialog_message.tool_call_id, is_error=False, content=[AnthropicToolUseResult(type='text', text=dialog_message.content)] if dialog_message.content else None))
        elif dialog_message.content:
            if isinstance(dialog_message.content, str):
                content.append(AnthropicContentPart(type='text', text=dialog_message.content))
            elif isinstance(dialog_message.content, list):
                for part in dialog_message.content:
                    if part.type == 'text':
                        content.append(AnthropicContentPart(type='text', text=part.text))
                    elif part.type == 'image_url':
                        content.append(AnthropicContentPart(
                            type='image',
                            source=AnthropicImageContent(
                                type='base64',
                                media_type='image/jpeg',
                                data=await get_image_base64(part.image_url.url),
                            )
                        ))
        role = OPENAI_TO_ANTHROPIC_ROLE_MAPPING.get(dialog_message.role, 'assistant')
        return cls(role=role, content=content)

    def to_dialog_message(self) -> DialogMessage:
        dialog_message = DialogMessage(role=self.role, content=[])
        for content_part in self.content:
            if content_part.type == 'text':
                dialog_message.content = content_part.text
            if content_part.type == 'tool_use':
                if not dialog_message.tool_calls:
                    dialog_message.tool_calls = []
                dialog_message.tool_calls.append(ToolCall(id=content_part.id, type='function', function=FunctionCall(name=content_part.name, arguments=json.dumps(content_part.input))))
        return dialog_message


class AnthropicChatGPT:
    def __init__(self, llm_model, system_prompt: str, function_storage: FunctionStorage = None):
        self.function_storage = function_storage
        self.llm_model = llm_model
        self.system_prompt = system_prompt

    async def send_messages(self, messages_to_send: List[DialogMessage]) -> (DialogMessage, CompletionUsage):
        additional_fields = self.create_additional_fields()

        messages = await self.create_context(messages_to_send, self.system_prompt)
        resp = await LLMClientFactory.get_client(self.llm_model.model_name).chat_completions_create(
            model=self.llm_model.model_name,
            messages=messages,
            temperature=settings.OPENAI_CHAT_COMPLETION_TEMPERATURE,
            **additional_fields,
        )
        completion_usage = CompletionUsage(
            model=self.llm_model.model_name,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            total_tokens=resp.usage.input_tokens + resp.usage.output_tokens,
        )

        anthropic_dialog_message = AnthropicDialogMessage(
            role='assistant',
            content=resp.content,
        )

        return anthropic_dialog_message.to_dialog_message(), completion_usage

    async def send_messages_streaming(self, messages_to_send: List[DialogMessage], is_cancelled: Callable[[], bool]) -> (DialogMessage, CompletionUsage):
        raise NotImplementedError('Streaming responses are not supported yet for Anthropic models')

    def create_additional_fields(self):
        additional_fields = {}
        if self.function_storage is not None:
            if self.llm_model.capabilities.function_calling or self.llm_model.capabilities.tool_calling:
                tools = self.function_storage.get_functions_info()
                for tool in tools:
                    tool['input_schema'] = tool.pop('parameters')

                additional_fields.update({
                    'tools': tools,
                    'tool_choice': {
                        'type': 'auto',
                    }
                })

        return additional_fields

    @staticmethod
    async def create_context(messages: List[DialogMessage], system_prompt: str) -> List[Any]:
        system_prompt = [{"role": "system", "content": system_prompt}]
        result = [await AnthropicDialogMessage.from_dialog_message(dialog_message) for dialog_message in messages]

        # find multiple message with one role and merge them
        merged_messages = []
        for message in result:
            if len(merged_messages) == 0 or merged_messages[-1].role != message.role:
                merged_messages.append(message)
            else:
                merged_messages[-1].content.extend(message.content)

        result = [message.dict(exclude_none=True) for message in merged_messages]
        return system_prompt + result
