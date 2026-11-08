"""显式任务客户端；不改变代理默认模式，不存储服务端任务状态。

仅调用 run 才启用任务握手。execute/verify 是调用者提供的可信边界；本模块
不授予权限，也不能中断已进入的同步执行器。等待 HTTP 响应头或空闲读取时，
取消最迟在当前 socket timeout（读取至多 15 秒、连接至多 5 秒，不含 DNS）后被观察到。
"""

from copy import deepcopy
import json
import math
import time
from urllib.parse import urlsplit, urlunsplit

import requests

from tools.contracts import (
    ToolContractError, strict_json_loads, validate_tool_calls, validate_tool_request,
)


_MAX_RESPONSE_BYTES = 1024 * 1024
_CONTROL_TOOLS = [
    {"type": "function", "function": {
        "name": "task_complete",
        "description": "仅在任务动作完成后申请独立验收；模型声明本身不代表成功。",
        "parameters": {
            "type": "object", "properties": {"summary": {"type": "string"}},
            "required": ["summary"], "additionalProperties": False,
        },
    }},
    {"type": "function", "function": {
        "name": "task_blocked",
        "description": "缺少输入、权限或外部依赖时如实报告阻塞，不猜测或绕过授权。",
        "parameters": {
            "type": "object", "properties": {
                "reason": {"type": "string", "enum": [
                    "missing_input", "permission", "external_unavailable",
                ]},
                "detail": {"type": "string"},
            },
            "required": ["reason", "detail"], "additionalProperties": False,
        },
    }},
]
_CONTROL_NAMES = {tool["function"]["name"] for tool in _CONTROL_TOOLS}
_TASK_INSTRUCTION = (
    "这是显式客户端任务模式。正文仅用于即时进度，不能结束任务。"
    "每轮执行一个独立动作批次，或单独调用 task_complete 申请独立验收；"
    "互不依赖的读取可同批请求，不要重复读取已经返回且未变化的内容。"
    "验收不通过须依据真实反馈继续。缺少输入、权限或外部依赖时调用 task_blocked，"
    "不得猜测参数、伪造结果或绕过授权。完成/阻塞控制工具必须单独调用，不得与其它调用混用。"
)


class _Stopped(Exception):
    def __init__(self, status):
        self.status = status


