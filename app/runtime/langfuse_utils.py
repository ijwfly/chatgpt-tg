import settings
from app.storage.db import User


def build_langfuse_metadata(user: User) -> dict:
    if not settings.LANGFUSE_ENABLED:
        return {}
    return {'langfuse_user_id': str(user.id)}
