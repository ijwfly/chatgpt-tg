import settings
from app.storage.db import User


# Kwargs that are extracted by Langfuse's OpenAiArgsExtractor.__init__
# and won't leak into the real OpenAI API call.
# See: langfuse.openai.OpenAiArgsExtractor
_LANGFUSE_SAFE_KWARGS = frozenset({
    'name', 'metadata', 'langfuse_prompt', 'langfuse_public_key',
    'trace_id', 'parent_observation_id',
})


def build_langfuse_metadata(user: User) -> dict:
    """Build kwargs for Langfuse OpenAI wrapper.

    Uses 'metadata' dict because it's properly extracted by OpenAiArgsExtractor
    and won't leak into the real OpenAI API call. Top-level kwargs like 'user_id'
    are NOT extracted by the wrapper and cause errors.
    """
    if not settings.LANGFUSE_ENABLED:
        return {}
    return {'metadata': {'user_id': str(user.id)}}
