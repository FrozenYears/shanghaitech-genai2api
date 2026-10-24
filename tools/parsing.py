import ast
import json
import logging
import re
import uuid

from tools.kimi import parse_kimi_output

logger = logging.getLogger(__name__)


class ToolCallParseError(ValueError):
    """工具调用正文不完整或无法无损解析。"""



# 部分上游模型会自行输出 GenAI 原生的 DSML 工具标记，而不是代理注入的
# <tool_call> 格式；同一模型还可能把标签写成 call / _call 等变体。
# 这里把所有以 call 结尾的 DSML 标签归一化成 <tool_call>，让解析器与流式检测统一处理。
# 同时接受全角 ｜ 与半角 | 两种竖线。
DSML_OPEN_PREFIX_FULLWIDTH = "<｜DSML｜"
DSML_OPEN_PREFIX_ASCII = "<|DSML|"
ACTION_OPEN_MARKER = "@@ACTION@@"
ACTION_CLOSE_MARKER = "@@END_ACTION@@"
TOOL_CALL_OPEN_MARKERS = (
    ACTION_OPEN_MARKER,
    "<|open|>",
    "<tool_call",
    DSML_OPEN_PREFIX_FULLWIDTH,
    DSML_OPEN_PREFIX_ASCII,
)

INLINE_CODE_RE = re.compile(r"(?<!`)(?P<ticks>`+)(?!`)[^\n]*?(?<!`)(?P=ticks)(?!`)")
CODE_QUOTE_RE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^ {0,3}(?P=fence)[ \t]*(?:\n|$)"
    + "|" + INLINE_CODE_RE.pattern,
    re.MULTILINE | re.DOTALL,
)
ACTION_OR_CODE_RE = re.compile(
    CODE_QUOTE_RE.pattern + r"|@@ACTION@@|@@ACTION@(?!@)|@@END_ACTION@@",
    re.MULTILINE | re.DOTALL,
)
CODE_OPEN_RE = re.compile(r"`+|^ {0,3}~{3,}", re.MULTILINE)

DSML_OPEN_TAG_RE = re.compile(r"<\s*[|｜]\s*DSML\s*[|｜]\s*([A-Za-z_][A-Za-z0-9_]*)?\s*>", re.IGNORECASE)
DSML_CLOSE_TAG_RE = re.compile(r"</\s*[|｜]\s*DSML\s*[|｜]\s*([A-Za-z_][A-Za-z0-9_]*)?\s*>", re.IGNORECASE)
# 变体：<｜DSML｜ name="read">{...}，工具名写在属性里。
DSML_ATTR_OPEN_TAG_RE = re.compile(
    r"<\s*[|｜]\s*DSML\s*[|｜]\s*name\s*=\s*[\"']([^\"']+)[\"']\s*>",
    re.IGNORECASE,
)
# 带属性的 DSML 标签（如 <｜DSML｜invoke name="x">），仅用于删除非 call 标签。
DSML_ANY_TAG_RE = re.compile(r"</?\s*[|｜]\s*DSML\s*[|｜]\s*[^>]*>", re.IGNORECASE)


def _is_tool_call_tag_name(name):
    # tool_call / call / _call / l_call 都算；空标签名 <｜DSML｜> 当普通工具标签处理；
    # 复数 tool_calls 是外层包裹标签，不算。
    if not name:
        return True
    return name.lower().endswith("call")


def normalize_tool_call_tags(text):
    """把 DSML 工具标记归一化为 <tool_call>，其余文本原样保留。"""
    if not text or "DSML" not in text:
        return text
    changed = False

    def replace_open(match):
        nonlocal changed
        if _is_tool_call_tag_name(match.group(1)):
            changed = True
            return "<tool_call>"
        return match.group(0)

    def replace_close(match):
        nonlocal changed
        if _is_tool_call_tag_name(match.group(1)):
            changed = True
            return "</tool_call>"
        return match.group(0)

    normalized = DSML_OPEN_TAG_RE.sub(replace_open, text)
    normalized = DSML_CLOSE_TAG_RE.sub(replace_close, normalized)
    # 工具名在属性里的变体：归一化成 <tool_call name="...">，复用属性解析路径。
    normalized = DSML_ATTR_OPEN_TAG_RE.sub(r'<tool_call name="\1">', normalized)
    if changed or normalized != text:
        logger.info("Normalized DSML tool tag(s) to <tool_call>")
    return normalized


