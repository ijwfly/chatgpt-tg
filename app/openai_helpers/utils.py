from decimal import Decimal
import openai



COMPLETION_PRICE = {
    'gpt-3.5-turbo': (Decimal('0.0015'), Decimal('0.002')),
    'gpt-3.5-turbo-16k': (Decimal('0.003'), Decimal('0.004')),
    'gpt-4': (Decimal('0.03'), Decimal('0.06')),
}

WHISPER_PRICE = Decimal('0.006')


def set_openai_token(token):
    openai.api_key = token


def calculate_completion_usage_price(prompt_tokens: int, completion_tokens: int, model: str) -> Decimal:
    price = COMPLETION_PRICE.get(model)
    if not price:
        raise ValueError(f"Unknown model: {model}")
    prompt_price, completion_price = price
    return prompt_price * prompt_tokens / 1000 + completion_price * completion_tokens / 1000


def calculate_whisper_usage_price(audio_seconds: int) -> Decimal:
    return WHISPER_PRICE / 60 * audio_seconds
