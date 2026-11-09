import json
import threading
from contextlib import contextmanager
import time

import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.task_client import TaskClient


TOOLS = [{"type": "function", "function": {
    "name": "write_file", "parameters": {
        "type": "object", "properties": {"content": {"type": "string"}},
        "required": ["content"], "additionalProperties": False,
    },
}}]


def delta(content=None, calls=None, finish=None, reasoning=None):
    value = {}
    if content is not None:
        value["content"] = content
    if calls is not None:
        value["tool_calls"] = calls
    if reasoning is not None:
        value["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "delta": value, "finish_reason": finish}]}


def call(name, parameters, call_id="call-1", index=0):
    return {"index": index, "id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(parameters, ensure_ascii=False),
    }}


def response(name, parameters, call_id="call-1"):
    return [delta(calls=[call(name, parameters, call_id)]), delta(finish="tool_calls"), "[DONE]"]


@contextmanager
def backend(scripts):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append({"path": self.path, "body": json.loads(body)})
            script = scripts[len(requests) - 1] if len(requests) <= len(scripts) else []
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                for item in script:
                    if callable(item):
                        item()
                        continue
                    if isinstance(item, bytes):
                        self.wfile.write(item)
                    else:
                        payload = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                        self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def file_executor(path):
    def execute(name, parameters):
        assert name == "write_file"
        path.write_text(parameters["content"], encoding="utf-8")
        return {"output": "已写入", "is_error": False}
    return execute


def file_checker(path):
    def verify(remaining):
        assert remaining > 0
        passed = path.exists() and path.read_text(encoding="utf-8") == "验收通过\n"
        return {"passed": passed, "missing": [] if passed else ["文件内容不符"]}
    return verify


def test_plan_then_real_file_action_requires_independent_completion(tmp_path):
    path = tmp_path / "result.txt"
    events = []
    initial = [{"role": "system", "content": "遵守任务约束"}]
    scripts = [
        [delta(content="先规划，再写文件。", reasoning="检查目标"), delta(finish="stop"), "[DONE]"],
        response("write_file", {"content": "验收通过\n"}, "write-1"),
        response("task_complete", {"summary": "文件已完成"}, "complete-1"),
    ]
    with backend(scripts) as (url, requests):
        result = TaskClient(url + "/v1/", "test-key", "test-model").run(
            "生成指定文件并验收", TOOLS, file_executor(path), file_checker(path),
            messages=initial, on_event=events.append,
        )
    assert result["status"] == "completed"
    assert result["passed"] is True
    assert result["requests"] == 3
    assert path.read_text(encoding="utf-8") == "验收通过\n"
    assert initial == [{"role": "system", "content": "遵守任务约束"}]
    assert any(event.get("text") == "先规划，再写文件。" for event in events)
    assert any(event["type"] == "reasoning" for event in events)
    tool_messages = [message for message in result["history"] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["write-1", "complete-1"]
    assert tool_messages[0]["content"] == "已写入"
    assert tool_messages[0]["is_error"] is False


def test_failed_independent_check_returns_real_gap_then_continues(tmp_path):
    path = tmp_path / "result.txt"
    scripts = [response("task_complete", {"summary": "我认为完成了"}, "premature"),
               response("write_file", {"content": "验收通过\n"}, "write"),
               response("task_complete", {"summary": "已修复"}, "final")]
    with backend(scripts) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path))
    assert result["status"] == "completed"
    feedback = next(message for message in requests[1]["body"]["messages"]
                    if message.get("tool_call_id") == "premature")
    assert feedback["is_error"] is True
    assert json.loads(feedback["content"]) == {"passed": False, "missing": ["文件内容不符"]}
    assert result["verification"] == {"passed": True, "missing": []}


@pytest.mark.parametrize("script", [
    [delta(calls=[call("write_file", {"content": "错误落盘"}),
                   call("task_complete", {"summary": "完成"}, "other", 1)]),
     delta(finish="tool_calls"), "[DONE]"],
    [delta(calls=[call("write_file", {"content": "错误落盘"}),
                   call("write_file", {"content": 3}, "bad", 1)]),
     delta(finish="tool_calls"), "[DONE]"],
    response("write_file", {"content": "不允许额外字段", "extra": True}),
    response("unknown", {"content": "未知工具"}),
    [delta(calls=[{"index": 0, "id": "one", "function": {"name": "write_file", "arguments":
        '{"content":"a","content":"b"}'}}]), delta(finish="tool_calls"), "[DONE]"],
    [delta(calls=[call("write_file", {"content": "a"})]), delta(finish="stop"), "[DONE]"],
    [delta(calls=[{"index": 0, "function": {"name": "write_file", "arguments": '{"content":"a"}'}}]),
     delta(finish="tool_calls"), "[DONE]"],
    [delta(calls=[call("write_file", {"content": "a"}, "one")]),
     delta(calls=[{"index": 0, "id": "changed"}]), delta(finish="tool_calls"), "[DONE]"],
])
def test_invalid_complete_batch_never_partially_writes(script, tmp_path):
    path = tmp_path / "result.txt"
    with backend([script]) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path))
    assert result["status"] == "error"
    assert result["passed"] is False
    assert not path.exists()
    assert result["executions"] == []
    assert len(requests) == 1