def strip_dsml_tags(text):
    """删除所有 DSML 标签（含带属性的 invoke / parameter），保留其中正文。"""
    if not text or "DSML" not in text:
        return text
    return DSML_ANY_TAG_RE.sub("", text)


def _tool_stream_boundary(text):
    cursor = 0
    while cursor < len(text):
        positions = [text.find(marker, cursor) for marker in TOOL_CALL_OPEN_MARKERS]
        marker_pos = min((position for position in positions if position >= 0), default=-1)
        code_open = CODE_OPEN_RE.search(text, cursor)
        if code_open is None or (marker_pos >= 0 and marker_pos < code_open.start()):
            return marker_pos, 0
        quotation = CODE_QUOTE_RE.match(text, code_open.start())
        if quotation is None:
            # 在闭合前连同起始反引号保留，不能从引用内的标记开始缓存工具调用。
            return -1, len(text) - code_open.start()
        cursor = quotation.end()
    return -1, 0


def find_tool_call_open(text):
    """返回代码引用外最早的显式工具调用起点。"""
    return _tool_stream_boundary(text)[0]


def action_call_prefix_len(text):
    """返回不完整 action 或 K3 原生开始标记的尾部长度。"""
    return max(_tag_prefix_len(text, ACTION_OPEN_MARKER), _tag_prefix_len(text, "<|open|>"))



def tool_call_prefix_len(text):
    """保留末尾未完成的工具标记或代码引用，等待后续流片段。"""
    longest = _tool_stream_boundary(text)[1]
    for marker in TOOL_CALL_OPEN_MARKERS:
        longest = max(longest, _tag_prefix_len(text, marker))
    return longest


COMMON_TOOL_ARG_KEYS = (
    "notebook_path",
    "new_string",
    "old_string",
    "file_path",
    "head_limit",
    "output_mode",
    "edit_mode",
    "description",
    "command",
    "pattern",
    "offset",
    "limit",
    "path",
)

COMMON_TOOL_NAMES = (
    "NotebookEdit",
    "TodoWrite",
    "MultiEdit",
    "WebFetch",
    "WebSearch",
    "Bash",
    "Read",
    "Glob",
    "Grep",
    "Edit",
    "Write",
    "LS",
)

TOOL_ARG_KEYS_BY_TOOL = {
    "Bash": ("command", "description"),
    "Read": ("file_path", "path", "offset", "limit"),
    "Glob": ("pattern", "path"),
    "Grep": ("pattern", "path", "output_mode", "head_limit"),
    "LS": ("path",),
}


def strip_think_blocks(content):
    return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()


def _extract_json_object(raw):
    start = raw.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(raw)):
            ch = raw[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start:idx + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        break
        start = raw.find("{", start + 1)
    return None


def _extract_balanced_brace_segment(text, start):
    if start < 0 or start >= len(text) or text[start] != "{":
        return None, None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1], idx + 1

    return None, None


def _sanitize_json_escapes(raw):
    return re.sub(r'\\(?=[^"\\/bfnrtu])', r'\\\\', raw)


def _load_relaxed_json(raw):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    sanitized = _sanitize_json_escapes(raw)
    if sanitized != raw:
        try:
            return json.loads(sanitized)
        except (json.JSONDecodeError, ValueError):
            pass

    stripped = sanitized.strip()

    # 部分模型会把工具参数写成 Python 字面量（单引号、True/False/None），
    # 这里回退到 ast.literal_eval 兼容这种形态。
    if stripped[:1] in ("{", "["):
        try:
            literal = ast.literal_eval(stripped)
            if isinstance(literal, (dict, list)):
                return literal
        except (ValueError, SyntaxError):
            pass

    return None


