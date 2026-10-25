"""三个协议共用的取消、心跳及工具交付边界。"""
import queue
import socket
import threading

from tools.contracts import ToolContractError, validate_tool_calls
from tools.parsing import extract_tool_calls, find_tool_call_open, tool_call_prefix_len


class StreamControl:
    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.response = None
        self.socket = None

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


def heartbeat_events(factory, interval=10):
    """有界桥接：消费者退出即关闭实际 HTTP 响应，生产者不得继续请求。"""
    control = StreamControl()
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
            try:
                event = events.get(timeout=interval)
            except queue.Empty:
                yield {"type": "heartbeat"}
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
    source_parts = []
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
                    yield {"type": "delta", "content": content}
                    continue
                buffer += content
                if captured:
                    continue
                position = find_tool_call_open(buffer)
                if position >= 0:
                    if position:
                        yield {"type": "delta", "content": buffer[:position]}
                    buffer = buffer[position:]
                    captured = True
                else:
                    prefix = tool_call_prefix_len(buffer)
                    safe, buffer = (buffer[:-prefix], buffer[-prefix:]) if prefix else (buffer, "")
                    if safe:
                        yield {"type": "delta", "content": safe}
                continue
            if kind != "done":
                continue
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
            if remaining:
                yield {"type": "delta", "content": remaining}
            for content in source_parts:
                yield {"type": "delta", "content": content}
            yield {**event, "tool_calls": calls}
            return
        yield {"type": "error", "message": "Upstream stream ended before completion"}
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            close()
