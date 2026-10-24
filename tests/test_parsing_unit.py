import json

import pytest

from tools.parsing import ToolCallParseError, extract_tool_calls


def test_extract_tool_calls_parses_arg_key_value_blocks():
    content = """
好的，我来看看你项目的整体情况。

<tool_call>
Bash<arg_key>command</arg_key><arg_value>git log --oneline -10</arg_value><arg_key>description</arg_key><arg_value>Show recent 10 commits</arg_value>
</tool_call>
<tool_call>
Bash<arg_key>command</arg_key><arg_value>git diff --stat HEAD</arg_value><arg_key>description</arg_key><arg_value>Show uncommitted changes summary</arg_value>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining == "好的，我来看看你项目的整体情况。"
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git log --oneline -10", "description": "Show recent 10 commits"}',
            },
        },
        {
            "id": tool_calls[1]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git diff --stat HEAD", "description": "Show uncommitted changes summary"}',
            },
        },
    ]


def test_extract_tool_calls_parses_embedded_json_body():
    content = """
<tool_call>
<tool_call>{"name": "Bash", "arguments": {"command": "git log --oneline -15", "description": "Show recent 15 commits"}}</arg_value>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git log --oneline -15", "description": "Show recent 15 commits"}',
            },
        }
    ]


def test_extract_tool_calls_parses_malformed_arg_pairs():
    content = """
<tool_call>
Bash<arg_key>command": "ls -la && git diff --stat</arg_value><arg_key>description":"Show project root and changed files</arg_value>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls -la && git diff --stat", "description": "Show project root and changed files"}',
            },
        }
    ]


def test_extract_tool_calls_parses_unterminated_tool_call_segments():
    content = (
        '我先来查看一下项目结构和当前状态。'
        '<tool_call>Bash<arg_key>command\\": \\"ls /home/xiangyk/Project/URDF-Studio/\\"}})\n'
        '<tool_call>Bash<arg_key>command\\": \\"git -C /home/xiangyk/Project/URDF-Studio diff --stat\\"}})\n'
    )

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining == "我先来查看一下项目结构和当前状态。"
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls /home/xiangyk/Project/URDF-Studio/"}',
            },
        },
        {
            "id": tool_calls[1]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git -C /home/xiangyk/Project/URDF-Studio diff --stat"}',
            },
        },
    ]


def test_extract_tool_calls_parses_equals_style_arguments():
    content = """
<tool_call>
Bash<arg_key>command="ls /home/xiangyk/Project/URDF-Studio/src/"</arg_value>description="List src directory</arg_value>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls /home/xiangyk/Project/URDF-Studio/src/", "description": "List src directory"}',
            },
        }
    ]


def test_extract_tool_calls_parses_colon_style_unquoted_arguments():
    content = """
<tool_call>
Bash<arg_key>command": ls /home/xiangyk/Project/URDF-Studio/</arg_value>description: List project root</arg_value>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls /home/xiangyk/Project/URDF-Studio/", "description": "List project root"}',
            },
        }
    ]


def test_extract_tool_calls_sanitizes_tool_name_prefix_noise():
    content = """
<tool_call>
Bash>("command": "ls -la /home/xiangyk/Project/URDF-Studio/", "description": "List project root files")</arg_value>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls -la /home/xiangyk/Project/URDF-Studio/", "description": "List project root files"}',
            },
        }
    ]


def test_extract_tool_calls_strips_json_fragment_suffix_from_value():
    content = """
<tool_call>
Bash<arg_key>command\\": \\"ls /home/xiangyk/Project/URDF-Studio/\\"}
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls /home/xiangyk/Project/URDF-Studio/"}',
            },
        }
    ]


def test_extract_tool_calls_parses_space_separated_arg_value():
    content = """
<tool_call>
Bash<arg_key>command</arg_key> "ls /home/xiangyk/Project/URDF-Studio/"</arg_value><arg_key>description</arg_key> "List project root directory"</arg_value>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls /home/xiangyk/Project/URDF-Studio/", "description": "List project root directory"}',
            },
        }
    ]


