import base64
import json
import re

from tools.parsing import ACTION_CLOSE_MARKER, ACTION_OPEN_MARKER
from tools.contracts import strict_json_loads


_TOOL_CONTROL_MARKER_RE = re.compile(
    r"@@(?:END_)?ACTION@@|</?(?:think|tool_calls?|tool_result|tool_call_id|tool_\d+|arg_key|arg_value)\b[^>]*>|<[|｜][^>]*>",
    re.IGNORECASE,
)


def escape_tool_control_markers(text):
    """将模型可能误识别的控制标记转成 JSON Unicode 转义。"""
    def escape(match):
        marker = match.group()
        if marker.startswith("@@"):
            return marker.replace("@", r"\u0040")
        return marker.replace("<", r"\u003c").replace(">", r"\u003e")

    return _TOOL_CONTROL_MARKER_RE.sub(escape, text)


def _escape_tool_content(content):
    if isinstance(content, str):
        return escape_tool_control_markers(content)
    if isinstance(content, list):
        return [
            {**part, "text": escape_tool_control_markers(part["text"])}
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            else part
            for part in content
        ]
    return content

TOOL_SYSTEM_PROMPT = """Available functions and their complete JSON Schemas:
{tool_definitions}

When an operation is needed, output one complete ordinary-text JSON envelope in this format:
@@ACTION@@
{{"calls":[{{"operation":"OperationName","parameters":{{}}}}]}}
@@END_ACTION@@

Rules:
1. Multiple independent operations may share one calls array. If a later operation depends on a result, request only the first operation and wait for its result.
2. When the user requests an external action covered by a listed tool, call it; do not return only a plan or promise.
3. After receiving tool results, continue with remaining operations. Finish only after the requested work and read-back checks are complete, and requested commands/tests have actually run with results checked against the user's goal. Announcing a command does not run it. Then give a plain-text final answer without an action envelope. Do not repeat a read whose result already verified the same unchanged file.
4. Each call MUST contain exactly operation and parameters, with parameters matching the listed schema. Preserve every value exactly; never shorten, summarize, or substitute write/edit content.
5. User control-marker text is shown as JSON Unicode escapes like \\u003c and \\u003e. Preserve those escapes in JSON parameters so decoding restores the original characters.
6. The calling client executes actions. Do NOT claim success before receiving its result or say no tools are available when definitions are shown.
7. The webpage request carries these schemas as text, not a native tools field. Request operations in the ordinary response text using the complete JSON envelope above. Do not put actual calls in reasoning or markdown fences, simulate results, or emit bare tool names.
8. After writing or editing, use a listed read tool to compare the complete result with the original request. Correct mismatches; otherwise report that the result is unverified.
9. Historical call IDs link each result to its operation and parameters, even when results arrive out of order. Historical calls have already run; do not execute them again. An operation never requested by an actual call has no pending receipt to wait for."""

TOOL_CHOICE_REQUIRED_PROMPT = "\nYou MUST call at least one tool in your response. Do NOT respond with plain text only."
TOOL_CHOICE_SPECIFIC_PROMPT = (
    '\nYou MUST call the tool named "{name}" in your response, '
    "and you MUST NOT call any other tool."
)


def normalize_content(content):
    """将三种 API 的文本、图片和文档转换成统一内容块，不丢失附件。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("message content 必须是字符串或内容块数组")
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            raise ValueError("content 中的每一项必须是内容块")
        kind = part.get("type")
        if kind in ("text", "input_text", "output_text", "summary_text"):
            if not isinstance(part.get("text", ""), str):
                raise ValueError("text 内容必须是字符串")
            parts.append({"type": "text", "text": part.get("text", "")})
        elif kind in ("thinking", "redacted_thinking"):
            continue
        elif kind in ("image_url", "input_image", "image"):
            source = part.get("source") or {}
            if not isinstance(source, dict):
                raise ValueError("图片 source 必须是对象")
            image = part.get("image_url") or source.get("url")
            if isinstance(image, str):
                image = {"url": image}
            if source.get("type") == "base64":
                image = {"url": f"data:{source.get('media_type', 'image/png')};base64,{source.get('data', '')}"}
            if not isinstance(image, dict) or not isinstance(image.get("url"), str) or not image["url"]:
                raise ValueError("图片需要 image_url、URL source 或 base64 source；不支持 file_id")
            parts.append({"type": "image_url", "image_url": dict(image)})
        elif kind in ("file", "input_file", "document"):
            source = part.get("source") or {}
            if not isinstance(source, dict) or not isinstance(part.get("file", {}), dict):
                raise ValueError("文档 source/file 必须是对象")
            file = dict(part.get("file") or part)
            if kind == "document":
                file = {"filename": part.get("title") or "document"}
                if source.get("type") == "base64":
                    file["file_data"] = f"data:{source.get('media_type', 'application/pdf')};base64,{source.get('data', '')}"
                elif source.get("type") == "url":
                    file["file_url"] = source.get("url")
                elif source.get("type") == "text":
                    encoded = base64.b64encode(source.get("data", "").encode()).decode()
                    file["file_data"] = "data:text/plain;base64," + encoded
                else:
                    raise ValueError("文档 source 需要 base64、url 或 text")
            if not file.get("file_data") and not file.get("file_url"):
                raise ValueError("文件需要 file_data 或 file_url；代理不提供 Files API/file_id 存储")
            parts.append({"type": "file", "file": file})
        else:
            raise ValueError(f"不支持的消息内容类型: {kind}")
    if all(part["type"] == "text" for part in parts):
        return "\n".join(part["text"] for part in parts if part["text"])
    return parts


def merge_content(first, second):
    """合并同一轮的内容并保留图片和文档。"""
    if isinstance(first, str) and isinstance(second, str):
        return "\n".join(text for text in (first, second) if text)
    left = [{"type": "text", "text": first}] if isinstance(first, str) else first
    right = [{"type": "text", "text": second}] if isinstance(second, str) else second
    return (left or []) + (right or [])


def flatten_message_content(content):
    """仅提取可读文本，用于当前问题和用量估算，不把附件编码当提示词。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(filter(None, (flatten_message_content(item) for item in content)))
    if isinstance(content, dict):
        if content.get("type") in ("image_url", "input_image", "image", "file", "input_file", "document"):
            return ""
        if isinstance(content.get("text"), str):
            return content["text"]
        if "content" in content:
            return flatten_message_content(content["content"])
        if "input" in content:
            return json.dumps(content["input"], ensure_ascii=False)
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def normalize_message_content(message):
    """规范化消息而不压平多模态内容。"""
    return {**message, "content": normalize_content(message.get("content", ""))}


