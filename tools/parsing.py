"""只解码明确、完整的工具协议；普通示例和损坏调用不推断为操作。"""

import json
import re
import uuid

from tools.contracts import ToolContractError, strict_json_decoder, strict_json_loads
from tools.kimi import parse_kimi_output


class ToolCallParseError(ToolContractError):
    """工具调用正文不完整或无法无损解析。"""

    code = "tool_call_parse_error"


DSML_OPEN_PREFIX_FULLWIDTH = "<｜DSML｜"
DSML_OPEN_PREFIX_ASCII = "<|DSML|"
ACTION_OPEN_MARKER = "@@ACTION@@"
ACTION_CLOSE_MARKER = "@@END_ACTION@@"
TOOL_CALL_OPEN_MARKERS = (
    ACTION_OPEN_MARKER, "<|open|>", "<tool_call", "<tool_",
    DSML_OPEN_PREFIX_FULLWIDTH, DSML_OPEN_PREFIX_ASCII,
    "@@ACTION@", ACTION_CLOSE_MARKER,
)
INLINE_CODE_RE = re.compile(r"(?<!`)(?P<ticks>`+)(?!`)[^\n]*?(?<!`)(?P=ticks)(?!`)")
CODE_QUOTE_RE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^ {0,3}(?P=fence)[ \t]*(?:\n|$)"
    + "|" + INLINE_CODE_RE.pattern,
    re.MULTILINE | re.DOTALL,
)
CODE_OPEN_RE = re.compile(r"`+|^ {0,3}~{3,}", re.MULTILINE)
_DATA_OPEN_RE = re.compile(CODE_OPEN_RE.pattern + r"|<think>", re.MULTILINE)


def _tool_stream_boundary(text):
    cursor = 0
    while cursor < len(text):
        positions = [text.find(marker, cursor) for marker in TOOL_CALL_OPEN_MARKERS]
        marker_pos = min((position for position in positions if position >= 0), default=-1)
        data_open = _DATA_OPEN_RE.search(text, cursor)
        if data_open is None or (marker_pos >= 0 and marker_pos < data_open.start()):
            return marker_pos, 0
        if data_open[0] == "<think>":
            end = text.find("</think>", data_open.end())
            if end < 0:
                return -1, len(text) - data_open.start()
            cursor = end + len("</think>")
        else:
            quotation = CODE_QUOTE_RE.match(text, data_open.start())
            if quotation is None:
                return -1, len(text) - data_open.start()
            cursor = quotation.end()
    return -1, 0


def find_tool_call_open(text):
    """返回代码引用外最早的显式控制标记起点。"""
    return _tool_stream_boundary(text)[0]


def _tag_prefix_len(text, tag):
    for length in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


def action_call_prefix_len(text):
    length = max(_tag_prefix_len(text, ACTION_OPEN_MARKER), _tag_prefix_len(text, "<|open|>"))
    # 单独的 @、@@、<、<| 仍是普通文本，只有可辨识的协议前缀才判截断。
    return length if length >= 3 else 0


def tool_call_prefix_len(text):
    """保留未完成的控制标记或引用，防止跨 chunk 的工具泄漏为正文。"""
    return max([_tool_stream_boundary(text)[1]] + [
        _tag_prefix_len(text, marker) for marker in (*TOOL_CALL_OPEN_MARKERS, "<think>")
    ])


def _call(name, arguments, allowed_names, call_id=None):
    if not isinstance(name, str) or not name or (allowed_names is not None and name not in allowed_names):
        raise ToolCallParseError("Tool call is not allowed")
    if not isinstance(arguments, dict):
        raise ToolCallParseError("Tool arguments must be a JSON object")
    if call_id is not None and (not isinstance(call_id, str) or not call_id):
        raise ToolCallParseError("Invalid tool call ID")
    return {
        "id": call_id or "call_" + uuid.uuid4().hex[:24],
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False, allow_nan=False)},
    }


