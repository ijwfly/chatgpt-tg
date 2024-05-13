import dataclasses
from decimal import Decimal
from functools import lru_cache

import settings
from app.storage.user_role import UserRole


@dataclasses.dataclass
class LLMPrice:
    # price per 1000 tokens
    input_tokens_price: Decimal
    output_tokens_price: Decimal


@dataclasses.dataclass
class LLMContextConfiguration:
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
    streaming_responses: bool = False


class LLModel:
    GPT_35_TURBO = 'gpt-3.5-turbo'
    GPT_35_TURBO_16K = 'gpt-3.5-turbo-16k'
    GPT_4 = 'gpt-4'
    GPT_4_TURBO = 'gpt-4-turbo'
    GPT_4O = 'gpt-4o'
    GPT_4_TURBO_PREVIEW = 'gpt-4-turbo-preview'
    GPT_4_VISION_PREVIEW = 'gpt-4-vision-preview'
    OPENROUTER_WIZARDLM2 = 'microsoft/wizardlm-2-8x22b'

    def __init__(self, *, model_name: str, api_key, context_configuration, model_readable_name=None, model_price=None, base_url=None,
                 capabilities=None, minimum_user_role=UserRole.STRANGER):
        if model_readable_name is None:
            model_readable_name = model_name

        if model_price is None:
            model_price = LLMPrice(input_tokens_price=Decimal('0'), output_tokens_price=Decimal('0'))

        if capabilities is None:
            capabilities = LLMCapabilities()

        self.model_name = model_name
        self.api_key = api_key
        self.context_configuration = context_configuration
        self.model_readable_name = model_readable_name
        self.model_price = model_price
        self.base_url = base_url
        self.capabilities = capabilities
        self.minimum_user_role = minimum_user_role


@lru_cache
def get_models():
    models = {}

    if settings.OPENAI_TOKEN:
        models.update({
            LLModel.GPT_35_TURBO: LLModel(
                model_name=LLModel.GPT_35_TURBO,
                model_readable_name='GPT-3.5',
                api_key=settings.OPENAI_TOKEN,
                minimum_user_role=settings.USER_ROLE_BOT_ACCESS,
                context_configuration=LLMContextConfiguration(
                    short_term_memory_tokens=10*1024,
                    summary_length=2*1024,
                    hard_max_context_size=13*1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.0005'),
                    output_tokens_price=Decimal('0.0015'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=True,
                    streaming_responses=True,
                ),
                base_url=settings.OPENAI_BASE_URL,
            ),
            LLModel.GPT_4_TURBO: LLModel(
                model_name=LLModel.GPT_4_TURBO,
                model_readable_name='GPT-4 Turbo',
                api_key=settings.OPENAI_TOKEN,
                minimum_user_role=settings.USER_ROLE_CHOOSE_MODEL,
                context_configuration=LLMContextConfiguration(
                    short_term_memory_tokens=5*1024,
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
                    streaming_responses=True,
                ),
                base_url=settings.OPENAI_BASE_URL,
            ),
            LLModel.GPT_4O: LLModel(
                model_name=LLModel.GPT_4O,
                model_readable_name='GPT-4o',
                api_key=settings.OPENAI_TOKEN,
                minimum_user_role=settings.USER_ROLE_CHOOSE_MODEL,
                context_configuration=LLMContextConfiguration(
                    short_term_memory_tokens=8 * 1024,
                    summary_length=2048,
                    hard_max_context_size=13 * 1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.005'),
                    output_tokens_price=Decimal('0.015'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=True,
                    image_processing=True,
                    streaming_responses=True,
                ),
                base_url=settings.OPENAI_BASE_URL,
            ),

            # Deprecated OpenAI models
            LLModel.GPT_35_TURBO_16K: LLModel(
                model_name=LLModel.GPT_35_TURBO_16K,
                model_readable_name='GPT-3.5 16K',
                api_key=settings.OPENAI_TOKEN,
                minimum_user_role=UserRole.NOONE,
                context_configuration=LLMContextConfiguration(
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
                model_readable_name='GPT-4',
                api_key=settings.OPENAI_TOKEN,
                minimum_user_role=UserRole.NOONE,
                context_configuration=LLMContextConfiguration(
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
            LLModel.GPT_4_TURBO_PREVIEW: LLModel(
                model_name=LLModel.GPT_4_TURBO_PREVIEW,
                model_readable_name='GPT-4 Turbo Preview',
                api_key=settings.OPENAI_TOKEN,
                minimum_user_role=UserRole.NOONE,
                context_configuration=LLMContextConfiguration(
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
                model_readable_name='GPT-4 Vision Preview',
                minimum_user_role=UserRole.NOONE,
                api_key=settings.OPENAI_TOKEN,
                context_configuration=LLMContextConfiguration(
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

    # OpenRouter.ai model example
    if settings.OPENROUTER_TOKEN:
        models.update({
            LLModel.OPENROUTER_WIZARDLM2: LLModel(
                model_name=LLModel.OPENROUTER_WIZARDLM2,
                model_readable_name='WizardLM-2 8x22b',
                api_key=settings.OPENROUTER_TOKEN,
                minimum_user_role=settings.USER_ROLE_CHOOSE_MODEL,
                context_configuration=LLMContextConfiguration(
                    short_term_memory_tokens=10*1024,
                    summary_length=2048,
                    hard_max_context_size=13 * 1024,
                ),
                model_price=LLMPrice(
                    input_tokens_price=Decimal('0.00065'),
                    output_tokens_price=Decimal('0.00065'),
                ),
                capabilities=LLMCapabilities(
                    function_calling=False,
                    image_processing=False,
                    streaming_responses=True,
                ),
                base_url=settings.OPENROUTER_BASE_URL,
            ),
        })

    return models


def get_model_by_name(model_name: str):
    model = get_models().get(model_name)
    if not model:
        raise ValueError(f"Unknown model: {model_name}")
    return model
