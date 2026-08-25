class BotSpy:
    """Wraps a mocked Bot to provide assertion helpers over captured requests."""

    def __init__(self, mock_bot):
        self.mock_bot = mock_bot

    def get_all_calls(self):
        """All requests the bot made: list of (api_method, data) pairs recorded by the mocked session."""
        return self.mock_bot.session.requests

    def get_calls_for_method(self, method_name):
        return [data for method, data in self.get_all_calls() if method == method_name]

    def get_sent_messages(self):
        return self.get_calls_for_method('sendMessage')

    def get_edited_messages(self):
        return self.get_calls_for_method('editMessageText')

    def get_last_sent_text(self):
        messages = self.get_sent_messages()
        if not messages:
            return None
        return messages[-1].get('text', '')

    def get_all_sent_texts(self):
        return [m.get('text', '') for m in self.get_sent_messages()]

    def get_all_edited_texts(self):
        return [m.get('text', '') for m in self.get_edited_messages()]

    def assert_sent_text_contains(self, substring):
        texts = self.get_all_sent_texts() + self.get_all_edited_texts()
        assert any(substring in t for t in texts), (
            f"Expected '{substring}' in sent/edited messages, got: {texts}"
        )

    def get_responses(self, method_name=None):
        """Telegram responses the bot received back: list of (method, data, result) triples."""
        responses = getattr(self.mock_bot, 'sent_responses', [])
        if method_name is None:
            return list(responses)
        return [r for r in responses if r[0] == method_name]

    def get_message_id_of_sent_text(self, substring):
        """tg message id of the message the bot sent whose text contains `substring`."""
        for method, data, result in self.get_responses():
            if substring in (data.get('text') or '') or substring in (data.get('caption') or ''):
                return result['message_id']
        raise AssertionError(
            f"No sent message contains '{substring}', got: "
            f"{[(d.get('text') or d.get('caption')) for _, d, _ in self.get_responses()]}"
        )

    def get_last_message_id_for_method(self, method_name):
        responses = self.get_responses(method_name)
        assert responses, f"Expected at least one {method_name} call"
        return responses[-1][2]['message_id']

    def assert_any_message_sent(self):
        messages = self.get_sent_messages()
        assert len(messages) > 0, "Expected at least one sendMessage call"
