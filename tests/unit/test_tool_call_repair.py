from app.context.tool_call_repair import INTERRUPTED_TOOL_CALL_RESULT, repair_dangling_tool_calls
from app.openai_helpers.chatgpt import DialogMessage, FunctionCall, ToolCall


def _tool_calls_message(*ids):
    return DialogMessage(role='assistant', content=None, tool_calls=[
        ToolCall(id=i, type='function', function=FunctionCall(name='read_file', arguments='{}')) for i in ids
    ])


def _tool_result(tool_call_id, content='ok'):
    return DialogMessage(role='tool', tool_call_id=tool_call_id, content=content)


def _roles(messages):
    return [(m.role, m.tool_call_id) for m in messages]


class TestRepairDanglingToolCalls:

    def test_valid_history_is_untouched(self):
        messages = [
            DialogMessage(role='user', content='hi'),
            _tool_calls_message('a', 'b'),
            _tool_result('a'),
            _tool_result('b'),
            DialogMessage(role='assistant', content='done'),
        ]
        assert repair_dangling_tool_calls(messages) == messages

    def test_unanswered_calls_get_error_results_right_after_the_assistant_message(self):
        messages = [
            _tool_calls_message('read_file_0', 'read_file_1'),
            DialogMessage(role='user', content='next question'),
        ]
        repaired = repair_dangling_tool_calls(messages)
        assert _roles(repaired) == [
            ('assistant', None), ('tool', 'read_file_0'), ('tool', 'read_file_1'), ('user', None),
        ]
        assert repaired[1].content == INTERRUPTED_TOOL_CALL_RESULT
        assert repaired[2].content == INTERRUPTED_TOOL_CALL_RESULT
        # the input list is left alone
        assert len(messages) == 2

    def test_partially_answered_group_stays_contiguous(self):
        messages = [
            _tool_calls_message('a', 'b', 'c'),
            _tool_result('a', 'real result'),
            DialogMessage(role='user', content='next'),
        ]
        repaired = repair_dangling_tool_calls(messages)
        assert _roles(repaired) == [
            ('assistant', None), ('tool', 'a'), ('tool', 'b'), ('tool', 'c'), ('user', None),
        ]
        assert repaired[1].content == 'real result'

    def test_dangling_call_at_the_end_of_history(self):
        repaired = repair_dangling_tool_calls([_tool_calls_message('x')])
        assert _roles(repaired) == [('assistant', None), ('tool', 'x')]

    def test_assistant_messages_without_tool_calls_are_ignored(self):
        messages = [DialogMessage(role='assistant', content='plain'), DialogMessage(role='user', content='u')]
        assert repair_dangling_tool_calls(messages) == messages
