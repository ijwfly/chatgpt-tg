import dataclasses
from decimal import Decimal
from functools import lru_cache

import settings


@dataclasses.dataclass
class LLMPrice:
    # price per 1000 tokens
    input_tokens_price: Decimal
    output_tokens_price: Decimal


@dataclasses.dataclass
class LLMContextConfiguration:
    # long term memory is based on embedding context search
    long_term_memory_tokens: int
    # short term memory is used for storing last messages
    short_term_memory_tokens: int
    # length of summary to be generated when context is too long
    summary_length: int
    # hard limit for context size, when this limit is reached, processing is being stopped,
    # summarization also cannot be done
    hard_max_context_size: int


@dataclasses.dataclass
class LLMCapabilities:
    function_calling: bool = False
    tool_calling: bool = False
    image_processing: bool = False


class LLModel:
    GPT_35_TURBO = 'gpt-3.5-turbo'
    GPT_35_TURBO_16K = 'gpt-3.5-turbo-16k'
    GPT_4 = 'gpt-4'
    GPT_4_TURBO = 'gpt-4-turbo'
    GPT_4_TURBO_PREVIEW = 'gpt-4-turbo-preview'
    GPT_4_VISION_PREVIEW = 'gpt-4-vision-preview'
    LLAMA3 = 'llama3'

    def __init__(self, *, model_name: str, api_key, context_configuration, model_price=None, base_url=None,
                 capabilities=None):
        if model_price is None:
            model_price = LLMPrice(input_tokens_price=Decimal('0'), output_tokens_price=Decimal('0'))

        if capabilities is None:
            capabilities = LLMCapabilities()

        self.model_name = model_name
        self.api_key = api_key
        self.context_configuration = context_configuration
        self.model_price = model_price
        self.base_url = base_url
        self.capabilities = capabilities


@lru_cache
def get_models():
    models = {}

    if settings.OPENAI_TOKEN:
        models.update({
            LLModel.GPT_35_TURBO: LLModel(
                model_name=LLModel.GPT_35_TURBO,
                api_key=settings.OPENAI_TOKEN,
                context_configuration=LLMContextConfiguration(
                    long_term_memory_tokens=512,
                    short_term_memory_tokens=2560,
                    summary_length=512,
                    hard_max_context_size=5*1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.0005'),
                    output_tokens_price=Decimal('0.0015'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=True,
                ),
            ),
            LLModel.GPT_35_TURBO_16K: LLModel(
                model_name=LLModel.GPT_35_TURBO_16K,
                api_key=settings.OPENAI_TOKEN,
                context_configuration=LLMContextConfiguration(
                    long_term_memory_tokens=1024,
                    short_term_memory_tokens=4096,
                    summary_length=1024,
                    hard_max_context_size=17*1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.003'),
                    output_tokens_price=Decimal('0.004'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=True,
                ),
            ),
            LLModel.GPT_4: LLModel(
                model_name=LLModel.GPT_4,
                api_key=settings.OPENAI_TOKEN,
                context_configuration=LLMContextConfiguration(
                    long_term_memory_tokens=512,
                    short_term_memory_tokens=2048,
                    summary_length=1024,
                    hard_max_context_size=9*1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.03'),
                    output_tokens_price=Decimal('0.06'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=True,
                ),
            ),
            LLModel.GPT_4_TURBO: LLModel(
                model_name=LLModel.GPT_4_TURBO,
                api_key=settings.OPENAI_TOKEN,
                context_configuration=LLMContextConfiguration(
                    long_term_memory_tokens=512,
                    short_term_memory_tokens=5120,
                    summary_length=2048,
                    hard_max_context_size=13*1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.01'),
                    output_tokens_price=Decimal('0.03'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=True,
                    image_processing=True,
                ),
            ),
            LLModel.GPT_4_TURBO_PREVIEW: LLModel(
                model_name=LLModel.GPT_4_TURBO_PREVIEW,
                api_key=settings.OPENAI_TOKEN,
                context_configuration=LLMContextConfiguration(
                    long_term_memory_tokens=512,
                    short_term_memory_tokens=5120,
                    summary_length=2048,
                    hard_max_context_size=13*1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.01'),
                    output_tokens_price=Decimal('0.03'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=True,
                ),
            ),
            LLModel.GPT_4_VISION_PREVIEW: LLModel(
                model_name=LLModel.GPT_4_VISION_PREVIEW,
                api_key=settings.OPENAI_TOKEN,
                context_configuration=LLMContextConfiguration(
                    long_term_memory_tokens=512,
                    short_term_memory_tokens=5120,
                    summary_length=2048,
                    hard_max_context_size=13*1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.01'),
                    output_tokens_price=Decimal('0.03'),
                ),
                capabilities=LLMCapabilities(
                    image_processing=True,
                ),
            ),
        })

    # example of using llama3 model in ollama
    if settings.OLLAMA_BASE_URL:
        models[LLModel.LLAMA3] = LLModel(
            model_name=LLModel.LLAMA3,
            api_key=settings.OLLAMA_API_KEY,
            context_configuration=LLMContextConfiguration(
                long_term_memory_tokens=512,
                short_term_memory_tokens=2048,
                summary_length=512,
                hard_max_context_size=13*1024,
            ),
            base_url=settings.OLLAMA_BASE_URL,
        )

    return models


def get_model_by_name(model_name: str):
    model = get_models().get(model_name)
    if not model:
        raise ValueError(f"Unknown model: {model_name}")
    return model