def _clean_arg_value(value):
    cleaned = value.strip()
    cleaned = re.sub(r'</?tool_call>\s*$', '', cleaned, flags=re.DOTALL).strip()
    cleaned = re.sub(r'</?arg_value>\s*$', '', cleaned, flags=re.DOTALL).strip()

    for suffix in ('"}})', '"}}', '"})', '"}', '">', '"'):
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)].rstrip()
            break

    return cleaned


def _clean_tool_name(name):
    cleaned = name.strip()
    match = re.search(r'[A-Za-z_][A-Za-z0-9_.-]*', cleaned)
    return match.group(0) if match else cleaned


def _extract_tool_name_from_attrs(attrs):
    if not attrs:
        return None

    match = re.search(r'\bname\s*=\s*["\']([^"\']+)["\']', attrs)
    if not match:
        return None

    return _clean_tool_name(match.group(1))


def _normalize_tool_payload(payload, tool_name=None):
    if not isinstance(payload, dict):
        return None

    if "name" in payload:
        name = _clean_tool_name(payload["name"])
        if "arguments" in payload:
            arguments = payload["arguments"]
        else:
            # 有些模型会把参数直接平铺在 name 旁边（缺少 arguments 包裹）。
            arguments = {key: value for key, value in payload.items() if key != "name"}
        if name == "Bash" and isinstance(arguments, str):
            arguments = {"command": arguments}
        return {
            "name": name,
            "arguments": arguments,
        }

    if len(payload) == 1:
        candidate_name, candidate_args = next(iter(payload.items()))
        cleaned_name = _clean_tool_name(candidate_name)
        if cleaned_name and isinstance(candidate_args, dict):
            return {"name": cleaned_name, "arguments": candidate_args}

    if tool_name:
        return {"name": tool_name, "arguments": payload}

    return None


def _parse_malformed_named_tool_payload(raw, tool_name=None):
    """Recover common model-emitted JSON with unescaped quotes inside Bash command."""
    normalized = raw.strip()
    name = tool_name
    if not name:
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', normalized)
        if name_match:
            name = _clean_tool_name(name_match.group(1))
    if not name or '"arguments"' not in normalized:
        return None

    command_match = re.search(r'"command"\s*:\s*"', normalized, re.DOTALL)
    if not command_match:
        return None

    tail = normalized[command_match.end():]
    value_match = re.match(r'(?s)(.*)"\s*}\s*}?\s*$', tail)
    if not value_match:
        return None

    command = value_match.group(1).replace('\\"', '"')
    return {"name": name, "arguments": {"command": command}}


def _parse_tagged_arguments(raw, tool_name=None):
    normalized = raw.replace('\\"', '"').strip()
    if '<arg_key>' not in normalized:
        return None

    name, remainder = normalized.split('<arg_key>', 1)
    name = _clean_tool_name(name)
    if not name:
        name = tool_name
    if not name:
        return None

    remainder = re.sub(
        r'</arg_value>\s*([A-Za-z0-9_]+)\s*(?=(?:":\s*"|":\s*|="\s*|=\s*|:\s*"|:\s*))',
        r'</arg_value><arg_key>\1',
        remainder,
    )
    remainder = re.sub(
        r'"\s*,\s*"([A-Za-z0-9_]+)"\s*:',
        r'"</arg_value><arg_key>\1":',
        remainder,
    )

    arg_pattern = re.compile(
        r'<arg_key>\s*"?([^<":=]+)"?\s*'
        r'(?:</arg_key>\s*<arg_value>\s*|</arg_key>\s*"|":\s*"|":\s*|="\s*|=\s*"|=\s*|:\s*"|:\s*)'
        r'(.*?)'
        r'(?=(?:</arg_value>\s*<arg_key>)|(?:</arg_value>)|(?:<arg_key>)|(?:</tool_call>)|$)',
        re.DOTALL,
    )

    arguments = {}
    for key, value in arg_pattern.findall('<arg_key>' + remainder):
        key = key.strip()
        if not key:
            continue
        arguments[key] = _clean_arg_value(value)

    if not arguments:
        return None

    return {"name": name, "arguments": arguments}


