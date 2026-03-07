from unittest.mock import MagicMock
from app.openai_helpers.llm_client import BaseLLMClient


class MockUsage:
    """Usage object that supports dict() conversion."""
    def __init__(self, prompt_tokens, completion_tokens, total_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

    def __iter__(self):
        return iter([
            ('prompt_tokens', self.prompt_tokens),
            ('completion_tokens', self.completion_tokens),
            ('total_tokens', self.total_tokens),
        ])


class MockLLMClient(BaseLLMClient):
    """Mock LLM client that returns canned responses from a queue."""

    def __init__(self, responses=None):
        super().__init__(api_key='test-key')
        self.responses = list(responses or [])
        self.calls = []

    def add_response(self, content, tool_calls=None, function_call=None,
                     prompt_tokens=10, completion_tokens=20):
        self.responses.append({
            'content': content,
            'tool_calls': tool_calls,
            'function_call': function_call,
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion_tokens,
        })

    async def chat_completions_create(self, model, messages, **additional_fields):
        self.calls.append({
            'model': model,
            'messages': messages,
            'additional_fields': additional_fields,
        })

        if not self.responses:
            raise ValueError("MockLLMClient: no more responses in queue")

        resp_data = self.responses.pop(0)
        return _build_response(resp_data, model)


def _build_response(resp_data, model):
    """Build a mock response object matching OpenAI SDK shape."""
    content = resp_data.get('content')
    tool_calls_data = resp_data.get('tool_calls')
    function_call_data = resp_data.get('function_call')
    prompt_tokens = resp_data.get('prompt_tokens', 10)
    completion_tokens = resp_data.get('completion_tokens', 20)

    # Build message mock
    message = MagicMock()
    message.role = 'assistant'
    message.content = content
    message.tool_calls = None
    message.function_call = None

    msg_dict = {'role': 'assistant', 'content': content}

    if tool_calls_data:
        mock_tool_calls = []
        tool_calls_dict_list = []
        for tc in tool_calls_data:
            mock_tc = MagicMock()
            mock_tc.id = tc['id']
            mock_tc.type = 'function'
            mock_tc.function = MagicMock()
            mock_tc.function.name = tc['function']['name']
            mock_tc.function.arguments = tc['function']['arguments']
            mock_tool_calls.append(mock_tc)
            tool_calls_dict_list.append({
                'id': tc['id'],
                'type': 'function',
                'function': {
                    'name': tc['function']['name'],
                    'arguments': tc['function']['arguments'],
                },
            })
        message.tool_calls = mock_tool_calls
        msg_dict['tool_calls'] = tool_calls_dict_list

    if function_call_data:
        mock_fc = MagicMock()
        mock_fc.name = function_call_data['name']
        mock_fc.arguments = function_call_data['arguments']
        message.function_call = mock_fc
        msg_dict['function_call'] = {
            'name': function_call_data['name'],
            'arguments': function_call_data['arguments'],
        }

    # message.dict() returns the dict representation
    message.dict = lambda: msg_dict

    # Build choice
    choice = MagicMock()
    choice.message = message

    # Build usage — needs to support dict() conversion via __iter__
    usage = MockUsage(prompt_tokens, completion_tokens, prompt_tokens + completion_tokens)

    # Build response
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage

    return resp
