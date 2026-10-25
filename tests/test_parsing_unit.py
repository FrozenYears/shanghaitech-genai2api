import json

import pytest

from tools.parsing import ToolCallParseError, extract_tool_calls, find_tool_call_open, tool_call_prefix_len


def action(calls):
    return "@@ACTION@@" + json.dumps({"calls": calls}, ensure_ascii=False) + "@@END_ACTION@@"


def test_action_preserves_literal_file_content_and_history_id():
    value = '雪\\path\n"quote" <think>x</think> </tool_call> <|DSML|call> @@ACTION@@ @@END_ACTION@@ `code`\n'
    text = action([{"id": "call-original", "operation": "write", "parameters": {"content": value}}])
    calls, remaining = extract_tool_calls(text, {"write"})
    assert remaining is None
    assert calls[0]["id"] == "call-original"
    assert json.loads(calls[0]["function"]["arguments"]) == {"content": value}


@pytest.mark.parametrize("text", [
    '<tool_call>{"name":"read","arguments":{"path":"a"}}</tool_call>',
    '<tool_call name="read">{"path":"a"}</tool_call>',
    '<tool_call>read\n{"path":"a"}</tool_call>',
    '<tool_call>{"read":{"path":"a"}}</tool_call>',
    '<｜DSML｜tool_call>{"name":"read","arguments":{"path":"a"}}</｜DSML｜tool_call>',
    '<|DSML|_call>{"name":"read","arguments":{"path":"a"}}</tool_call>',
    '<｜DSML｜ name="read">{"path":"a"}</｜DSML｜>',
    '<tool_calls><tool_001>{"name":"read","arguments":{"path":"a"}}</tool_001></tool_calls>',
    '<tool_call>read<arg_key>path</arg_key><arg_value>a</arg_value></tool_call>',
])
def test_complete_explicit_formats_produce_exact_arguments(text):
    calls, remaining = extract_tool_calls("Inspecting.\n" + text, {"read"})
    assert remaining == "Inspecting."
    assert [(call["function"]["name"], json.loads(call["function"]["arguments"])) for call in calls] == [("read", {"path": "a"})]


def test_xml_json_arguments_do_not_end_at_control_tags_in_strings():
    args = {"content": 'before </tool_call><think>keep</think> <|DSML|call> "雪"\n'}
    text = '<tool_call name="write">' + json.dumps(args) + '</tool_call>'
    calls, _ = extract_tool_calls(text, {"write"})
    assert json.loads(calls[0]["function"]["arguments"]) == args


def test_tagged_string_arguments_are_not_trimmed_or_coerced():
    text = '<tool_call>write<arg_key>content</arg_key><arg_value>  "001"\n雪\n </arg_value></tool_call>'
    calls, _ = extract_tool_calls(text, {"write"})
    assert json.loads(calls[0]["function"]["arguments"]) == {"content": '  "001"\n雪\n '}


@pytest.mark.parametrize("text", [
    '```json\n{"arguments":{"path":"a"}}\n```',
    '```json\n{"name":"read","arguments":{"path":"a"}}\n```',
    '{"name":"read","arguments":{"path":"a"}}',
    'read({"path":"a"})',
    '`<tool_call>{"name":"read","arguments":{}}</tool_call>`',
    '```text\n@@ACTION@@{"calls":[{"operation":"read","parameters":{}}]}@@END_ACTION@@\n```',
    '```text\n<|open|>tools<|sep|>\n```',
    '`<think>keep</think>`, `</tool_call>`, `<|DSML|call>`, `@@ACTION@@`',
])
def test_documentation_and_bare_json_never_become_calls(text):
    content = "Example only, do not execute:\n" + text
    calls, remaining = extract_tool_calls(content, {"read"})
    assert calls is None
    assert remaining == content


