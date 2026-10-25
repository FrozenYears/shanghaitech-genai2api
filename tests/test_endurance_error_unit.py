import json
import copy
from types import SimpleNamespace
import queue

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
@pytest.mark.parametrize("budget", [0, 1, 3])
@pytest.mark.parametrize("reasoning", ["", "Only analysis, not a final answer"])
def test_empty_or_reasoning_only_turn_is_not_a_success(monkeypatch, protocol, stream, reasoning, budget):
    closed = patch_web(monkeypatch, reasoning=reasoning)
    post = genai.requests.post
    attempts = []

    def counted_post(*args, **kwargs):
        attempts.append(True)
        return post(*args, **kwargs)

    monkeypatch.setattr(genai.requests, "post", counted_post)
    app = create_app(Config(Token(), 0, None, False, "both", empty_continuations=budget))
    path, body = request_body(protocol, stream)
    response = app.test_client().post(path, json=body)
    failure = error_from(response, stream)
    assert failure["code"] == "upstream_empty_response"
    assert len(attempts) == (1 if reasoning else budget + 1)
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


@pytest.mark.parametrize("budget", [0, 1, 3])
def test_empty_continuation_budget_delivers_one_complete_batch(monkeypatch, budget):
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
        return Response("" if len(questions) <= budget else action)

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    app = create_app(Config(Token(), 0, None, False, "both", empty_continuations=budget))
    path, body = request_body("chat", False)
    response = app.test_client().post(path, json=body)
    assert response.status_code == 200
    calls = response.get_json()["choices"][0]["message"]["tool_calls"]
    assert len(calls) == 1
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "x.txt", "content": "雪"}
    assert len(questions) == budget + 1
    assert all(question.startswith(questions[0]) and question != questions[0] for question in questions[1:])
    assert len(set(questions)) == budget + 1
    assert len(closed) == budget + 1
    assert closed.count(action) == 1


@pytest.mark.parametrize("mode, expected_code", [
    ("reasoning", "upstream_empty_response"),
    ("bad_schema", "tool_arguments_invalid"),
    ("partial", "tool_call_parse_error"),
    ("length", "tool_contract_error"),
    ("disconnect", "upstream_stream_error"),
    ("empty_disconnect", "upstream_stream_error"),
    ("request_failure", "upstream_stream_error"),
    ("bad_json", "tool_call_parse_error"),
    ("empty_incomplete", "upstream_stream_incomplete"),
])
def test_nonempty_or_failed_generation_is_never_replayed_with_repair_disabled(monkeypatch, mode, expected_code):
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
            elif mode == "bad_json":
                content = '@@ACTION@@{"calls":invalid}@@END_ACTION@@'
            elif mode == "length":
                content = action
            elif mode == "disconnect":
                content = "Already visible text"
            yield ("data: " + json.dumps({"choices": [{"delta": {
                "content": content, "reasoning_content": "Actual thinking" if mode == "reasoning" else "",
            }, "finish_reason": None}]})).encode()
            if mode in ("disconnect", "empty_disconnect"):
                raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")
            if mode == "empty_incomplete":
                return
            yield ("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "length" if mode == "length" else "stop"}]})).encode()

        def close(self):
            pass

    def post(*args, **kwargs):
        requested.append(True)
        if mode == "request_failure":
            raise requests.exceptions.ConnectionError("Connection refused")
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    app = create_app(Config(Token(), 0, None, False, "both", tool_repair_attempts=0))
    path, body = request_body("chat", True)
    response = app.test_client().post(path, json=body)
    failure = error_from(response, True)
    assert failure["code"] == expected_code
    assert requested == [True]


def test_visible_text_is_delivered_without_empty_continuation(monkeypatch):
    patch_web(monkeypatch, content="The task is complete.")
    post = genai.requests.post
    requested = []

    def counted_post(*args, **kwargs):
        requested.append(True)
        return post(*args, **kwargs)

    monkeypatch.setattr(genai.requests, "post", counted_post)
    app = create_app(Config(Token(), 0, None, False, "both", empty_continuations=3))
    path, body = request_body("chat", False)
    response = app.test_client().post(path, json=body)
    assert response.status_code == 200
    assert response.get_json()["choices"][0]["message"]["content"] == "The task is complete."
    assert requested == [True]


def test_each_continuation_keeps_original_history_model_and_task(monkeypatch):
    history = [
        {"role": "system", "content": "Preserve exact bytes: 雪"},
        {"role": "user", "content": "Inspect first"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "already_executed", "type": "function",
            "function": {"name": "read", "arguments": '{"path":"a.txt"}'},
        }]},
        {"role": "tool", "tool_call_id": "already_executed", "content": "Previously executed result"},
    ]
    original = copy.deepcopy(history)
    question = "Use the result to write the final file"
    requested = []
    closed = []

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            delta = {} if len(requested) < 4 else {"content": "Final answer"}
            yield ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": "stop"}]})).encode()

        def close(self):
            closed.append(True)

    def post(*args, **kwargs):
        requested.append(copy.deepcopy(kwargs["json"]))
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    config = Config(Token(), 0, None, False, empty_continuations=3)
    events = list(genai.iter_genai_stream(question, history, "chosen-model", 1000, config,
                                         upstream_options={"temperature": 0.25}, recover_empty=True))
    assert events[-1]["type"] == "done"
    assert "".join(event.get("content", "") for event in events) == "Final answer"
    assert len(requested) == len(closed) == 4
    assert history == original
    assert all(request["messages"] == original for request in requested)
    assert all(request["aiType"] == "chosen-model" and request["temperature"] == 0.25 for request in requested)
    assert requested[0]["chatInfo"] == question
    questions = [request["chatInfo"] for request in requested[1:]]
    assert all(value.startswith(question) and value != question for value in questions)
    assert len(set(questions)) == 3
    assert all(earlier not in later for earlier, later in zip(questions, questions[1:]))


