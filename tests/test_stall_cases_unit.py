import importlib.util
import json
from pathlib import Path

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "stall_cases", Path(__file__).resolve().parents[1] / "validation" / "native-first-endurance" / "stall_cases.py"
)
stall_cases = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(stall_cases)


def tool_event(case, name, parameters):
    return {"kind": "tool", "name": name, "parameters": parameters,
            **stall_cases.execute(case, name, parameters)}


def test_executor_records_exact_values_and_exposes_real_readback():
    case = stall_cases.make_case("pending", "executor")
    assert stall_cases.execute(case, "read_state", {"key": "missing"})["is_error"] is True
    assert stall_cases.execute(case, "commit_result", {})["is_error"] is True
    assert stall_cases.execute(case, "commit_result", {"value": "x", "extra": "x"})["is_error"] is True
    assert case["committed"] == []
    assert stall_cases.execute(case, "commit_result", {"value": "exact 雪"})["is_error"] is False
    assert stall_cases.execute(case, "read_state", {"key": "result"}) == {
        "output": "exact 雪", "is_error": False,
    }
    assert case["committed"] == ["exact 雪"]
    with pytest.raises(ValueError):
        stall_cases.execute(case, "unknown", {})
    with pytest.raises(TypeError):
        stall_cases.execute(case, "read_state", "left")
    with pytest.raises(TypeError):
        stall_cases.execute(case, "commit_result", {"value": 1})
    assert case["committed"] == ["exact 雪"]


@pytest.mark.parametrize("name", ["pending", "long_pending", "failed_last", "success_last", "completed", "qa"])
def test_history_receipts_can_be_reproduced_by_the_real_executor(name):
    case = stall_cases.make_case(name, "history")
    replay = {"state": {key: value for key, value in case["state"].items() if key != "result"},
              "committed": []}
    calls = {}
    for message in case["messages"]:
        for call in message.get("tool_calls", []):
            assert call["id"] not in calls
            calls[call["id"]] = call["function"]
        if message["role"] == "tool":
            call = calls[message["tool_call_id"]]
            receipt = stall_cases.execute(replay, call["name"], json.loads(call["arguments"]))
            assert {"output": message["content"], "is_error": message["is_error"]} == receipt
    assert replay["committed"] == case["committed"]


@pytest.mark.parametrize("name", ["pending", "long_pending", "failed_last", "success_last"])
def test_unfinished_normal_stop_is_not_success(name):
    result = stall_cases.verdict(stall_cases.make_case(name, "stop"), [{"kind": "stop", "text": "已完成"}])
    assert result["passed"] is False
    assert result["unexpected_stop"] is True


@pytest.mark.parametrize("name", ["pending", "long_pending", "failed_last", "success_last"])
def test_exact_commit_and_final_answer_complete_the_task(name):
    case = stall_cases.make_case(name, "complete")
    events = []
    if name in {"failed_last", "success_last"}:
        events.append(tool_event(case, "read_state", {"key": "right_backup"}))
    events.append(tool_event(case, "commit_result", {"value": case["expected"]}))
    assert stall_cases.verdict(case, events)["status"] == "incomplete"
    events.append({"kind": "stop", "text": "已提交。"})
    result = stall_cases.verdict(case, events)
    assert result["passed"] is True
    assert result["committed"] == [case["expected"]]
    json.dumps(result, ensure_ascii=False)


def test_failed_read_must_be_recovered_before_commit():
    case = stall_cases.make_case("failed_last", "recovery")
    events = [tool_event(case, "commit_result", {"value": case["expected"]}),
              {"kind": "stop", "text": "已提交。"}]
    assert stall_cases.verdict(case, events)["passed"] is False


@pytest.mark.parametrize("value", ["wrong", ""])
def test_nonempty_or_empty_wrong_commit_cannot_pass(value):
    case = stall_cases.make_case("pending", "wrong")
    events = [tool_event(case, "commit_result", {"value": value}), {"kind": "stop", "text": "完成"}]
    result = stall_cases.verdict(case, events)
    assert result["passed"] is False
    assert result["unexpected_stop"] is True
    assert result["committed"] == [value]


def test_duplicate_commit_preserves_both_side_effects_and_fails():
    case = stall_cases.make_case("pending", "duplicate")
    events = [tool_event(case, "commit_result", {"value": case["expected"]}) for _ in range(2)]
    events.append({"kind": "stop", "text": "已完成"})
    result = stall_cases.verdict(case, events)
    assert result["passed"] is False
    assert result["repeated_actions"] == 1
    assert result["committed"] == [case["expected"], case["expected"]]


def test_repeated_successful_read_does_not_count_as_progress():
    case = stall_cases.make_case("pending", "read-again")
    events = [tool_event(case, "read_state", {"key": "left"}),
              tool_event(case, "commit_result", {"value": case["expected"]}),
              {"kind": "stop", "text": "已完成"}]
    result = stall_cases.verdict(case, events)
    assert result["passed"] is False
    assert result["repeated_actions"] == 1


@pytest.mark.parametrize("name", ["completed", "qa"])
def test_negative_controls_need_only_a_final_answer(name):
    case = stall_cases.make_case(name, "control")
    assert stall_cases.verdict(case, [{"kind": "stop", "text": "4"}])["passed"] is True
    events = [tool_event(case, "read_state", {"key": "left"}), {"kind": "stop", "text": "4"}]
    result = stall_cases.verdict(case, events)
    assert result["passed"] is False
    assert result["unnecessary_actions"] == 1


def test_completed_commit_must_not_be_replayed():
    case = stall_cases.make_case("completed", "already-done")
    events = [tool_event(case, "commit_result", {"value": case["expected"]}),
              {"kind": "stop", "text": "已完成"}]
    result = stall_cases.verdict(case, events)
    assert result["passed"] is False
    assert result["repeated_actions"] == 1
    assert result["unnecessary_actions"] == 1
    assert result["committed"] == [case["expected"], case["expected"]]


def test_invalid_commit_and_fabricated_receipt_cannot_pass():
    case = stall_cases.make_case("pending", "invalid")
    events = [tool_event(case, "commit_result", {}), {"kind": "stop", "text": "已完成"}]
    assert stall_cases.verdict(case, events)["passed"] is False
    fabricated = [{"kind": "tool", "name": "commit_result", "parameters": {"value": case["expected"]},
                   "is_error": False, "output": "committed:" + case["expected"]},
                  {"kind": "stop", "text": "已完成"}]
    result = stall_cases.verdict(case, fabricated)
    assert result["passed"] is False
    assert result["errors"]


def test_empty_final_answer_and_transport_error_are_not_success():
    case = stall_cases.make_case("pending", "final")
    event = tool_event(case, "commit_result", {"value": case["expected"]})
    assert stall_cases.verdict(case, [event, {"kind": "stop", "text": " "}])["passed"] is False
    result = stall_cases.verdict(case, [event, {"kind": "error", "message": "transport failed"}])
    assert result["passed"] is False
    assert result["errors"]