def _parse_broken_arg_key_lines(raw, tool_name=None):
    normalized = raw.replace('\\"', '"').strip()
    if '</arg_key>' not in normalized:
        return None

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return None

    name = tool_name or _clean_tool_name(lines[0])
    if not name:
        return None

    arguments = {}
    for line in lines[1:]:
        line = re.sub(r'</?tool_call\b[^>]*>', '', line).strip()
        line = re.sub(r'</?arg_value>', '', line).strip()
        if not line:
            continue

        if '</arg_key>' in line:
            key, value = line.split('</arg_key>', 1)
            key = re.sub(r'</?arg_key>', '', key).strip().strip('"')
            if key and value:
                arguments[key] = _clean_arg_value(value)
            continue

        for key in COMMON_TOOL_ARG_KEYS:
            if line.startswith(key) and len(line) > len(key):
                value = line[len(key):]
                if value:
                    arguments[key] = _clean_arg_value(value)
                    break

    if not arguments:
        return None

    return {"name": name, "arguments": arguments}


def _extract_compact_tool_name(text, tool_name=None):
    if tool_name:
        return tool_name, text

    for candidate in COMMON_TOOL_NAMES:
        if text.startswith(candidate):
            return candidate, text[len(candidate):]

    return None, text


def _parse_compact_tool_arguments(raw, tool_name=None):
    normalized = raw.replace('\\"', '"').strip()
    normalized = re.sub(r'</?tool_call\b[^>]*>', '', normalized).strip()
    normalized = re.sub(r'</?arg_value>', '', normalized).strip()
    if not normalized:
        return None

    name, remainder = _extract_compact_tool_name(normalized, tool_name=tool_name)
    if not name or not remainder:
        return None
    remainder = remainder.strip()

    keys = TOOL_ARG_KEYS_BY_TOOL.get(name, COMMON_TOOL_ARG_KEYS)
    sorted_keys = sorted(keys, key=len, reverse=True)
    matches = []
    position = 0
    while position < len(remainder):
        matched_key = None
        for key in sorted_keys:
            if remainder.startswith(key, position):
                matched_key = key
                break
        if matched_key:
            matches.append((position, matched_key))
            position += len(matched_key)
            continue
        position += 1

    if not matches:
        return None
    if matches[0][0] != 0:
        return None

    arguments = {}
    for index, (start, key) in enumerate(matches):
        value_start = start + len(key)
        value_end = matches[index + 1][0] if index + 1 < len(matches) else len(remainder)
        value = remainder[value_start:value_end].strip()
        if value:
            arguments[key] = _clean_arg_value(value)

    if not arguments:
        return None

    return {"name": name, "arguments": arguments}


def _parse_argument_tag(raw, tool_name=None):
    if not tool_name:
        return None

    match = re.search(r'<argument>\s*(.*?)\s*</argument>', raw, re.DOTALL)
    if not match:
        return None

    argument = match.group(1).strip()
    if not argument:
        return None

    key = "command" if tool_name == "Bash" else "argument"
    return {"name": tool_name, "arguments": {key: argument}}


