import copy
import json
from types import SimpleNamespace

import pytest
import requests

from app import create_app
from config import Config
from provider import genai, streaming
from tests.test_endurance_error_unit import Token, error_from, request_body


PARAMETERS = {"path": "result.txt", "content": "valid 雪"}
TOOLS = [{"type": "function", "function": {
    "name": "write", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"], "additionalProperties": False,
    },
}}]


def action(parameters):
    return "@@ACTION@@" + json.dumps({"calls": [{"operation": "write", "parameters": parameters}]}) + "@@END_ACTION@@"


def native(arguments, index=0, name="write"):
    return {"index": index, "id": "call_valid" if index == 0 else "call_second", "type": "function",
            "function": {"name": name, "arguments": arguments}}


def generation(*, content="", reasoning="", calls=None, finish="stop", failure=None):
    return {"content": content, "reasoning_content": reasoning, "tool_calls": calls or [],
            "finish": finish, "failure": failure}


def patch_generations(monkeypatch, generations):
    requests_seen = []
    closed = []

    class Response:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def iter_lines(self, **kwargs):
            if isinstance(self.data["failure"], dict):
                yield ("data: " + json.dumps(self.data["failure"])).encode()
                return
            delta = {key: self.data[key] for key in ("content", "reasoning_content", "tool_calls")}
            yield ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": None}]})).encode()
            if self.data["failure"]:
                raise self.data["failure"]
            yield ("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": self.data["finish"]}]})).encode()

        def close(self):
            closed.append(self.data)

    def post(*args, **kwargs):
        requests_seen.append(copy.deepcopy(kwargs["json"]))
        return Response(generations[min(len(requests_seen) - 1, len(generations) - 1)])

    monkeypatch.setattr(genai.model_registry, "get_root_ai_type", lambda *args: "xinference")
    monkeypatch.setattr(genai.requests, "post", post)
    return requests_seen, closed


