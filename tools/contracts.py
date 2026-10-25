"""工具边界：严格 JSON、完整参数 Schema 和整批调用约束。"""

import json
import math
import uuid

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable


class ToolContractError(ValueError):
    """请求或模型调用违反工具协议；不得交付部分调用。"""

    code = "tool_contract_error"

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code or type(self).code


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ToolContractError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ToolContractError("Non-finite JSON number: " + value)


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ToolContractError("JSON number is outside the finite range")
    return number


def strict_json_decoder():
    return json.JSONDecoder(
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
    )


def strict_json_loads(text):
    try:
        return strict_json_decoder().decode(text)
    except (ValueError, RecursionError) as exc:
        raise ToolContractError("Invalid tool JSON: " + str(exc)) from exc


def _check_references(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                not isinstance(child, str) or not child.startswith("#")
            ):
                raise ToolContractError("Tool schemas only support local JSON references")
            _check_references(child)
    elif isinstance(value, list):
        for child in value:
            _check_references(child)


def validate_tool_definitions(tools):
    """返回按名称索引的离线 validator，不允许 Schema 触发网络访问。"""
    if tools is not None and not isinstance(tools, (list, tuple)):
        raise ToolContractError("tools must be an array")
    validators = {}
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ToolContractError("Only normalized function tools are accepted")
        function = tool.get("function")
        if not isinstance(function, dict):
            raise ToolContractError("Tool function must be an object")
        name = function.get("name")
        if not isinstance(name, str) or not name or name in validators:
            raise ToolContractError("Tool names must be non-empty and unique")
        schema = function.get("parameters", {"type": "object", "properties": {}, "additionalProperties": False})
        if not isinstance(schema, dict):
            raise ToolContractError("Tool parameters must be a JSON Schema object")
        try:
            _check_references(schema)
            cls = validator_for(schema)
            cls.check_schema(schema)
            validators[name] = cls(schema, registry=Registry())
        except (ValueError, RecursionError, SchemaError) as exc:
            raise ToolContractError("Invalid schema for tool " + name) from exc
    return validators


def validate_tool_request(tools, tool_choice=None, parallel_tool_calls=True):
    """入口检查：必需工具请求没有工具定义时直接拒绝。"""
    validators = validate_tool_definitions(tools)
    if not isinstance(parallel_tool_calls, bool):
        raise ToolContractError("parallel_tool_calls must be a boolean")
    choice = tool_choice if tool_choice is not None else "auto"
    named = None
    if isinstance(choice, dict):
        if choice.get("type") != "function" or not isinstance(choice.get("function"), dict):
            raise ToolContractError("Unsupported tool_choice")
        named = choice["function"].get("name")
        if not isinstance(named, str) or named not in validators:
            raise ToolContractError("tool_choice names an unavailable tool")
    elif not isinstance(choice, str) or choice not in {"auto", "none", "required"}:
        raise ToolContractError("Unsupported tool_choice")
    if choice == "required" and not validators:
        raise ToolContractError("Required tool choice needs at least one tool")
    return validators


def validate_tool_calls(calls, tools, tool_choice=None, parallel_tool_calls=True):
    """验证整批后返回 Chat 调用，已有 ID 和合法参数字符串不做重写。"""
    validators = validate_tool_request(tools, tool_choice, parallel_tool_calls)
    choice = tool_choice if tool_choice is not None else "auto"
    named = choice["function"]["name"] if isinstance(choice, dict) else None
    if calls is None:
        calls = []
    if not isinstance(calls, (list, tuple)):
        raise ToolContractError("tool_calls must be an array")
    if choice == "none" and calls:
        raise ToolContractError("Tool calls are disabled")
    if (choice == "required" or named is not None) and not calls:
        raise ToolContractError("Required tool call was not returned")
    if not parallel_tool_calls and len(calls) > 1:
        raise ToolContractError("Parallel tool calls are disabled")
    result = []
    ids = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type", "function") != "function":
            raise ToolContractError("Invalid tool call type")
        function = call.get("function")
        if not isinstance(function, dict):
            raise ToolContractError("Missing tool function")
        name = function.get("name")
        if not isinstance(name, str) or name not in validators or (named is not None and name != named):
            raise ToolContractError("Tool call is not allowed")
        raw = function.get("arguments")
        arguments = strict_json_loads(raw) if isinstance(raw, str) else raw
        if not isinstance(arguments, dict):
            raise ToolContractError("Tool arguments must be a JSON object")
        try:
            serialized = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
            validators[name].validate(arguments)
        except ValidationError as exc:
            # 只报告约束/字段名，不把文件正文或命令等参数值写进错误。
            path = ".".join(str(part) for part in exc.absolute_path) or "parameters"
            missing = ""
            if exc.validator == "required" and isinstance(exc.instance, dict):
                names = [key for key in exc.validator_value if key not in exc.instance]
                missing = "; missing fields: " + ", ".join(names)
            raise ToolContractError(
                f"Tool {name!r} arguments fail {exc.validator} at {path}{missing}"[:512],
                code="tool_arguments_invalid",
            ) from exc
        except (ValueError, Unresolvable, RecursionError) as exc:
            raise ToolContractError("Tool arguments could not be validated for " + name,
                                    code="tool_arguments_invalid") from exc
        call_id = call.get("id")
        if call_id is None:
            call_id = "call_" + uuid.uuid4().hex[:24]
        if not isinstance(call_id, str) or not call_id or call_id in ids:
            raise ToolContractError("Tool call IDs must be non-empty and unique")
        ids.add(call_id)
        result.append({
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": raw if isinstance(raw, str) else serialized},
        })
    return result
