"""Adapt the Responses API to the application's Chat Completions contract."""

import inspect
import json
import time

from openai.types.chat import ChatCompletion, ChatCompletionChunk

from app.openai_helpers.llm_client import GenericAsyncOpenAIClient


def _dict(value):
    if value is None:
        return {}
    return value if isinstance(value, dict) else value.model_dump(exclude_none=True)


def _content(content, assistant=False):
    if isinstance(content, str):
        return [{"type": "output_text" if assistant else "input_text", "text": content,
                 **({"annotations": []} if assistant else {})}]
    result = []
    for raw in content or []:
        part = dict(_dict(raw))
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text"):
            result.extend(_content(part["text"], assistant))
        elif kind == "image_url":
            image = part["image_url"]
            image = {"url": image} if isinstance(image, str) else _dict(image)
            result.append({"type": "input_image", "image_url": image["url"],
                           "detail": image.get("detail", "auto")})
        elif kind in ("input_image", "input_file", "refusal"):
            result.append(part)
        else:
            raise ValueError(f"Unsupported Responses message content: {kind!r}")
    return result


def _input(messages):
    result = []
    legacy_calls = []
    for index, raw in enumerate(messages):
        message = _dict(raw)
        role = message["role"]
        if role in ("tool", "function"):
            call_id = message.get("tool_call_id")
            if role == "function":
                match = next((call for call in legacy_calls if call[1] == message.get("name")), None)
                if match is None:
                    raise ValueError("Legacy function result has no preceding function call")
                legacy_calls.remove(match)
                call_id = match[0]
            if not call_id:
                raise ValueError("Tool result has no tool_call_id")
            output = message.get("content") or ""
            result.append({"type": "function_call_output", "call_id": call_id,
                           "output": output if isinstance(output, str) else _content(output)})
            continue
        if role not in ("system", "developer", "user", "assistant"):
            raise ValueError(f"Unsupported Responses message role: {role!r}")
        if role == "assistant":
            result.extend(dict(_dict(item)) for item in message.get("responses_reasoning") or [])
        content = _content(message.get("content"), assistant=role == "assistant")
        if message.get("refusal"):
            content.append({"type": "refusal", "refusal": message["refusal"]})
        if content:
            item = {"role": role, "content": content, "type": "message"}
            if role == "assistant":
                item.update(id=f"msg_history_{index}", status="completed")
            result.append(item)
        for raw_call in message.get("tool_calls") or []:
            call = _dict(raw_call)
            function = _dict(call["function"])
            result.append({"type": "function_call", "call_id": call["id"],
                           "name": function["name"], "arguments": function["arguments"]})
        if message.get("function_call"):
            function = _dict(message["function_call"])
            call_id = f"legacy_call_{index}"
            legacy_calls.append((call_id, function["name"]))
            result.append({"type": "function_call", "call_id": call_id,
                           "name": function["name"], "arguments": function["arguments"]})
    return result


def _tool(raw):
    tool = dict(_dict(raw))
    if tool.get("type") != "function":
        return tool
    function = tool.pop("function", None)
    if function is not None:
        tool.update(_dict(function))
    tool.setdefault("strict", False)
    return tool


def _params(params):
    params = dict(params)
    for old in ("max_completion_tokens", "max_tokens"):
        if old in params:
            params.setdefault("max_output_tokens", params.pop(old))
    if "reasoning_effort" in params:
        effort = params.pop("reasoning_effort")
        params["reasoning"] = {"effort": effort, **_dict(params.get("reasoning"))}
    if "response_format" in params:
        format_ = dict(_dict(params.pop("response_format")))
        if format_.get("type") == "json_schema":
            format_ = {"type": "json_schema", **_dict(format_["json_schema"])}
        params["text"] = {"format": format_, **_dict(params.get("text"))}
    functions = params.pop("functions", None)
    if functions is not None:
        params.setdefault("tools", [{"type": "function", "function": function} for function in functions])
    if "tools" in params:
        params["tools"] = [_tool(tool) for tool in params["tools"]]
    if "function_call" in params:
        choice = params.pop("function_call")
        params.setdefault("tool_choice", {"type": "function", **choice} if isinstance(choice, dict) else choice)
    choice = params.get("tool_choice")
    if isinstance(choice, dict) and "function" in choice:
        params["tool_choice"] = {"type": "function", **_dict(choice["function"])}
    # Responses always includes usage in the terminal event. Other stream options
    # must reach the SDK (and be rejected there if unsupported), not disappear.
    if "stream_options" in params:
        options = dict(params["stream_options"] or {})
        options.pop("include_usage", None)
        if options:
            params["stream_options"] = options
        else:
            params.pop("stream_options")
    params.setdefault("store", False)
    include = list(params.get("include") or [])
    if "reasoning.encrypted_content" not in include:
        include.append("reasoning.encrypted_content")
    params["include"] = include
    # Full history is supplied locally; server-side continuation would duplicate it.
    if params.get("previous_response_id") is not None or params.get("conversation") is not None:
        raise ValueError("Responses client uses local history, not server-side conversation state")
    return params


