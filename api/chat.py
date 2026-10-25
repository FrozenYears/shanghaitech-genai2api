import logging
import time
import uuid
from datetime import datetime

from flask import Blueprint, current_app, request, jsonify, stream_with_context, Response

from errors import UpstreamError, openai_error
from provider.features import prepare_request, is_web_search_tool
from tools.prompts import inject_tool_prompt, normalize_tool_history
from tools.contracts import validate_tool_request
from provider.genai import (
    collect_genai_response,
    convert_messages_to_genai_format,
    estimate_messages_tokens,
    estimate_text_tokens,
    stream_genai_response,
)

logger = logging.getLogger(__name__)

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    config = current_app.config["APP_CONFIG"]
    request_id = f"req_{uuid.uuid4().hex[:16]}"
    start_time = time.monotonic()

    try:
        req_data = request.get_json()

        if not req_data or 'messages' not in req_data:
            return openai_error("Missing 'messages' field in request body")

        messages = req_data.get('messages', [])
        model = req_data.get('model', 'gpt-3.5-turbo')
        stream = req_data.get('stream', False)
        max_tokens = req_data.get('max_tokens', 30000)
        tools = req_data.get('tools', [])
        if tools is not None and not isinstance(tools, list):
            raise ValueError("tools must be an array")
        tools = [tool for tool in tools or [] if not (isinstance(tool, dict) and is_web_search_tool(tool))]
        tool_choice = req_data.get('tool_choice')
        parallel_tool_calls = req_data.get('parallel_tool_calls', True)
        validate_tool_request(tools, tool_choice, parallel_tool_calls)
        has_tools = bool(tools)

        logger.info("[%s] model=%s stream=%s tools=%s messages=%d",
                     request_id, model, stream, bool(has_tools), len(messages))

        if has_tools:
            messages = inject_tool_prompt(messages, tools, tool_choice, parallel_tool_calls)
        elif any(message.get("role") == "tool" or message.get("tool_calls") for message in messages):
            messages = normalize_tool_history(messages)

        messages, upstream_options = prepare_request(messages, model, config, req_data, "chat")
        chat_info = convert_messages_to_genai_format(messages)

        if not chat_info:
            return openai_error("No user message found in 'messages'")

        if stream:
            gen = stream_genai_response(
                chat_info, messages, model, max_tokens, config,
                upstream_options=upstream_options, tools=tools, tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
            )
            return Response(
                stream_with_context(gen),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'Content-Type': 'text/event-stream',
                    'X-Accel-Buffering': 'no',
                }
            )

        else:
            try:
                complete_content, complete_reasoning, finish_reason, tool_calls = collect_genai_response(
                    chat_info, messages, model, max_tokens, config,
                    upstream_options=upstream_options, tools=tools, tool_choice=tool_choice,
                    parallel_tool_calls=parallel_tool_calls,
                )
            except UpstreamError as e:
                logger.warning("[%s] upstream error: %s", request_id, e)
                return openai_error(str(e), error_type="upstream_error", code=e.code, status=502)

            completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

            if tool_calls:
                message_obj = {
                    "role": "assistant",
                    "content": complete_content,
                    "tool_calls": tool_calls
                }
                finish_reason = "tool_calls"
            else:
                message_obj = {
                    "role": "assistant",
                    "content": complete_content
                }
                finish_reason = "length" if finish_reason == "length" else "stop"

            if complete_reasoning:
                message_obj["reasoning_content"] = complete_reasoning

            prompt_tokens = estimate_messages_tokens(messages)
            completion_tokens = estimate_text_tokens(complete_content + complete_reasoning)
            response = {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(datetime.now().timestamp()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": message_obj,
                    "finish_reason": finish_reason
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens
                }
            }
            return jsonify(response)

    except ValueError as e:
        return openai_error(str(e), error_type="invalid_request_error", status=400)
    except UpstreamError as e:
        return openai_error(str(e), error_type="upstream_error", code=e.code, status=502)
    except Exception as e:
        logger.exception("[%s] Unhandled error", request_id)
        return openai_error(
            str(e),
            error_type="server_error",
            code="internal_error",
            status=500
        )
    finally:
        elapsed = time.monotonic() - start_time
        logger.info("[%s] completed in %.2fs", request_id, elapsed)
