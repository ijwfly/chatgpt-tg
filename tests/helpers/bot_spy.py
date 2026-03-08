class BotSpy:
    """Wraps a mocked Bot to provide assertion helpers over captured requests."""

    def __init__(self, mock_bot):
        self.mock_bot = mock_bot

    def get_all_calls(self):
        return self.mock_bot.request.call_args_list

    def get_calls_for_method(self, method_name):
        results = []
        for call in self.get_all_calls():
            args, kwargs = call
            # Bot.request(method, data) — method is first positional arg
            if args and args[0] == method_name:
                results.append(args[1] if len(args) > 1 else kwargs.get('data', {}))
        return results

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

    def assert_any_message_sent(self):
        messages = self.get_sent_messages()
        assert len(messages) > 0, "Expected at least one sendMessage call"
