import settings
from app.bot.telegram_runtime_adapter import TelegramRuntimeAdapter
from app.bot.telegram_side_effects import TelegramSideEffectHandler
from app.bot.utils import message_is_forward
from app.context.context_manager import build_context_manager
from app.runtime.context_utils import add_user_input_to_context
from app.runtime.conversation_session import ConversationSession
from app.runtime.default_runtime import DefaultLLMRuntime
from app.runtime.user_input import UserInput
from app.storage.db import DB, User

from aiogram.types import Message


class MessageProcessor:
    def __init__(self, db: DB, user: User, message: Message):
        self.db = db
        self.user = user
        self.message = message

    def _build_session(self) -> ConversationSession:
        reply_to_id = None
        if self.message.reply_to_message is not None:
            reply_to_id = self.message.reply_to_message.message_id
        return ConversationSession(
            chat_id=self.message.chat.id,
            reply_to_message_id=reply_to_id,
            is_forwarded=message_is_forward(self.message),
        )

    async def add_context_only(self, user_input: UserInput):
        """Add user input to context without calling LLM (for context-only batches)."""
        session = self._build_session()
        context_manager = await build_context_manager(self.db, self.user, session)
        await add_user_input_to_context(user_input, context_manager)

    async def process(self, is_cancelled, user_input: UserInput):
        session = self._build_session()
        context_manager = await build_context_manager(self.db, self.user, session)

        side_effects = TelegramSideEffectHandler(self.message)
        if self.user.agent_mode and settings.ENABLE_AGENT_RUNTIME:
            from app.runtime.agent_runtime import AgentRuntime
            runtime = AgentRuntime(self.db, self.user, side_effects, context_manager)
        else:
            runtime = DefaultLLMRuntime(self.db, self.user, side_effects, context_manager)
        adapter = TelegramRuntimeAdapter(self.message, self.user, context_manager)
        await adapter.handle_turn(runtime, user_input, session, is_cancelled)
