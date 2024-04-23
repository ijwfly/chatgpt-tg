from decimal import Decimal
import openai

from app.llm_models import get_models

WHISPER_PRICE = Decimal('0.006')

TTS_PRICE = {
    'tts-1': Decimal('0.015'),
    'tts-1-hd': Decimal('0.030')
}

IMAGE_GENERATION_PRICE = {
    'dall-e-3': {
        '1024x1024': Decimal('0.04'),
        '1792x1024': Decimal('0.08'),
        '1024x1792': Decimal('0.08'),
    }
}


def calculate_completion_usage_price(prompt_tokens: int, completion_tokens: int, model: str) -> Decimal:
    llm_model = get_models().get(model)
    if not llm_model:
        raise ValueError(f"Unknown model: {model}")

    price = llm_model.model_price
    if not price:
        raise ValueError(f"Unknown model: {model}")
    prompt_price, completion_price = price
    return prompt_price * prompt_tokens / 1000 + completion_price * completion_tokens / 1000


def calculate_whisper_usage_price(audio_seconds: int) -> Decimal:
    return WHISPER_PRICE / 60 * audio_seconds


def calculate_tts_usage_price(characters_count: int, model: str) -> Decimal:
    model_price = TTS_PRICE.get(model, 0)
    return model_price * characters_count / 1000


def calculate_image_generation_usage_price(model, resolution, num_images):
    price = IMAGE_GENERATION_PRICE.get(model)
    if not price:
        raise ValueError(f"Unknown model: {model}")
    return price[resolution] * num_images


class OpenAIAsync:
    _key = None
    _base_url = None
    _instance = None

    @classmethod
    def init(cls, api_key, base_url=None):
        cls._key = api_key
        cls._base_url = base_url

    @classmethod
    def instance(cls):
        params = {}
        if cls._base_url:
            params['base_url'] = cls._base_url

        if cls._key is None:
            raise ValueError("OpenAIAsync is not initialized")

        params['api_key'] = cls._key

        if cls._instance is None:
            cls._instance = openai.AsyncOpenAI(**params)
        return cls._instance
