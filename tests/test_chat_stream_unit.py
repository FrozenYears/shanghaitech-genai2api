import json
import threading

from provider import genai
from provider.genai import stream_genai_response


class DummyTokenManager:
    def get_token(self):
        return "token"

    def force_refresh(self):
        return None


class DummyConfig:
    token_manager = DummyTokenManager()


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
    monkeypatch.setattr(genai, "heartbeat_events", lambda factory: heartbeat(factory, interval=0.01))
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