def test_extract_tool_calls_splits_inline_json_style_arguments():
    content = """
<tool_call>
Bash<arg_key>command\\": \\"ls /home/xiangyk/Project/URDF-Studio/src/\\", \\"description\\": \\"List src directory\\"}
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls /home/xiangyk/Project/URDF-Studio/src/", "description": "List src directory"}',
            },
        }
    ]


def test_extract_tool_calls_parses_name_attribute_with_json_arguments():
    content = """
我来快速了解一下这个项目的当前状态和结构。

<tool_call name="Bash">
{"command": "cd /home/xiangyk/Project/URDF-Studio && git status --short", "description": "Show working tree status"}
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining == "我来快速了解一下这个项目的当前状态和结构。"
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "cd /home/xiangyk/Project/URDF-Studio && git status --short", "description": "Show working tree status"}',
            },
        }
    ]


def test_extract_tool_calls_parses_unterminated_name_attribute_segments():
    content = (
        "我来快速了解一下这个项目的当前状态和结构。"
        '<tool_call name="Bash">{"command": "cd /home/xiangyk/Project/URDF-Studio && git status --short", "description": "Show working tree status"}\n'
        '<tool_call name="Bash">{"command": "cd /home/xiangyk/Project/URDF-Studio && ls -la src/", "description": "List top-level src directory"}\n'
    )

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining == "我来快速了解一下这个项目的当前状态和结构。"
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "cd /home/xiangyk/Project/URDF-Studio && git status --short", "description": "Show working tree status"}',
            },
        },
        {
            "id": tool_calls[1]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "cd /home/xiangyk/Project/URDF-Studio && ls -la src/", "description": "List top-level src directory"}',
            },
        },
    ]


def test_extract_tool_calls_parses_single_key_json_tool_shape():
    content = """
<tool_call>
{"Bash": {"command": "git log --oneline -15", "description": "Show recent commit history"}}
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git log --oneline -15", "description": "Show recent commit history"}',
            },
        }
    ]


def test_extract_tool_calls_strips_fake_tool_result_blocks_from_remaining_text():
    content = """
这是一个 URDF Studio 项目。

<tool_call>
{"Bash": {"command": "git status --short", "description": "Show working tree status"}}
</tool_call>

<tool_result>
M src/app/components/SnapshotDialog.tsx
</tool_result>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining == "这是一个 URDF Studio 项目。"
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git status --short", "description": "Show working tree status"}',
            },
        }
    ]


def test_extract_tool_calls_rejects_truncated_json_tool_object():
    content = (
        '<tool_call>{"name": "Bash", "arguments": '
        '{"command": "git log --oneline -20", '
        '"description": "Show recent commit history"}</tool_call>'
    )

    with pytest.raises(ToolCallParseError):
        extract_tool_calls(content, allowed_tool_names={"Bash"})
def test_extract_tool_calls_preserves_tag_like_write_content():
    content = "before </tool_call><think>keep</think> <|DSML|call> literal"
    call = {
        "name": "write",
        "arguments": {"path": "notes.md", "content": content},
    }

    tool_calls, remaining = extract_tool_calls(
        "<tool_call>" + json.dumps(call, ensure_ascii=False) + "</tool_call>",
        allowed_tool_names={"write"},
    )

    assert remaining is None
    assert json.loads(tool_calls[0]["function"]["arguments"]) == call["arguments"]


def test_extract_tool_calls_preserves_multiple_edit_payloads_and_rejects_partial_batch():
    first = {"name": "edit", "arguments": {"path": "a.txt", "old": "x", "new": "雪\\n\\\"ok\\\""}}
    second = {"name": "write", "arguments": {"path": "b.txt", "content": "tail <think>value</think>"}}
    content = "".join(
        "<tool_call>" + json.dumps(call, ensure_ascii=False) + "</tool_call>"
        for call in (first, second)
    )

    tool_calls, _ = extract_tool_calls(content, allowed_tool_names={"edit", "write"})
    assert [json.loads(call["function"]["arguments"]) for call in tool_calls] == [
        first["arguments"], second["arguments"]
    ]

    partial = content[:content.rfind("<tool_call>")] + (
        '<tool_call>{"name":"write","arguments":{"path":"b.txt","content":"tail'
    )
    with pytest.raises(ToolCallParseError):
        extract_tool_calls(partial, allowed_tool_names={"edit", "write"})

