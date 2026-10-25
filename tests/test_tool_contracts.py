import json

import pytest

from tools.contracts import ToolContractError, strict_json_loads, validate_tool_calls, validate_tool_request


TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}}}]


def call(arguments, call_id="call-a", name="read"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_native_argument_bytes_and_call_id_survive_validation():
    raw = '{ "path" : "雪\\\\file.txt" }'
    original = call(raw)
    result = validate_tool_calls([original], TOOLS)
    assert result[0]["id"] == "call-a"
    assert result[0]["function"]["arguments"] == raw


@pytest.mark.parametrize("raw", [
    '{"path":"a","path":"b"}',
    '{"nested":{"x":1,"x":2}}',
    '{"value":NaN}',
    '{"value":Infinity}',
    '{"value":1e999}',
])
def test_ambiguous_or_nonfinite_json_is_rejected(raw):
    with pytest.raises(ToolContractError):
        strict_json_loads(raw)


@pytest.mark.parametrize("second", [
    call('{"path":3}', "call-b"),
    call('{"path":"b","extra":true}', "call-b"),
    call('{"path":"b"}', "call-b", "unknown"),
    call('{"path":"b"}', "call-a"),
])
def test_invalid_member_rejects_entire_native_batch(second):
    with pytest.raises(ToolContractError):
        validate_tool_calls([call('{"path":"a"}'), second], TOOLS)


def test_tool_choice_and_parallel_constraints_are_enforced():
    calls = [call('{"path":"a"}'), call('{"path":"b"}', "call-b")]
    with pytest.raises(ToolContractError):
        validate_tool_calls(calls, TOOLS, parallel_tool_calls=False)
    with pytest.raises(ToolContractError):
        validate_tool_calls(calls, TOOLS, tool_choice="none")
    with pytest.raises(ToolContractError):
        validate_tool_calls([], TOOLS, tool_choice="required")
    with pytest.raises(ToolContractError):
        validate_tool_calls(calls, TOOLS, tool_choice={"type": "function", "function": {"name": "absent"}})
    assert [c["id"] for c in validate_tool_calls(calls, TOOLS, tool_choice="required")] == ["call-a", "call-b"]


def test_omitted_parameter_schema_means_no_arguments_not_unchecked_arguments():
    tools = [{"type": "function", "function": {"name": "read"}}]
    result = validate_tool_calls([call('{}')], tools)
    assert json.loads(result[0]["function"]["arguments"]) == {}
    with pytest.raises(ToolContractError):
        validate_tool_calls([call('{"path":"a"}')], tools)


def test_local_schema_references_validate_without_external_resolution():
    tools = [{"type": "function", "function": {"name": "read", "parameters": {
        "type": "object", "properties": {"path": {"$ref": "#/$defs/path"}},
        "required": ["path"], "$defs": {"path": {"type": "string", "minLength": 1}},
    }}}]
    with pytest.raises(ToolContractError):
        validate_tool_calls([call('{"path":""}')], tools)
    assert json.loads(validate_tool_calls([call('{"path":"a"}')], tools)[0]["function"]["arguments"]) == {"path": "a"}
    tools[0]["function"]["parameters"]["properties"]["path"] = {"$ref": "https://example.test/schema"}
    with pytest.raises(ToolContractError):
        validate_tool_request(tools)


@pytest.mark.parametrize("tools,choice", [
    ([], "required"),
    (TOOLS + TOOLS, "auto"),
    ([{"type": "function", "function": {"name": "read", "parameters": {"type": "not-a-type"}}}], "auto"),
])
def test_invalid_tool_request_is_rejected_before_generation(tools, choice):
    with pytest.raises(ToolContractError):
        validate_tool_request(tools, choice)