@pytest.mark.parametrize("text", [
    '@@ACTION@@{"calls":[{"operation":"read","parameters":{"path":"a","path":"b"}}]}@@END_ACTION@@',
    '@@ACTION@@{"calls":[{"operation":"read","parameters":{"x":NaN}}]}@@END_ACTION@@',
    '@@ACTION@@{"calls":[{"operation":"read","parameters":{"x":1e999}}]}@@END_ACTION@@',
    '@@ACTION@{"calls":[]}',
    '@@ACTION@@{"calls":[',
    '@@END_ACTION@@',
    '<tool_call>{"name":"read","arguments":{"path":"a"}}',
    '<tool_call{"name":"read","arguments":{}}',
    '<tool_call>{"name":"read","arguments":{"path":"a","path":"b"}}</tool_call>',
    '<tool_call>{"name":"read","arguments":"a"}</tool_call>',
    '<tool_call>{\'name\':\'read\',\'arguments\':{}}</tool_call>',
    '<tool_call>read<arg_key>path": "a</arg_value></tool_call>',
    '<tool_call>{"name":"read","arguments":{}}</tool_call><tool_result>simulated</tool_result>',
    '<tool_calls><tool_001>{"name":"read","arguments":{}}</tool_002></tool_calls>',
])
def test_malformed_explicit_calls_fail_closed(text):
    with pytest.raises(ToolCallParseError):
        extract_tool_calls(text, {"read"})


def test_multiple_envelopes_or_malformed_prefix_do_not_select_the_last_call():
    valid = action([{"operation": "read", "parameters": {"path": "a"}}])
    for content in (valid + valid, '@@ACTION@@{broken}' + valid):
        with pytest.raises(ToolCallParseError):
            extract_tool_calls(content, {"read"})


@pytest.mark.parametrize("second", [
    '<tool_call>{"name":"read","arguments":',
    '<tool_call>{"name":"unknown","arguments":{}}</tool_call>',
])
def test_bad_second_call_rejects_entire_xml_batch(second):
    first = '<tool_call>{"name":"read","arguments":{"path":"a"}}</tool_call>'
    with pytest.raises(ToolCallParseError):
        extract_tool_calls(first + second, {"read"})


def test_quoted_example_before_action_does_not_hide_real_call():
    prefix = '`@@ACTION@@` and `<think>keep</think>` are literal examples.\n'
    calls, remaining = extract_tool_calls(prefix + action([{"operation": "read", "parameters": {"path": "a"}}]), {"read"})
    assert remaining == prefix.strip()
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "a"}


def test_stream_boundary_buffers_unclosed_quote_and_partial_marker():
    quote = '```text\n<tool_call>{"name":"read"}'
    assert find_tool_call_open(quote) == -1
    assert tool_call_prefix_len(quote) == len(quote)
    content = quote + '\n```\n@@ACT'
    assert find_tool_call_open(content) == -1
    assert tool_call_prefix_len(content) == len('@@ACT')


def test_empty_allowlist_cannot_authorize_a_call():
    with pytest.raises(ToolCallParseError):
        extract_tool_calls(action([{"operation": "read", "parameters": {}}]), set())


def test_thinking_cannot_become_an_executable_tool_call():
    hidden = '<think>' + action([{"operation": "read", "parameters": {"path": "private"}}]) + '</think>'
    calls, remaining = extract_tool_calls(hidden + 'Final answer', {"read"})
    assert calls is None
    assert remaining == hidden + 'Final answer'
    assert tool_call_prefix_len("<thi") == 4
    assert find_tool_call_open(hidden[:-8]) == -1


@pytest.mark.parametrize("wrapper", ["tool_calls", "function_calls"])
def test_deepseek_native_dsml_preserves_strings_and_json_types(wrapper):
    raw = '  雪 "quote"\n<tool_call>literal</tool_call>  '
    text = (
        f'<｜DSML｜{wrapper}>'
        '<｜DSML｜invoke name="write">'
        '<｜DSML｜parameter name="content" string="true">' + raw + '</｜DSML｜parameter>'
        '<｜DSML｜parameter name="options" string="false">{"append":false,"rows":[1,null]}</｜DSML｜parameter>'
        '</｜DSML｜invoke>' + f'</｜DSML｜{wrapper}>'
    )
    calls, remaining = extract_tool_calls(text, {"write"})
    assert remaining is None
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "content": raw, "options": {"append": False, "rows": [1, None]},
    }
    with pytest.raises(ToolCallParseError):
        extract_tool_calls(text.replace('</｜DSML｜invoke>', ''), {"write"})


@pytest.mark.parametrize("text", ["address@", "literal @@", "comparison <", "literal <|"])
def test_ambiguous_short_prefixes_remain_literal_text(text):
    calls, remaining = extract_tool_calls(text, {"read"})
    assert calls is None
    assert remaining == text
