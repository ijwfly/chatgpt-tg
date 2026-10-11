import pytest

import settings
from app.speech import transcription


@pytest.mark.parametrize(('name', 'value', 'error'), [
    ('STT_PROVIDER', 'unknown', 'STT_PROVIDER'),
    ('STT_MODEL', '', 'STT_MODEL'),
    ('STT_MODEL', '   ', 'STT_MODEL'),
    ('STT_MODEL', None, 'STT_MODEL'),
    ('OPENAI_TOKEN', '', 'OPENAI_TOKEN'),
    ('STT_TEMPERATURE', -0.1, 'STT_TEMPERATURE'),
    ('STT_TEMPERATURE', 1.1, 'STT_TEMPERATURE'),
    ('STT_TEMPERATURE', float('nan'), 'STT_TEMPERATURE'),
    ('STT_TEMPERATURE', float('inf'), 'STT_TEMPERATURE'),
    ('STT_TEMPERATURE', True, 'STT_TEMPERATURE'),
    ('STT_TEMPERATURE', '0', 'STT_TEMPERATURE'),
])
def test_invalid_configuration_fails_before_startup(monkeypatch, name, value, error):
    monkeypatch.setattr(settings, name, value)
    with pytest.raises(ValueError, match=error):
        transcription.initialize()


def test_openrouter_requires_its_own_key(monkeypatch):
    monkeypatch.setattr(settings, 'STT_PROVIDER', 'openrouter')
    monkeypatch.setattr(settings, 'OPENROUTER_TOKEN', '')
    with pytest.raises(ValueError, match='OPENROUTER_TOKEN'):
        transcription.initialize()
