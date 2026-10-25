import json
import time
import uuid
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from provider.anthropic import parse_tool_arguments
from provider.genai import estimate_text_tokens, iter_genai_stream
from provider.streaming import validated_events
from provider.features import is_web_search_tool
from tools.parsing import ACTION_CLOSE_MARKER, ACTION_OPEN_MARKER
from tools.prompts import escape_tool_control_markers, flatten_message_content, normalize_content, merge_content


def _qualified_name(name, namespace=None):
    if not isinstance(name, str) or not name:
        raise ValueError("Tool name must be a non-empty string")
    if namespace is None:
        return name
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("Tool namespace must be a non-empty string")
    return f"{namespace}.{name}"


def _tool_entries(tools, namespace=None):
    """保留命名空间身份，内部使用 namespace.leaf，输出恢复独立字段。"""
    if tools is not None and not isinstance(tools, list):
        raise ValueError("tools must be an array")
    for tool in tools or []:
        if not isinstance(tool, dict):
            raise ValueError("Each tool must be an object")
        kind = tool.get("type")
        if kind == "namespace":
            if not isinstance(tool.get("tools"), list):
                raise ValueError("Namespace tools must be an array")
            yield from _tool_entries(tool["tools"], _qualified_name(tool.get("name"), namespace))
        elif kind in ("function", "custom"):
            _qualified_name(tool.get("name"), namespace)
            identity = {"name": tool["name"]}
            if namespace is not None:
                identity["namespace"] = namespace
            yield tool, identity
        elif not is_web_search_tool(tool):
            raise ValueError(f"Unsupported Responses tool type: {kind}")


def responses_tools_to_chat_tools(tools: Any) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """把 Responses 的工具定义转成 Chat Completions 格式，并返回 custom 工具名集合。

    Codex 会把顶层 function/custom 工具包在一个 `type: "namespace"` 里发送，
    这里一并展平；custom 工具（自由文本，例如 apply_patch）用单字段 input 表达。
    """
    chat_tools: List[Dict[str, Any]] = []
    custom_tool_names: Set[str] = set()

    def add_function(tool: Dict[str, Any], name: str) -> None:
        chat_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {"type": "object", "properties": {}, "additionalProperties": False}),
            },
        })

    def add_custom(tool: Dict[str, Any], name: str) -> None:
        custom_format = tool.get("format")
        if custom_format is not None and (
            not isinstance(custom_format, dict) or custom_format.get("type", "text") != "text"
        ):
            raise ValueError("Custom tool grammar formats are not supported; use text format")
        description = tool.get("description", "")
        chat_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "该工具的完整自由文本输入",
                        }
                    },
                    "required": ["input"],
                    "additionalProperties": False,
                },
            },
        })
        custom_tool_names.add(name)

    for tool, identity in _tool_entries(tools):
        name = _qualified_name(identity["name"], identity.get("namespace"))
        if tool["type"] == "function":
            add_function(tool, name)
        else:
            add_custom(tool, name)

    return chat_tools, custom_tool_names


def responses_tool_choice_to_chat(tool_choice: Any) -> Any:
    """把 Responses 的 tool_choice 映射成 inject_tool_prompt 认识的形态。"""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str) and tool_choice in ("none", "required", "auto"):
        return tool_choice
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") in ("function", "custom") and tool_choice.get("name"):
            return {"type": "function", "function": {"name": _qualified_name(tool_choice["name"], tool_choice.get("namespace"))}}
    raise ValueError("Unsupported Responses tool_choice")