def _action(text, allowed_names):
    if not text.startswith(ACTION_OPEN_MARKER):
        raise ToolCallParseError("Action envelope marker is malformed or misplaced")
    body = text[len(ACTION_OPEN_MARKER):].lstrip()
    try:
        payload, end = strict_json_decoder().raw_decode(body)
    except (ValueError, RecursionError) as exc:
        raise ToolCallParseError("Action envelope contains invalid or incomplete JSON") from exc
    if body[end:].strip() != ACTION_CLOSE_MARKER:
        raise ToolCallParseError("Expected one complete final action envelope")
    if not isinstance(payload, dict) or set(payload) != {"calls"}:
        raise ToolCallParseError("Action envelope must contain only calls")
    if not isinstance(payload["calls"], list) or not payload["calls"]:
        raise ToolCallParseError("Action calls must be a non-empty array")
    calls = []
    for call in payload["calls"]:
        if not isinstance(call, dict) or not {"operation", "parameters"} <= set(call) or set(call) - {"operation", "parameters", "id"}:
            raise ToolCallParseError("Action calls require operation and parameters")
        calls.append(_call(call["operation"], call["parameters"], allowed_names, call.get("id")))
    return calls


# 只转换标签本身；JSON 字符串和原始参数值绝不做全局替换。
_OPEN_CALL = re.compile(
    r'<(?P<xml>tool_call|tool_\d+)\b(?P<attrs>[^>]*)>'
    r'|<[|｜]DSML[|｜](?P<dsml>tool_call|call|_call|l_call)?(?P<dsattrs>\s+[^>]*)?>'
)
_CLOSE_CALL = re.compile(r'</(?:tool_call|tool_\d+|[|｜]DSML[|｜](?:tool_call|call|_call|l_call)?)\s*>')
_WRAPPER_OPEN = re.compile(r'<(?:tool_calls|[|｜]DSML[|｜](?:tool_calls|function_calls))>')
_WRAPPER_CLOSE = re.compile(r'</(?:tool_calls|[|｜]DSML[|｜](?:tool_calls|function_calls))>')
_NAME_ATTRIBUTE = re.compile(r'''\s+name\s*=\s*["']([^"']+)["']\s*''')
_TAGGED_ARGUMENT = re.compile(r'<arg_key>([^<]+)</arg_key><arg_value>(.*?)</arg_value>', re.DOTALL)
_DSML_INVOKE = re.compile(r'<[|｜]DSML[|｜]invoke name="([^"]+)">')
_DSML_INVOKE_END = re.compile(r'</[|｜]DSML[|｜]invoke>')
_DSML_PARAMETER = re.compile(r'<[|｜]DSML[|｜]parameter name="([^"]+)" string="(true|false)">')
_DSML_PARAMETER_END = re.compile(r'</[|｜]DSML[|｜]parameter>')


def _dsml_invoke(text, opening, allowed_names):
    """解析 DeepSeek 的明确 invoke/parameter 通道，不修补标签或参数。"""
    cursor = opening.end()
    arguments = {}
    while True:
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        closing = _DSML_INVOKE_END.match(text, cursor)
        if closing:
            return _call(opening[1], arguments, allowed_names), closing.end()
        parameter = _DSML_PARAMETER.match(text, cursor)
        if parameter is None or parameter[1] in arguments:
            raise ToolCallParseError("DSML parameter is malformed, duplicated, or incomplete")
        value_start = parameter.end()
        if parameter[2] == "true":
            end = _DSML_PARAMETER_END.search(text, value_start)
            if end is None:
                raise ToolCallParseError("DSML string parameter is incomplete")
            value = text[value_start:end.start()]
        else:
            while value_start < len(text) and text[value_start].isspace():
                value_start += 1
            value, size = strict_json_decoder().raw_decode(text[value_start:])
            end_start = value_start + size
            while end_start < len(text) and text[end_start].isspace():
                end_start += 1
            end = _DSML_PARAMETER_END.match(text, end_start)
            if end is None:
                raise ToolCallParseError("DSML JSON parameter is incomplete")
        arguments[parameter[1]] = value
        cursor = end.end()


def _xml_payload(body, name):
    raw = body.strip()
    if raw.startswith("{"):
        payload = strict_json_loads(raw)
        if name is not None:
            return name, payload
        if isinstance(payload, dict) and set(payload) == {"name", "arguments"}:
            return payload["name"], payload["arguments"]
        if isinstance(payload, dict) and len(payload) == 1:
            return next(iter(payload.items()))
        raise ToolCallParseError("XML tool body must contain name and arguments")
    named_json = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_.-]*)\s*\n\s*(\{.*\})', raw, re.DOTALL)
    if named_json and name is None:
        return named_json[1], strict_json_loads(named_json[2])
    # 保留完整旧版 arg_key/arg_value 格式；不猜类型，也不修复不闭合标签。
    if name is None:
        named = re.match(r'([A-Za-z_][A-Za-z0-9_.-]*)\s*(?=<arg_key>)', raw)
        if not named:
            raise ToolCallParseError("Unrecognized explicit tool body")
        name, raw = named[1], raw[named.end():]
    args = {}
    cursor = 0
    while cursor < len(raw):
        if not raw[cursor:].strip():
            break
        match = _TAGGED_ARGUMENT.match(raw, cursor)
        if not match or match[1] in args:
            raise ToolCallParseError("Tool arguments are malformed or duplicated")
        args[match[1]] = match[2]
        cursor = match.end()
        while cursor < len(raw) and raw[cursor].isspace():
            cursor += 1
    if not args:
        raise ToolCallParseError("Missing explicit tool arguments")
    return name, args


