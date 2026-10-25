import json

import pytest

from app import create_app
from config import Config
from provider import genai


class DummyTokenManager:
    mode = "static"

    def get_token(self):
        return "token"

    def force_refresh(self):
        return None


def _app():
    return create_app(Config(
        token_manager=DummyTokenManager(),
        port=0,
        api_key=None,
        debug=False,
        api_format="both",
    ))


def _patch_upstream(monkeypatch, content, finish_reason="stop"):
    class FakeResponse:
        status_code = 200
        text = ""

        def iter_lines(self, **kwargs):
            split = content.find("<think>")
            split = split if split >= 0 else len(content) // 2
            for fragment in (content[:split], content[split:]):
                if fragment:
                    yield ("data: " + json.dumps({
                        "choices": [{"delta": {"content": fragment}, "finish_reason": None}]
                    })).encode()
            yield ("data: " + json.dumps({
                "choices": [{"delta": {}, "finish_reason": finish_reason}]
            })).encode()

        def close(self):
            pass

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda model, token: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: FakeResponse())


def _request(client, protocol, stream=False, tool_choice=None):
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    if protocol == "chat":
        return client.post("/v1/chat/completions", json={
            "model": "chatglm",
            "stream": stream,
            "tool_choice": tool_choice,
            "messages": [{"role": "user", "content": "Write the file"}],
            "tools": [{"type": "function", "function": {
                "name": "write", "description": "Write a file", "parameters": parameters,
            }}],
        })
    if protocol == "messages":
        return client.post("/v1/messages", json={
            "model": "chatglm",
            "stream": stream,
            "tool_choice": {"type": "any"} if tool_choice == "required" else tool_choice,
            "messages": [{"role": "user", "content": "Write the file"}],
            "tools": [{"name": "write", "description": "Write a file", "input_schema": parameters}],
        })
    return client.post("/v1/responses", json={
        "model": "chatglm",
        "stream": stream,
        "tool_choice": tool_choice,
        "input": "Write the file",
        "tools": [{"type": "function", "name": "write", "description": "Write a file", "parameters": parameters}],
    })


def _action_call(name, arguments):
    payload = {"calls": [{"operation": name, "parameters": arguments}]}
    return "@@ACTION@@" + json.dumps(payload, ensure_ascii=False) + "@@END_ACTION@@"


def _native_call(name, arguments):
    content = '<|open|>tools<|sep|>' + f'<|open|>call tool="{name}" index="1"<|sep|>'
    for key, value in arguments.items():
        content += f'<|open|>argument key="{key}" type="string"<|sep|>{value}<|close|>argument<|sep|>'
    return content + '<|close|>call<|sep|><|close|>tools<|sep|>'


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("wire", [_action_call, _native_call])
def test_tool_arguments_preserve_markup_and_escaped_unicode(monkeypatch, protocol, wire):
    arguments = {
        "path": "notes/雪.txt",
        "content": 'before <think>keep exactly</think> </tool_call> <|DSML|call>\nquote: "keep"',
    }
    _patch_upstream(monkeypatch, wire("write", arguments))

    response = _request(_app().test_client(), protocol)

    assert response.status_code == 200
    data = response.get_json()
    if protocol == "chat":
        encoded = data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        actual = json.loads(encoded)
    elif protocol == "messages":
        block = next(block for block in data["content"] if block["type"] == "tool_use")
        actual = block["input"]
    else:
        item = next(item for item in data["output"] if item["type"] == "function_call")
        actual = json.loads(item["arguments"])
    assert actual == arguments