def _usage(raw):
    if raw is None:
        return None
    usage = _dict(raw)
    prompt = usage.get("input_tokens", 0)
    completion = usage.get("output_tokens", 0)
    result = {"prompt_tokens": prompt, "completion_tokens": completion,
              "total_tokens": usage.get("total_tokens", prompt + completion)}
    if usage.get("input_tokens_details") is not None:
        result["prompt_tokens_details"] = _dict(usage["input_tokens_details"])
    if usage.get("output_tokens_details") is not None:
        result["completion_tokens_details"] = _dict(usage["output_tokens_details"])
    return result


def _validate(response, items):
    error = response.get("error")
    status = response.get("status")
    if error or status in ("failed", "cancelled"):
        raise RuntimeError(f"Responses API failed: {error or status}")
    incomplete = status == "incomplete"
    if incomplete:
        reason = _dict(response.get("incomplete_details")).get("reason")
        if reason != "max_output_tokens":
            raise RuntimeError(f"Responses API incomplete: {reason or 'unknown reason'}")
    elif status not in (None, "completed"):
        raise RuntimeError(f"Responses API did not complete: {status}")
    for item in items:
        if item.get("type") != "function_call":
            continue
        if incomplete or item.get("status") in ("in_progress", "incomplete"):
            raise RuntimeError("Responses API returned an incomplete function call")
        if not item.get("call_id") or not item.get("name"):
            raise RuntimeError("Responses API returned a function call without call_id or name")
        try:
            json.loads(item.get("arguments", ""))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Responses API returned incomplete or invalid function arguments") from exc
    return "length" if incomplete else "tool_calls" if any(item.get("type") == "function_call" for item in items) else "stop"


def _message(items):
    text, refusals, calls, reasoning = [], [], [], []
    for item in items:
        kind = item.get("type")
        if kind == "message":
            for raw_part in item.get("content", []):
                part = _dict(raw_part)
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refusal = part.get("refusal", "")
                    text.append(refusal)
                    refusals.append(refusal)
        elif kind == "function_call":
            calls.append({"id": item["call_id"], "type": "function", "function": {
                "name": item["name"], "arguments": item["arguments"]}})
        elif kind == "reasoning":
            reasoning.append(item)
        else:
            raise RuntimeError(f"Unsupported Responses output item: {kind!r}")
    return {"role": "assistant", "content": "".join(text) or None,
            "refusal": "".join(refusals) or None, "tool_calls": calls or None,
            "responses_reasoning": reasoning}


class OpenAIResponsesClient(GenericAsyncOpenAIClient):
    async def chat_completions_create(self, model: str, messages, **additional_fields):
        params = _params(self._merge_extra_params(additional_fields))
        response = await self.client.responses.create(model=model, input=_input(messages), **params)
        if params.get("stream"):
            return _ResponsesStream(response, model)
        response = _dict(response)
        items = [_dict(item) for item in response.get("output", [])]
        finish = _validate(response, items)
        return ChatCompletion.model_validate({
            "id": response["id"], "object": "chat.completion", "model": response.get("model", model),
            "created": int(response.get("created_at", time.time())),
            "choices": [{"index": 0, "message": _message(items), "finish_reason": finish}],
            "usage": _usage(response.get("usage")),
        })