def _xml_calls(text, allowed_names):
    cursor = 0
    wrapper = _WRAPPER_OPEN.match(text)
    if wrapper:
        cursor = wrapper.end()
    calls = []
    while True:
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if wrapper and _WRAPPER_CLOSE.match(text, cursor):
            close = _WRAPPER_CLOSE.match(text, cursor)
            if close[0] != "</" + wrapper[0][1:]:
                raise ToolCallParseError("Tool wrapper closing tag does not match")
            cursor = close.end()
            break
        if cursor == len(text):
            if wrapper:
                raise ToolCallParseError("Tool batch wrapper is incomplete")
            break
        invoke = _DSML_INVOKE.match(text, cursor)
        if invoke:
            call, cursor = _dsml_invoke(text, invoke, allowed_names)
            calls.append(call)
            continue
        opening = _OPEN_CALL.match(text, cursor)
        if not opening:
            raise ToolCallParseError("Unparsed content inside tool batch")
        attrs = opening["attrs"] if opening["xml"] else opening["dsattrs"]
        attrs = attrs or ""
        name = None
        if attrs.strip():
            attr = _NAME_ATTRIBUTE.fullmatch(attrs)
            if not attr:
                raise ToolCallParseError("Tool attributes are malformed")
            name = attr[1]
        body_start = opening.end()
        # 对 JSON 参数先找 JSON 边界，防止文件内容中的关闭标签提前结束工具。
        json_start = body_start
        while json_start < len(text) and text[json_start].isspace():
            json_start += 1
        named = re.match(r'[A-Za-z_][A-Za-z0-9_.-]*\s*\n\s*(?=\{)', text[json_start:])
        value_start = json_start + (named.end() if named else 0)
        if text[value_start:value_start + 1] == "{":
            try:
                _, size = strict_json_decoder().raw_decode(text[value_start:])
            except (ValueError, RecursionError) as exc:
                raise ToolCallParseError("Tool JSON is invalid or incomplete") from exc
            close_start = value_start + size
            while close_start < len(text) and text[close_start].isspace():
                close_start += 1
            closing = _CLOSE_CALL.match(text, close_start)
        else:
            closing = _CLOSE_CALL.search(text, body_start)
        if closing is None:
            raise ToolCallParseError("Tool call closing tag is missing")
        # DSML 网页存在混合 tool_call 关闭形式；编号标签则必须对应编号。
        if opening["xml"] and opening["xml"].startswith("tool_") and opening["xml"] != "tool_call":
            if closing[0] != "</" + opening["xml"] + ">":
                raise ToolCallParseError("Numbered tool closing tag does not match")
        name, args = _xml_payload(text[body_start:closing.start()], name)
        calls.append(_call(name, args, allowed_names))
        cursor = closing.end()
    if not calls or text[cursor:].strip():
        raise ToolCallParseError("Tool batch is empty or has trailing content")
    return calls


def extract_tool_calls(content, allowed_tool_names=None):
    if not isinstance(content, str):
        return None, content
    start = find_tool_call_open(content)
    if start < 0:
        if action_call_prefix_len(content) and not _tool_stream_boundary(content)[1]:
            raise ToolCallParseError("Tool marker is incomplete")
        return None, content
    prefix, body = content[:start], content[start:].strip()
    try:
        if body.startswith("@@"):
            calls = _action(body, allowed_tool_names)
        elif body.startswith("<|open|>"):
            native_text, raw_calls = parse_kimi_output(body, allowed_tool_names)
            calls = [_call(call["name"], call["arguments"], allowed_tool_names) for call in raw_calls]
            prefix += native_text
        else:
            calls = _xml_calls(body, allowed_tool_names)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, ToolCallParseError):
            raise
        raise ToolCallParseError(str(exc)) from exc
    return calls or None, prefix.strip() or None
