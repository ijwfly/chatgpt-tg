import anthropic

from app import observability


class BaseLLMClient:
    def __init__(self, api_key, base_url=None, extra_completion_params=None):
        self.api_key = api_key
        self.base_url = base_url
        self.extra_completion_params = extra_completion_params or {}

    async def chat_completions_create(self, model: str, messages, **additional_fields):
        raise NotImplementedError()

    def _merge_extra_params(self, additional_fields):
        if not self.extra_completion_params:
            return additional_fields
        merged = {**self.extra_completion_params, **additional_fields}
        model_eb = self.extra_completion_params.get('extra_body')
        call_eb = additional_fields.get('extra_body')
        if isinstance(model_eb, dict) and isinstance(call_eb, dict):
            merged['extra_body'] = {**model_eb, **call_eb}
        return merged


class GenericAsyncOpenAIClient(BaseLLMClient):
    """
    The purpose of this client is to give a generic client for OpenAI compatible APIs
    without any specific OpenAI features.
    """
    def __init__(self, api_key, base_url=None, extra_completion_params=None):
        super().__init__(api_key, base_url, extra_completion_params)
        self.client = observability.create_openai_client(api_key=api_key, base_url=base_url)

    async def chat_completions_create(self, model: str, messages, **additional_fields):
        additional_fields = self._merge_extra_params(additional_fields)
        return await self.client.chat.completions.create(model=model, messages=messages, **additional_fields)


class OpenAISpecificAsyncOpenAIClient(GenericAsyncOpenAIClient):
    """
    This client is for OpenAI specific features.
    """
    async def chat_completions_create(self, model: str, messages, **additional_fields):
        additional_fields = self._merge_extra_params(additional_fields)
        inner_additional_fields = {}
        if additional_fields.get("stream"):
            additional_fields.pop("stream_options", None)
            inner_additional_fields["stream_options"] = {
                "include_usage": True,
            }
        return await self.client.chat.completions.create(
            model=model,
            messages=messages,
            **inner_additional_fields,
            **additional_fields,
        )


class AnthropicAsyncClient(BaseLLMClient):
    def __init__(self, api_key, base_url=None, extra_completion_params=None):
        super().__init__(api_key, base_url, extra_completion_params)
        self.client = anthropic.AsyncClient(api_key=api_key, base_url=base_url)

    async def chat_completions_create(self, model: str, messages, **additional_fields):
        additional_fields = self._merge_extra_params(additional_fields)
        # find system prompt in messages
        system_prompt = None
        if len(messages) > 0 and messages[0]['role'] == 'system':
            system_prompt = messages[0]['content']
            messages = messages[1:]

        return await self.client.messages.create(max_tokens=4096, model=model, messages=messages, system=system_prompt, **additional_fields)