def _parse_tool_call_body(raw, tool_name=None):
    raw = raw.strip()
    normalized_raw = raw.replace('\\"', '"')

    call = _load_relaxed_json(raw)
    normalized_call = _normalize_tool_payload(call, tool_name=tool_name)
    if normalized_call:
        return normalized_call

    embedded_json = _extract_json_object(raw)
    normalized_call = _normalize_tool_payload(embedded_json, tool_name=tool_name)
    if normalized_call:
        return normalized_call

    embedded_json = _extract_json_object(normalized_raw)
    normalized_call = _normalize_tool_payload(embedded_json, tool_name=tool_name)
    if normalized_call:
        return normalized_call

    malformed_call = _parse_malformed_named_tool_payload(raw, tool_name=tool_name)
    if malformed_call:
        return malformed_call

    # 变体：标签内先写裸工具名，再直接跟参数 JSON（缺少 {"name": ...} 包裹）。
    named_json = re.match(
        r'^([A-Za-z_][A-Za-z0-9_.-]*)\s*\n\s*(\{.*\})\s*$',
        normalized_raw,
        re.DOTALL,
    )
    if named_json:
        arguments = _load_relaxed_json(named_json.group(2))
        if isinstance(arguments, dict):
            return {
                "name": _clean_tool_name(named_json.group(1)),
                "arguments": arguments,
            }

    tagged_arguments = _parse_tagged_arguments(raw, tool_name=tool_name)
    if tagged_arguments:
        return tagged_arguments

    broken_arg_key_lines = _parse_broken_arg_key_lines(raw, tool_name=tool_name)
    if broken_arg_key_lines:
        return broken_arg_key_lines

    compact_tool_arguments = _parse_compact_tool_arguments(raw, tool_name=tool_name)
    if compact_tool_arguments:
        return compact_tool_arguments

    argument_tag = _parse_argument_tag(raw, tool_name=tool_name)
    if argument_tag:
        return argument_tag

    inline_pairs = re.findall(
        r'([A-Za-z0-9_]+)"\s*:\s*"([^"]*)"',
        normalized_raw,
        re.DOTALL,
    )
    if inline_pairs:
        name = _clean_tool_name(
            normalized_raw.split('<arg_key>', 1)[0].split('{', 1)[0].strip()
        )
        if not name:
            name = tool_name
        if name and not name.startswith('"'):
            arguments = {
                key.strip(): value.strip()
                for key, value in inline_pairs
                if key.strip()
            }
            if arguments:
                return {"name": name, "arguments": arguments}

    name_m = re.search(r'<name>\s*(.*?)\s*</name>', raw, re.DOTALL)
    args_m = re.search(r'<arguments>\s*(.*?)\s*</arguments>', raw, re.DOTALL)
    if name_m:
        name = _clean_tool_name(name_m.group(1))
        arguments = {}
        if args_m:
            args_str = args_m.group(1).strip()
            try:
                arguments = json.loads(args_str)
            except (json.JSONDecodeError, ValueError):
                arguments = {"raw": args_str}
        return {"name": name, "arguments": arguments}

    return None


