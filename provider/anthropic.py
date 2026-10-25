import json
import re
import uuid
from typing import Any, Dict, Generator, List, Tuple

from provider.genai import estimate_messages_tokens, estimate_text_tokens, iter_genai_stream
from provider.streaming import validated_events
from tools.contracts import strict_json_loads, validate_tool_request
from tools.parsing import CODE_QUOTE_RE, ACTION_CLOSE_MARKER, ACTION_OPEN_MARKER
from tools.prompts import escape_tool_control_markers, inject_tool_prompt, normalize_content, merge_content, flatten_message_content
from provider.features import is_web_search_tool

# 上游 GenAI 没有 Anthropic 的 thinking 签名，这里使用明确的兼容占位值，
# 并且在把历史 thinking 块转回 GenAI 时会直接丢弃，不会伪造真实签名。
COMPAT_THINKING_SIGNATURE = "genai-compat-no-signature"

THINK_OPEN_PREFIX = "<think"
THINK_CLOSE = "</think>"


def anthropic_content_to_text(content: Any) -> str:
    """Convert Anthropic content format to plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)

    text_parts: List[str] = []
    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            text_parts.append(str(part))
            continue
        part_type = part.get("type")
        if part_type == "text":
            text_parts.append(part.get("text", ""))
        elif part_type == "tool_use":
            text_parts.append(
                f"[tool_use name={part.get('name', '')} id={part.get('id', '')}] "
                f"{json.dumps(part.get('input', {}), ensure_ascii=False)}"
            )
        elif part_type == "tool_result":
            text_parts.append(f"[tool_result id={part.get('tool_use_id', '')}] {tool_result_content_to_text(part.get('content', ''))}")
        elif part_type in ("thinking", "redacted_thinking"):
            # thinking 块是本地展示用的，不重新喂给上游模型
            continue
        elif part_type in ("image", "document"):
            # 附件由消息转换单独保留；不把 base64 或占位文本发给模型。
            continue
        else:
            text_parts.append(json.dumps(part, ensure_ascii=False))
    return "\n".join(part for part in text_parts if part)


def anthropic_tools_to_openai_tools(tools: Any) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    if tools is None:
        return converted
    if not isinstance(tools, list):
        raise ValueError("tools must be an array")

    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("Each tool must be an object")
        if is_web_search_tool(tool):
            continue
        if not tool.get("name") or "input_schema" not in tool:
            raise ValueError("Tool name and input_schema are required")
        converted.append({
            "type": "function",
            "function": {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "parameters": tool["input_schema"],
            },
        })
    return converted


def anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if not isinstance(tool_choice, dict):
        raise ValueError("Anthropic tool_choice must be an object")
    if not isinstance(tool_choice.get("disable_parallel_tool_use", False), bool):
        raise ValueError("disable_parallel_tool_use must be a boolean")
    choice_type = tool_choice.get("type")
    if choice_type == "any":
        return "required"
    if choice_type == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    if choice_type in ("none", "auto"):
        return choice_type
    raise ValueError("Unsupported Anthropic tool_choice")


def tool_result_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return anthropic_content_to_text(content)
    if content is None:
        return ""
    return str(content)


def escape_tool_result_attr(value: Any) -> str:
    return str(value or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def anthropic_tool_results_visible_text(tool_results: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    for result in tool_results:
        attrs = [f'tool_use_id="{escape_tool_result_attr(result.get("tool_use_id", ""))}"']
        if "is_error" in result:
            attrs.append(f'is_error="{str(bool(result.get("is_error"))).lower()}"')
        content = tool_result_content_to_text(result.get("content", ""))
        blocks.append(f"<tool_result {' '.join(attrs)}>\n{content}\n</tool_result>")
    return "<tool_results>\n" + "\n".join(blocks) + "\n</tool_results>" if blocks else ""


def split_anthropic_content(content: Any) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    text_parts: List[str] = []
    tool_uses: List[Dict[str, Any]] = []
    tool_results: List[Dict[str, Any]] = []
    if isinstance(content, str):
        return content, tool_uses, tool_results
    if not isinstance(content, list):
        return ("" if content is None else str(content)), tool_uses, tool_results

    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            text_parts.append(str(part))
            continue
        part_type = part.get("type")
        if part_type == "text":
            text_parts.append(part.get("text", ""))
        elif part_type == "tool_use":
            tool_uses.append(part)
        elif part_type == "tool_result":
            tool_results.append(part)
        elif part_type in ("thinking", "redacted_thinking"):
            # 历史里的 thinking 块不参与 GenAI 转换
            continue
        elif part_type in ("image", "document"):
            continue
        else:
            text_parts.append(json.dumps(part, ensure_ascii=False))

    return "\n".join(part for part in text_parts if part), tool_uses, tool_results


def anthropic_message_to_genai_messages(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    """保留消息及工具结果中的附件，将工具调用转换成平台可读文本。"""
    role = message.get("role", "user")
    if role not in ("user", "assistant", "system"):
        role = "user"

    original = message.get("content", "")
    text, tool_uses, tool_results = split_anthropic_content(original)
    attachments = []
    if isinstance(original, list):
        for part in original:
            if isinstance(part, dict) and part.get("type") in ("image", "document"):
                attachments.append(part)
    for result in tool_results:
        if not isinstance(result.get("tool_use_id"), str) or not result["tool_use_id"]:
            raise ValueError("Tool results require an explicit tool_use_id")
        nested = result.get("content")
        if isinstance(nested, list):
            attachments.extend(part for part in nested if isinstance(part, dict) and part.get("type") in ("image", "document"))

    if tool_results:
        visible_text = anthropic_tool_results_visible_text(tool_results)
        text = merge_content(visible_text, text)
        role = "user"
    elif role == "assistant" and tool_uses:
        for tool_use in tool_uses:
            if not isinstance(tool_use.get("id"), str) or not tool_use["id"]:
                raise ValueError("Tool history requires an explicit tool_use id")
            call_obj = {"calls": [{
                "id": tool_use.get("id"),
                "operation": tool_use.get("name", ""),
                "parameters": tool_use.get("input", {}),
            }]}
            call_json = escape_tool_control_markers(json.dumps(call_obj, ensure_ascii=False))
            text += f"\n{ACTION_OPEN_MARKER}{call_json}{ACTION_CLOSE_MARKER}"
        text = text.strip()

    content = merge_content(text, normalize_content(attachments)) if attachments else text
    return [{"role": role, "content": content}]


def anthropic_messages_to_genai_format(body: Dict[str, Any], token: str) -> Tuple[str, List[Dict[str, Any]], str]:
    """Convert Anthropic Messages API format to GenAI format.

    Returns:
        Tuple of (system_prompt, messages, model)
    """
    model = body.get("model", "GPT-5.5")
    # Extract system prompt
    system_texts: List[str] = []
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        system_texts.append(system)
    elif isinstance(system, list):
        for item in system:
            if isinstance(item, dict) and item.get("type") == "text":
                system_texts.append(item.get("text", ""))
            elif isinstance(item, str):
                system_texts.append(item)

    system_prompt = "\n\n".join(system_texts)

    # Convert messages
    messages: List[Dict[str, Any]] = []
    for message in body.get("messages", []):
        if not isinstance(message, dict):
            continue
        messages.extend(anthropic_message_to_genai_messages(message))

    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})

    openai_tools = anthropic_tools_to_openai_tools(body.get("tools"))
    tool_choice = anthropic_tool_choice_to_openai(body.get("tool_choice"))
    validate_tool_request(openai_tools, tool_choice, not (body.get("tool_choice") or {}).get("disable_parallel_tool_use", False))
    if openai_tools and tool_choice != "none":
        messages = inject_tool_prompt(messages, openai_tools, tool_choice,
                                      not (body.get("tool_choice") or {}).get("disable_parallel_tool_use", False))

    return system_prompt, messages, model


def filter_thinking_and_dsml(text: str) -> str:
    """Filter out <think>...</think> blocks.

    DSML 工具标签不再在这里删除：它们由 tools.parsing 统一归一化，
    在检测之前删掉标签会让解析器看不到工具调用。
    """
    return re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)


def partial_marker_start(text: str, position: int, marker: str) -> int:
    marker = marker.lower()
    for index in range(len(text) - 1, position - 1, -1):
        suffix = text[index:].lower()
        if marker.startswith(suffix) and suffix != marker:
            return index
    return -1


def filter_thinking_text_delta(text: str, state: Dict[str, Any]) -> str:
    """Filter thinking markup even when <think> spans multiple stream chunks."""
    pending = state.pop("pending_think_open", "")
    if pending:
        text = pending + text

    output: List[str] = []
    position = 0
    while position < len(text):
        lower = text.lower()
        if state.get("in_thinking"):
            close_index = lower.find(THINK_CLOSE, position)
            if close_index < 0:
                return "".join(output)
            position = close_index + len(THINK_CLOSE)
            state["in_thinking"] = False
            continue

        open_index = lower.find(THINK_OPEN_PREFIX, position)
        stray_close_index = lower.find(THINK_CLOSE, position)
        if stray_close_index >= 0 and (open_index < 0 or stray_close_index < open_index):
            output.append(text[position:stray_close_index])
            position = stray_close_index + len(THINK_CLOSE)
            continue

        if open_index < 0:
            partial_open = partial_marker_start(text, position, THINK_OPEN_PREFIX)
            if partial_open >= 0:
                output.append(text[position:partial_open])
                state["pending_think_open"] = text[partial_open:]
            else:
                output.append(text[position:])
            break

        output.append(text[position:open_index])
        tag_end = text.find(">", open_index)
        if tag_end < 0:
            state["in_thinking"] = True
            break
        position = tag_end + 1
        state["in_thinking"] = True

    return filter_thinking_and_dsml("".join(output))


def _filter_thinking_outside_code(text: str, state: Dict[str, Any]) -> str:
    parts = []
    cursor = 0
    for quotation in CODE_QUOTE_RE.finditer(text):
        parts.append(filter_thinking_text_delta(text[cursor:quotation.start()], state))
        if not state.get("in_thinking"):
            parts.append(quotation.group())
        cursor = quotation.end()
    parts.append(filter_thinking_text_delta(text[cursor:], state))
    return "".join(parts)


def parse_tool_arguments(arguments: Any) -> Dict[str, Any]:
    """只解码真实 JSON 对象，不修补或改写工具参数。"""
    parsed = strict_json_loads(arguments) if isinstance(arguments, str) else arguments
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return parsed


def stream_genai_as_anthropic(
    messages: List[Dict[str, Any]], model: str, max_tokens: int, token: str, config: Any,
    tools=None, tool_choice=None, parallel_tool_calls=True, upstream_options=None,
) -> Generator[str, None, None]:
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    input_tokens = estimate_messages_tokens(messages)
    history = list(messages)
    chat_info = ""
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") == "user":
            chat_info = flatten_message_content(history.pop(index).get("content", ""))
            break

    def sse(event, data):
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield sse("message_start", {"type": "message_start", "message": {
        "id": message_id, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
    }})
    index = -1
    block_type = None
    output_tokens = 0
    thinking_state = {"in_thinking": False}

    def close_block():
        if block_type == "thinking":
            yield sse("content_block_delta", {"type": "content_block_delta", "index": index,
                "delta": {"type": "signature_delta", "signature": COMPAT_THINKING_SIGNATURE}})
        if block_type is not None:
            yield sse("content_block_stop", {"type": "content_block_stop", "index": index})

    source = iter_genai_stream(chat_info, history, model, max_tokens, config, token=token,
                               upstream_options=upstream_options, recover_empty=bool(tools))
    events = validated_events(source, tools, tool_choice, parallel_tool_calls)
    try:
        for event in events:
            if event["type"] == "heartbeat":
                yield sse("ping", {"type": "ping"})
                continue
            if event["type"] == "error":
                yield sse("error", {"type": "error", "error": {
                    "type": "api_error", "message": event["message"],
                    "code": event.get("code", "upstream_error"),
                }})
                return
            if event["type"] == "delta":
                for field, target in (("reasoning", "thinking"), ("content", "text")):
                    value = event.get(field)
                    if not value:
                        continue
                    if target == "text":
                        value = _filter_thinking_outside_code(value, thinking_state)
                    if not value:
                        continue
                    if block_type != target:
                        yield from close_block()
                        index += 1
                        block_type = target
                        block = {"type": target, target: ""}
                        if target == "thinking":
                            block["signature"] = ""
                        yield sse("content_block_start", {"type": "content_block_start", "index": index, "content_block": block})
                    output_tokens += estimate_text_tokens(value)
                    yield sse("content_block_delta", {"type": "content_block_delta", "index": index,
                        "delta": {"type": f"{target}_delta", target: value}})
                continue
            if event["type"] == "done":
                yield from close_block()
                calls = event["tool_calls"]
                for call in calls:
                    index += 1
                    function = call["function"]
                    yield sse("content_block_start", {"type": "content_block_start", "index": index,
                        "content_block": {"type": "tool_use", "id": call["id"], "name": function["name"], "input": {}}})
                    yield sse("content_block_delta", {"type": "content_block_delta", "index": index,
                        "delta": {"type": "input_json_delta", "partial_json": function["arguments"]}})
                    yield sse("content_block_stop", {"type": "content_block_stop", "index": index})
                stop = "tool_use" if calls else ("max_tokens" if event.get("finish_reason") == "length" else "end_turn")
                yield sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens}})
                yield sse("message_stop", {"type": "message_stop"})
                return
    finally:
        events.close()