def test_fragmented_native_arguments_and_unicode_are_only_executed_after_done(tmp_path):
    path = tmp_path / "result.txt"
    raw = '{"content":"验收通过\\n"}'
    script = [delta(calls=[{"index": 0, "id": "fragmented", "type": "function",
                          "function": {"name": "write_", "arguments": raw[:8]}}]),
              delta(calls=[{"index": 0, "function": {"name": "file", "arguments": raw[8:]}}]),
              delta(finish="tool_calls"), "[DONE]"]
    with backend([script, response("task_complete", {"summary": "完成"}, "complete")]) as (url, _):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path))
    assert result["status"] == "completed"
    assistant = next(message for message in result["history"] if message.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["arguments"] == raw


@pytest.mark.parametrize("script", [
    [delta(calls=[call("write_file", {"content": "不应写入"})])],
    [delta(calls=[call("write_file", {"content": "不应写入"})]), delta(finish="tool_calls")],
    [delta(calls=[call("write_file", {"content": "不应写入"})]), "[DONE]"],
    [delta(calls=[call("write_file", {"content": "不应写入"})]), delta(finish="length"), "[DONE]"],
    [b'data: {"choices":['],
    [b'data: \xff\n\n'],
    [b'data: ' + b'x' * (1024 * 1024 + 1) + b'\n\n'],
])
def test_truncated_network_or_invalid_stream_never_executes_or_replays(script, tmp_path):
    path = tmp_path / "result.txt"
    with backend([script, response("write_file", {"content": "不应重放"})]) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path))
    assert result["status"] == "error"
    assert result["passed"] is False
    assert len(requests) == 1
    assert not path.exists()

@pytest.mark.parametrize("terminated", [False, True])
def test_interrupted_tool_arguments_do_not_poison_continuation_history(tmp_path, terminated):
    from tools.prompts import inject_tool_prompt

    path = tmp_path / "result.txt"
    fragment = {"index": 0, "id": "partial-write", "type": "function", "function": {
        "name": "write_file", "arguments": '{"content":"unfinished',
    }}
    script = [delta(content="已收到进度，但工具尚未收齐"), delta(calls=[fragment])]
    if terminated:
        script += [delta(finish="tool_calls")]
    with backend([script]) as (url, requests):
        failed = TaskClient(url, "key", "model").run(
            "写入文件", TOOLS, file_executor(path), file_checker(path))
    assert failed["status"] == "error"
    assert not path.exists()
    assert failed["history"][-1]["content"] == "已收到进度，但工具尚未收齐"
    # 下一次真实第一跳组包不能再把未交付的半调用当成历史操作。
    inject_tool_prompt(failed["history"], TOOLS)
    with backend([response("write_file", {"content": "验收通过\n"}, "fresh-write"),
                  response("task_complete", {"summary": "已核验"}, "fresh-complete")]) as (url, requests):
        resumed = TaskClient(url, "key", "model").run(
            "完成文件", TOOLS, file_executor(path), file_checker(path), messages=failed["history"])
    assert resumed["passed"] is True
    assert path.read_text(encoding="utf-8") == "验收通过\n"
    assert [item["id"] for item in resumed["executions"]] == ["fresh-write"]



def test_only_prose_and_bare_json_exhaust_without_fake_tool_results(tmp_path):
    path = tmp_path / "result.txt"
    script = [delta(content='{"name":"write_file","content":"已完成"}'),
              delta(finish="stop"), "[DONE]"]
    with backend([script] * 4) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path), max_requests=10)
    assert result["status"] == "exhausted"
    assert result["passed"] is False
    assert len(requests) == 3
    assert not path.exists()
    assert not any(message["role"] == "tool" for message in result["history"])
    assert len([message for message in result["history"] if message["role"] == "assistant"]) == 3


def test_request_budget_includes_failed_verifications(tmp_path):
    path = tmp_path / "result.txt"
    with backend([response("task_complete", {"summary": "完成"})]) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path), max_requests=1)
    assert result["status"] == "exhausted"
    assert result["verification"]["passed"] is False
    assert len(requests) == 1


