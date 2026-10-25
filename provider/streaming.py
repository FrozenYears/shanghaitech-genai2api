"""三个协议共用的取消、心跳及工具交付边界。"""
import queue
import socket
import threading
import time

from errors import UpstreamError
from tools.contracts import ToolContractError, validate_tool_calls
from tools.parsing import extract_tool_calls, find_tool_call_open, tool_call_prefix_len


NATIVE_TOOL_ERRORS = frozenset({
    "Native tool calls must be an array",
    "Native tool call fragment must be an object",
    "Native tool call has an invalid index",
    "Unsupported native tool call type",
    "Invalid native tool call ID",
    "Invalid native tool function",
    "Invalid native tool call fragment",
    "Native tool call output was truncated",
    "Native tool calls did not complete",
    "Native tool call indices are incomplete",
    "Native tool call is incomplete",
})

class StreamControl:
    def __init__(self, timeout=None):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.response = None
        self.socket = None
        self.deadline = None if timeout is None else time.monotonic() + timeout

    def remaining(self):
        return None if self.deadline is None else max(0.0, self.deadline - time.monotonic())

    def attach(self, response):
        # urllib3 的连接对象在 Connection: close 时可能已清空，保存实际读 socket。
        transport_socket = None
        for path in (("raw", "_connection", "sock"), ("raw", "_fp", "fp", "raw", "_sock"),
                     ("raw", "_original_response", "fp", "raw", "_sock")):
            value = response
            for name in path:
                value = getattr(value, name, None)
            if value is not None:
                transport_socket = value
                break
        with self.lock:
            self.response = response
            self.socket = transport_socket
            cancelled = self.cancelled.is_set()
        if cancelled:
            self.close()
        return not cancelled

    def finish(self):
        with self.lock:
            self.response = None
            self.socket = None

    def close(self):
        self.cancelled.set()
        with self.lock:
            response, transport_socket = self.response, self.socket
        # 先中断 recv，再 close；否则 HTTPResponse.close 可能等待读锁至300秒。
        if transport_socket is not None:
            try:
                transport_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if response is not None:
            response.close()


def heartbeat_events(factory, interval=10, timeout=None):
    """有界桥接：全生成窗口到期或消费者退出即取消已取得的 HTTP 响应。"""
    control = StreamControl(timeout)
    events = queue.Queue(maxsize=8)
    sentinel = object()

    def put(value):
        while not control.cancelled.is_set():
            try:
                events.put(value, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def produce():
        source = factory(control)
        try:
            for event in source:
                if not put(event):
                    break
        except Exception as exc:
            put({"type": "error", "message": str(exc)})
        finally:
            source.close()
            control.finish()
            put(sentinel)

    worker = threading.Thread(target=produce, name="genai-stream", daemon=True)
    worker.start()
    try:
        while True:
            remaining = control.remaining()
            if remaining == 0:
                yield {"type": "error", "code": "upstream_request_timeout",
                       "message": "Upstream generation exceeded the total tool-request time budget"}
                return
            try:
                event = events.get(timeout=interval if remaining is None else min(interval, remaining))
            except queue.Empty:
                if control.remaining() == 0:
                    continue
                yield {"type": "heartbeat"}
                continue
            if control.remaining() == 0:
                continue
            if event is sentinel:
                return
            yield event
    finally:
        try:
            control.close()
        finally:
            worker.join(timeout=0.2)


def validated_events(source, tools=None, tool_choice=None, parallel_tool_calls=True):
    """先流式交付安全正文；工具批次在完整结束并校验后才可见。"""
    buffer = ""
    captured = False
    visible_text = False
    source_parts = []
    normal_completion = False
    try:
        for event in source:
            kind = event["type"]
            if kind in ("heartbeat", "error"):
                yield event
                if kind == "error":
                    return
                continue
            if kind == "delta":
                if event.get("sources"):
                    source_parts.append(event["content"])
                    continue
                if event.get("reasoning"):
                    yield {"type": "delta", "reasoning": event["reasoning"]}
                content = event.get("content")
                if not content:
                    continue
                if not tools:
                    visible_text = visible_text or bool(content.strip())
                    yield {"type": "delta", "content": content}
                    continue
                buffer += content
                if captured:
                    continue
                position = find_tool_call_open(buffer)
                if position >= 0:
                    if position:
                        visible_text = visible_text or bool(buffer[:position].strip())
                        yield {"type": "delta", "content": buffer[:position]}
                    buffer = buffer[position:]
                    captured = True
                else:
                    prefix = tool_call_prefix_len(buffer)
                    safe, buffer = (buffer[:-prefix], buffer[-prefix:]) if prefix else (buffer, "")
                    if safe:
                        visible_text = visible_text or bool(safe.strip())
                        yield {"type": "delta", "content": safe}
                continue
            if kind != "done":
                continue
            normal_completion = event.get("finish_reason") in ("stop", "tool_calls")
            if event.get("tool_error"):
                diagnostic = event["tool_error"]
                raise ToolContractError(diagnostic, feedback=(
                    "原生工具批次校验失败：" + diagnostic + "；请重新生成完整合法批次。"
                    if diagnostic in NATIVE_TOOL_ERRORS else None
                ))
            native = event.get("tool_calls")
            if native:
                calls, remaining = native, buffer
            elif not tools:
                calls, remaining = [], buffer
            else:
                calls, remaining = extract_tool_calls(buffer)
            if event.get("finish_reason") == "tool_calls" and not calls:
                raise ToolContractError("Upstream reported tool_calls without any complete call")
            if calls and event.get("finish_reason") == "length":
                raise ToolContractError("Tool call output was truncated; no tool call was emitted")
            calls = validate_tool_calls(calls or [], tools or [], tool_choice, parallel_tool_calls)
            if not calls and not visible_text and not (remaining or "").strip() and event.get("finish_reason") != "length":
                raise UpstreamError("Upstream completed without an answer or tool call", code="upstream_empty_response")
            if remaining:
                yield {"type": "delta", "content": remaining}
            for content in source_parts:
                yield {"type": "delta", "content": content}
            yield {**event, "tool_calls": calls}
            return
        yield {"type": "error", "message": "Upstream stream ended before completion", "code": "upstream_stream_incomplete"}
    except (ValueError, UpstreamError) as exc:
        error = {"type": "error", "message": str(exc), "code": getattr(exc, "code", "tool_call_parse_error")}
        if normal_completion and isinstance(exc, ToolContractError):
            error["repair_feedback"] = exc.feedback
        yield error
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            close()