@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("wire", [_action_call, _native_call])
def test_streamed_tool_arguments_preserve_markup_and_escaped_unicode(monkeypatch, protocol, wire):
    arguments = {
        "path": "notes/雪.txt",
        "content": 'before <think>keep exactly</think> </tool_call> <|DSML|call>\nquote: "keep"',
    }
    _patch_upstream(monkeypatch, wire("write", arguments))

    response = _request(_app().test_client(), protocol, stream=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    if protocol == "chat":
        events = [
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: ") and line[6:] != "[DONE]"
        ]
        fragments = [
            tool["function"].get("arguments", "")
            for event in events
            for tool in event.get("choices", [{}])[0].get("delta", {}).get("tool_calls", [])
        ]
        actual = json.loads("".join(fragments))
    else:
        event_data = []
        for block in body.strip().split("\n\n"):
            lines = block.splitlines()
            name = next((line[7:] for line in lines if line.startswith("event: ")), None)
            data_line = next((line[6:] for line in lines if line.startswith("data: ")), None)
            if name and data_line:
                event_data.append((name, json.loads(data_line)))
        if protocol == "messages":
            fragments = [
                data["delta"]["partial_json"]
                for name, data in event_data
                if name == "content_block_delta"
                and data.get("delta", {}).get("type") == "input_json_delta"
            ]
            actual = json.loads("".join(fragments))
        else:
            arguments_text = next(
                data["arguments"]
                for name, data in event_data
                if name == "response.function_call_arguments.done"
            )
            actual = json.loads(arguments_text)
    assert actual == arguments


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("failure", ["length", "invalid-json", "malformed-action", "malformed-native"])
def test_tool_call_failure_never_returns_success_or_executable_partial_call(
    monkeypatch, protocol, failure
):
    if failure == "length":
        arguments = {"path": "notes.txt", "content": "partial"}
        finish_reason = "length"
        content = _action_call("write", arguments)
    elif failure == "malformed-action":
        finish_reason = "stop"
        content = '@@ACTION@@{"calls":[{"operation":"write","parameters":{"path":"notes.txt"}}]'
    elif failure == "malformed-native":
        finish_reason = "stop"
        content = '<|open|>tools<|sep|><|open|>call tool="write" index="1"<|sep|>'
    else:
        finish_reason = "stop"
        content = (
            '<tool_call>{"name":"write","arguments":'
            '{"path":"notes.txt","content":"truncated"}</tool_call>'
        )
    _patch_upstream(monkeypatch, content, finish_reason)

    response = _request(_app().test_client(), protocol)

    assert response.status_code == 502
    assert "error" in response.get_json()
    assert "tool_calls" not in response.get_json()


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_required_tool_choice_rejects_text_only_completion(monkeypatch, protocol, stream):
    _patch_upstream(monkeypatch, "I will perform the requested file operation now.", "stop")

    response = _request(
        _app().test_client(), protocol, stream=stream, tool_choice="required"
    )

    if not stream:
        assert response.status_code == 502
        assert "error" in response.get_json()
        return

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "error" in body
    if protocol == "chat":
        assert '"finish_reason": "tool_calls"' not in body
    elif protocol == "messages":
        assert "event: error" in body
        assert "event: message_stop" not in body
    else:
        assert "event: error" in body
        assert "response.completed" not in body


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_streamed_tool_truncation_emits_error_not_tool_use(monkeypatch, protocol):
    call = {"name": "write", "arguments": {"path": "notes.txt", "content": "partial"}}
    _patch_upstream(
        monkeypatch,
        "<tool_call>" + json.dumps(call) + "</tool_call>",
        "length",
    )

    response = _request(_app().test_client(), protocol, stream=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "truncated" in body
    if protocol == "chat":
        assert '"error"' in body
        assert '"tool_calls"' not in body
    elif protocol == "messages":
        assert "event: error" in body
        assert '"type":"tool_use"' not in body
        assert "event: message_stop" not in body
    else:
        assert "event: error" in body
        assert "response.completed" not in body
        assert '"type":"function_call"' not in body


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_streamed_incomplete_action_marker_emits_error(monkeypatch, protocol):
    _patch_upstream(monkeypatch, "The call starts with @@ACTION@", "stop")

    response = _request(_app().test_client(), protocol, stream=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    if protocol == "chat":
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        assert any("error" in event for event in events)
        assert "[DONE]" not in body
        assert '"tool_calls"' not in body
    elif protocol == "messages":
        assert "event: error" in body
        assert "event: message_stop" not in body
    else:
        assert "event: error" in body
        assert "response.completed" not in body


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("quoted", [
    '`<think>keep</think>`, `</tool_call>`, `<|DSML|call>`, `@@ACTION@@`, `@@END_ACTION@@`',
    '```text\n<think>keep</think> </tool_call> <|DSML|call> @@ACTION@@ @@END_ACTION@@\n```',
])
def test_final_verification_examples_do_not_trigger_streamed_tools(monkeypatch, protocol, quoted):
    content = "Verified read-back. Literal data:\n" + quoted
    _patch_upstream(monkeypatch, content)

    response = _request(_app().test_client(), protocol, stream=True)
    raw = response.get_data(as_text=True)
    events = [json.loads(line[6:]) for line in raw.splitlines()
              if line.startswith("data: ") and line[6:] != "[DONE]"]

    assert response.status_code == 200
    assert not any("error" in event for event in events)
    if protocol == "chat":
        actual = "".join(event["choices"][0]["delta"].get("content", "") for event in events)
        assert events[-1]["choices"][0]["finish_reason"] == "stop"
    elif protocol == "messages":
        actual = "".join(event.get("delta", {}).get("text", "") for event in events
                         if event.get("delta", {}).get("type") == "text_delta")
        assert events[-1]["type"] == "message_stop"
    else:
        actual = "".join(event.get("delta", "") for event in events
                         if event.get("type") == "response.output_text.delta")
        assert events[-1]["type"] == "response.completed"
    assert actual == content


def _patch_native_upstream(monkeypatch, arguments, *, name="write", finish="tool_calls", second=False):
    class NativeResponse:
        status_code = 200
        text = ""

        def iter_lines(self, **kwargs):
            fragments = [
                {"index": 0, "id": "call_preserved", "type": "function",
                 "function": {"name": name[:2], "arguments": arguments[:9]}},
                {"index": 0, "function": {"name": name[2:], "arguments": arguments[9:]}},
            ]
            for fragment in fragments:
                yield ("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [fragment]}, "finish_reason": None}]})).encode()
            if second:
                yield ("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{
                    "index": 1, "id": "call_second", "type": "function",
                    "function": {"name": "write", "arguments": arguments},
                }]}, "finish_reason": None}]})).encode()
            if finish is not None:
                yield ("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]})).encode()

        def close(self):
            pass

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda model, token: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: NativeResponse())