def _clean_remaining_text(text):
    if not text:
        return None

    cleaned = re.sub(r'<tool_result>.*?</tool_result>', '', text, flags=re.DOTALL)
    cleaned = re.sub(r'<tool_condition>.*?</tool_condition>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<agent-other-thinking>.*?</agent-other-thinking>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'</?tool_condition\b[^>]*>', '', cleaned)
    cleaned = re.sub(r'</?tool_call\b[^>]*>', '', cleaned)
    cleaned = re.sub(r'</?tool_calls\b[^>]*>', '', cleaned)
    cleaned = re.sub(r'</?tool_\d+>', '', cleaned)
    cleaned = re.sub(r'</?tool_utils\b[^>]*>', '', cleaned)
    cleaned = strip_dsml_tags(cleaned)
    cleaned = cleaned.replace('</think>', '')
    cleaned = re.sub(r'^\s*\[\]\s*$', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = cleaned.strip()
    return cleaned or None


def _extract_function_style_calls(cleaned, allowed_tool_names):
    call_pattern = re.compile(r'([A-Za-z_][A-Za-z0-9_.-]*)\(\s*{')
    matches = list(call_pattern.finditer(cleaned))
    if not matches:
        return [], None

    spans = []
    tool_calls = []
    for i, match in enumerate(matches):
        tool_name = _clean_tool_name(match.group(1))
        json_text, end_idx = _extract_balanced_brace_segment(cleaned, match.end() - 1)
        if not json_text:
            continue

        payload = _load_relaxed_json(json_text)
        if not isinstance(payload, dict):
            continue
        if allowed_tool_names and tool_name not in allowed_tool_names:
            logger.warning("Skipping disallowed function-style tool_call[%d] name=%s", i, tool_name)
            continue

        span_end = end_idx
        while span_end < len(cleaned) and cleaned[span_end].isspace():
            span_end += 1
        if span_end < len(cleaned) and cleaned[span_end] == ")":
            span_end += 1

        spans.append((match.start(), span_end))
        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(payload, ensure_ascii=False),
            }
        })

    if not tool_calls:
        return [], None

    remaining_parts = []
    cursor = 0
    for start, end in sorted(spans):
        if start > cursor:
            remaining_parts.append(cleaned[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(cleaned):
        remaining_parts.append(cleaned[cursor:])

    return tool_calls, _clean_remaining_text("".join(remaining_parts))


def _extract_bare_json_tool_calls(cleaned, allowed_tool_names):
    tool_calls = []
    spans = []
    idx = 0
    while idx < len(cleaned):
        start = cleaned.find("{", idx)
        if start == -1:
            break

        json_text, end_idx = _extract_balanced_brace_segment(cleaned, start)
        if not json_text:
            idx = start + 1
            continue

        payload = _load_relaxed_json(json_text)
        call = _normalize_tool_payload(payload)
        if not call:
            idx = start + 1
            continue
        if allowed_tool_names and call["name"] not in allowed_tool_names:
            logger.warning("Skipping disallowed bare-json tool_call name=%s", call["name"])
            idx = end_idx
            continue

        spans.append((start, end_idx))
        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
            }
        })
        idx = end_idx

    if not tool_calls:
        return [], None

    remaining_parts = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            remaining_parts.append(cleaned[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(cleaned):
        remaining_parts.append(cleaned[cursor:])

    return tool_calls, _clean_remaining_text("".join(remaining_parts))


def _run_fallback_extractors(cleaned, allowed_tool_names):
    for extractor in (
        _extract_numbered_tool_calls,
        _extract_function_style_calls,
        _extract_bare_json_tool_calls,
        _extract_malformed_named_tool_calls,
        _extract_fenced_argument_calls,
    ):
        tool_calls, remaining = extractor(cleaned, allowed_tool_names)
        if tool_calls:
            return tool_calls, remaining
    return None, None


def _extract_fenced_argument_calls(cleaned, allowed_tool_names):
    if not allowed_tool_names or len(allowed_tool_names) != 1:
        return [], None

    tool_name = next(iter(allowed_tool_names))
    fence_pattern = re.compile(r'```(?:json)?\s*({.*?})\s*```', re.DOTALL)
    matches = list(fence_pattern.finditer(cleaned))
    if not matches:
        return [], None

    tool_calls = []
    for match in matches:
        payload = _load_relaxed_json(match.group(1))
        if not isinstance(payload, dict) or "arguments" not in payload:
            continue

        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(payload["arguments"], ensure_ascii=False),
            }
        })

    if not tool_calls:
        return [], None

    remaining = fence_pattern.sub('', cleaned).strip()
    return tool_calls, _clean_remaining_text(remaining)


def _extract_malformed_named_tool_calls(cleaned, allowed_tool_names):
    name_match = re.search(r'"name"\s*:\s*"([^"]+)"', cleaned)
    if not name_match:
        return [], None

    start = cleaned.rfind("{", 0, name_match.start())
    if start < 0:
        start = name_match.start()

    call = _parse_malformed_named_tool_payload(cleaned[start:])
    if not call:
        return [], None
    if allowed_tool_names and call["name"] not in allowed_tool_names:
        logger.warning("Skipping disallowed malformed tool_call name=%s", call["name"])
        return [], None

    return [{
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {
            "name": call["name"],
            "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
        }
    }], _clean_remaining_text(cleaned[:start])


def _extract_numbered_tool_calls(cleaned, allowed_tool_names):
    block_pattern = re.compile(r'<tool_\d+>\s*(.*?)\s*</tool_\d+>', re.DOTALL)
    matches = list(block_pattern.finditer(cleaned))
    if not matches:
        return [], None

    tool_calls = []
    for i, match in enumerate(matches):
        call = _parse_tool_call_body(match.group(1))
        if not call:
            continue
        if allowed_tool_names and call["name"] not in allowed_tool_names:
            logger.warning("Skipping disallowed numbered tool_call[%d] name=%s", i, call["name"])
            continue
        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
            }
        })

    if not tool_calls:
        return [], None

    remaining = block_pattern.sub('', cleaned)
    remaining = re.sub(r'</?tool_calls>', '', remaining).strip()
    return tool_calls, _clean_remaining_text(remaining)


