import json
import threading
import queue
from types import SimpleNamespace

import pytest

from provider import streaming

from provider import genai
from provider.genai import stream_genai_response


class DummyTokenManager:
    def get_token(self):
        return "token"

    def force_refresh(self):
        return None


class DummyConfig:
    token_manager = DummyTokenManager()
    empty_continuations = 3
    tool_request_timeout_seconds = 600.0


def _fake_response(chunks):
    class FakeResponse:
        status_code = 200
        text = ""

        def iter_lines(self, **kwargs):
            for chunk in chunks:
                yield ("data: " + json.dumps(chunk)).encode()
        def close(self):
            pass

    return FakeResponse()


def _patch_upstream(monkeypatch, chunks):
    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda model, token: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: _fake_response(chunks))


def _data_chunks(raw_chunks):
    result = []
    for line in raw_chunks:
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            continue
        result.append(json.loads(payload))
    return result


def test_stream_genai_response_uses_fixed_id_and_keeps_reasoning(monkeypatch):
    _patch_upstream(monkeypatch, [
        {"choices": [{"delta": {"reasoning_content": "想一想"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "答案"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])

    chunks = _data_chunks(stream_genai_response(
        "提问", [{"role": "user", "content": "提问"}], "chatglm", 100, DummyConfig()
    ))

    assert {chunk["id"] for chunk in chunks} == {chunks[0]["id"]}
    reasoning = "".join(
        chunk["choices"][0]["delta"].get("reasoning_content", "") for chunk in chunks
    )
    content = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)
    assert reasoning == "想一想"
    assert content == "答案"
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_stream_genai_response_with_tools_keeps_reasoning_and_tool_calls(monkeypatch):
    _patch_upstream(monkeypatch, [
        {"choices": [{"delta": {"reasoning_content": "需要执行命令"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": '@@ACTION@@{"calls":[{"operation":"Bash","parameters":{"command":"pwd"}}]}@@END_ACTION@@'}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])

    chunks = _data_chunks(stream_genai_response(
        "跑一下", [{"role": "user", "content": "跑一下"}], "chatglm", 100,
        DummyConfig(), tools=[{"type": "function", "function": {"name": "Bash", "parameters": {"type": "object"}}}],
    ))

    reasoning = "".join(
        chunk["choices"][0]["delta"].get("reasoning_content", "") for chunk in chunks
    )
    assert reasoning == "需要执行命令"

    tool_call_deltas = [
        tc for chunk in chunks
        for tc in chunk["choices"][0]["delta"].get("tool_calls", [])
    ]
    names = [tc.get("function", {}).get("name") for tc in tool_call_deltas if tc.get("function", {}).get("name")]
    arguments = "".join(
        tc.get("function", {}).get("arguments", "") for tc in tool_call_deltas
    )
    assert names == ["Bash"]
    assert json.loads(arguments) == {"command": "pwd"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    # 工具标记不应泄漏到正文
    assert all(
        "@@ACTION@@" not in chunk["choices"][0]["delta"].get("content", "")
        for chunk in chunks
    )




def test_upstream_stream_without_finish_or_done_is_an_error(monkeypatch):
    class IncompleteResponse:
        status_code = 200
        text = ""

        def iter_lines(self, **kwargs):
            yield ("data: " + json.dumps({
                "choices": [{"delta": {"content": "partial"}, "finish_reason": None}]
            })).encode()

        def close(self):
            pass

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda model, token: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: IncompleteResponse())

    events = list(genai.iter_genai_stream("question", [], "chatglm", 100, DummyConfig()))

    assert [event["type"] for event in events] == ["delta", "error"]
    assert events[-1]["code"] == "upstream_stream_incomplete"


def test_chat_first_role_precedes_upstream_request(monkeypatch):
    requested = []
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: requested.append(True))
    stream = stream_genai_response("hi", [{"role": "user", "content": "hi"}], "chatglm", 100, DummyConfig())
    first = next(stream)
    assert json.loads(first[6:])["choices"][0]["delta"]["role"] == "assistant"
    assert requested == []
    stream.close()


def test_native_fragments_preserve_id_and_argument_bytes(monkeypatch):
    arguments = '{ "command": "echo @@ACTION@@ <think>literal</think> 雪" }'
    _patch_upstream(monkeypatch, [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_native", "type": "function",
            "function": {"name": "Ba", "arguments": arguments[:15]}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0,
            "function": {"name": "sh", "arguments": arguments[15:]}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])
    chunks = _data_chunks(stream_genai_response(
        "run", [{"role": "user", "content": "run"}], "chatglm", 100, DummyConfig(),
        tools=[{"type": "function", "function": {"name": "Bash", "parameters": {"type": "object"}}}],
    ))
    calls = [call for chunk in chunks for call in chunk.get("choices", [{}])[0].get("delta", {}).get("tool_calls", [])]
    assert calls == [{"index": 0, "id": "call_native", "type": "function",
                      "function": {"name": "Bash", "arguments": arguments}}]
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_waiting_stream_sends_heartbeat_and_close_releases_http_response(monkeypatch):
    released = threading.Event()
    producer_stopped = threading.Event()

    class BlockingResponse:
        status_code = 200
        text = ""

        def iter_lines(self, **kwargs):
            try:
                released.wait(2)
                yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            finally:
                producer_stopped.set()

        def close(self):
            released.set()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda model, token: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: BlockingResponse())
    heartbeat = genai.heartbeat_events
    monkeypatch.setattr(genai, "heartbeat_events", lambda factory, **kwargs: heartbeat(factory, interval=0.01, **kwargs))
    stream = stream_genai_response("hi", [{"role": "user", "content": "hi"}], "chatglm", 100, DummyConfig())
    assert '"role": "assistant"' in next(stream)
    assert next(stream) == ": keep-alive\n\n"
    assert not released.is_set()
    stream.close()
    assert released.is_set()
    assert producer_stopped.wait(1)


def test_native_call_not_emitted_until_upstream_completion(monkeypatch):
    completed = []

    def source(*args, **kwargs):
        yield {"type": "delta", "content": "safe prose"}
        assert not completed
        completed.append(True)
        yield {"type": "done", "finish_reason": "tool_calls", "tool_calls": [{
            "id": "call_waited", "type": "function", "function": {"name": "Bash", "arguments": '{"command":"pwd"}'},
        }]}

    monkeypatch.setattr(genai, "iter_genai_stream", source)
    stream = stream_genai_response("run", [{"role": "user", "content": "run"}], "chatglm", 100, DummyConfig(),
        tools=[{"type": "function", "function": {"name": "Bash", "parameters": {"type": "object"}}}])
    assert '"role": "assistant"' in next(stream)
    assert "safe prose" in next(stream)
    assert completed == []
    assert "call_waited" in next(stream)
    assert completed == [True]
    stream.close()


def test_no_tools_keeps_action_text_for_explicit_text_mode(monkeypatch):
    content = '@@ACTION@@{"calls":[{"operation":"echo","parameters":{"text":"literal"}}]}@@END_ACTION@@'
    _patch_upstream(monkeypatch, [
        {"choices": [{"delta": {"content": content[:17]}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": content[17:]}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])
    events = _data_chunks(stream_genai_response("hi", [{"role": "user", "content": "hi"}], "chatglm", 1000, DummyConfig()))
    assert "".join(event["choices"][0]["delta"].get("content", "") for event in events) == content
    assert not any(event["choices"][0]["delta"].get("tool_calls") for event in events)
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


def _controlled_polls(monkeypatch, ready):
    """只在消费者读空队列时推进时钟，不依赖线程调度或真实等待时长。"""
    clock = SimpleNamespace(value=100.0)
    workers = []

    def start_worker(*args, **kwargs):
        worker = threading.Thread(*args, **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(streaming, "threading", SimpleNamespace(
        Thread=start_worker, Event=threading.Event, Lock=threading.Lock,
    ))

    class ControlledQueue(queue.Queue):
        def get(self, block=True, timeout=None):
            assert ready.wait(2), "上游未到达可控阻塞点"
            try:
                return super().get(block=False)
            except queue.Empty:
                clock.value += 4
                raise

    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setattr(streaming, "queue", SimpleNamespace(Queue=ControlledQueue, Empty=queue.Empty, Full=queue.Full))
    return clock, workers


def test_stream_control_remaining_uses_one_absolute_deadline(monkeypatch):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock.value))
    bounded = streaming.StreamControl(timeout=10)
    unbounded = streaming.StreamControl()
    assert bounded.remaining() == 10
    clock.value += 7
    assert bounded.remaining() == 3
    clock.value += 50
    assert bounded.remaining() == 0
    assert unbounded.remaining() is None


@pytest.mark.parametrize("prefix", ["none", "text", "native", "markup"])
@pytest.mark.parametrize("cancel", [False, True])
def test_tool_deadline_and_cancellation_close_socket_and_drop_unvalidated_calls(monkeypatch, prefix, cancel):
    waiting = threading.Event()
    released = threading.Event()
    producer_stopped = threading.Event()
    shutdown = threading.Event()
    closed = threading.Event()
    _clock, workers = _controlled_polls(monkeypatch, waiting)
    requested = []

    class Socket:
        def shutdown(self, how):
            assert how == streaming.socket.SHUT_RDWR
            shutdown.set()
            released.set()

    class Response:
        status_code = 200
        raw = SimpleNamespace(_connection=SimpleNamespace(sock=Socket()))

        def iter_lines(self, **kwargs):
            try:
                if prefix == "text":
                    delta = {"content": "Already visible prose"}
                elif prefix == "native":
                    delta = {"tool_calls": [{"index": 0, "id": "call_late", "type": "function",
                                             "function": {"name": "Bash", "arguments": '{"command":"pwd"}'}}]}
                elif prefix == "markup":
                    delta = {"content": '@@ACTION@@{"calls":[{"operation":"Bash","parameters":{"command":"pwd"}}]}@@END_ACTION@@'}
                else:
                    delta = {}
                yield ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]})).encode()
                waiting.set()
                assert released.wait(2), "取消未释放已取得的响应"
                yield b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}'
            finally:
                producer_stopped.set()

        def close(self):
            closed.set()
            released.set()

    def post(*args, **kwargs):
        requested.append(True)
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    config = DummyConfig()
    config.tool_request_timeout_seconds = 10
    stream = stream_genai_response("run", [], "chatglm", 1000, config, tools=[
        {"type": "function", "function": {"name": "Bash", "parameters": {"type": "object"}}},
    ])
    assert '"role": "assistant"' in next(stream)
    chunks = []
    if cancel:
        while True:
            chunk = next(stream)
            chunks.append(chunk)
            if chunk == ": keep-alive\n\n":
                break
        stream.close()
    else:
        chunks = list(stream)
        assert chunks.count(": keep-alive\n\n") == 2
        errors = [chunk["error"] for chunk in _data_chunks(chunks) if "error" in chunk]
        assert [error["code"] for error in errors] == ["upstream_request_timeout"]
    assert shutdown.is_set()
    assert closed.is_set()
    assert producer_stopped.wait(2)
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert requested == [True]
    assert "[DONE]" not in "".join(chunks)
    assert not any(chunk.get("choices", [{}])[0].get("delta", {}).get("tool_calls") for chunk in _data_chunks(chunks))
    if prefix == "text":
        assert "Already visible prose" in "".join(chunks)


def test_timeout_before_response_headers_cleans_late_response_without_restarting(monkeypatch):
    headers_waiting = threading.Event()
    release_headers = threading.Event()
    post_returned = threading.Event()
    closed = threading.Event()
    iterated = []
    requested = []
    _clock, workers = _controlled_polls(monkeypatch, headers_waiting)

    class Response:
        status_code = 200

        def iter_lines(self, **kwargs):
            iterated.append(True)
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'

        def close(self):
            closed.set()

    def post(*args, **kwargs):
        requested.append(True)
        headers_waiting.set()
        assert release_headers.wait(2), "测试未释放尚未返回响应头的请求"
        post_returned.set()
        return Response()

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *a: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    config = DummyConfig()
    config.tool_request_timeout_seconds = 10
    try:
        events = list(genai.iter_genai_stream("run", [], "chatglm", 1000, config, recover_empty=True))
        assert [event["code"] for event in events if event["type"] == "error"] == ["upstream_request_timeout"]
        assert not post_returned.is_set()
        assert not closed.is_set()
    finally:
        release_headers.set()
    assert post_returned.wait(2)
    assert closed.wait(2)
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert requested == [True]
    assert iterated == []
