from aiogram import types


CANCELLATION_PREFIX = 'cancel'


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


class CancellationManager:
    """
    Class that manages the cancellation of message processing for streaming messages
    """
    def __init__(self, bot, dispatcher):
        self._cancellation_tokens = {}
        dispatcher.register_callback_query_handler(self.process_callback, lambda c: CANCELLATION_PREFIX in c.data)
        self.bot = bot

    async def process_callback(self, callback_query: types.CallbackQuery):
        """
        Process the telegram callback query
        """
        chat_id = callback_query.from_user.id
        self.cancel(chat_id)
        await self.bot.answer_callback_query(callback_query.id)

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
