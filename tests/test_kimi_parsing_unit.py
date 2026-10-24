import json

import pytest

from tools.parsing import ToolCallParseError, extract_tool_calls


TOOLS_OPEN = "<|open|>tools<|sep|>"
TOOLS_CLOSE = "<|close|>tools<|sep|>"
CALL_CLOSE = "<|close|>call<|sep|>"
ARG_CLOSE = "<|close|>argument<|sep|>"


def _call(name, arguments):
    body = f'<|open|>call tool="{name}" index="0"<|sep|>'
    for key, kind, value in arguments:
        body += f'<|open|>argument key="{key}" type="{kind}"<|sep|>{value}{ARG_CLOSE}'
    return body + CALL_CLOSE


def test_k3_native_batch_preserves_raw_strings_and_typed_arguments():
    literal = '  <think>保留</think> </tool_call> @@ACTION@@\nC:\\workspace\\x "quoted"  '
    raw_json_string = '{"not": "an object"}'
    content = (
        TOOLS_OPEN
        + _call("write", [("path", "string", "雪.txt"), ("content", "string", literal)])
        + _call("command", [
            ("argv", "array", '["python","-c","print(1)"]'),
            ("options", "object", '{"nested":[true,null,3]}'),
            ("count", "number", "3"),
            ("enabled", "boolean", "false"),
            ("tag", "string", raw_json_string),
        ])
        + TOOLS_CLOSE
    )

    calls, remaining = extract_tool_calls(content, {"write", "command"})

    assert remaining is None
    assert [call["function"]["name"] for call in calls] == ["write", "command"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "雪.txt", "content": literal}
    assert json.loads(calls[1]["function"]["arguments"]) == {
        "argv": ["python", "-c", "print(1)"],
        "options": {"nested": [True, None, 3]},
        "count": 3,
        "enabled": False,
        "tag": raw_json_string,
    }


def test_k3_json_argument_block_and_response_channel():
    arguments = {"argv": ["python", "-c", 'print("<|close|>json<|sep|>")']}
    content = (
        '<|open|>response<|sep|>Run the command.<|close|>response<|sep|>'
        + TOOLS_OPEN
        + '<|open|>call tool="command" index="1"<|sep|>'
        + '<|open|>json type="object"<|sep|>' + json.dumps(arguments)
        + '<|close|>json<|sep|>' + CALL_CLOSE + TOOLS_CLOSE
        + '<|close|>message<|sep|><|end_of_msg|>'
    )

    calls, remaining = extract_tool_calls(content, {"command"})

    assert remaining == "Run the command."
    assert json.loads(calls[0]["function"]["arguments"]) == arguments


def test_k3_response_without_tool_calls_is_plain_text():
    calls, remaining = extract_tool_calls(
        '<|open|>response<|sep|>Verified 雪.<|close|>response<|sep|><|close|>message<|sep|>',
        {"write"},
    )
    assert calls is None
    assert remaining == "Verified 雪."


def test_k3_real_model_terminal_response_marker_after_tools():
    content = (
        TOOLS_OPEN + _call("command", [("argv", "array", '["python","--version"]')])
        + TOOLS_CLOSE + '<|close|>response<|sep|><|close|>message<|sep|>'
    )
    calls, remaining = extract_tool_calls(content, {"command"})
    assert remaining is None
    assert json.loads(calls[0]["function"]["arguments"]) == {"argv": ["python", "--version"]}


@pytest.mark.parametrize("tail", [
    '<|open|>call tool="write" index="1"<|sep|>',
    _call("unknown", []),
    _call("write", [("x", "number", "true")]),
    _call("write", [("x", "array", "not-json")]),
    _call("write", [("x", "number", "NaN")]),
    _call("write", [("x", "object", '{"invalid":NaN}')]),
    _call("write", [("x", "string", "first"), ("x", "string", "second")]),
    '<|open|>call tool="write" index="1"<|sep|><|open|>argument key="x" type="string"<|sep|>unfinished',
])
def test_k3_bad_second_call_rejects_entire_batch(tail):
    content = TOOLS_OPEN + _call("write", [("path", "string", "first.txt")]) + tail + TOOLS_CLOSE
    with pytest.raises(ToolCallParseError):
        extract_tool_calls(content, {"write"})


def test_k3_truncated_channel_is_never_repaired():
    content = TOOLS_OPEN + _call("write", [("path", "string", "first.txt")])
    with pytest.raises(ToolCallParseError):
        extract_tool_calls(content, {"write"})


def test_k3_quoted_native_example_is_not_executed():
    sample = TOOLS_OPEN + _call("write", [("path", "string", "x")]) + TOOLS_CLOSE
    for content in (f'Example `{sample}`', f'```text\n{sample}\n```'):
        calls, remaining = extract_tool_calls(content, {"write"})
        assert calls is None
        assert remaining == content
