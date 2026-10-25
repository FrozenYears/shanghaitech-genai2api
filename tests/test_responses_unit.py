import json
import pytest

from app import create_app
from config import Config
from provider import genai
from provider.responses import (
    responses_input_to_genai_messages,
    responses_tool_choice_to_chat,
    responses_tools_to_chat_tools,
    stream_genai_as_responses,
)


def _events(chunks):
    events = []
    for chunk in chunks:
        event_name = None
        data = None
        for line in chunk.strip().splitlines():
            if line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event_name and data is not None:
            events.append((event_name, data))
    return events


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


class DummyTokenManager:
    def get_token(self):
        return "token"

    def force_refresh(self):
        return None


class DummyConfig:
    token_manager = DummyTokenManager()


def _patch_upstream(monkeypatch, chunks):
    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda model, token: "xinference")
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: _fake_response(chunks))


# ---------------- 请求转换 ----------------

def test_responses_input_conversion_with_tools_and_reasoning_history():
    messages = responses_input_to_genai_messages(
        [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "你好"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "好的"}]},
            {"type": "function_call", "name": "shell", "arguments": '{"command":["ls"]}', "call_id": "call_1"},
            {"type": "function_call_output", "call_id": "call_1", "output": "file.txt"},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "思考中"}]},
            {"type": "message", "role": "user", "content": "继续"},
        ],
        instructions="你是助手",
    )

    assert messages[0] == {"role": "system", "content": "你是助手"}
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "@@ACTION@@" in messages[2]["content"]
    assert '"operation": "shell"' in messages[2]["content"]
    assert "<tool_call_id>call_1</tool_call_id>" in messages[3]["content"]
    assert "file.txt" in messages[3]["content"]
    assert "继续" in messages[3]["content"]
    # 历史 reasoning 不该重新喂给上游
    assert all("思考中" not in m["content"] for m in messages)


def test_responses_input_accepts_plain_string():
    messages = responses_input_to_genai_messages("直接提问")
    assert messages == [{"role": "user", "content": "直接提问"}]


def test_responses_tools_flatten_namespace_and_mark_custom():
    chat_tools, custom_names = responses_tools_to_chat_tools([
        {"type": "namespace", "name": "functions", "description": "", "tools": [
            {"type": "function", "name": "shell", "description": "run", "parameters": {"type": "object"}},
            {"type": "custom", "name": "apply_patch", "description": "patch",
             "format": {"type": "text"}},
        ]},
        {"type": "function", "name": "top_level", "description": "", "parameters": {}},
    ])

    assert [tool["function"]["name"] for tool in chat_tools] == ["functions.shell", "functions.apply_patch", "top_level"]
    assert custom_names == {"functions.apply_patch"}
    assert chat_tools[1]["function"]["parameters"]["required"] == ["input"]


def test_responses_tool_choice_mapping():
    assert responses_tool_choice_to_chat("none") == "none"
    assert responses_tool_choice_to_chat("required") == "required"
    assert responses_tool_choice_to_chat("auto") == "auto"
    assert responses_tool_choice_to_chat({"type": "function", "name": "shell"}) == {
        "type": "function",
        "function": {"name": "shell"},
    }


# ---------------- 流式事件 ----------------

def _run_stream(monkeypatch, chunks, **kwargs):
    _patch_upstream(monkeypatch, chunks)
    return _events(
        stream_genai_as_responses(
            [{"role": "user", "content": "看看目录"}],
            "chatglm",
            1000,
            DummyConfig(),
            **kwargs,
        )
    )


