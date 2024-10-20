import openai
import anthropic


class BaseLLMClient:
    def __init__(self, api_key, base_url=None):
        self.api_key = api_key
        self.base_url = base_url

    async def chat_completions_create(self, model: str, messages, **additional_fields):
        raise NotImplementedError()


class GenericAsyncOpenAIClient(BaseLLMClient):
    """
    The purpose of this client is to give a generic client for OpenAI compatible APIs
    without any specific OpenAI features.
    """
    def __init__(self, api_key, base_url=None):
        super().__init__(api_key, base_url)
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat_completions_create(self, model: str, messages, **additional_fields):
        return await self.client.chat.completions.create(model=model, messages=messages, **additional_fields)


class OpenAISpecificAsyncOpenAIClient(GenericAsyncOpenAIClient):
    """
    This client is for OpenAI specific features.
    """
    async def chat_completions_create(self, model: str, messages, **additional_fields):
        inner_additional_fields = {}
        if additional_fields.get("stream"):
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
    def __init__(self, api_key, base_url=None):
        super().__init__(api_key, base_url)
        self.client = anthropic.AsyncClient(api_key=api_key, base_url=base_url)

    async def chat_completions_create(self, model: str, messages, **additional_fields):
        # find system prompt in messages
        system_prompt = None
        if len(messages) > 0 and messages[0]['role'] == 'system':
            system_prompt = messages[0]['content']
            messages = messages[1:]

        return await self.client.messages.create(max_tokens=4096, model=model, messages=messages, system=system_prompt, **additional_fields)
