import dataclasses
from decimal import Decimal

import settings


@dataclasses.dataclass
class LLMModelPrice:
    # price per 1000 tokens
    input_tokens_price: Decimal
    output_tokens_price: Decimal


@dataclasses.dataclass
class LLMModelContextConfiguration:
    # long term memory is based on embedding context search
    long_term_memory_tokens: int
    # short term memory is used for storing last messages
    short_term_memory_tokens: int
    # length of summary to be generated when context is too long
    summary_length: int
    # hard limit for context size, when this limit is reached, processing is being stopped,
    # summarization also cannot be done
    hard_max_context_size: int


class LLMModel:
    def __init__(self, model_name: str, api_key, context_configuration, model_price=None, base_url=None):
        if model_price is None:
            model_price = LLMModelPrice(input_tokens_price=Decimal('0'), output_tokens_price=Decimal('0'))

        self.model_name = model_name
        self.api_key = api_key
        self.context_configuration = context_configuration
        self.model_price = model_price
        self.base_url = base_url


class LLMModels:
    GPT_35_TURBO = 'gpt-3.5-turbo'
    GPT_35_TURBO_16K = 'gpt-3.5-turbo-16k'
    GPT_4 = 'gpt-4'
    GPT_4_TURBO = 'gpt-4-turbo'
    GPT_4_TURBO_PREVIEW = 'gpt-4-turbo-preview'
    GPT_4_VISION_PREVIEW = 'gpt-4-vision-preview'
    LLAMA3 = 'llama3'


def get_models():
    models = {}
    openai_models = {
        LLMModels.GPT_35_TURBO: LLMModel(
            model_name=LLMModels.GPT_35_TURBO,
            api_key=settings.OPENAI_TOKEN,
            context_configuration=LLMModelContextConfiguration(
                long_term_memory_tokens=512,
                short_term_memory_tokens=2560,
                summary_length=512,
                hard_max_context_size=5*1024,
            ),
            model_price=LLMModelPrice(
                input_tokens_price=Decimal('0.0005'),
                output_tokens_price=Decimal('0.0015'),
            ),
        ),
        LLMModels.GPT_35_TURBO_16K: LLMModel(
            model_name=LLMModels.GPT_35_TURBO_16K,
            api_key=settings.OPENAI_TOKEN,
            context_configuration=LLMModelContextConfiguration(
                long_term_memory_tokens=1024,
                short_term_memory_tokens=4096,
                summary_length=1024,
                hard_max_context_size=17*1024,
            ),
            model_price=LLMModelPrice(
                input_tokens_price=Decimal('0.003'),
                output_tokens_price=Decimal('0.004'),
            ),
        ),
        LLMModels.GPT_4: LLMModel(
            model_name=LLMModels.GPT_4,
            api_key=settings.OPENAI_TOKEN,
            context_configuration=LLMModelContextConfiguration(
                long_term_memory_tokens=512,
                short_term_memory_tokens=2048,
                summary_length=1024,
                hard_max_context_size=9*1024,
            ),
            model_price=LLMModelPrice(
                input_tokens_price=Decimal('0.03'),
                output_tokens_price=Decimal('0.06'),
            ),
        ),
        LLMModels.GPT_4_TURBO: LLMModel(
            model_name=LLMModels.GPT_4_TURBO,
            api_key=settings.OPENAI_TOKEN,
            context_configuration=LLMModelContextConfiguration(
                long_term_memory_tokens=512,
                short_term_memory_tokens=5120,
                summary_length=2048,
                hard_max_context_size=13*1024,
            ),
            model_price=LLMModelPrice(
                input_tokens_price=Decimal('0.01'),
                output_tokens_price=Decimal('0.03'),
            ),
        ),
        LLMModels.GPT_4_TURBO_PREVIEW: LLMModel(
            model_name=LLMModels.GPT_4_TURBO_PREVIEW,
            api_key=settings.OPENAI_TOKEN,
            context_configuration=LLMModelContextConfiguration(
                long_term_memory_tokens=512,
                short_term_memory_tokens=5120,
                summary_length=2048,
                hard_max_context_size=13*1024,
            ),
            model_price=LLMModelPrice(
                input_tokens_price=Decimal('0.01'),
                output_tokens_price=Decimal('0.03'),
            ),
        ),
        LLMModels.GPT_4_VISION_PREVIEW: LLMModel(
            model_name=LLMModels.GPT_4_VISION_PREVIEW,
            api_key=settings.OPENAI_TOKEN,
            context_configuration=LLMModelContextConfiguration(
                long_term_memory_tokens=512,
                short_term_memory_tokens=5120,
                summary_length=2048,
                hard_max_context_size=13*1024,
            ),
            model_price=LLMModelPrice(
                input_tokens_price=Decimal('0.01'),
                output_tokens_price=Decimal('0.03'),
            ),
        ),
    }

    if settings.OPENAI_TOKEN:
        models.update(openai_models)

    if settings.OLLAMA_BASE_URL:
        models[LLMModels.LLAMA3] = LLMModel(
            model_name=LLMModels.LLAMA3,
            api_key=settings.OLLAMA_API_KEY,
            context_configuration=LLMModelContextConfiguration(
                long_term_memory_tokens=512,
                short_term_memory_tokens=2048,
                summary_length=512,
                hard_max_context_size=13*1024,
            ),
            base_url=settings.OLLAMA_BASE_URL,
        )

    return models