def test_responses_stream_text_and_reasoning_sequence(monkeypatch):
    events = _run_stream(monkeypatch, [
        {"choices": [{"delta": {"reasoning_content": "先看目录。"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "有两个文件。"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])

    names = [name for name, _ in events]
    assert names[0] == "response.created"
    assert names[1] == "response.in_progress"
    assert names[-1] == "response.completed"
    assert names.count("response.output_item.done") == 2

    sequence_numbers = [data["sequence_number"] for _, data in events]
    assert sequence_numbers == list(range(len(sequence_numbers)))

    completed = events[-1][1]["response"]
    assert completed["status"] == "completed"
    assert completed["object"] == "response"
    assert [item["type"] for item in completed["output"]] == ["reasoning", "message"]
    assert completed["usage"]["output_tokens"] > 0
    assert completed["usage"]["output_tokens_details"]["reasoning_tokens"] > 0

    reasoning_done = next(
        data for name, data in events if name == "response.reasoning_summary_text.done"
    )
    assert reasoning_done["summary_index"] == 0
    assert reasoning_done["item_id"] == completed["output"][0]["id"]
    assert reasoning_done["text"] == "先看目录。"


def test_responses_stream_function_call_arguments_are_json_string(monkeypatch):
    events = _run_stream(
        monkeypatch,
        [
            {"choices": [{"delta": {"content": '<tool_call>{"name": "shell", "arguments": {"command": ["ls"]}}</tool_call>'}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ],
        tools=[{"type": "function", "function": {"name": "shell", "parameters": {"type": "object"}}}],
    )

    done_item = next(
        data["item"] for name, data in events if name == "response.output_item.done"
    )
    assert done_item["type"] == "function_call"
    assert isinstance(done_item["arguments"], str)
    assert json.loads(done_item["arguments"]) == {"command": ["ls"]}
    assert done_item["call_id"]
    assert done_item["id"].startswith("fc_")

    arguments_done = next(
        data for name, data in events if name == "response.function_call_arguments.done"
    )
    assert arguments_done["arguments"] == done_item["arguments"]

    completed = events[-1][1]["response"]
    assert completed["output"] == [done_item]
    assert "<tool_call>" not in json.dumps(completed)


def test_responses_stream_custom_tool_call(monkeypatch):
    events = _run_stream(
        monkeypatch,
        [
            {"choices": [{"delta": {"content": '<tool_call>{"name": "apply_patch", "arguments": {"input": "*** Begin Patch"}}</tool_call>'}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ],
        tools=responses_tools_to_chat_tools([{"type": "custom", "name": "apply_patch"}])[0],
        custom_tool_names={"apply_patch"},
    )

    done_item = next(
        data["item"] for name, data in events if name == "response.output_item.done"
    )
    assert done_item["type"] == "custom_tool_call"
    assert done_item["name"] == "apply_patch"
    assert done_item["input"] == "*** Begin Patch"

    input_done = next(
        data for name, data in events if name == "response.custom_tool_call_input.done"
    )
    assert input_done["input"] == "*** Begin Patch"


def test_responses_stream_error_event(monkeypatch):
    _patch_upstream(monkeypatch, [{"success": False, "message": "额度上限了"}])
    events = _events(
        stream_genai_as_responses(
            [{"role": "user", "content": "hi"}], "chatglm", 100, DummyConfig()
        )
    )
    assert events[-1][0] == "error"
    assert "额度上限了" in events[-1][1]["message"]


# ---------------- 路由 ----------------

def test_responses_route_non_streaming(monkeypatch):
    _patch_upstream(monkeypatch, [
        {"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])

    app = create_app(
        Config(token_manager=DummyTokenManager(), port=0, api_key=None, debug=False, api_format="both")
    )
    client = app.test_client()

    response = client.post("/v1/responses", json={
        "model": "chatglm",
        "stream": False,
        "input": "你好",
    })

    assert response.status_code == 200
    data = response.get_json()
    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["output"][0]["content"][0]["text"] == "你好"


def test_responses_route_requires_input():
    app = create_app(
        Config(token_manager=DummyTokenManager(), port=0, api_key=None, debug=False, api_format="both")
    )
    client = app.test_client()

    response = client.post("/v1/responses", json={"model": "chatglm"})
    assert response.status_code == 400
    assert response.get_json()["error"]["type"] == "invalid_request_error"


def test_responses_stream_truncation_emits_incomplete(monkeypatch):
    events = _run_stream(monkeypatch, [
        {"choices": [{"delta": {"content": "半句"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    ])

    assert events[-1][0] == "response.incomplete"
    completed = events[-1][1]["response"]
    assert completed["status"] == "incomplete"
    assert completed["incomplete_details"] == {"reason": "max_output_tokens"}


def test_responses_completed_echoes_request_fields(monkeypatch):
    _patch_upstream(monkeypatch, [
        {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ])

    events = _events(stream_genai_as_responses(
        [{"role": "user", "content": "hi"}], "chatglm", 100, DummyConfig(),
        response_meta={
            "tools": [{"type": "function", "name": "shell"}],
            "tool_choice": "auto",
            "store": False,
            "parallel_tool_calls": False,
        },
    ))

    completed = events[-1][1]["response"]
    assert completed["tools"] == [{"type": "function", "name": "shell"}]
    assert completed["tool_choice"] == "auto"
    assert completed["store"] is False
    assert completed["parallel_tool_calls"] is False


@pytest.mark.parametrize("fields", [
    {"previous_response_id": "resp_old"},
    {"store": True},
    {"input": [{"type": "item_reference", "id": "item_old"}]},
    {"input": [{"type": "function_call", "id": "item_not_call_id", "name": "run", "arguments": "{}"}]},
    {"conversation": "conv_old"},
    {"background": True},
])
def test_stateless_responses_rejects_remote_state_before_upstream(monkeypatch, fields):
    called = []
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: called.append(True))
    app = create_app(Config(token_manager=DummyTokenManager(), port=0, api_key=None, debug=False, api_format="both"))
    response = app.test_client().post("/v1/responses", json={"model": "chatglm", "input": "hi", **fields})
    assert response.status_code == 400
    assert called == []


def test_native_custom_tool_retains_call_id_and_exact_free_text(monkeypatch):
    text = "*** Begin Patch\n@@ACTION@@ <think>literal</think>\n*** End Patch"
    events = _run_stream(monkeypatch, [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_patch", "type": "function",
            "function": {"name": "apply_patch", "arguments": json.dumps({"input": text})}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ], tools=responses_tools_to_chat_tools([{"type": "custom", "name": "apply_patch"}])[0],
       custom_tool_names={"apply_patch"}, tool_choice={"type": "function", "function": {"name": "apply_patch"}})
    result = events[-1][1]["response"]["output"][0]
    assert result["type"] == "custom_tool_call"
    assert result["call_id"] == "call_patch"
    assert result["input"] == text


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["function", "custom"])
def test_responses_namespace_roundtrip_and_named_choice(monkeypatch, stream, kind):
    arguments = '{"input":"exact patch"}' if kind == "custom" else '{"command":"pwd"}'
    _patch_upstream(monkeypatch, [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_namespaced", "type": "function",
            "function": {"name": "workspace.run", "arguments": arguments}}]}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])
    tool = {"type": kind, "name": "run"}
    if kind == "function":
        tool["parameters"] = {"type": "object"}
    app = create_app(Config(token_manager=DummyTokenManager(), port=0, api_key=None, debug=False, api_format="both"))
    response = app.test_client().post("/v1/responses", json={
        "model": "chatglm", "stream": stream, "input": "run",
        "tools": [{"type": "namespace", "name": "workspace", "tools": [tool]}],
        "tool_choice": {"type": kind, "name": "run", "namespace": "workspace"},
    })
    assert response.status_code == 200
    if stream:
        events = _events(response.get_data(as_text=True).split("\n\n"))
        data = events[-1][1]["response"]
    else:
        data = response.get_json()
    item = data["output"][0]
    assert item["name"] == "run"
    assert item["namespace"] == "workspace"
    assert item["call_id"] == "call_namespaced"
    if kind == "custom":
        assert item["input"] == "exact patch"
    else:
        assert item["arguments"] == arguments


def test_responses_custom_grammar_rejected_before_upstream(monkeypatch):
    called = []
    monkeypatch.setattr(genai.requests, "post", lambda *args, **kwargs: called.append(True))
    app = create_app(Config(token_manager=DummyTokenManager(), port=0, api_key=None, debug=False, api_format="both"))
    response = app.test_client().post("/v1/responses", json={
        "model": "chatglm", "input": "run",
        "tools": [{"type": "custom", "name": "patch", "format": {"type": "grammar", "syntax": "lark", "definition": "start: patch"}}],
    })
    assert response.status_code == 400
    assert called == []