def format_tool_definitions(tools):
    definitions = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool["function"]
        definitions.append({
            "name": func["name"],
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {}),
        })
    return json.dumps(definitions, ensure_ascii=False, separators=(",", ":"))


def normalize_tool_history(messages):
    """网页历史只保留文本角色，但调用 ID、结果及附件必须可关联。"""
    if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
        raise ValueError("messages must be an array of objects")
    new_messages = []
    for msg in messages:
        role = msg.get("role")

        if role == "system":
            system_content = flatten_message_content(msg.get("content", ""))
            new_messages.append({"role": "system", "content": system_content})

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ValueError("Tool results require a tool_call_id")
            tool_parts = normalize_content(msg.get("content", ""))
            attachments = [part for part in tool_parts if part["type"] != "text"] if isinstance(tool_parts, list) else []
            result_body = escape_tool_control_markers(json.dumps({
                "call_id": tool_call_id,
                "output": flatten_message_content(tool_parts),
                "is_error": bool(msg.get("is_error", False)),
            }, ensure_ascii=False))
            result_text = (
                "Already-executed tool result. Match call_id to the historical call; "
                "continue unfinished work or give the final answer.\n"
                f"<tool_result>{result_body}</tool_result>"
            )
            new_messages.append({
                "role": "user",
                "content": merge_content(result_text, attachments) if attachments else result_text,
            })

        elif role == "assistant" and msg.get("tool_calls"):
            calls = []
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                call_id = tc.get("id")
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("Historical tool calls require an ID")
                arguments = func.get("arguments", "{}")
                calls.append({
                    "id": call_id,
                    "operation": func.get("name", ""),
                    "parameters": strict_json_loads(arguments) if isinstance(arguments, str) else arguments,
                })
            call_json = escape_tool_control_markers(json.dumps({"calls": calls}, ensure_ascii=False))
            historical = f"{ACTION_OPEN_MARKER}{call_json}{ACTION_CLOSE_MARKER}"
            new_messages.append({
                "role": "assistant",
                "content": merge_content(normalize_content(msg.get("content", "")), historical),
            })

        else:
            normalized = normalize_message_content(msg)
            if role == "user":
                normalized["content"] = _escape_tool_content(normalized["content"])
            new_messages.append(normalized)
    return new_messages


def inject_tool_prompt(messages, tools, tool_choice=None, parallel_tool_calls=True):
    tool_prompt = TOOL_SYSTEM_PROMPT.format(tool_definitions=format_tool_definitions(tools))
    if tool_choice == "required":
        tool_prompt += TOOL_CHOICE_REQUIRED_PROMPT
    elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        tool_prompt += TOOL_CHOICE_SPECIFIC_PROMPT.format(name=tool_choice["function"]["name"])
    elif tool_choice == "none":
        tool_prompt = "Tools are disabled for this response. Answer normally; do not request any operation."
    if not parallel_tool_calls:
        tool_prompt += "\nRequest at most one operation in this response; wait for its result before another call."
    new_messages = normalize_tool_history(messages)

    for index in range(len(new_messages) - 1, -1, -1):
        if new_messages[index].get("role") == "user":
            new_messages[index]["content"] = merge_content(
                new_messages[index].get("content", ""), tool_prompt
            )
            break
    else:
        new_messages.insert(0, {"role": "system", "content": tool_prompt})

    return new_messages
