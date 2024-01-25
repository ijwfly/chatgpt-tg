from decimal import Decimal
import openai


COMPLETION_PRICE = {
    'gpt-3.5-turbo': (Decimal('0.0005'), Decimal('0.0015')),
    'gpt-3.5-turbo-16k': (Decimal('0.003'), Decimal('0.004')),
    'gpt-4': (Decimal('0.03'), Decimal('0.06')),
    'gpt-4-1106-preview': (Decimal('0.01'), Decimal('0.03')),
    'gpt-4-vision-preview': (Decimal('0.01'), Decimal('0.03')),
    'gpt-4-turbo-preview': (Decimal('0.01'), Decimal('0.03')),
}

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
    price = COMPLETION_PRICE.get(model)
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
    _instance = None

    @classmethod
    def init(cls, api_key):
        cls._key = api_key

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = openai.AsyncOpenAI(api_key=cls._key)
        return cls._instance
