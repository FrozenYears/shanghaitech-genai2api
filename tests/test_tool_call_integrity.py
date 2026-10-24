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

        def iter_lines(self):
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


def _request(client, protocol, stream=False):
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
            "messages": [{"role": "user", "content": "Write the file"}],
            "tools": [{"type": "function", "function": {
                "name": "write", "description": "Write a file", "parameters": parameters,
            }}],
        })
    if protocol == "messages":
        return client.post("/v1/messages", json={
            "model": "chatglm",
            "stream": stream,
            "messages": [{"role": "user", "content": "Write the file"}],
            "tools": [{"name": "write", "description": "Write a file", "input_schema": parameters}],
        })
    return client.post("/v1/responses", json={
        "model": "chatglm",
        "stream": stream,
        "input": "Write the file",
        "tools": [{"type": "function", "name": "write", "description": "Write a file", "parameters": parameters}],
    })


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_tool_arguments_preserve_markup_and_escaped_unicode(monkeypatch, protocol):
    arguments = {
        "path": "notes/雪.txt",
        "content": 'before <think>keep exactly</think> </tool_call> <|DSML|call>\nquote: "keep"',
    }
    call = {"name": "write", "arguments": arguments}
    _patch_upstream(
        monkeypatch,
        "<tool_call>" + json.dumps(call, ensure_ascii=False) + "</tool_call>",
    )

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
def test_streamed_tool_arguments_preserve_markup_and_escaped_unicode(monkeypatch, protocol):
    arguments = {
        "path": "notes/雪.txt",
        "content": 'before <think>keep exactly</think> </tool_call> <|DSML|call>\nquote: "keep"',
    }
    call = {"name": "write", "arguments": arguments}
    _patch_upstream(
        monkeypatch,
        "<tool_call>" + json.dumps(call, ensure_ascii=False) + "</tool_call>",
    )

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
@pytest.mark.parametrize("failure", ["length", "invalid-json"])
def test_tool_call_failure_never_returns_success_or_executable_partial_call(
    monkeypatch, protocol, failure
):
    if failure == "length":
        call = {"name": "write", "arguments": {"path": "notes.txt", "content": "partial"}}
        finish_reason = "length"
        content = "<tool_call>" + json.dumps(call) + "</tool_call>"
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