@pytest.mark.parametrize("reason", ["permission", "missing_input", "external_unavailable"])
def test_reported_blocker_is_not_verified_success_or_permission_grant(reason, tmp_path):
    path = tmp_path / "result.txt"
    with backend([response("task_blocked", {"reason": reason, "detail": "需要操作者处理"})]) as (url, _):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path))
    assert result["status"] == "blocked"
    assert result["passed"] is False
    assert result["model_reported"] is True
    assert result["reason"] == reason
    assert result["detail"] == "需要操作者处理"
    assert result["verification"] is None
    assert not path.exists()


def test_permission_failure_is_preserved_before_model_reports_blocker(tmp_path):
    path = tmp_path / "result.txt"

    def denied(name, parameters):
        return {"output": "没有文件写权限", "is_error": True, "error": "permission_denied"}

    with backend([response("write_file", {"content": "越权"}),
                  response("task_blocked", {"reason": "permission", "detail": "写权限被拒"}, "blocked")]) as (url, requests):
        result = TaskClient(url, "key", "model").run("写文件", TOOLS, denied, file_checker(path))
    assert result["status"] == "blocked"
    tool = next(message for message in requests[1]["body"]["messages"] if message["role"] == "tool")
    assert tool["content"] == "没有文件写权限"
    assert tool["is_error"] is True
    assert tool["error"] == "permission_denied"
    assert not path.exists()


def test_executor_exception_after_side_effect_is_unknown_and_never_replayed(tmp_path):
    path = tmp_path / "result.txt"

    def failed(name, parameters):
        path.write_text("已执行但结果未知", encoding="utf-8")
        raise PermissionError("不应暴露的底层详情")

    with backend([response("write_file", {"content": "value"})] * 2) as (url, requests):
        result = TaskClient(url, "key", "model").run("写文件", TOOLS, failed, file_checker(path))
    assert result["status"] == "error"
    assert len(requests) == 1
    assert result["executions"][0]["state"] == "unknown"
    assert path.read_text(encoding="utf-8") == "已执行但结果未知"


@pytest.mark.parametrize("bad_result", [{"passed": "true"}, {"passed": 1}, {}, None])
def test_checker_result_requires_strict_boolean(bad_result, tmp_path):
    path = tmp_path / "result.txt"
    with backend([response("task_complete", {"summary": "声称完成"})]) as (url, _):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), lambda remaining: bad_result)
    assert result["status"] == "error"
    assert result["passed"] is False


def test_checker_exception_is_terminal_not_completion(tmp_path):
    def broken(remaining):
        raise RuntimeError("checker不可用")

    with backend([response("task_complete", {"summary": "声称完成"})] * 2) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(tmp_path / "result.txt"), broken)
    assert result["status"] == "error"
    assert result["passed"] is False
    assert len(requests) == 1


def test_stream_cancellation_emits_progress_but_never_executes_half_tool(tmp_path):
    path = tmp_path / "result.txt"
    cancel = threading.Event()

    def observe(event):
        if event["type"] == "text":
            cancel.set()

    with backend([[delta(content="立即显示此进度"),
                   *response("write_file", {"content": "不应写入"})]]) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "写文件", TOOLS, file_executor(path), file_checker(path), on_event=observe, cancel=cancel)
    assert result["status"] == "cancelled"
    assert result["events"][0] == {"type": "text", "text": "立即显示此进度"}
    assert not path.exists()
    assert len(requests) == 1


def test_progress_is_delivered_while_http_response_is_still_open(tmp_path):
    observed = threading.Event()
    gate = []

    def wait_for_progress():
        gate.append(observed.wait(2))

    def observe(event):
        if event["type"] == "text":
            observed.set()

    with backend([[delta(content="流式进度"), wait_for_progress,
                   *response("task_blocked", {"reason": "missing_input", "detail": "输入不足"})]]) as (url, _):
        result = TaskClient(url, "key", "model").run(
            "任务", TOOLS, file_executor(tmp_path / "result.txt"),
            file_checker(tmp_path / "result.txt"), on_event=observe)
    assert gate == [True]
    assert result["status"] == "blocked"


@pytest.mark.parametrize("phase", ["before_request", "before_execute", "during_verify"])
def test_cancellation_never_starts_a_new_tool_or_announces_completion(phase, tmp_path):
    path = tmp_path / "result.txt"
    cancel = threading.Event()
    if phase == "before_request":
        cancel.set()

    def observe(event):
        if phase == "before_execute" and event["type"] == "tool_start":
            cancel.set()

    def verify(remaining):
        cancel.set()
        return {"passed": True}

    script = response("task_complete", {"summary": "完成"}) if phase == "during_verify" else response(
        "write_file", {"content": "不应写入"})
    with backend([script]) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "任务", TOOLS, file_executor(path), verify, cancel=cancel, on_event=observe)
    assert result["status"] == "cancelled"
    assert result["passed"] is False
    assert not path.exists()
    assert len(requests) == (0 if phase == "before_request" else 1)


