from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, F, types


CANCELLATION_PREFIX = 'cancel'
# Bot API 10.3 update sent when the user presses the native Stop button of a streamed draft
STOPPED_GENERATION_UPDATE = 'stopped_message_generation'


class CancellationToken:
    """
    Class that represents a cancellation token
    """
    def __init__(self):
        self.is_canceled = False

    def __call__(self):
        return self.is_canceled

    def cancel(self):
        self.is_canceled = True


class StoppedGenerationMiddleware(BaseMiddleware):
    """Handles the `stopped_message_generation` update with aiogram 3.30 (Bot API 10.2).

    aiogram does not know this update type yet, so it only survives as an extra field on `Update` and the
    dispatcher would skip it with a RuntimeWarning. As an outer middleware on `dp.update` this runs first,
    cancels the user's turn and swallows the update. TODO: replace with the native observer once aiogram
    ships Bot API 10.3 support.
    """

    def __init__(self, cancellation_manager: 'CancellationManager'):
        self.cancellation_manager = cancellation_manager

    async def __call__(
        self,
        handler: Callable[[types.Update, Dict[str, Any]], Awaitable[Any]],
        update: types.Update,
        data: Dict[str, Any],
    ) -> Any:
        payload = (update.model_extra or {}).get(STOPPED_GENERATION_UPDATE)
        if payload is None:
            return await handler(update, data)
        chat = payload.get('chat') or {}
        if chat.get('id') is not None:
            # drafts exist only in private chats, so the chat id is the user id the token is keyed by
            self.cancellation_manager.cancel(chat['id'])
        return None


class CancellationManager:
    """
    Class that manages the cancellation of message processing for streaming messages
    """
    def __init__(self, bot, dispatcher):
        self._cancellation_tokens = {}
        dispatcher.callback_query.register(self.process_callback, F.data.contains(CANCELLATION_PREFIX))
        dispatcher.update.outer_middleware(StoppedGenerationMiddleware(self))
        self.bot = bot

    async def process_callback(self, callback_query: types.CallbackQuery):
        """
        Process the telegram callback query
        """
        chat_id = callback_query.from_user.id
        self.cancel(chat_id)
        await callback_query.answer()

    def get_token(self, tg_user_id):
        """
        Get a cancellation token for the user
        """
        key = str(tg_user_id)
        if key not in self._cancellation_tokens:
            self._cancellation_tokens[key] = CancellationToken()
        return self._cancellation_tokens[key]

    def cancel(self, tg_user_id):
        """
        Cancel the message processing for the user
        """
        key = str(tg_user_id)
        if key in self._cancellation_tokens:
            self._cancellation_tokens[key].cancel()
            del self._cancellation_tokens[key]


def get_cancel_button():
    return types.InlineKeyboardButton(text='Stop', callback_data=f'{CANCELLATION_PREFIX}.cancel')
