"""Responses wire-protocol tests, including real Telegram/runtime tool turns.

Only the HTTP transport is replaced: requests and SSE events pass through the real
OpenAI SDK and the same ChatGPT consumer used by the application.
"""

import asyncio
import copy
import json

import httpx
import openai
import pytest
import pytest_asyncio
from openai.types.chat import ChatCompletion
from pydantic import BaseModel

from app.llm_models import get_models
from app.openai_helpers.chatgpt import ChatGPT, DialogMessage
from app.openai_helpers.llm_client_factory import LLMClientFactory
from app.openai_helpers.responses_client import OpenAIResponsesClient
from tests.helpers.bot_spy import BotSpy
from tests.helpers.telegram_factory import make_text_message


MODEL = 'gpt-3.5-turbo'
REASONING = {
    'id': 'rs_private',
    'type': 'reasoning',
    'summary': [],
    'encrypted_content': 'opaque-encrypted-reasoning',
}


def _function(item_id, call_id, settings_text):
    return {
        'id': item_id,
        'type': 'function_call',
        'status': 'completed',
        'call_id': call_id,
        'name': 'save_user_settings',
        'arguments': json.dumps({'settings_text': settings_text}),
    }


def _message(text, item_id='msg_answer'):
    return {
        'id': item_id,
        'type': 'message',
        'role': 'assistant',
        'status': 'completed',
        'content': [{'type': 'output_text', 'text': text, 'annotations': []}],
    }


def _response(output, response_id='resp_test', status='completed'):
    return {
        'id': response_id,
        'object': 'response',
        'created_at': 1700000000,
        'model': MODEL,
        'status': status,
        'error': None,
        'incomplete_details': None,
        'instructions': None,
        'metadata': {},
        'parallel_tool_calls': True,
        'output': output,
        'temperature': 1.0,
        'tool_choice': 'auto',
        'tools': [],
        'top_p': 1.0,
        'usage': {
            'input_tokens': 23,
            'input_tokens_details': {'cached_tokens': 5},
            'output_tokens': 11,
            'output_tokens_details': {'reasoning_tokens': 7},
            'total_tokens': 34,
        },
    }


def _events(response, deltas=True):
    """Send both incremental and authoritative done data, as the real API does."""
    initial = {**response, 'output': [], 'status': 'in_progress', 'usage': None}
    events = [{'type': 'response.created', 'response': initial}]
    for index, item in enumerate(response['output']):
        added = copy.deepcopy(item)
        added['status'] = 'in_progress'
        if item['type'] == 'function_call':
            added['arguments'] = ''
        elif item['type'] == 'message':
            added['content'] = []
        elif item['type'] == 'reasoning':
            added.pop('encrypted_content', None)
        events.append({'type': 'response.output_item.added', 'output_index': index, 'item': added})
        if item['type'] == 'function_call':
            arguments = item['arguments']
            if deltas:
                midpoint = len(arguments) // 2
                for fragment in (arguments[:midpoint], arguments[midpoint:]):
                    events.append({
                        'type': 'response.function_call_arguments.delta',
                        'item_id': item['id'], 'output_index': index, 'delta': fragment,
                    })
            events.append({
                'type': 'response.function_call_arguments.done',
                'item_id': item['id'], 'output_index': index, 'arguments': arguments,
            })
        elif item['type'] == 'message':
            part = item['content'][0]
            events.append({
                'type': 'response.content_part.added', 'item_id': item['id'],
                'output_index': index, 'content_index': 0,
                'part': {'type': 'output_text', 'text': '', 'annotations': []},
            })
            if deltas:
                midpoint = len(part['text']) // 2
                for fragment in (part['text'][:midpoint], part['text'][midpoint:]):
                    events.append({
                        'type': 'response.output_text.delta', 'item_id': item['id'],
                        'output_index': index, 'content_index': 0, 'delta': fragment,
                    })
            events.extend([
                {'type': 'response.output_text.done', 'item_id': item['id'],
                 'output_index': index, 'content_index': 0, 'text': part['text']},
                {'type': 'response.content_part.done', 'item_id': item['id'],
                 'output_index': index, 'content_index': 0, 'part': part},
            ])
        events.append({'type': 'response.output_item.done', 'output_index': index, 'item': item})
    terminal = 'response.completed' if response['status'] == 'completed' else f"response.{response['status']}"
    events.append({'type': terminal, 'response': response})
    return events