def test_extract_tool_calls_parses_argument_tag_for_bash_name_attribute():
    content = """
<tool_call name="Bash">
  <argument>find src -maxdepth 1 -type d | sort</argument>
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content)

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "find src -maxdepth 1 -type d | sort"}',
            },
        }
    ]


def test_extract_tool_calls_filters_out_disallowed_tool_names():
    content = """
<tool_call>
{"name": "Read", "arguments": {"filePath": "/home/xiangyk/Project/URDF-Studio/package.json"}}
</tool_call>
<tool_call>
{"name": "Bash", "arguments": {"command": "git status --short", "description": "Show working tree status"}}
</tool_call>
"""

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Bash"})

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git status --short", "description": "Show working tree status"}',
            },
        }
    ]


def test_extract_tool_calls_parses_fenced_argument_blocks_for_single_allowed_tool():
    content = """
```json
{
  "arguments": {
    "command": "git status --short",
    "description": "Show working tree status"
  }
}
```
```json
{
  "arguments": {
    "command": "ls src | head",
    "description": "List top-level entries under src"
  }
}
```
"""

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Bash"})

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git status --short", "description": "Show working tree status"}',
            },
        },
        {
            "id": tool_calls[1]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls src | head", "description": "List top-level entries under src"}',
            },
        },
    ]


def test_extract_tool_calls_parses_tool_calls_wrapper_with_numbered_tags():
    content = """
<tool_calls>
<tool_001>
{"name": "Bash", "arguments": {"command": "git status --short", "description": "Show working tree status"}}
</tool_001>
<tool_002>
{"name": "Bash", "arguments": {"command": "ls src | head -40", "description": "List top-level src directories"}}
</tool_002>
</tool_calls>
"""

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Bash"})

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "git status --short", "description": "Show working tree status"}',
            },
        },
        {
            "id": tool_calls[1]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "ls src | head -40", "description": "List top-level src directories"}',
            },
        },
    ]


def test_extract_tool_calls_parses_function_style_calls_inside_broken_wrappers():
    content = """
Let me find the CHANGELOG and .omx files first.

<tool_call>
<tool_condition>
[]
</tool_condition>

<tool_call>
  <tool_condition>
[]
</tool_condition>

<tool_condition>
  <tool_condition>
[]
</tool_condition>

Glob({"pattern": "CHANGELOG*"})
Glob({"pattern": "**/*.omx"})
Glob({"pattern": "**/changelog*", "path": "/home/xiangyk/Project/URDF-Studio"})
</tool_condition>
"""

    tool_calls, remaining = extract_tool_calls(
        content,
        allowed_tool_names={"Glob", "Bash", "Grep"},
    )

    assert remaining == "Let me find the CHANGELOG and .omx files first."
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Glob",
                "arguments": '{"pattern": "CHANGELOG*"}',
            },
        },
        {
            "id": tool_calls[1]["id"],
            "type": "function",
            "function": {
                "name": "Glob",
                "arguments": '{"pattern": "**/*.omx"}',
            },
        },
        {
            "id": tool_calls[2]["id"],
            "type": "function",
            "function": {
                "name": "Glob",
                "arguments": '{"pattern": "**/changelog*", "path": "/home/xiangyk/Project/URDF-Studio"}',
            },
        },
    ]


def test_extract_tool_calls_parses_missing_arg_key_open_tag_lines():
    content = """<tool_call>Glob
