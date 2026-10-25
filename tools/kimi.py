"""Kimi K3 XTML 的完整响应解析；仅接受闭合、可无损解码的工具批次。"""

import math
import re

from tools.contracts import strict_json_decoder


_OPEN = "<|open|>"
_CLOSE = "<|close|>"
_ATTR = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"')
_TAG = re.compile(r"<\|open\|>\s*([A-Za-z_]+)\b((?:(?!<\|sep\|>).)*)<\|sep\|>", re.DOTALL)


def _attributes(raw):
    attributes = {}
    cursor = 0
    while cursor < len(raw):
        if not raw[cursor:].strip():
            break
        match = _ATTR.match(raw, cursor)
        if not match or match[1] in attributes:
            raise ValueError("Kimi K3 attributes are malformed or duplicated")
        attributes[match[1]] = match[2].replace("&quot;", '"').replace("&amp;", "&")
        cursor = match.end()
    return attributes


def _opening(text, cursor, expected):
    match = _TAG.match(text, cursor)
    if not match or match[1] != expected:
        raise ValueError(f"Kimi K3 expected a complete {expected} opening tag")
    return _attributes(match[2]), match.end()


def _closing(text, cursor, name):
    match = re.compile(r"<\|close\|>\s*" + re.escape(name) + r"\s*<\|sep\|>").match(text, cursor)
    if not match:
        raise ValueError(f"Kimi K3 {name} block is incomplete")
    return match.end()


def _space(text, cursor):
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor


def _value(text, cursor, kind, block):
    if kind == "string":
        close = re.compile(r"<\|close\|>\s*" + re.escape(block) + r"\s*<\|sep\|>").search(text, cursor)
        if close is None:
            raise ValueError("Kimi K3 string argument is incomplete")
        return text[cursor:close.start()], close.end()
    value_start = _space(text, cursor)
    try:
        value, size = strict_json_decoder().raw_decode(text[value_start:])
    except (ValueError, RecursionError) as exc:
        raise ValueError("Kimi K3 typed argument contains invalid JSON") from exc
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "number": type(value) in (int, float),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "null": value is None,
    }
    if not valid.get(kind, False) or (type(value) is float and not math.isfinite(value)):
        raise ValueError("Kimi K3 argument does not match its declared JSON type")
    end = _space(text, value_start + size)
    return value, _closing(text, end, block)


def _call(text, cursor, allowed_names):
    attributes, cursor = _opening(text, cursor, "call")
    name = attributes.get("tool")
    if not name or (allowed_names is not None and name not in allowed_names):
        raise ValueError("Kimi K3 tool is missing or not allowed")
    if set(attributes) - {"tool", "index"}:
        raise ValueError("Kimi K3 call has unsupported attributes")
    arguments = {}
    json_block = False
    while True:
        cursor = _space(text, cursor)
        if text.startswith(_CLOSE, cursor):
            return {"name": name, "arguments": arguments}, _closing(text, cursor, "call")
        tag = _TAG.match(text, cursor)
        if not tag:
            raise ValueError("Kimi K3 tool arguments are incomplete")
        if tag[1] == "json":
            if arguments or json_block:
                raise ValueError("Kimi K3 JSON block cannot be combined with keyed arguments")
            attrs, cursor = _opening(text, cursor, "json")
            if attrs != {"type": "object"}:
                raise ValueError("Kimi K3 JSON arguments must be an object")
            arguments, cursor = _value(text, cursor, "object", "json")
            json_block = True
        elif tag[1] == "argument":
            attrs, cursor = _opening(text, cursor, "argument")
            key = attrs.get("key")
            if not key or key in arguments or json_block or set(attrs) - {"key", "type"}:
                raise ValueError("Kimi K3 argument key is missing, duplicated, or invalid")
            arguments[key], cursor = _value(text, cursor, attrs.get("type", "string"), "argument")
        else:
            raise ValueError("Kimi K3 tool call contains an unexpected block")


def parse_kimi_output(text, allowed_names):
    """解析从第一个 XTML 开始标签起的响应；返回普通正文及未序列化调用。"""
    cursor = 0
    response = ""
    calls = []
    if text.startswith(_OPEN + "response"):
        attrs, cursor = _opening(text, cursor, "response")
        if attrs:
            raise ValueError("Kimi K3 response has unsupported attributes")
        close = re.compile(r"<\|close\|>\s*response\s*<\|sep\|>").search(text, cursor)
        if close is None:
            raise ValueError("Kimi K3 response channel is incomplete")
        response = text[cursor:close.start()]
        cursor = close.end()
    cursor = _space(text, cursor)
    has_tools = text.startswith(_OPEN + "tools", cursor)
    if has_tools:
        attrs, cursor = _opening(text, cursor, "tools")
        if attrs:
            raise ValueError("Kimi K3 tools channel has unsupported attributes")
    while text.startswith(_OPEN + "call", _space(text, cursor)):
        call, cursor = _call(text, _space(text, cursor), allowed_names)
        calls.append(call)
    cursor = _space(text, cursor)
    if has_tools:
        cursor = _closing(text, cursor, "tools")
        if not calls:
            raise ValueError("Kimi K3 tools channel contains no calls")
    if not has_tools and not calls and not response:
        raise ValueError("Unrecognized Kimi K3 native response")
    cursor = _space(text, cursor)
    response_end = re.compile(r"<\|close\|>\s*response\s*<\|sep\|>").match(text, cursor)
    if calls and response_end:
        cursor = _space(text, response_end.end())
    if text.startswith(_CLOSE, cursor):
        cursor = _closing(text, cursor, "message")
    cursor = _space(text, cursor)
    if text.startswith("<|end_of_msg|>", cursor):
        cursor += len("<|end_of_msg|>")
    if text[cursor:].strip():
        raise ValueError("Kimi K3 response has trailing or unparsed content")
    return response, calls