class _StreamError(ValueError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class TaskClient:
    def __init__(self, base_url, api_key, model, *, session=None):
        if not isinstance(base_url, str):
            raise ValueError("base_url must be an HTTP(S) URL")
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError("base_url must be an HTTP(S) URL without credentials/query/fragment")
        path = parsed.path.rstrip("/")
        if not path.endswith("/v1"):
            path += "/v1"
        self.url = urlunsplit((parsed.scheme, parsed.netloc, path + "/chat/completions", "", ""))
        if not isinstance(api_key, str) or not isinstance(model, str) or not model:
            raise ValueError("api_key and model must be strings; model must not be empty")
        self.api_key = api_key
        self.model = model
        self.session = session if session is not None else requests.Session()
        # 注入的 Session 也不能偷偷重试带副作用的任务请求。
        adapter = self.session.get_adapter(self.url)
        if adapter.max_retries.total != 0:
            raise ValueError("TaskClient requires a Session with retries disabled")

    def run(self, goal, tools, execute, verify, *, messages=None, max_requests=20,
            max_seconds=300, on_event=None, cancel=None):
        """执行独立任务，返回状态、完整历史及不含工具参数的事件。

        status: completed/blocked/exhausted/error/cancelled/deadline。
        tool_calls 是完整合法调用数（包含控制工具）；executions 记录普通工具
        的执行状态，异常时 unknown 表示可能已有副作用，绝不能自动重放。
        history 始终为本次独立副本；普通工具输出保留真实 output/is_error。
        """
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        if (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
                or not math.isfinite(max_seconds) or max_seconds <= 0):
            raise ValueError("max_seconds must be positive and finite")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        if not callable(execute) or not callable(verify):
            raise ValueError("execute and verify must be callable")
        if on_event is not None and not callable(on_event):
            raise ValueError("on_event must be callable")
        if cancel is not None and not callable(getattr(cancel, "is_set", None)):
            raise ValueError("cancel must provide is_set()")
        if messages is not None and not isinstance(messages, (list, tuple)):
            raise ValueError("messages must be an array")
        definitions = deepcopy(tools)
        validators = validate_tool_request(definitions)
        if _CONTROL_NAMES.intersection(validators):
            raise ToolContractError("Task control tool names are reserved")
        definitions = list(definitions or []) + deepcopy(_CONTROL_TOOLS)
        validate_tool_request(definitions, "required", False)
        history = deepcopy(list(messages or []))
        history.insert(0, {"role": "system", "content": _TASK_INSTRUCTION})
        history.append({"role": "user", "content": goal})
        result = {
            "status": "exhausted", "passed": False, "requests": 0,
            "tool_calls": 0, "verification": None, "history": history,
            "events": [], "executions": [],
        }
        deadline = time.monotonic() + max_seconds
        prose_corrections = 0
        seen_ids = set()
        for message in history:
            if isinstance(message, dict):
                for previous in message.get("tool_calls", []) or []:
                    if isinstance(previous, dict) and isinstance(previous.get("id"), str):
                        seen_ids.add(previous["id"])

        def check():
            if cancel is not None and cancel.is_set():
                raise _Stopped("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Stopped("deadline")
            return remaining

        def emit(event):
            result["events"].append(deepcopy(event))
            if on_event is not None:
                on_event(deepcopy(event))

        def tool_result(call, value):
            message = {
                "role": "tool", "tool_call_id": call["id"],
                "content": value["output"], "is_error": value["is_error"],
            }
            for key in ("details", "error"):
                if key in value:
                    message[key] = deepcopy(value[key])
            _json(message)
            history.append(message)

        try:
            while result["requests"] < max_requests:
                remaining = check()
                result["requests"] += 1
                assistant, finish = self._request({
                    "model": self.model, "messages": deepcopy(history),
                    "tools": definitions, "tool_choice": "required",
                    "parallel_tool_calls": True, "stream": True,
                }, remaining, check, emit, history)
                check()
                calls = assistant.get("tool_calls", [])
                if not calls:
                    if finish != "stop":
                        raise _StreamError("Response did not terminate normally")
                    if prose_corrections >= 2:
                        result["error"] = "Task handshake exhausted: no complete tool call"
                        return result
                    prose_corrections += 1
                    history.append({"role": "user", "content": (
                        "客户端反馈：已收到上面的真实进度正文，但尚未收到工具调用，任务未完成。"
                        "请执行一个合法动作，或调用 task_complete 申请验收，或如实 task_blocked。"
                    )})
                    continue
                if finish != "tool_calls":
                    raise _StreamError("Tool calls require tool_calls finish_reason")
                calls = validate_tool_calls(calls, definitions, "required", True)
                if len(calls) > 1 and any(call['function']['name'] in _CONTROL_NAMES for call in calls):
                    raise _StreamError("Task control tools must be the only call in a batch")
                if any(call["id"] in seen_ids for call in calls):
                    raise _StreamError("Reused tool call ID; refusing possible replay")
                seen_ids.update(call["id"] for call in calls)
                assistant["tool_calls"] = calls
                result["tool_calls"] += len(calls)
                for call in calls:
                    name = call["function"]["name"]
                    parameters = strict_json_loads(call["function"]["arguments"])
                    check()
                    emit({"type": "tool_start", "name": name, "id": call["id"]})
                    check()
                    if name == "task_complete":
                        verification = verify(check())
                        if not isinstance(verification, dict) or type(verification.get("passed")) is not bool:
                            raise ValueError("verify must return an object with boolean passed")
                        _json(verification)
                        result["verification"] = deepcopy(verification)
                        check()
                        tool_result(call, {"output": _json(verification), "is_error": not verification["passed"]})
                        emit({"type": "verification", "passed": verification["passed"], "id": call["id"]})
                        check()
                        if verification["passed"]:
                            result.update(status="completed", passed=True, summary=parameters["summary"])
                            return result
                    elif name == "task_blocked":
                        check()
                        tool_result(call, {"output": _json(parameters), "is_error": True})
                        result.update(status="blocked", model_reported=True, reason=parameters["reason"],
                                      detail=parameters["detail"])
                        return result
                    else:
                        execution = {"name": name, "id": call["id"], "state": "unknown"}
                        result["executions"].append(execution)
                        value = execute(name, parameters)
                        execution["state"] = "executed"
                        if (not isinstance(value, dict) or not isinstance(value.get("output"), str)
                                or type(value.get("is_error")) is not bool):
                            raise ValueError("execute must return output:string and is_error:boolean")
                        tool_result(call, value)
                        emit({"type": "tool_result", "name": name, "id": call["id"],
                              "is_error": value["is_error"]})
                        check()
            check()
            result["error"] = "Task request budget exhausted"
        except _Stopped as exc:
            result.update(status=exc.status, passed=False, error="Task " + exc.status)
        except Exception as exc:
            # 取消/过期优先于此时才到达的网络或执行器错误，不掩盖已执行记录。
            try:
                check()
            except _Stopped as stopped:
                result.update(status=stopped.status, passed=False, error="Task " + stopped.status)
            else:
                message = str(exc) if isinstance(exc, (ToolContractError, _StreamError)) else type(exc).__name__
                if self.api_key:
                    message = message.replace(self.api_key, "[REDACTED]")
                result.update(status="error", passed=False, error=message[:512])
                if isinstance(exc, _StreamError) and exc.code:
                    result["error_code"] = exc.code
        return result

    def _request(self, payload, remaining, check, emit, history):
        response = self.session.post(
            self.url, json=payload,
            headers={"Authorization": "Bearer " + self.api_key, "Accept": "text/event-stream"},
            stream=True, allow_redirects=False,
            timeout=(min(5.0, remaining), min(15.0, remaining)),
        )
        try:
            check()
            if response.status_code != 200:
                raise _StreamError("HTTP status " + str(response.status_code))
            return self._consume(response, check, emit, history)
        finally:
            response.close()

    @staticmethod
    def _consume(response, check, emit, history):
        assistant = {"role": "assistant", "content": ""}
        # 出错/取消时保留真实已接收正文；分片调用仅在完整终态后加入历史，绝不提前执行。
        history.append(assistant)
        calls = {}
        id_indices = {}
        finish = None
        total = 0
        line = bytearray()
        data_lines = []

        def dispatch():
            nonlocal finish
            if not data_lines:
                return False
            text = "\n".join(data_lines)
            data_lines.clear()
            if text == "[DONE]":
                if finish is None:
                    raise _StreamError("DONE without finish_reason")
                return True
            chunk = strict_json_loads(text)
            if not isinstance(chunk, dict):
                raise _StreamError("Invalid SSE response object")
            if "error" in chunk:
                failure = chunk["error"]
                if not isinstance(failure, dict):
                    raise _StreamError("Upstream stream failed")
                code = failure.get("code")
                if not isinstance(code, str) or len(code) > 96 or not code.replace('_', '').replace('-', '').isalnum():
                    code = None
                message = failure.get("message")
                raise _StreamError(message[:512] if isinstance(message, str) else "Upstream stream failed", code)
            choices = chunk.get("choices")
            if choices == [] and isinstance(chunk.get("usage"), dict):
                return False
            if not isinstance(choices, list) or len(choices) != 1:
                raise _StreamError("Expected exactly one stream choice")
            choice = choices[0]
            if (not isinstance(choice, dict) or type(choice.get("index", 0)) is not int
                    or choice.get("index", 0) != 0):
                raise _StreamError("Invalid choice index")
            if finish is not None:
                raise _StreamError("Choice received after finish_reason")
            delta = choice.get("delta")
            if not isinstance(delta, dict) or "function_call" in delta:
                raise _StreamError("Expected normalized stream delta")
            if "role" in delta and delta["role"] != "assistant":
                raise _StreamError("Invalid assistant role")
            for key, event_type in (("content", "text"), ("reasoning_content", "reasoning")):
                text_value = delta.get(key)
                if text_value is None:
                    continue
                if not isinstance(text_value, str):
                    raise _StreamError("Stream text must be a string")
                if key == "content":
                    assistant["content"] += text_value
                else:
                    assistant[key] = assistant.get(key, "") + text_value
                if text_value:
                    emit({"type": event_type, "text": text_value})
                    check()
            fragments = delta.get("tool_calls", [])
            if not isinstance(fragments, list):
                raise _StreamError("Tool fragments must be an array")
            for fragment in fragments:
                if not isinstance(fragment, dict):
                    raise _StreamError("Invalid tool fragment")
                index = fragment.get("index")
                if type(index) is not int or index < 0:
                    raise _StreamError("Invalid tool call index")
                call = calls.setdefault(index, {"id": None, "type": "function", "function": {
                    "name": "", "arguments": "",
                }})
                assistant["tool_calls"] = list(calls.values())
                if "type" in fragment and fragment["type"] != "function":
                    raise _StreamError("Invalid tool call type")
                if "id" in fragment:
                    call_id = fragment["id"]
                    if not isinstance(call_id, str) or not call_id:
                        raise _StreamError("Missing tool call ID")
                    if call["id"] is not None and call["id"] != call_id:
                        raise _StreamError("Tool call ID changed within index")
                    if call_id in id_indices and id_indices[call_id] != index:
                        raise _StreamError("Tool call ID reused across indices")
                    call["id"] = call_id
                    id_indices[call_id] = index
                function = fragment.get("function", {})
                if not isinstance(function, dict):
                    raise _StreamError("Invalid tool function fragment")
                for field in ("name", "arguments"):
                    if field in function:
                        if not isinstance(function[field], str):
                            raise _StreamError("Tool fragments must be strings")
                        call["function"][field] += function[field]
            reason = choice.get("finish_reason")
            if reason is not None:
                if reason not in {"stop", "tool_calls"}:
                    raise _StreamError("Response terminated without a complete result")
                finish = reason
            return False

        # 单字节读取避免 iter_lines/大块读取在小型 SSE 帧上延迟正文事件；总量有硬上限。
        for part in response.iter_content(chunk_size=1):
            check()
            total += len(part)
            if total > _MAX_RESPONSE_BYTES:
                raise _StreamError("Response exceeds 1 MiB")
            for byte in part:
                if byte != 10:
                    line.append(byte)
                    continue
                decoded = bytes(line).rstrip(b"\r").decode("utf-8", errors="strict")
                line.clear()
                if not decoded:
                    if dispatch():
                        check()
                        if calls:
                            if sorted(calls) != list(range(len(calls))):
                                raise _StreamError("Tool call indices must be contiguous")
                            ordered = [calls[index] for index in sorted(calls)]
                            if any(call["id"] is None for call in ordered):
                                raise _StreamError("Complete tool calls require explicit IDs")
                            assistant["tool_calls"] = ordered
                        return assistant, finish
                elif decoded.startswith("data:"):
                    value = decoded[5:]
                    data_lines.append(value[1:] if value.startswith(" ") else value)
        check()
        raise _StreamError("Stream ended without finish_reason and DONE")