class _ResponsesStream:
    """An owned async iterator, including the legacy ``response.aclose`` handle."""

    def __init__(self, stream, model):
        self._stream = stream
        self._model = model
        self._closed = False
        self._iterator = self._iterate()
        self.response = self

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._iterator.__anext__()

    async def _close_stream(self):
        if not self._closed:
            self._closed = True
            close = getattr(self._stream, "close", None) or getattr(self._stream, "aclose", None)
            if close is None:
                close = self._stream.response.aclose
            result = close()
            if inspect.isawaitable(result):
                await result

    async def close(self):
        try:
            await self._iterator.aclose()
        finally:
            await self._close_stream()

    async def aclose(self):
        await self.close()

    async def _iterate(self):
        items = {}
        parts = {}
        sent_calls = {}
        response = {}
        terminal = False

        def chunk(delta, finish=None, usage=None):
            return ChatCompletionChunk.model_validate({
                "id": response.get("id", "responses-stream"), "object": "chat.completion.chunk",
                "created": int(response.get("created_at", time.time())),
                "model": response.get("model", self._model),
                "choices": [{"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": finish}],
                "usage": usage,
            })

        def text_delta(index, content_index, value, complete=False, refusal=False):
            key = (index, content_index)
            previous = parts.get(key, "")
            if complete:
                if not value.startswith(previous):
                    raise RuntimeError("Responses API changed text already streamed")
                delta = value[len(previous):]
                parts[key] = value
            else:
                delta = value
                parts[key] = previous + value
            if delta:
                return {"content": delta, **({"refusal": delta} if refusal else {})}
            return None

        def call_delta(index):
            item = items[index]
            if not item.get("call_id") or not item.get("name"):
                return None
            previous = sent_calls.get(index)
            arguments = item.get("arguments", "")
            if previous:
                if item["call_id"] != previous["id"] or item["name"] != previous["name"] or not arguments.startswith(previous["arguments"]):
                    raise RuntimeError("Responses API changed function call already streamed")
                suffix = arguments[len(previous["arguments"]):]
                if not suffix:
                    return None
                function = {"arguments": suffix}
                call = {"index": index, "function": function}
            else:
                call = {"index": index, "id": item["call_id"], "type": "function",
                        "function": {"name": item["name"], "arguments": arguments}}
            sent_calls[index] = {"id": item["call_id"], "name": item["name"], "arguments": arguments}
            return {"tool_calls": [call]}

        def reconcile(index, raw):
            item = _dict(raw)
            items[index] = item
            if item.get("type") == "message":
                for content_index, raw_part in enumerate(item.get("content", [])):
                    part = _dict(raw_part)
                    kind = part.get("type")
                    if kind in ("output_text", "refusal"):
                        delta = text_delta(index, content_index, part.get("text" if kind == "output_text" else "refusal", ""),
                                           complete=True, refusal=kind == "refusal")
                        if delta:
                            yield delta
            elif item.get("type") == "function_call":
                delta = call_delta(index)
                if delta:
                    yield delta

        try:
            async for raw_event in self._stream:
                event = _dict(raw_event)
                if terminal:
                    continue
                kind = event.get("type")
                if kind in ("response.created", "response.in_progress", "response.queued"):
                    response = _dict(event.get("response"))
                elif kind == "error":
                    raise RuntimeError(f"Responses API stream error: {event.get('message') or event}")
                elif kind in ("response.failed", "response.incomplete", "response.completed"):
                    response = _dict(event.get("response"))
                    response.setdefault("status", kind.split(".")[-1])
                    final_items = dict(items)
                    for index, raw_item in enumerate(response.get("output", [])):
                        item = _dict(raw_item)
                        # Some servers omit encrypted content in the final snapshot;
                        # the completed reasoning item remains the replay authority.
                        if item.get("type") == "reasoning" and not item.get("encrypted_content"):
                            item = {**item, **items.get(index, {})}
                        final_items[index] = item
                    ordered = [final_items[index] for index in sorted(final_items)]
                    finish = _validate(response, ordered)
                    # Validate terminal status before returning the finished turn.
                    message = _message(ordered)
                    for index in sorted(final_items):
                        for delta in reconcile(index, final_items[index]):
                            yield chunk(delta)
                    terminal = True
                    yield chunk({"responses_reasoning": message["responses_reasoning"]}, finish, _usage(response.get("usage")))
                    continue
                elif kind in ("response.output_item.added", "response.output_item.done"):
                    index = event["output_index"]
                    if kind.endswith(".done"):
                        for delta in reconcile(index, event["item"]):
                            yield chunk(delta)
                    else:
                        item = _dict(event["item"])
                        items[index] = item
                        if item.get("type") == "function_call":
                            delta = call_delta(index)
                            if delta:
                                yield chunk(delta)
                elif kind in ("response.output_text.delta", "response.refusal.delta"):
                    delta = text_delta(event["output_index"], event.get("content_index", 0), event["delta"],
                                       refusal=kind == "response.refusal.delta")
                    if delta:
                        yield chunk(delta)
                elif kind in ("response.output_text.done", "response.refusal.done"):
                    refusal = kind == "response.refusal.done"
                    delta = text_delta(event["output_index"], event.get("content_index", 0),
                                       event["refusal" if refusal else "text"], complete=True, refusal=refusal)
                    if delta:
                        yield chunk(delta)
                elif kind in ("response.function_call_arguments.delta", "response.function_call_arguments.done"):
                    index = event["output_index"]
                    item = items.setdefault(index, {"type": "function_call", "arguments": ""})
                    if kind.endswith(".delta"):
                        item["arguments"] = item.get("arguments", "") + event["delta"]
                    else:
                        item["arguments"] = event["arguments"]
                        if event.get("name"):
                            item["name"] = event["name"]
                    delta = call_delta(index)
                    if delta:
                        yield chunk(delta)
            if not terminal:
                raise RuntimeError("Responses API stream ended without a terminal response")
        finally:
            await self._close_stream()