class _SSEBody(httpx.AsyncByteStream):
    def __init__(self, events, block=False):
        self.events = events
        self.block = block
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        for sequence, event in enumerate(self.events):
            data = {'sequence_number': sequence, **event}
            yield f"event: {event['type']}\ndata: {json.dumps(data)}\n\n".encode()
        if self.block:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


class _Endpoint:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.bodies = []

    def __call__(self, request):
        assert request.method == 'POST'
        assert request.url.path == '/v1/responses'
        self.requests.append(json.loads(request.content))
        assert self.replies, 'Unexpected extra Responses request (possible repeated tool execution)'
        reply = self.replies.pop(0)
        if isinstance(reply, _SSEBody):
            self.bodies.append(reply)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=reply)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


@pytest_asyncio.fixture
async def responses_client_factory(monkeypatch):
    clients = []

    def create(replies, extra_completion_params=None):
        endpoint = _Endpoint(replies)
        sdk = openai.AsyncOpenAI(
            api_key='responses-test-key', base_url='https://responses.invalid/v1',
            max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(endpoint)),
        )
        clients.append(sdk)
        monkeypatch.setattr('app.openai_helpers.llm_client.observability.create_openai_client', lambda **kwargs: sdk)
        adapter = OpenAIResponsesClient(api_key='responses-test-key', extra_completion_params=extra_completion_params)
        return adapter, endpoint

    yield create
    for client in clients:
        await client.close()


def _chat_consumer(monkeypatch, adapter):
    monkeypatch.setitem(LLMClientFactory._model_clients, MODEL, adapter)
    model = get_models()[MODEL]
    return ChatGPT(model, 'You are a helpful assistant.')


async def _consume(consumer):
    updates = [update async for update in consumer.send_messages_streaming(
        [DialogMessage(role='user', content='Save both settings')], lambda: False,
    )]
    assert updates, 'A completed response must reach the ChatGPT consumer'
    return updates[-1]