def _coerce_tool_arguments(tool_name, arguments):
    """把模型解析出的参数统一成 dict，保证序列化后是合法 JSON 对象。"""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        parsed = _load_relaxed_json(arguments)
        if isinstance(parsed, dict):
            return parsed
        if tool_name == "Bash":
            return {"command": arguments}
        return {"arguments": arguments}
    if arguments is None:
        return {}
    return {"arguments": arguments}


def _transform_outside_json_strings(text, transform):
    parts = []
    start = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
                parts.append(text[start:index + 1])
                start = index + 1
        elif char == '"':
            parts.append(transform(text[start:index]))
            parts.append(char)
            start = index + 1
            in_string = True
    parts.append(text[start:] if in_string else transform(text[start:]))
    return "".join(parts)


def _find_unquoted_match(pattern, text, start=0):
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == pattern.pattern[0]:
            match = pattern.match(text, index)
            if match:
                return match
    return None


def _find_tool_call_end(text, start):
    close_pattern = re.compile(r"</tool_call\s*>", re.IGNORECASE)
    open_pattern = re.compile(r"<tool_call\b[^>]*>", re.IGNORECASE)
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "<":
            close = close_pattern.match(text, index)
            if close:
                return index, close.end(), True
            next_open = open_pattern.match(text, index)
            if next_open:
                return index, index, False
    next_open = open_pattern.search(text, start)
    if next_open:
        return next_open.start(), next_open.start(), False
    return len(text), len(text), False


def _extract_action_envelope(content, allowed_tool_names):
    opening = None
    for match in ACTION_OR_CODE_RE.finditer(content):
        marker = match.group()
        if marker == ACTION_OPEN_MARKER:
            opening = match
            break
        if marker in {"@@ACTION@", ACTION_CLOSE_MARKER}:
            raise ToolCallParseError("Action envelope marker is malformed or misplaced")
    if not opening:
        return None

    close_pattern = re.compile(re.escape(ACTION_CLOSE_MARKER))
    closing = _find_unquoted_match(close_pattern, content, opening.end())
    if not closing:
        raise ToolCallParseError("Action envelope is incomplete")
    if content[closing.end():].strip():
        raise ToolCallParseError("Action envelope must be the final response content")

    try:
        payload = json.loads(content[opening.end():closing.start()])
    except json.JSONDecodeError as exc:
        raise ToolCallParseError("Action envelope contains invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"calls"}:
        raise ToolCallParseError("Action envelope must contain only a calls array")
    if not isinstance(payload["calls"], list) or not payload["calls"]:
        raise ToolCallParseError("Action envelope calls must be a non-empty array")

    tool_calls = []
    for index, call in enumerate(payload["calls"]):
        if not isinstance(call, dict) or set(call) != {"operation", "parameters"}:
            raise ToolCallParseError(f"Action call {index} must contain operation and parameters")
        name = call["operation"]
        parameters = call["parameters"]
        if not isinstance(name, str) or not isinstance(parameters, dict):
            raise ToolCallParseError(f"Action call {index} has invalid operation or parameters")
        if allowed_tool_names and name not in allowed_tool_names:
            raise ToolCallParseError("Action envelope requested a tool that is not allowed")
        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(parameters, ensure_ascii=False),
            },
        })
    return tool_calls, content[:opening.start()].strip() or None