pattern</arg_key>tests/test_*unit.py
path/home/xiangyk/Project/GenAI2OpenAI
"""

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Glob"})

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Glob",
                "arguments": '{"pattern": "tests/test_*unit.py", "path": "/home/xiangyk/Project/GenAI2OpenAI"}',
            },
        }
    ]


def test_extract_tool_calls_parses_compact_tool_argument_stream():
    content = (
        "<tool_call>Grepoutput_modecontent"
        "path/home/xiangyk/Project/GenAI2OpenAI/README.md"
        "patternClaude Code</arg_value>"
    )

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Grep"})

    assert remaining is None
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Grep",
                "arguments": (
                    '{"output_mode": "content", '
                    '"path": "/home/xiangyk/Project/GenAI2OpenAI/README.md", '
                    '"pattern": "Claude Code"}'
                ),
            },
        }
    ]


def test_extract_tool_calls_parses_bare_json_tool_objects_without_tags():
    content = """
先看看 .gitignore 有没有忽略它，以及它是否被 git 跟踪。
{"name": "Bash", "arguments": {"command": "cd /home/xiangyk/Project/URDF-Studio && git ls-files .omx", "description": "Check if .omx is tracked by git"}}
{"name": "Grep", "arguments": {"pattern": "\\.omx", "path": "/home/xiangyk/Project/URDF-Studio/.gitignore", "output_mode": "content"}}
"""

    tool_calls, remaining = extract_tool_calls(
        content,
        allowed_tool_names={"Bash", "Grep"},
    )

    assert remaining == "先看看 .gitignore 有没有忽略它，以及它是否被 git 跟踪。"
    assert tool_calls == [
        {
            "id": tool_calls[0]["id"],
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": '{"command": "cd /home/xiangyk/Project/URDF-Studio && git ls-files .omx", "description": "Check if .omx is tracked by git"}',
            },
        },
        {
            "id": tool_calls[1]["id"],
            "type": "function",
            "function": {
                "name": "Grep",
                "arguments": '{"pattern": "\\\\.omx", "path": "/home/xiangyk/Project/URDF-Studio/.gitignore", "output_mode": "content"}',
            },
        },
    ]


def test_extract_tool_calls_parses_malformed_bash_command_with_heredoc_quotes():
    content = """<tool_call>
{"name": "Bash", "arguments": {"command": "cd /home/xiangyk/Project/GenAI2OpenAI && git commit -m "$(cat <<'EOF'
feat: add Anthropic Messages API support and improve tool calling

- Add /v1/messages endpoint for Claude Code compatibility
- Add provider/anthropic.py for Anthropic Messages format conversion
EOF
)" 2>&1"}}
</tool_call>"""

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Bash"})

    assert remaining is None
    assert tool_calls[0]["function"]["name"] == "Bash"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {
        "command": (
            "cd /home/xiangyk/Project/GenAI2OpenAI && git commit -m \"$(cat <<'EOF'\n"
            "feat: add Anthropic Messages API support and improve tool calling\n\n"
            "- Add /v1/messages endpoint for Claude Code compatibility\n"
            "- Add provider/anthropic.py for Anthropic Messages format conversion\n"
            "EOF\n"
            ")\" 2>&1"
        )
    }


def test_extract_tool_calls_parses_bare_malformed_bash_command_with_heredoc_quotes():
    content = """{"name": "Bash", "arguments": {"command": "cd /tmp && git commit -m "$(cat <<'EOF'
subject
EOF
)" 2>&1"}}"""

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Bash"})

    assert remaining is None
    assert tool_calls[0]["function"]["name"] == "Bash"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {
        "command": "cd /tmp && git commit -m \"$(cat <<'EOF'\nsubject\nEOF\n)\" 2>&1"
    }


def test_extract_tool_calls_maps_bash_string_arguments_to_command():
    content = '{"name": "Bash", "arguments": "pwd"}'

    tool_calls, remaining = extract_tool_calls(content, allowed_tool_names={"Bash"})

    assert remaining is None
    assert tool_calls[0]["function"]["name"] == "Bash"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {"command": "pwd"}


def test_extract_tool_calls_rejects_an_incomplete_opening_marker():
    with pytest.raises(ToolCallParseError):
        extract_tool_calls('<tool_call{"name":"write","arguments":{"path":"a"}')