@pytest.mark.parametrize('streaming', [False, True], ids=['json', 'sse'])
async def test_telegram_tool_cycle_persists_and_replays_reasoning(bot_app, responses_client_factory, streaming):
    """Two distinct call_ids survive DB persistence, tool execution, and a later turn."""
    telegram_bot, dp, bot = bot_app
    user_id = 986001
    user = await telegram_bot.db.get_or_create_user(user_id)
    user.use_functions = True
    user.system_prompt_settings_enabled = True
    user.streaming_answers = streaming
    await telegram_bot.db.update_user(user)

    calls = [
        _function('fc_item_first', 'call_public_first', 'Name: First'),
        _function('fc_item_second', 'call_public_second', 'Name: Second'),
    ]
    responses = [
        _response([REASONING, *calls], 'resp_tools'),
        _response([_message('Both settings saved.')], 'resp_answer'),
        _response([_message('I remember the saved settings.')], 'resp_later'),
    ]
    replies = [_SSEBody(_events(response)) for response in responses] if streaming else responses
    adapter, endpoint = responses_client_factory(replies)
    LLMClientFactory._model_clients[MODEL] = adapter

    await asyncio.wait_for(dp.feed_update(bot, make_text_message('Save my settings twice', user_id=user_id)), 5)
    spy = BotSpy(bot)
    spy.assert_sent_text_contains('Both settings saved.')
    assert (await telegram_bot.db.get_user(user_id)).system_prompt_settings == 'Name: Second'
    assert len(endpoint.requests) == 2

    rows = await telegram_bot.db.connection_pool.fetch(
        'SELECT id, message FROM chatgpttg.message WHERE user_id = $1 ORDER BY id', user.id,
    )
    messages = [DialogMessage.model_validate_json(row['message']) for row in rows]
    assert [message.role for message in messages] == ['user', 'assistant', 'tool', 'tool', 'assistant']
    assistant = messages[1]
    assert assistant.responses_reasoning == [REASONING]
    assert [call.id for call in assistant.tool_calls] == ['call_public_first', 'call_public_second']
    assert [call.function.name for call in assistant.tool_calls] == ['save_user_settings'] * 2
    assert [json.loads(call.function.arguments) for call in assistant.tool_calls] == [
        {'settings_text': 'Name: First'}, {'settings_text': 'Name: Second'},
    ]
    assert [message.tool_call_id for message in messages if message.role == 'tool'] == [
        'call_public_first', 'call_public_second',
    ]
    assert [message.content for message in messages if message.role == 'tool'] == ['success', 'success']
    loaded = await telegram_bot.db.get_messages_by_ids([rows[1]['id']])
    assert loaded[0].message.responses_reasoning == [REASONING]
    assert loaded[0].message.openai_message()['responses_reasoning'] == [REASONING]

    await asyncio.wait_for(dp.feed_update(bot, make_text_message('What did you save?', user_id=user_id)), 5)
    spy.assert_sent_text_contains('I remember the saved settings.')
    assert len(endpoint.requests) == 3
    for request in endpoint.requests:
        assert request['store'] is False
        assert 'reasoning.encrypted_content' in request['include']
        assert 'previous_response_id' not in request
        assert 'messages' not in request
        assert 'stream_options' not in request
    for request in endpoint.requests[1:]:
        inputs = request['input']
        reasoning_index = next(index for index, item in enumerate(inputs) if item.get('type') == 'reasoning')
        assert inputs[reasoning_index] == REASONING
        function_calls = [item for item in inputs if item.get('type') == 'function_call']
        assert [item['call_id'] for item in function_calls] == ['call_public_first', 'call_public_second']
        assert all(inputs.index(item) > reasoning_index for item in function_calls)
        assert [json.loads(item['arguments']) for item in function_calls] == [
            {'settings_text': 'Name: First'}, {'settings_text': 'Name: Second'},
        ]
        outputs = [item for item in inputs if item.get('type') == 'function_call_output']
        assert [(item['call_id'], item['output']) for item in outputs] == [
            ('call_public_first', 'success'), ('call_public_second', 'success'),
        ]
        assert all('responses_reasoning' not in item for item in inputs)
    assert len(spy.get_responses('sendMessage')) >= 2  # actual tool side effects, not canned tool results
    usage = await telegram_bot.db.get_user_current_month_completion_usage(user.id)
    assert len(usage) == 1
    assert (usage[0].prompt_tokens, usage[0].completion_tokens, usage[0].total_tokens) == (69, 33, 102)
    assert not endpoint.replies
    assert all(body.closed for body in endpoint.bodies)