def calls_from(response, protocol, stream):
    assert response.status_code == 200
    if not stream:
        data = response.get_json()
        if protocol == "chat":
            return [json.loads(call["function"]["arguments"]) for call in data["choices"][0]["message"].get("tool_calls", [])]
        if protocol == "messages":
            return [block["input"] for block in data["content"] if block["type"] == "tool_use"]
        return [json.loads(item["arguments"]) for item in data["output"] if item["type"] == "function_call"]
    raw = response.get_data(as_text=True)
    events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ") and line[6:] != "[DONE]"]
    assert not any("error" in event or event.get("type") == "error" for event in events)
    if protocol == "chat":
        assert raw.endswith("data: [DONE]\n\n")
        return [json.loads(call["function"]["arguments"]) for event in events
                for call in event.get("choices", [{}])[0].get("delta", {}).get("tool_calls", [])]
    if protocol == "messages":
        assert events[-1]["type"] == "message_stop"
        starts = [event for event in events if event.get("content_block", {}).get("type") == "tool_use"]
        arguments = [json.loads(event["delta"]["partial_json"]) for event in events
                     if event.get("delta", {}).get("type") == "input_json_delta"]
        assert len(starts) == len(arguments)
        return arguments
    assert events[-1]["type"] == "response.completed"
    return [json.loads(event["item"]["arguments"]) for event in events
            if event.get("type") == "response.output_item.done" and event["item"]["type"] == "function_call"]


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["missing_path", "bad_json", "bad_member", "native_json", "native_schema", "native_index", "native_fragment"])
def test_undelivered_invalid_batch_repairs_to_one_call(monkeypatch, protocol, stream, failure):
    bad = generation(content=action({"content": "secret-body"}))
    if failure == "bad_json":
        bad = generation(content='@@ACTION@@{"calls":invalid}@@END_ACTION@@')
    elif failure == "bad_member":
        bad = generation(content='@@ACTION@@' + json.dumps({"calls": [
            {"operation": "write", "parameters": PARAMETERS}, {"operation": "write", "parameters": {"content": "secret-body"}},
        ]}) + '@@END_ACTION@@')
    elif failure == "native_json":
        bad = generation(calls=[native('{"path":')], finish="tool_calls")
    elif failure == "native_schema":
        bad = generation(calls=[native('{"content":"secret-body"}')], finish="tool_calls")
    elif failure == "native_index":
        bad = generation(calls=[native(json.dumps(PARAMETERS), index=1)], finish="tool_calls")
    elif failure == "native_fragment":
        bad = generation(calls=[{"index": -1}], finish="stop")
    requests_seen, closed = patch_generations(monkeypatch, [bad, generation(calls=[native(json.dumps(PARAMETERS))], finish="tool_calls")])
    client = create_app(Config(Token(), 0, None, False)).test_client()
    path, body = request_body(protocol, stream)
    response = client.post(path, json=body)
    assert calls_from(response, protocol, stream) == [PARAMETERS]
    assert len(requests_seen) == len(closed) == 2
    assert "1/3" in requests_seen[1]["chatInfo"]
    assert "尚未交付" in requests_seen[1]["chatInfo"] and "没有执行" in requests_seen[1]["chatInfo"]
    assert "secret-body" not in requests_seen[1]["chatInfo"]
    assert requests_seen[1]["messages"] == requests_seen[0]["messages"]


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("prefix", ["content", "reasoning"])
@pytest.mark.parametrize("failure", ["bad_json", "missing_i", "trailing_text"])
def test_visible_progress_does_not_prevent_undelivered_tool_repair(monkeypatch, protocol, stream, prefix, failure):
    parameters = {**PARAMETERS, "i": "Updating the fixture"}
    valid = "@@ACTION@@" + json.dumps({"calls": [{"operation": "edit", "parameters": parameters}]}) + "@@END_ACTION@@"
    if failure == "bad_json":
        bad = '@@ACTION@@{"calls":invalid}@@END_ACTION@@'
    elif failure == "missing_i":
        bad = "@@ACTION@@" + json.dumps({"calls": [{"operation": "edit", "parameters": PARAMETERS}]}) + "@@END_ACTION@@"
    else:
        bad = valid + "\nextra text after envelope"
    requests_seen, _ = patch_generations(monkeypatch, [
        generation(content=("Visible progress. " if prefix == "content" else "") + bad,
                   reasoning="Visible analysis." if prefix == "reasoning" else ""),
        generation(content=valid),
    ])
    path, body = request_body(protocol, stream)
    tool = body["tools"][0]["function"] if protocol == "chat" else body["tools"][0]
    tool["name"] = "edit"
    schema = tool["input_schema" if protocol == "messages" else "parameters"]
    schema["properties"]["i"] = {"type": "string"}
    schema["required"].append("i")
    response = create_app(Config(Token(), 0, None, False)).test_client().post(path, json=body)
    assert calls_from(response, protocol, stream) == [parameters]
    visible = response.get_data(as_text=True)
    progress = visible
    if protocol == "responses" and stream:
        event_type = "response.output_text.delta" if prefix == "content" else "response.reasoning_summary_text.delta"
        events = [json.loads(line[6:]) for line in visible.splitlines() if line.startswith("data: ")]
        progress = "".join(event.get("delta", "") for event in events if event.get("type") == event_type)
    assert progress.count("Visible progress." if prefix == "content" else "Visible analysis.") == 1
    assert "@@ACTION@@" not in visible and "extra text after envelope" not in visible
    assert len(requests_seen) == 2
    assert requests_seen[1]["messages"] == requests_seen[0]["messages"]