def extract_tool_calls(content, allowed_tool_names=None):
    if not isinstance(content, str):
        return None, content

    code_free = CODE_QUOTE_RE.sub(lambda match: " " * len(match.group()), content)
    if code_free != content and find_tool_call_open(code_free) < 0 and any(
        marker in content for marker in TOOL_CALL_OPEN_MARKERS
    ):
        return None, content
    native_marker = _find_unquoted_match(re.compile(re.escape("<|open|>")), code_free)
    action_marker = _find_unquoted_match(re.compile(re.escape(ACTION_OPEN_MARKER)), code_free)
    if native_marker and (action_marker is None or native_marker.start() < action_marker.start()):
        native_start = native_marker.start()
        try:
            native_text, native_calls = parse_kimi_output(content[native_start:], allowed_tool_names)
        except ValueError as exc:
            raise ToolCallParseError(str(exc)) from exc
        calls = [{
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(call["arguments"], ensure_ascii=False),
            },
        } for call in native_calls]
        return calls or None, (content[:native_start] + native_text).strip() or None
    # 只解析外层协议；原生字符串中的 action 标记不能被当成另一种封套。
    action_result = _extract_action_envelope(content, allowed_tool_names)
    if action_result is not None:
        return action_result

    transform_text = lambda text: normalize_tool_call_tags(strip_think_blocks(text))
    cleaned = _transform_outside_json_strings(content, transform_text)
    fence_pattern = re.compile(
        r'```(?:xml|json|plaintext|text)?\s*\n?\s*(<tool_call\b[^>]*>.*?</tool_call>)\s*\n?\s*```',
        re.DOTALL,
    )
    cleaned = _transform_outside_json_strings(
        cleaned,
        lambda text: fence_pattern.sub(r"\1", text),
    )

    open_pattern = re.compile(r"<tool_call\b([^>]*)>", re.IGNORECASE)
    cursor = 0
    spans = []
    tool_calls = []
    index = 0
    parse_failed = False
    invalid_tool_body = False
    while True:
        opening = _find_unquoted_match(open_pattern, cleaned, cursor)
        if not opening:
            break
        body_start = opening.end()
        body_end, span_end, closed = _find_tool_call_end(cleaned, body_start)
        body = cleaned[body_start:body_end].strip()
        spans.append((opening.start(), span_end))
        cursor = span_end
        if not body:
            if closed:
                raise ToolCallParseError("Tool call body is empty")
            continue

        if body.lstrip().startswith("{"):
            parsed_body = _load_relaxed_json(body)
            if parsed_body is None and (not closed or body.count("{") > body.count("}")):
                raise ToolCallParseError("Tool call JSON is incomplete")
        tool_name = _extract_tool_name_from_attrs(opening.group(1))
        call = _parse_tool_call_body(body, tool_name=tool_name)
        if not call:
            parse_failed = True
            invalid_tool_body = invalid_tool_body or bool(
                tool_name
                or re.search(r'"(?:name|arguments)"\s*:|<arg_key>|<argument>', body, re.IGNORECASE)
            )
            index += 1
            continue
        if allowed_tool_names and call["name"] not in allowed_tool_names:
            logger.warning("Skipping disallowed tool_call[%d] name=%s", index, call["name"])
            index += 1
            continue
        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(
                    _coerce_tool_arguments(call["name"], call.get("arguments", {})),
                    ensure_ascii=False,
                ),
            },
        })
        index += 1
    if invalid_tool_body:
        raise ToolCallParseError("Tool call is incomplete or its arguments could not be parsed")

    partial_open_pattern = re.compile(r"<tool_call\b(?![^>]*>)", re.IGNORECASE)
    partial_call = tool_call_prefix_len(cleaned) > 0 or bool(
        _find_unquoted_match(partial_open_pattern, cleaned)
    )
    if partial_call:
        raise ToolCallParseError("Tool call marker is incomplete")
    if not spans or parse_failed:
        fallback_tool_calls, fallback_remaining = _run_fallback_extractors(cleaned, allowed_tool_names)
        if fallback_tool_calls and parse_failed and tool_calls:
            raise ToolCallParseError("Tool call batch contains an incomplete call")
        if fallback_tool_calls:
            return fallback_tool_calls, fallback_remaining
        if parse_failed:
            raise ToolCallParseError("Tool call is incomplete or its arguments could not be parsed")
    if not spans:
        return None, content

    remaining_parts = []
    position = 0
    for start, end in spans:
        remaining_parts.append(cleaned[position:start])
        position = max(position, end)
    remaining_parts.append(cleaned[position:])
    if tool_call_prefix_len(cleaned[position:]) > 0 or _find_unquoted_match(
        partial_open_pattern, cleaned, position
    ):
        raise ToolCallParseError("Tool call batch ends with an incomplete marker")
    remaining = _clean_remaining_text("".join(remaining_parts))
    return (tool_calls or None), remaining


def _tag_prefix_len(text, tag):
    max_len = min(len(tag) - 1, len(text))
    for length in range(max_len, 0, -1):
        if text[-length:] == tag[:length]:
            return length
    return 0