async def test_image_and_function_schema_are_responses_wire_shapes(responses_client_factory):
    adapter, endpoint = responses_client_factory([_response([_message('A red square.')])])
    parameters = {
        'type': 'object', 'properties': {'color': {'type': 'string'}},
        'required': ['color'], 'additionalProperties': False,
    }
    messages = [
        {'role': 'system', 'content': 'Describe images.'},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'What color is this?'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA', 'detail': 'high'}},
        ]},
    ]
    original = copy.deepcopy(messages)
    result = await adapter.chat_completions_create(
        model=MODEL, messages=messages, max_tokens=128,
        tools=[{'type': 'function', 'function': {
            'name': 'record_color', 'description': 'Record the color.', 'parameters': parameters, 'strict': True,
        }}],
        tool_choice={'type': 'function', 'function': {'name': 'record_color'}},
    )
    assert isinstance(result, ChatCompletion)
    assert result.choices[0].message.content == 'A red square.'
    assert result.choices[0].finish_reason == 'stop'
    assert (result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.total_tokens) == (23, 11, 34)
    request = endpoint.requests[0]
    assert request['max_output_tokens'] == 128
    assert 'max_tokens' not in request
    assert request['tools'] == [{
        'type': 'function', 'name': 'record_color', 'description': 'Record the color.',
        'parameters': parameters, 'strict': True,
    }]
    assert request['tool_choice'] == {'type': 'function', 'name': 'record_color'}
    user_input = next(item for item in request['input'] if item.get('role') == 'user')
    assert user_input['content'] == [
        {'type': 'input_text', 'text': 'What color is this?'},
        {'type': 'input_image', 'image_url': 'data:image/png;base64,AAAA', 'detail': 'high'},
    ]
    assert messages == original, 'Translation must not mutate stored dialog history'


@pytest.mark.parametrize('deltas', [False, True], ids=['done-only', 'fragments-and-done'])
async def test_stream_tool_fragments_are_reconciled_once(responses_client_factory, monkeypatch, deltas):
    calls = [
        _function('fc_item_a', 'call_a', 'Name: A'),
        _function('fc_item_b', 'call_b', 'Name: B'),
    ]
    body = _SSEBody(_events(_response([REASONING, *calls]), deltas=deltas))
    adapter, endpoint = responses_client_factory([body])
    message, usage = await _consume(_chat_consumer(monkeypatch, adapter))
    assert [call.id for call in message.tool_calls] == ['call_a', 'call_b']
    assert [call.function.name for call in message.tool_calls] == ['save_user_settings'] * 2
    assert [json.loads(call.function.arguments) for call in message.tool_calls] == [
        {'settings_text': 'Name: A'}, {'settings_text': 'Name: B'},
    ]
    assert message.responses_reasoning == [REASONING]
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (23, 11, 34)
    assert body.closed
    assert len(endpoint.requests) == 1


@pytest.mark.parametrize('deltas', [False, True], ids=['done-only', 'fragments-and-done'])
async def test_stream_text_is_not_duplicated_by_done_events(responses_client_factory, monkeypatch, deltas):
    body = _SSEBody(_events(_response([REASONING, _message('Exactly one final answer.')]), deltas=deltas))
    adapter, _ = responses_client_factory([body])
    message, usage = await _consume(_chat_consumer(monkeypatch, adapter))
    assert message.content == 'Exactly one final answer.'
    assert message.responses_reasoning == [REASONING]
    assert message.role == 'assistant'
    assert usage.total_tokens == 34
    assert body.closed


async def test_stream_completion_only_falls_back_to_complete_output(responses_client_factory, monkeypatch):
    response = _response([REASONING, _message('Only the terminal event carried the answer.')])
    body = _SSEBody([{'type': 'response.completed', 'response': response}])
    adapter, _ = responses_client_factory([body])
    message, usage = await _consume(_chat_consumer(monkeypatch, adapter))
    assert message.content == 'Only the terminal event carried the answer.'
    assert message.responses_reasoning == [REASONING]
    assert usage.total_tokens == 34
    assert body.closed


async def test_http_api_error_is_not_a_successful_empty_response(responses_client_factory):
    adapter, endpoint = responses_client_factory([httpx.Response(400, json={
        'error': {'message': 'Unsupported Responses model', 'type': 'invalid_request_error', 'code': 'model_not_supported'},
    })])
    with pytest.raises(openai.BadRequestError, match='Unsupported Responses model'):
        await adapter.chat_completions_create(model=MODEL, messages=[{'role': 'user', 'content': 'Hello'}])
    assert len(endpoint.requests) == 1