def test_late_action_after_fixed_deadline_never_executes(tmp_path):
    path = tmp_path / "result.txt"
    with backend([[lambda: time.sleep(0.08), *response("write_file", {"content": "迟到"})]]) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "任务", TOOLS, file_executor(path), file_checker(path), max_seconds=0.02)
    assert result["status"] == "deadline"
    assert result["passed"] is False
    assert not path.exists()
    assert len(requests) == 1


def test_successful_checker_returning_after_deadline_cannot_pass(tmp_path):
    def slow(remaining):
        time.sleep(remaining + 0.01)
        return {"passed": True}

    with backend([response("task_complete", {"summary": "完成"})]) as (url, _):
        result = TaskClient(url, "key", "model").run(
            "任务", TOOLS, file_executor(tmp_path / "result.txt"), slow, max_seconds=0.2)
    assert result["status"] == "deadline"
    assert result["passed"] is False


def test_reused_call_id_is_not_executed_twice(tmp_path):
    path = tmp_path / "result.txt"
    with backend([response("write_file", {"content": "第一次"}, "same"),
                  response("write_file", {"content": "不应重放"}, "same")]) as (url, requests):
        result = TaskClient(url, "key", "model").run(
            "任务", TOOLS, file_executor(path), file_checker(path))
    assert result["status"] == "error"
    assert path.read_text(encoding="utf-8") == "第一次"
    assert len(result["executions"]) == 1
    assert len(requests) == 2


@pytest.mark.parametrize("kwargs", [
    {"max_requests": True}, {"max_requests": 0}, {"max_requests": 1.5},
    {"max_seconds": False}, {"max_seconds": 0}, {"max_seconds": float("inf")},
    {"max_seconds": float("nan")},
])
def test_invalid_budgets_are_rejected_before_http(kwargs, tmp_path):
    with backend([]) as (url, requests):
        with pytest.raises(ValueError):
            TaskClient(url, "key", "model").run(
                "任务", TOOLS, file_executor(tmp_path / "result.txt"),
                file_checker(tmp_path / "result.txt"), **kwargs)
    assert requests == []


def test_reserved_control_names_are_rejected_before_http(tmp_path):
    reserved = [{"type": "function", "function": {"name": "task_complete"}}]
    with backend([]) as (url, requests):
        with pytest.raises(ValueError):
            TaskClient(url, "key", "model").run(
                "任务", reserved, file_executor(tmp_path / "result.txt"),
                file_checker(tmp_path / "result.txt"))
    assert requests == []


def test_independent_actions_share_one_request_without_partial_batch_execution(tmp_path):
    tools = [{"type": "function", "function": {"name": "write_named", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"], "additionalProperties": False,
    }}}]
    def execute(name, parameters):
        (tmp_path / parameters["path"]).write_text(parameters["content"], encoding="utf-8")
        return {"output": "written", "is_error": False}
    def verify(remaining):
        return {"passed": all((tmp_path / name).read_text(encoding="utf-8") == text
                              for name, text in (("a.txt", "A"), ("b.txt", "B")))}
    scripts = [[delta(calls=[call("write_named", {"path": "a.txt", "content": "A"}, "a", 0),
                             call("write_named", {"path": "b.txt", "content": "B"}, "b", 1)]),
                delta(finish="tool_calls"), "[DONE]"],
               response("task_complete", {"summary": "two files delivered"}, "finish")]
    with backend(scripts) as (url, _):
        result = TaskClient(url, "key", "model").run("write two files", tools, execute, verify, max_requests=2)
    assert result["passed"] and result["requests"] == 2
    assert len(result["executions"]) == 2


def test_upstream_stream_error_keeps_reason_without_replaying_tools(tmp_path):
    script = [{"error": {"code": "upstream_authentication_failed", "message": "Token rejected test-key"}}]
    with backend([script]) as (url, requests):
        result = TaskClient(url, "test-key", "model").run(
            "write file", TOOLS, file_executor(tmp_path / "result.txt"), file_checker(tmp_path / "result.txt"))
    assert result["status"] == "error" and not result["passed"]
    assert result["error_code"] == "upstream_authentication_failed"
    assert result["error"] == "Token rejected [REDACTED]"
    assert len(requests) == 1 and result["executions"] == []
