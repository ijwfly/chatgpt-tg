"""Mock Anthropic client that returns real `anthropic` SDK objects.

Responses are built through the SDK's own pydantic models (Message, Raw*Event, TextBlock, ToolUseBlock, ...),
so a shape the installed SDK version no longer produces fails validation here instead of silently passing.
"""
import asyncio
import json

from anthropic.types import (
    InputJSONDelta,
    Message,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)

from app.openai_helpers.llm_client import BaseLLMClient


class MockAnthropicClient(BaseLLMClient):
    """Stands in for `AnthropicAsyncClient`; returns canned responses in order."""

    def __init__(self):
        super().__init__(api_key='test-key')
        self.responses = []
        self.calls = []

    def add_response(self, text=None, tool_use=None, input_tokens=10, output_tokens=20):
        """Non-streaming reply. `tool_use` = {'id', 'name', 'input'} adds a tool_use block."""
        self.responses.append({
            'streaming': False, 'text': text, 'tool_use': tool_use,
            'input_tokens': input_tokens, 'output_tokens': output_tokens,
        })

    def add_streaming_response(self, text_chunks=(), tool_use=None, input_tokens=10, output_tokens=20,
                               extra_event=None):
        """Streaming reply: text deltas, optionally a tool_use block streamed as input_json_delta chunks.

        `extra_event` is yielded before message_stop to imitate an event type this code base does not know.
        """
        self.responses.append({
            'streaming': True, 'text_chunks': list(text_chunks), 'tool_use': tool_use,
            'input_tokens': input_tokens, 'output_tokens': output_tokens, 'extra_event': extra_event,
        })

    async def chat_completions_create(self, model, messages, **additional_fields):
        self.calls.append({'model': model, 'messages': messages, 'additional_fields': additional_fields})
        if not self.responses:
            raise ValueError('MockAnthropicClient: no more responses in queue')
        resp = self.responses.pop(0)
        if additional_fields.get('stream') and resp['streaming']:
            return _stream(resp, model)
        return _message(resp, model)


def _blocks(resp):
    blocks = []
    if resp.get('text') is not None:
        blocks.append(TextBlock(type='text', text=resp['text']))
    if resp.get('tool_use'):
        tu = resp['tool_use']
        blocks.append(ToolUseBlock(type='tool_use', id=tu['id'], name=tu['name'], input=tu['input']))
    return blocks


def _message(resp, model):
    blocks = _blocks(resp)
    return Message(
        id='msg_test', type='message', role='assistant', model=model, content=blocks,
        stop_reason='tool_use' if resp.get('tool_use') else 'end_turn',
        usage=Usage(input_tokens=resp['input_tokens'], output_tokens=resp['output_tokens']),
    )


async def _stream(resp, model):
    yield RawMessageStartEvent(type='message_start', message=Message(
        id='msg_test', type='message', role='assistant', model=model, content=[],
        stop_reason=None, usage=Usage(input_tokens=resp['input_tokens'], output_tokens=1),
    ))

    index = 0
    if resp['text_chunks']:
        yield RawContentBlockStartEvent(type='content_block_start', index=index,
                                        content_block=TextBlock(type='text', text=''))
        for chunk in resp['text_chunks']:
            yield RawContentBlockDeltaEvent(type='content_block_delta', index=index,
                                            delta=TextDelta(type='text_delta', text=chunk))
            await asyncio.sleep(0)
        yield RawContentBlockStopEvent(type='content_block_stop', index=index)
        index += 1

    if resp.get('tool_use'):
        tu = resp['tool_use']
        yield RawContentBlockStartEvent(type='content_block_start', index=index,
                                        content_block=ToolUseBlock(type='tool_use', id=tu['id'], name=tu['name'], input={}))
        payload = json.dumps(tu['input'])
        half = len(payload) // 2
        for part in (payload[:half], payload[half:]):
            yield RawContentBlockDeltaEvent(type='content_block_delta', index=index,
                                            delta=InputJSONDelta(type='input_json_delta', partial_json=part))
        yield RawContentBlockStopEvent(type='content_block_stop', index=index)

    yield RawMessageDeltaEvent(
        type='message_delta',
        delta={'stop_reason': 'tool_use' if resp.get('tool_use') else 'end_turn', 'stop_sequence': None},
        usage=MessageDeltaUsage(output_tokens=resp['output_tokens']),
    )
    if resp.get('extra_event') is not None:
        yield resp['extra_event']
    yield RawMessageStopEvent(type='message_stop')
