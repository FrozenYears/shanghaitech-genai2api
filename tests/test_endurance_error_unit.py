import json

import pytest
import requests

from app import create_app
from config import Config
from provider import genai


class Token:
    def get_token(self):
        return "test-token"

    def force_refresh(self):
        return None


def request_body(protocol, stream):
    schema = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}
    common = {"model": "local", "stream": stream}
    if protocol == "chat":
        return "/v1/chat/completions", {**common, "messages": [{"role": "user", "content": "Write the file"}], "tools": [{"type": "function", "function": {"name": "write", "parameters": schema}}]}
    if protocol == "messages":
        return "/v1/messages", {**common, "messages": [{"role": "user", "content": "Write the file"}], "tools": [{"name": "write", "input_schema": schema}], "max_tokens": 1000}
    return "/v1/responses", {**common, "input": "Write the file", "tools": [{"type": "function", "name": "write", "parameters": schema}]}


def patch_web(monkeypatch, *, content="", reasoning="", disconnect=False):
    closed = []

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": content, "reasoning_content": reasoning}, "finish_reason": None}]})).encode()
            if disconnect:
                raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'

        def close(self):
            closed.append(True)

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *a, **kw: Response())
    return closed


def error_from(response, stream):
    if not stream:
        assert response.status_code == 502
        return response.get_json()["error"]
    events = []
    raw = response.get_data(as_text=True)
    for line in raw.splitlines():
        if line.startswith("data: "):
            value = json.loads(line[6:])
            events.append(value)
    assert "[DONE]" not in raw
    assert "event: message_stop" not in raw
    assert "event: response.completed" not in raw
    assert not any(e.get("choices", [{}])[0].get("delta", {}).get("tool_calls") for e in events)
    failure = next(e for e in events if "error" in e or e.get("type") == "error")
    return failure.get("error", failure)


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reasoning", ["", "Only analysis, not a final answer"])
def test_empty_or_reasoning_only_turn_is_not_a_success(monkeypatch, protocol, stream, reasoning):
    closed = patch_web(monkeypatch, reasoning=reasoning)
    post = genai.requests.post
    attempts = []

    def counted_post(*args, **kwargs):
        attempts.append(True)
        return post(*args, **kwargs)

    monkeypatch.setattr(genai.requests, "post", counted_post)
    app = create_app(Config(Token(), 0, None, False, "both"))
    path, body = request_body(protocol, stream)
    response = app.test_client().post(path, json=body)
    failure = error_from(response, stream)
    assert failure["code"] == "upstream_empty_response"
    assert len(attempts) == (1 if reasoning else 2)
    response.close()
    assert closed


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_schema_failure_reports_constraint_without_argument_values(monkeypatch, protocol, stream):
    secret_path = "private-destination-never-echo"
    content = '@@ACTION@@' + json.dumps({"calls": [{"operation": "write", "parameters": {"path": secret_path}}]}) + '@@END_ACTION@@'
    patch_web(monkeypatch, content=content)
    app = create_app(Config(Token(), 0, None, False, "both"))
    path, body = request_body(protocol, stream)
    response = app.test_client().post(path, json=body)
    failure = error_from(response, stream)
    assert failure["code"] == "tool_arguments_invalid"
    assert "required" in failure["message"]
    assert "content" in failure["message"]
    assert secret_path not in failure["message"]


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_incomplete_markup_reports_parse_failure_not_empty_success(monkeypatch, protocol):
    patch_web(monkeypatch, content='@@ACTION@@{"calls":[')
    app = create_app(Config(Token(), 0, None, False, "both"))
    path, body = request_body(protocol, True)
    failure = error_from(app.test_client().post(path, json=body), True)
    assert failure["code"] == "tool_call_parse_error"


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_transport_failure_after_visible_text_does_not_complete_or_replay(monkeypatch, protocol):
    closed = patch_web(monkeypatch, content="I will inspect the file.", disconnect=True)
    app = create_app(Config(Token(), 0, None, False, "both"))
    path, body = request_body(protocol, True)
    response = app.test_client().post(path, json=body)
    failure = error_from(response, True)
    assert failure["code"] == "upstream_stream_error"
    response.close()
    assert closed


def test_fully_empty_stop_has_one_continuation_and_one_delivered_call(monkeypatch):
    questions = []
    closed = []
    action = '@@ACTION@@{"calls":[{"operation":"write","parameters":{"path":"x.txt","content":"雪"}}]}@@END_ACTION@@'

    class Response:
        status_code = 200

        def __init__(self, content):
            self.content = content

        def iter_lines(self, **kwargs):
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": self.content}, "finish_reason": None}]})).encode()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'

        def close(self):
            closed.append(self.content)

    def post(*args, **kwargs):
        questions.append(kwargs["json"]["chatInfo"])
        return Response("" if len(questions) == 1 else action)

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    app = create_app(Config(Token(), 0, None, False, "both"))
    path, body = request_body("chat", False)
    response = app.test_client().post(path, json=body)
    assert response.status_code == 200
    calls = response.get_json()["choices"][0]["message"]["tool_calls"]
    assert len(calls) == 1
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "x.txt", "content": "雪"}
    assert len(questions) == 2
    assert questions[1].startswith(questions[0]) and questions[1] != questions[0]
    assert "" in closed and action in closed


@pytest.mark.parametrize("mode", ["reasoning", "bad_schema", "partial", "length", "disconnect"])
def test_nonempty_or_failed_generation_is_never_replayed(monkeypatch, mode):
    requested = []
    action = '@@ACTION@@{"calls":[{"operation":"write","parameters":{"path":"x","content":"valid"}}]}@@END_ACTION@@'

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            content = ""
            if mode == "bad_schema":
                content = '@@ACTION@@{"calls":[{"operation":"write","parameters":{"path":"x"}}]}@@END_ACTION@@'
            elif mode == "partial":
                content = '@@ACTION@@{"calls":['
            elif mode == "length":
                content = action
            elif mode == "disconnect":
                content = "Already visible text"
            yield ("data: " + json.dumps({"choices": [{"delta": {
                "content": content, "reasoning_content": "Actual thinking" if mode == "reasoning" else "",
            }, "finish_reason": None}]})).encode()
            if mode == "disconnect":
                raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")
            yield ("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "length" if mode == "length" else "stop"}]})).encode()

        def close(self):
            pass

    def post(*args, **kwargs):
        requested.append(True)
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    app = create_app(Config(Token(), 0, None, False, "both"))
    path, body = request_body("chat", True)
    response = app.test_client().post(path, json=body)
    failure = error_from(response, True)
    assert failure["code"]
    assert requested == [True]
