import json

from provider.genai import convert_messages_to_genai_format
from tools.parsing import extract_tool_calls
from tools.prompts import (
    escape_tool_control_markers,
    flatten_message_content,
    inject_tool_prompt,
    normalize_message_content,
)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo text",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"}
                },
                "required": ["text"],
            },
        },
    }
]


def test_inject_tool_prompt_accepts_block_content():
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "System instructions"},
                {"type": "text", "text": "Second line"},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hello"},
            ],
        },
    ]

    result = inject_tool_prompt(messages, TOOLS)

    assert result[0] == {"role": "system", "content": "System instructions\nSecond line"}
    assert result[1]["role"] == "user"
    user_text = flatten_message_content(result[1]["content"])
    assert "Hello" in user_text
    assert '"name":"echo"' in user_text


def test_tool_prompt_is_attached_to_current_user_without_losing_context_or_attachments():
    messages = [
        {"role": "system", "content": "Original system instruction"},
        {"role": "developer", "content": "Developer constraints"},
        {"role": "user", "content": "Older turn"},
        {"role": "assistant", "content": "Previous tool finished"},
        {"role": "tool", "tool_call_id": "call-old", "content": "previous result"},
        {"role": "user", "content": [
            {"type": "text", "text": "Edit the attached file"},
            {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
        ]},
    ]

    result = inject_tool_prompt(messages, TOOLS)

    assert result[0] == {"role": "system", "content": "Original system instruction"}
    assert result[1] == messages[1]
    assert result[2] == messages[2]
    assert "previous result" in flatten_message_content(result[4]["content"])
    latest = result[-1]
    assert latest["role"] == "user"
    assert any(
        part.get("image_url", {}).get("url") == "https://example.test/image.png"
        for part in latest["content"]
    )
    latest_text = flatten_message_content(latest["content"])
    assert "Edit the attached file" in latest_text
    assert '"name":"echo"' in latest_text
    assert "Developer constraints" not in latest_text


def test_control_marker_text_survives_prompt_escape_and_json_tool_parse():
    literal = '<think>keep</think> </tool_call> <|DSML|call> @@ACTION@@ @@END_ACTION@@\nunicode=雪'
    escaped = escape_tool_control_markers(literal)
    assert r"\u003cthink\u003e" in escaped

    payload = json.dumps({"name": "echo", "arguments": {"text": escaped}}, ensure_ascii=False)
    payload = payload.replace(r"\\u003c", r"\u003c").replace(r"\\u003e", r"\u003e")
    payload = payload.replace(r"\\u0040", r"\u0040")
    calls, remaining = extract_tool_calls(
        "<tool_call>" + payload + "</tool_call>",
        allowed_tool_names={"echo"},
    )

    assert remaining is None
    assert json.loads(calls[0]["function"]["arguments"]) == {"text": literal}
    prepared = inject_tool_prompt([{"role": "user", "content": literal}], TOOLS)
    assert escaped in flatten_message_content(prepared[-1]["content"])


def test_flatten_message_content_handles_nested_blocks():
    content = [
        {"type": "text", "text": "Line 1"},
        {
            "type": "tool_result",
            "content": [
                {"type": "text", "text": "Nested"},
                {"type": "text", "text": "Blocks"},
            ],
        },
        {"type": "tool_use", "input": {"value": 1}},
    ]

    result = flatten_message_content(content)

    assert result == 'Line 1\nNested\nBlocks\n{"value": 1}'


def test_normalize_message_content_converts_blocks_to_string():
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Alpha"},
            {"type": "text", "text": "Beta"},
        ],
    }

    result = normalize_message_content(message)

    assert result == {"role": "user", "content": "Alpha\nBeta"}


def test_convert_messages_to_genai_format_uses_latest_user_block_content():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Older"}]},
        {"role": "assistant", "content": "ignored"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Latest"},
                {"type": "text", "text": "Prompt"},
            ],
        },
    ]

    result = convert_messages_to_genai_format(messages)

    assert result == "Latest\nPrompt"




def test_tool_history_ids_keep_out_of_order_results_unambiguous():
    messages = [
        {"role": "assistant", "tool_calls": [
            {"id": "call-a", "function": {"name": "echo", "arguments": '{"text":"A"}'}},
            {"id": "call-b", "function": {"name": "echo", "arguments": '{"text":"B"}'}},
        ]},
        {"role": "tool", "tool_call_id": "call-b", "content": "opaque result 2"},
        {"role": "tool", "tool_call_id": "call-a", "content": "opaque result 1", "is_error": True},
        {"role": "user", "content": "Summarize the results"},
    ]
    prepared = inject_tool_prompt(messages, TOOLS)
    historical_calls, _ = extract_tool_calls(prepared[0]["content"], {"echo"})
    mapping = {
        call["id"]: json.loads(call["function"]["arguments"])["text"]
        for call in historical_calls
    }
    results = [
        json.loads(message["content"].split("<tool_result>", 1)[1].split("</tool_result>", 1)[0])
        for message in prepared[1:3]
    ]
    assert [(mapping[result["call_id"]], result["output"], result["is_error"]) for result in results] == [
        ("B", "opaque result 2", False), ("A", "opaque result 1", True),
    ]