def _native_request(protocol, stream, **overrides):
    parameters = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                  "required": ["path", "content"], "additionalProperties": False}
    body = {"model": "chatglm", "stream": stream}
    if protocol == "chat":
        path = "/v1/chat/completions"
        body.update(messages=[{"role": "user", "content": "write"}],
                    tools=[{"type": "function", "function": {"name": "write", "parameters": parameters}}])
    elif protocol == "messages":
        path = "/v1/messages"
        body.update(messages=[{"role": "user", "content": "write"}],
                    tools=[{"name": "write", "input_schema": parameters}])
    else:
        path = "/v1/responses"
        body.update(input="write", tools=[{"type": "function", "name": "write", "parameters": parameters}])
    body.update(overrides)
    return _app().test_client().post(path, json=body)


def _wire_events(response):
    return [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ") and line[6:] != "[DONE]"]


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_native_fragments_preserve_calls_through_all_protocols(monkeypatch, protocol, stream):
    arguments = '{ "path": "雪.txt", "content": "@@ACTION@@ <think>literal</think> </tool_call>" }'
    _patch_native_upstream(monkeypatch, arguments)
    response = _native_request(protocol, stream)
    assert response.status_code == 200
    if not stream:
        data = response.get_json()
        if protocol == "chat":
            call = data["choices"][0]["message"]["tool_calls"][0]
            assert call["id"] == "call_preserved"
            assert call["function"]["arguments"] == arguments
        elif protocol == "messages":
            call = next(block for block in data["content"] if block["type"] == "tool_use")
            assert call["id"] == "call_preserved"
            assert call["input"] == json.loads(arguments)
        else:
            call = next(item for item in data["output"] if item["type"] == "function_call")
            assert call["call_id"] == "call_preserved"
            assert call["arguments"] == arguments
        return
    events = _wire_events(response)
    assert not any("error" in event for event in events)
    if protocol == "chat":
        calls = [call for event in events for call in event["choices"][0]["delta"].get("tool_calls", [])]
        assert calls[0]["id"] == "call_preserved"
        assert "".join(call["function"].get("arguments", "") for call in calls) == arguments
    elif protocol == "messages":
        block = next(event["content_block"] for event in events
                     if event.get("content_block", {}).get("type") == "tool_use")
        assert block["id"] == "call_preserved"
        assert "".join(event.get("delta", {}).get("partial_json", "") for event in events) == arguments
    else:
        call = next(event["item"] for event in events if event.get("type") == "response.output_item.done"
                    and event["item"]["type"] == "function_call")
        assert call["call_id"] == "call_preserved"
        assert call["arguments"] == arguments


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["json", "duplicate", "nan", "disconnect", "length", "unknown", "schema", "none", "no-tools", "parallel"])
def test_native_invalid_batch_never_exposes_executable_calls(monkeypatch, protocol, stream, failure):
    arguments = '{"path":"file.txt","content":"ok"}'
    overrides = {}
    finish = "tool_calls"
    name = "write"
    if failure == "json":
        arguments = '{"path":'
    elif failure == "duplicate":
        arguments = '{"path":"a","path":"b","content":"ok"}'
    elif failure == "nan":
        arguments = '{"path":"a","content":NaN}'
    elif failure == "disconnect":
        finish = None
    elif failure == "length":
        finish = "length"
    elif failure == "unknown":
        name = "unregistered"
    elif failure == "schema":
        arguments = '{"path":42,"content":"ok"}'
    elif failure == "none":
        overrides["tool_choice"] = {"type": "none"} if protocol == "messages" else "none"
    elif failure == "no-tools":
        overrides["tools"] = []
    elif failure == "parallel":
        if protocol == "messages":
            overrides["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        else:
            overrides["parallel_tool_calls"] = False
    _patch_native_upstream(monkeypatch, arguments, name=name, finish=finish, second=failure == "parallel")
    response = _native_request(protocol, stream, **overrides)
    if not stream:
        assert response.status_code == 502
        assert "error" in response.get_json()
        return
    assert response.status_code == 200
    events = _wire_events(response)
    assert any("error" in event or event.get("type") == "error" for event in events)
    assert not any(event.get("choices", [{}])[0].get("delta", {}).get("tool_calls") for event in events)
    assert not any(event.get("content_block", {}).get("type") == "tool_use" for event in events)
    assert not any(event.get("item", {}).get("type") == "function_call" for event in events)
    assert not any(event.get("type") in ("message_stop", "response.completed") for event in events)


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_invalid_schema_rejected_before_upstream(monkeypatch, protocol):
    called = []
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: called.append(True))
    schema = {"type": "not-a-json-schema-type"}
    tools = ([{"name": "write", "input_schema": schema}] if protocol == "messages" else
             [{"type": "function", "name": "write", "parameters": schema}] if protocol == "responses" else
             [{"type": "function", "function": {"name": "write", "parameters": schema}}])
    response = _native_request(protocol, False, tools=tools)
    assert response.status_code == 400
    assert called == []


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["schema", "unknown-batch", "none", "parallel"])
def test_text_tool_batch_uses_same_atomic_contract(monkeypatch, protocol, stream, failure):
    arguments = {"path": "a.txt", "content": "text"}
    if failure == "schema":
        arguments["path"] = 42
    calls = [{"operation": "write", "parameters": arguments}]
    overrides = {}
    if failure in ("unknown-batch", "parallel"):
        calls.append({"operation": "unknown" if failure == "unknown-batch" else "write", "parameters": arguments})
    if failure == "none":
        overrides["tool_choice"] = {"type": "none"} if protocol == "messages" else "none"
    elif failure == "parallel":
        if protocol == "messages":
            overrides["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        else:
            overrides["parallel_tool_calls"] = False
    _patch_upstream(monkeypatch, "@@ACTION@@" + json.dumps({"calls": calls}) + "@@END_ACTION@@")
    response = _native_request(protocol, stream, **overrides)
    if not stream:
        assert response.status_code == 502
        return
    events = _wire_events(response)
    assert any("error" in event or event.get("type") == "error" for event in events)
    assert not any(event.get("choices", [{}])[0].get("delta", {}).get("tool_calls") for event in events)
    assert not any(event.get("content_block", {}).get("type") == "tool_use" for event in events)
    assert not any(event.get("item", {}).get("type") == "function_call" for event in events)
    assert not any(event.get("type") in ("message_stop", "response.completed") for event in events)


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_protocol_lifecycle_precedes_model_request(monkeypatch, protocol):
    called = []
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: called.append(True))
    response = _native_request(protocol, True)
    first = next(iter(response.response)).decode()
    if protocol == "chat":
        assert json.loads(first[6:])["choices"][0]["delta"]["role"] == "assistant"
    elif protocol == "messages":
        assert "event: message_start" in first
    else:
        assert "event: response.created" in first
    assert called == []
    response.close()


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_named_choice_rejects_other_declared_tool(monkeypatch, protocol):
    _patch_native_upstream(monkeypatch, '{"path":"a","content":"b"}')
    if protocol == "messages":
        tools = [{"name": name, "input_schema": {"type": "object"}} for name in ("write", "other")]
        choice = {"type": "tool", "name": "other"}
    elif protocol == "responses":
        tools = [{"type": "function", "name": name, "parameters": {"type": "object"}} for name in ("write", "other")]
        choice = {"type": "function", "name": "other"}
    else:
        tools = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in ("write", "other")]
        choice = {"type": "function", "function": {"name": "other"}}
    response = _native_request(protocol, True, tools=tools, tool_choice=choice)
    events = _wire_events(response)
    assert any("error" in event or event.get("type") == "error" for event in events)
    assert not any(event.get("choices", [{}])[0].get("delta", {}).get("tool_calls") for event in events)
    assert not any(event.get("content_block", {}).get("type") == "tool_use" for event in events)
    assert not any(event.get("item", {}).get("type") == "function_call" for event in events)


def test_chat_history_without_current_tools_accepts_tool_result_turn(monkeypatch):
    _patch_upstream(monkeypatch, "The earlier operation completed.")
    response = _app().test_client().post("/v1/chat/completions", json={
        "model": "chatglm", "stream": False, "tools": [],
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_history", "type": "function",
                "function": {"name": "write", "arguments": '{"path":"a.txt","content":"done"}'},
            }]},
            {"role": "tool", "tool_call_id": "call_history", "content": "success"},
        ],
    })
    assert response.status_code == 200
    message = response.get_json()["choices"][0]["message"]
    assert message["content"] == "The earlier operation completed."
    assert not message.get("tool_calls")