@pytest.mark.parametrize("budget, expiry_attempt", [(0, 1), (1, 1), (3, 1), (1, 2), (3, 2)])
def test_total_generation_window_is_shared_and_stops_new_requests(monkeypatch, budget, expiry_attempt):
    from provider import streaming

    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock.value))
    requested = []
    closed = []

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            clock.value += 12 if expiry_attempt == 1 else 6
            delta = {} if len(requested) < expiry_attempt else {
                "content": '@@ACTION@@{"calls":[{"operation":"write","parameters":{"path":"x","content":"late"}}]}@@END_ACTION@@',
            }
            yield ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": "stop"}]})).encode()

        def close(self):
            closed.append(True)

    def post(*args, **kwargs):
        requested.append(True)
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    app = create_app(Config(Token(), 0, None, False, "both", empty_continuations=budget,
                            tool_request_timeout_seconds=10))
    path, body = request_body("chat", True)
    response = app.test_client().post(path, json=body)
    failure = error_from(response, True)
    assert failure["code"] == "upstream_request_timeout"
    response.close()
    assert len(requested) == expiry_attempt
    assert len(closed) >= expiry_attempt


@pytest.mark.parametrize("content", ["", "Plain answer"])
def test_no_tool_generation_has_no_continuations_or_tool_deadline(monkeypatch, content):
    from provider import streaming

    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock.value))
    requested = []
    closed = []

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            clock.value += 1000
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]})).encode()

        def close(self):
            closed.append(True)

    def post(*args, **kwargs):
        requested.append(True)
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    config = Config(Token(), 0, None, False, empty_continuations=3, tool_request_timeout_seconds=1)
    app = create_app(config)
    response = app.test_client().post("/v1/chat/completions", json={
        "model": "chatglm", "messages": [{"role": "user", "content": "question"}], "max_tokens": 1000,
    })
    if content:
        assert response.status_code == 200
        assert response.get_json()["choices"][0]["message"]["content"] == content
    else:
        assert response.status_code == 502
        assert response.get_json()["error"]["code"] == "upstream_empty_response"
    assert requested == [True]
    assert closed


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_upstream_pings_do_not_extend_tool_generation_window(monkeypatch, protocol):
    from provider import streaming

    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock.value))
    requested = []
    closed = []

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            for _ in range(3):
                clock.value += 4
                yield b": upstream-ping"
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'

        def close(self):
            closed.append(True)

    def post(*args, **kwargs):
        requested.append(True)
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    app = create_app(Config(Token(), 0, None, False, "both", empty_continuations=3,
                            tool_request_timeout_seconds=10))
    path, body = request_body(protocol, True)
    response = app.test_client().post(path, json=body)
    assert error_from(response, True)["code"] == "upstream_request_timeout"
    response.close()
    assert requested == [True]
    assert closed


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
def test_completed_tool_batch_queued_until_deadline_is_not_delivered(monkeypatch, protocol):
    from provider import streaming

    clock = SimpleNamespace(value=100.0)

    class DeadlineOnDeliveryQueue(queue.Queue):
        def get(self, block=True, timeout=None):
            event = super().get(block=block, timeout=timeout)
            if isinstance(event, dict) and event.get("type") == "done":
                clock.value += 10
            return event

    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setattr(streaming, "queue", SimpleNamespace(
        Queue=DeadlineOnDeliveryQueue, Empty=queue.Empty, Full=queue.Full,
    ))

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"Visible before deadline."},"finish_reason":null}]}'
            yield ("data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "late", "type": "function",
                "function": {"name": "write", "arguments": '{"path":"x","content":"late"}'},
            }]}, "finish_reason": None}]})).encode()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}'

        def close(self):
            pass

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *a, **kw: Response())
    app = create_app(Config(Token(), 0, None, False, "both", tool_request_timeout_seconds=10))
    path, body = request_body(protocol, True)
    response = app.test_client().post(path, json=body)
    assert error_from(response, True)["code"] == "upstream_request_timeout"
    assert "Visible before deadline." in response.get_data(as_text=True)
    response.close()