@pytest.mark.parametrize('kind', ['failed', 'error', 'truncated', 'incomplete-tool'])
async def test_unsuccessful_stream_raises_and_closes(responses_client_factory, kind):
    response = _response([_function('fc_cut', 'call_cut', 'Never execute')])
    if kind == 'failed':
        response['status'] = 'failed'
        response['error'] = {'code': 'server_error', 'message': 'Provider failed'}
        events = [{'type': 'response.failed', 'response': response}]
    elif kind == 'error':
        events = [{'type': 'error', 'code': 'server_error', 'message': 'Provider failed', 'param': None}]
    elif kind == 'incomplete-tool':
        response['status'] = 'incomplete'
        response['incomplete_details'] = {'reason': 'max_output_tokens'}
        events = _events(response)
    else:
        events = _events(response)[:-1]  # clean transport EOF is not a completed model response
    body = _SSEBody(events)
    adapter, _ = responses_client_factory([body])
    stream = await adapter.chat_completions_create(model=MODEL, messages=[{'role': 'user', 'content': 'Hello'}], stream=True)
    with pytest.raises((openai.OpenAIError, RuntimeError, ValueError)):
        async for _ in stream:
            pass
    assert body.closed


@pytest.mark.parametrize('close_method', ['close', 'response.aclose'])
async def test_explicit_stream_close_releases_transport(responses_client_factory, close_method):
    events = [{'type': 'response.output_text.delta', 'item_id': 'msg_live',
               'output_index': 0, 'content_index': 0, 'delta': 'Visible text'}]
    body = _SSEBody(events, block=True)
    adapter, _ = responses_client_factory([body])
    stream = await adapter.chat_completions_create(model=MODEL, messages=[{'role': 'user', 'content': 'Hello'}], stream=True)
    try:
        first = await asyncio.wait_for(anext(stream), 1)
        assert first.choices[0].delta.content == 'Visible text'
        if close_method == 'close':
            await stream.close()
        else:
            await stream.response.aclose()
        assert body.closed
    finally:
        await stream.close()


async def test_task_cancellation_closes_inflight_stream(responses_client_factory):
    body = _SSEBody([], block=True)
    adapter, _ = responses_client_factory([body])
    stream = await adapter.chat_completions_create(model=MODEL, messages=[{'role': 'user', 'content': 'Hello'}], stream=True)
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.wait_for(body.waiting.wait(), 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        assert body.closed
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.close()


async def test_optional_pydantic_tool_params_do_not_become_strict(responses_client_factory):
    class SearchParams(BaseModel):
        query: str
        limit: int = 5

    parameters = SearchParams.model_json_schema()
    adapter, endpoint = responses_client_factory([_response([_message('No search needed.')])])
    await adapter.chat_completions_create(
        model=MODEL, messages=[{'role': 'user', 'content': 'Search if needed'}],
        tools=[{'type': 'function', 'function': {
            'name': 'search', 'description': 'Search documents.', 'parameters': parameters,
        }}],
    )
    tool = endpoint.requests[0]['tools'][0]
    assert tool['strict'] is False
    assert tool['parameters'] == parameters
    assert tool['parameters']['required'] == ['query']
    assert tool['parameters']['properties']['limit']['default'] == 5


@pytest.mark.parametrize('stop', ['aclose', 'cancel'])
async def test_chatgpt_consumer_stop_closes_underlying_response(responses_client_factory, monkeypatch, stop):
    body = _SSEBody([{
        'type': 'response.output_text.delta', 'item_id': 'msg_live',
        'output_index': 0, 'content_index': 0, 'delta': 'Partial answer',
    }], block=True)
    adapter, _ = responses_client_factory([body])
    consumer = _chat_consumer(monkeypatch, adapter)
    cancelled = False
    iterator = consumer.send_messages_streaming(
        [DialogMessage(role='user', content='Start writing')], lambda: cancelled,
    )
    try:
        message, _ = await asyncio.wait_for(anext(iterator), 1)
        assert message.content == 'Partial answer'
        if stop == 'aclose':
            await iterator.aclose()
        else:
            cancelled = True
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(iterator), 1)
        assert body.closed
    finally:
        await iterator.aclose()