def _merge_adjacent_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并连续的同角色消息，避免把同一轮拆成多条。"""
    merged: List[Dict[str, Any]] = []
    for message in messages:
        if (
            merged
            and merged[-1]["role"] == message["role"]
            and message["role"] in ("assistant", "user")
        ):
            previous = merged[-1]["content"]
            current = message["content"]
            merged[-1]["content"] = merge_content(previous, current)
        else:
            merged.append(dict(message))
    return merged


def responses_input_to_genai_messages(input_value: Any, instructions: Any = None) -> List[Dict[str, Any]]:
    """把 Responses 的 input items 转换成 GenAI 的 messages。"""
    if isinstance(input_value, str):
        items: List[Any] = [{"type": "message", "role": "user", "content": input_value}]
    elif isinstance(input_value, list):
        items = input_value
    else:
        items = []

    messages: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue

        item_type = item.get("type") or "message"
        if item_type in ("function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"):
            if not isinstance(item.get("call_id"), str) or not item["call_id"]:
                raise ValueError("Tool history requires an explicit call_id")
        if item_type == "message":
            role = item.get("role", "user")
            if role == "developer":
                role = "system"
            if role not in ("user", "assistant", "system"):
                role = "user"
            messages.append({"role": role, "content": normalize_content(item.get("content"))})
        elif item_type == "function_call":
            call_obj = {"calls": [{
                "id": item["call_id"],
                "operation": _qualified_name(item.get("name"), item.get("namespace")),
                "parameters": parse_tool_arguments(item.get("arguments", {})),
            }]}
            call_json = escape_tool_control_markers(json.dumps(call_obj, ensure_ascii=False))
            messages.append({
                "role": "assistant",
                "content": f"{ACTION_OPEN_MARKER}{call_json}{ACTION_CLOSE_MARKER}",
            })
        elif item_type in ("custom_tool_call",):
            call_obj = {"calls": [{
                "id": item["call_id"],
                "operation": _qualified_name(item.get("name"), item.get("namespace")),
                "parameters": {"input": item.get("input", "")},
            }]}
            call_json = escape_tool_control_markers(json.dumps(call_obj, ensure_ascii=False))
            messages.append({
                "role": "assistant",
                "content": f"{ACTION_OPEN_MARKER}{call_json}{ACTION_CLOSE_MARKER}",
            })
        elif item_type in ("function_call_output", "custom_tool_call_output"):
            output_parts = normalize_content(item.get("output"))
            output = flatten_message_content(output_parts)
            attachments = [part for part in output_parts if part["type"] != "text"] if isinstance(output_parts, list) else []
            call_id = item["call_id"]
            text = (
                "<tool_result>\n"
                f"  <tool_call_id>{call_id}</tool_call_id>\n"
                f"  <result>\n{output}\n  </result>\n"
                "</tool_result>"
            )
            messages.append({
                "role": "user",
                "content": merge_content(text, attachments) if attachments else text,
            })
        elif item_type == "reasoning":
            continue
        else:
            raise ValueError(f"Unsupported Responses input item type: {item_type}")

    text = flatten_message_content(instructions)
    if text.strip():
        messages.insert(0, {"role": "system", "content": text})

    return _merge_adjacent_messages(messages)


def _custom_tool_input(arguments: Any) -> str:
    """custom 工具的自由文本输入。"""
    return parse_tool_arguments(arguments)["input"]


def _split_chat_info(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """取出最后一条 user 作为当前提问，其余作为历史。"""
    chat_info = ""
    history = list(messages)
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") == "user":
            chat_info = flatten_message_content(history[index].get("content", ""))
            del history[index]
            break
    return chat_info, history


def _base_response(
    response_id: str,
    created_at: int,
    model: str,
    status: str,
    output: List[Dict[str, Any]],
    instructions: Any,
    max_output_tokens: Optional[int],
    response_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta = response_meta or {}
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": model,
        "output": output,
        "instructions": instructions if isinstance(instructions, str) else None,
        "parallel_tool_calls": meta.get("parallel_tool_calls", True),
        "tool_choice": meta.get("tool_choice") or "auto",
        "tools": meta.get("tools") or [],
        "error": None,
        "incomplete_details": None,
        "metadata": {},
        "temperature": None,
        "top_p": None,
        "max_output_tokens": max_output_tokens,
        "previous_response_id": None,
        "reasoning": meta.get("reasoning"),
        "store": meta.get("store", False),
        "text": {"format": {"type": "text"}},
        "truncation": "disabled",
        "usage": None,
        "user": None,
    }


def stream_genai_as_responses(
    messages: List[Dict[str, Any]],
    model: str,
    max_tokens: int,
    config: Any,
    instructions: Any = None,
    custom_tool_names: Optional[Set[str]] = None,
    tools=None,
    max_output_tokens: Optional[int] = None,
    response_meta: Optional[Dict[str, Any]] = None,
    upstream_options: Optional[Dict[str, Any]] = None,
    tool_choice=None,
    parallel_tool_calls=True,
) -> Generator[str, None, None]:
    """把 GenAI 流转换成 OpenAI Responses API 的 SSE 事件。"""
    custom_tool_names = custom_tool_names or set()
    output_names = {
        _qualified_name(identity["name"], identity.get("namespace")): identity
        for _, identity in _tool_entries((response_meta or {}).get("tools"))
    }
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    created_at = int(time.time())
    sequence_number = 0

    chat_info, history_messages = _split_chat_info(messages)

    def ev(name: str, payload: Dict[str, Any]) -> str:
        nonlocal sequence_number
        body = {"type": name, "sequence_number": sequence_number}
        body.update(payload)
        sequence_number += 1
        return f"event: {name}\ndata: {json.dumps(body, ensure_ascii=False, separators=(',', ':'))}\n\n"

    def base(status: str, output: List[Dict[str, Any]]) -> Dict[str, Any]:
        return _base_response(
            response_id, created_at, model, status, output, instructions,
            max_output_tokens, response_meta,
        )

    yield ev("response.created", {"response": base("in_progress", [])})
    yield ev("response.in_progress", {"response": base("in_progress", [])})

    output_items: List[Dict[str, Any]] = []
    next_output_index = 0

    reasoning_item_id: Optional[str] = None
    reasoning_index: Optional[int] = None
    reasoning_parts: List[str] = []

    message_item_id: Optional[str] = None
    message_index: Optional[int] = None
    message_parts: List[str] = []


    def start_reasoning():
        nonlocal reasoning_item_id, reasoning_index, next_output_index
        reasoning_item_id = f"rs_{uuid.uuid4().hex[:24]}"
        reasoning_index = next_output_index
        next_output_index += 1
        reasoning_parts.clear()
        item = {
            "id": reasoning_item_id,
            "type": "reasoning",
            "summary": [],
            "content": [],
            "encrypted_content": None,
            "status": "in_progress",
        }
        yield ev("response.output_item.added", {"output_index": reasoning_index, "item": item})
        yield ev("response.reasoning_summary_part.added", {
            "item_id": reasoning_item_id,
            "output_index": reasoning_index,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": ""},
        })

    def finish_reasoning():
        nonlocal reasoning_item_id
        if reasoning_item_id is None:
            return
        text = "".join(reasoning_parts)
        item_id = reasoning_item_id
        index = reasoning_index
        reasoning_item_id = None
        yield ev("response.reasoning_summary_text.done", {
            "item_id": item_id,
            "output_index": index,
            "summary_index": 0,
            "text": text,
        })
        yield ev("response.reasoning_summary_part.done", {
            "item_id": item_id,
            "output_index": index,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": text},
        })
        item = {
            "id": item_id,
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": text}],
            "content": [],
            "encrypted_content": None,
            "status": "completed",
        }
        yield ev("response.output_item.done", {"output_index": index, "item": item})
        output_items.append(item)

    def start_message():
        nonlocal message_item_id, message_index, next_output_index
        message_item_id = f"msg_{uuid.uuid4().hex[:24]}"
        message_index = next_output_index
        next_output_index += 1
        message_parts.clear()
        item = {
            "id": message_item_id,
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
        yield ev("response.output_item.added", {"output_index": message_index, "item": item})
        yield ev("response.content_part.added", {
            "item_id": message_item_id,
            "output_index": message_index,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        })

    def message_delta(delta: str):
        yield ev("response.output_text.delta", {
            "item_id": message_item_id,
            "output_index": message_index,
            "content_index": 0,
            "delta": delta,
            "logprobs": [],
        })

    def finish_message():
        nonlocal message_item_id
        if message_item_id is None:
            return
        text = "".join(message_parts)
        item_id = message_item_id
        index = message_index
        message_item_id = None
        yield ev("response.output_text.done", {
            "item_id": item_id,
            "output_index": index,
            "content_index": 0,
            "text": text,
            "logprobs": [],
        })
        yield ev("response.content_part.done", {
            "item_id": item_id,
            "output_index": index,
            "content_index": 0,
            "part": {"type": "output_text", "text": text, "annotations": []},
        })
        item = {
            "id": item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
        yield ev("response.output_item.done", {"output_index": index, "item": item})
        output_items.append(item)

    def emit_tool_calls(tool_calls: List[Dict[str, Any]]):
        nonlocal next_output_index
        for tool_call in tool_calls:
            function = tool_call["function"]
            name = function["name"]
            identity = output_names.get(name, {"name": name})
            arguments = function["arguments"]
            call_id = tool_call["id"]

            if name in custom_tool_names:
                item_id = f"ctc_{uuid.uuid4().hex[:24]}"
                index = next_output_index
                next_output_index += 1
                input_text = _custom_tool_input(arguments)
                yield ev("response.output_item.added", {
                    "output_index": index,
                    "item": {
                        "id": item_id,
                        "type": "custom_tool_call",
                        "call_id": call_id,
                        **identity,
                        "input": "",
                    },
                })
                yield ev("response.custom_tool_call_input.delta", {
                    "item_id": item_id,
                    "output_index": index,
                    "delta": input_text,
                })
                yield ev("response.custom_tool_call_input.done", {
                    "item_id": item_id,
                    "output_index": index,
                    "input": input_text,
                })
                item = {
                    "id": item_id,
                    "type": "custom_tool_call",
                    "call_id": call_id,
                    **identity,
                    "input": input_text,
                    "status": "completed",
                }
                yield ev("response.output_item.done", {"output_index": index, "item": item})
                output_items.append(item)
            else:
                item_id = f"fc_{uuid.uuid4().hex[:24]}"
                index = next_output_index
                next_output_index += 1
                yield ev("response.output_item.added", {
                    "output_index": index,
                    "item": {
                        "id": item_id,
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": call_id,
                        **identity,
                        "arguments": "",
                    },
                })
                yield ev("response.function_call_arguments.delta", {
                    "item_id": item_id,
                    "output_index": index,
                    "delta": arguments,
                })
                yield ev("response.function_call_arguments.done", {
                    "item_id": item_id,
                    "output_index": index,
                    "arguments": arguments,
                })
                item = {
                    "id": item_id,
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    **identity,
                    "arguments": arguments,
                }
                yield ev("response.output_item.done", {"output_index": index, "item": item})
                output_items.append(item)

    source = iter_genai_stream(chat_info, history_messages, model, max_tokens, config,
                               upstream_options=upstream_options, recover_empty=bool(tools))
    events = validated_events(source, tools, tool_choice, parallel_tool_calls)
    try:
        for event in events:
            if event["type"] == "heartbeat":
                yield ": keep-alive\n\n"
                continue
            if event["type"] == "error":
                yield ev("error", {"code": event.get("code", "upstream_error"), "message": event["message"], "param": None})
                return
            if event["type"] == "delta":
                reasoning = event.get("reasoning")
                if reasoning:
                    if reasoning_item_id is None:
                        yield from start_reasoning()
                    reasoning_parts.append(reasoning)
                    yield ev("response.reasoning_summary_text.delta", {
                        "item_id": reasoning_item_id, "output_index": reasoning_index,
                        "summary_index": 0, "delta": reasoning,
                    })
                content = event.get("content")
                if content:
                    if message_item_id is None:
                        yield from start_message()
                    message_parts.append(content)
                    yield from message_delta(content)
                continue
            if event["type"] != "done":
                continue
            yield from finish_reasoning()
            yield from finish_message()
            yield from emit_tool_calls(event["tool_calls"])
            output_tokens = estimate_text_tokens("".join(message_parts)) + estimate_text_tokens("".join(reasoning_parts))
            input_tokens = estimate_text_tokens(chat_info) + sum(
                estimate_text_tokens(flatten_message_content(message.get("content", ""))) for message in history_messages
            )
            output_items.sort(key=lambda item: (
                reasoning_index if item["type"] == "reasoning"
                else message_index if item["type"] == "message" else next_output_index
            ))
            truncated = event.get("finish_reason") == "length"
            response_object = base("incomplete" if truncated else "completed", output_items)
            response_object["usage"] = {
                "input_tokens": input_tokens,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": output_tokens,
                "output_tokens_details": {"reasoning_tokens": estimate_text_tokens("".join(reasoning_parts))},
                "total_tokens": input_tokens + output_tokens,
            }
            if truncated:
                response_object["incomplete_details"] = {"reason": "max_output_tokens"}
                yield ev("response.incomplete", {"response": response_object})
            else:
                yield ev("response.completed", {"response": response_object})
            return
    finally:
        events.close()