@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("kind", ["network", "length", "native_length", "upstream_error"])
def test_unsafe_or_failed_generation_is_not_replayed(monkeypatch, protocol, stream, kind):
    bad = generation(content=action({"content": "hidden"}))
    if kind == "network":
        bad["failure"] = requests.exceptions.ChunkedEncodingError("broken transport")
        expected = "upstream_stream_error"
    elif kind == "length":
        bad = generation(content='@@ACTION@@{"calls":[', finish="length")
        expected = "tool_call_parse_error"
    elif kind == "native_length":
        bad = generation(calls=[native('{"path":')], finish="length")
        expected = "tool_contract_error"
    elif kind == "upstream_error":
        bad["failure"] = {"success": False, "message": "upstream unavailable"}
        expected = "upstream_error"
    requests_seen, _ = patch_generations(monkeypatch, [bad, generation(content=action(PARAMETERS))])
    path, body = request_body(protocol, stream)
    response = create_app(Config(Token(), 0, None, False)).test_client().post(path, json=body)
    assert error_from(response, stream)["code"] == expected
    assert len(requests_seen) == 1


@pytest.mark.parametrize("budget", [0, 1, 3])
@pytest.mark.parametrize("protocol", ["chat", "messages", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_repair_exhaustion_retains_specific_terminal_code(monkeypatch, budget, protocol, stream):
    requests_seen, _ = patch_generations(monkeypatch, [generation(content=action({"content": "secret"}))])
    path, body = request_body(protocol, stream)
    response = create_app(Config(Token(), 0, None, False, tool_repair_attempts=budget)).test_client().post(path, json=body)
    error = error_from(response, stream)
    assert error["code"] == "tool_arguments_invalid"
    assert "exhausted" in error["message"]
    assert len(requests_seen) == budget + 1


@pytest.mark.parametrize("terminal, expected", [("good", None), ("empty", "upstream_empty_response"), ("bad", "tool_arguments_invalid")])
def test_interleaved_recoveries_have_independent_counts_and_one_deadline(monkeypatch, terminal, expected):
    clock = [0.0]
    controls = []
    windows = []
    questions = []
    items = ["empty", "bad", "empty", "bad", terminal]
    history = [{"role": "system", "content": "Exact schema and rules"},
               {"role": "assistant", "tool_calls": [{"id": "executed", "type": "function", "function": {"name": "write", "arguments": json.dumps(PARAMETERS)}}]},
               {"role": "tool", "tool_call_id": "executed", "content": "Real previous result"}]
    original = copy.deepcopy(history)
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def bridge(factory, timeout=None):
        windows.append(timeout)
        control = streaming.StreamControl(timeout)
        try:
            yield from factory(control)
        finally:
            control.close()

    def upstream(question, messages, model, max_tokens, config, token, options, control):
        controls.append((control, control.deadline, control.remaining()))
        questions.append(question)
        assert messages == original and model == "chosen" and options == {"temperature": 0.2}
        item = items[len(questions) - 1]
        clock[0] += 1
        if item != "empty":
            yield {"type": "delta", "content": action(PARAMETERS if item == "good" else {"content": "secret"})}
        yield {"type": "done", "finish_reason": "stop", "tool_calls": []}

    monkeypatch.setattr(genai, "heartbeat_events", bridge)
    monkeypatch.setattr(genai, "_iter_genai_stream", upstream)
    config = Config(Token(), 0, None, False, empty_continuations=2, tool_repair_attempts=2)
    events = list(genai.iter_genai_stream("Original question", history, "chosen", 1000, config,
                                        upstream_options={"temperature": 0.2}, recover_empty=True, tools=TOOLS))
    assert len(questions) == 5
    assert windows == [900]
    assert len({id(control) for control, _, _ in controls}) == 1
    assert [deadline for _, deadline, _ in controls] == [900] * 5
    assert [remaining for _, _, remaining in controls] == [900, 899, 898, 897, 896]
    assert "1/2" in questions[1] and "1/2" in questions[2]
    assert "2/2" in questions[3] and "2/2" in questions[4]
    assert all(question.startswith("Original question") for question in questions)
    assert history == original
    if expected:
        assert events[-1]["code"] == expected and "exhausted" in events[-1]["message"]
    else:
        assert events[-1]["type"] == "done" and len(events[-1]["tool_calls"]) == 1


def test_cancelled_bad_generation_does_not_start_repair(monkeypatch):
    requested = []

    def bridge(factory, timeout=None):
        yield from factory(streaming.StreamControl(timeout))

    def upstream(*args):
        requested.append(True)
        control = args[-1]
        yield {"type": "delta", "content": action({"content": "secret"})}
        control.close()
        yield {"type": "done", "finish_reason": "stop", "tool_calls": []}

    monkeypatch.setattr(genai, "heartbeat_events", bridge)
    monkeypatch.setattr(genai, "_iter_genai_stream", upstream)
    events = list(genai.iter_genai_stream("question", [], "model", 1000, Config(Token(), 0, None, False), tools=TOOLS))
    assert events == [] and requested == [True]


@pytest.mark.parametrize("arguments", ['{"private-sensitive-key":1,"private-sensitive-key":2}',
                                        '{"path":"private-path","content":"private-value","private-key":true}',
                                        '{"path":42,"content":"private-value"}'])
def test_repair_feedback_never_echoes_model_parameter_values_or_keys(monkeypatch, arguments, caplog):
    requests_seen, _ = patch_generations(monkeypatch, [generation(calls=[native(arguments)], finish="stop"),
                                                      generation(content=action(PARAMETERS))])
    events = list(genai.iter_genai_stream("Original question", [], "model", 1000, Config(Token(), 0, None, False), tools=TOOLS))
    assert events[-1]["type"] == "done" and len(requests_seen) == 2
    feedback = requests_seen[1]["chatInfo"]
    for secret in ("private-sensitive-key", "private-path", "private-value", "private-key"):
        assert secret not in feedback and secret not in caplog.text


def test_auto_plain_text_plan_is_final_without_an_action_requirement(monkeypatch):
    requests_seen, _ = patch_generations(monkeypatch, [generation(content="I plan to inspect the file.")])
    events = list(genai.iter_genai_stream("question", [], "model", 1000, Config(Token(), 0, None, False), tools=TOOLS))
    assert len(requests_seen) == 1
    assert events[-1]["type"] == "done" and events[-1]["tool_calls"] == []


def test_required_tool_repairs_after_visible_plan_without_repeating_it(monkeypatch):
    requests_seen, _ = patch_generations(monkeypatch, [
        generation(content="Visible plan."), generation(content=action(PARAMETERS)),
    ])
    events = list(genai.iter_genai_stream("question", [], "model", 1000, Config(Token(), 0, None, False),
                                        tools=TOOLS, tool_choice="required"))
    assert len(requests_seen) == 2 and events[-1]["type"] == "done"
    assert len(events[-1]["tool_calls"]) == 1
    assert sum(event.get("content") == "Visible plan." for event in events) == 1


@pytest.mark.parametrize("first", ["empty", "bad"])
def test_exhausted_shared_deadline_prevents_any_next_generation(monkeypatch, first):
    clock = [0.0]
    requested = []
    monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def bridge(factory, timeout=None):
        yield from factory(streaming.StreamControl(timeout))

    def upstream(*args):
        requested.append(True)
        if first == "bad":
            yield {"type": "delta", "content": action({"content": "secret"})}
        clock[0] = 2
        yield {"type": "done", "finish_reason": "stop", "tool_calls": []}

    monkeypatch.setattr(genai, "heartbeat_events", bridge)
    monkeypatch.setattr(genai, "_iter_genai_stream", upstream)
    config = Config(Token(), 0, None, False, tool_request_timeout_seconds=1)
    events = list(genai.iter_genai_stream("question", [], "model", 1000, config, recover_empty=True, tools=TOOLS))
    assert events[-1]["code"] == "upstream_request_timeout"
    assert requested == [True]
