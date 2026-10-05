import math

from openai import AsyncOpenAI

import settings


_client: AsyncOpenAI | None = None
_model = ''
_temperature: float | None = None


def initialize() -> None:
    """Validate STT configuration and create a client separate from TTS/embeddings."""
    global _client, _model, _temperature
    if _client is not None:
        raise RuntimeError('STT client is already initialized')

    provider = settings.STT_PROVIDER
    if provider == 'openai':
        token, base_url = settings.OPENAI_TOKEN, settings.OPENAI_BASE_URL
    elif provider == 'openrouter':
        token, base_url = settings.OPENROUTER_TOKEN, settings.OPENROUTER_BASE_URL
    else:
        raise ValueError("STT_PROVIDER must be 'openai' or 'openrouter'")

    model = settings.STT_MODEL
    if not isinstance(model, str) or not model.strip():
        raise ValueError('STT_MODEL must be a non-empty model ID')
    if not isinstance(token, str) or not token.strip():
        key_name = 'OPENAI_TOKEN' if provider == 'openai' else 'OPENROUTER_TOKEN'
        raise ValueError(f'{key_name} is required for STT_PROVIDER={provider!r}')

    temperature = settings.STT_TEMPERATURE
    if temperature is not None and (
        isinstance(temperature, bool)
        or not isinstance(temperature, (int, float))
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 1
    ):
        raise ValueError('STT_TEMPERATURE must be None or a number between 0 and 1')

    _client = AsyncOpenAI(api_key=token, base_url=base_url)
    _model = model
    _temperature = temperature


async def close() -> None:
    global _client
    client, _client = _client, None
    if client is not None:
        await client.close()


async def get_audio_speech_to_text(filename: str) -> str:
    if _client is None:
        raise RuntimeError('STT client is not initialized')
    params = {} if _temperature is None else {'temperature': _temperature}
    with open(filename, 'rb') as audio_file:
        transcript = await _client.audio.transcriptions.create(
            file=audio_file, model=_model, **params,
        )
    return transcript.text
