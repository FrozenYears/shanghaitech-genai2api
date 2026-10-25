import json
import logging
import time
import uuid

from flask import Blueprint, current_app, request, jsonify, stream_with_context, Response

from provider.responses import (
    responses_input_to_genai_messages,
    responses_tool_choice_to_chat,
    responses_tools_to_chat_tools,
    stream_genai_as_responses,
)
from tools.prompts import inject_tool_prompt
from provider.features import prepare_request
from errors import UpstreamError
from tools.contracts import validate_tool_request

logger = logging.getLogger(__name__)

responses_bp = Blueprint('responses', __name__)


def responses_error(message: str, error_type: str = "invalid_request_error", code: str | None = None, status: int = 400) -> tuple:
    """Return an OpenAI Responses API style error response."""
    return jsonify({
        "error": {
            "code": code,
            "message": message,
            "param": None,
            "type": error_type,
        }
    }), status


def _build_request(body):
    """把 Responses 请求转换成 GenAI messages 和生成器参数。"""
    if body.get("previous_response_id") is not None:
        raise ValueError("previous_response_id is not supported; provide complete input history")
    if body.get("store") not in (None, False):
        raise ValueError("store=true is not supported")
    if body.get("conversation") is not None or body.get("background"):
        raise ValueError("Remote conversations and background responses are not supported")
    model = body.get("model", "GPT-5.5")
    max_output_tokens = body.get("max_output_tokens")
    max_tokens = max_output_tokens or 30000

    chat_tools, custom_tool_names = responses_tools_to_chat_tools(body.get("tools"))
    tool_choice = responses_tool_choice_to_chat(body.get("tool_choice"))
    validate_tool_request(chat_tools, tool_choice, body.get("parallel_tool_calls", True))
    messages = responses_input_to_genai_messages(body.get("input"), body.get("instructions"))

    if chat_tools and tool_choice != "none":
        messages = inject_tool_prompt(messages, chat_tools, tool_choice, body.get("parallel_tool_calls", True))

    return (
        messages,
        model,
        max_tokens,
        custom_tool_names,
        chat_tools,
        max_output_tokens,
    )


@responses_bp.route('/responses', methods=['POST'])
@responses_bp.route('/v1/responses', methods=['POST'])
def responses():
    """Handle OpenAI Responses API requests."""
    config = current_app.config["APP_CONFIG"]
    request_id = f"resp_{uuid.uuid4().hex[:16]}"
    start_time = time.monotonic()

    try:
        body = request.get_json()
        if not body or "input" not in body:
            return responses_error("Missing 'input' field in request body")

        model = body.get("model", "GPT-5.5")
        stream = body.get("stream", False)

        (
            messages,
            model,
            max_tokens,
            custom_tool_names,
            chat_tools,
            max_output_tokens,
        ) = _build_request(body)

        if not messages:
            return responses_error("No valid input provided")

        logger.info(
            "[%s] model=%s stream=%s tools=%d messages=%d",
            request_id,
            model,
            stream,
            len(chat_tools),
            len(messages),
        )

        messages, upstream_options = prepare_request(messages, model, config, body, "responses")

        generator_args = dict(
            upstream_options=upstream_options,
            instructions=body.get("instructions"),
            custom_tool_names=custom_tool_names,
            tools=chat_tools,
            tool_choice=responses_tool_choice_to_chat(body.get("tool_choice")),
            parallel_tool_calls=body.get("parallel_tool_calls", True),
            max_output_tokens=max_output_tokens,
            response_meta={
                "tools": body.get("tools") or [],
                "tool_choice": body.get("tool_choice") or "auto",
                "parallel_tool_calls": body.get("parallel_tool_calls", True),
                "store": body.get("store", False),
                "reasoning": body.get("reasoning"),
            },
        )

        if stream:
            gen = stream_genai_as_responses(messages, model, max_tokens, config, **generator_args)
            return Response(
                stream_with_context(gen),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'Connection': 'close',
                    'Content-Type': 'text/event-stream; charset=utf-8',
                    'X-Accel-Buffering': 'no',
                },
            )

        # 非流式：收集到 response.completed 后一次性返回
        response_object = None
        for chunk in stream_genai_as_responses(messages, model, max_tokens, config, **generator_args):
            event_name = None
            event_data = None
            for line in chunk.strip().splitlines():
                if line.startswith("event: "):
                    event_name = line[7:]
                elif line.startswith("data: "):
                    try:
                        event_data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        event_data = None

            if not event_data:
                continue
            if event_name == "error":
                return responses_error(
                    event_data.get("message", "Upstream error"),
                    error_type="api_error",
                    code=event_data.get("code"),
                    status=502,
                )
            if event_name in ("response.completed", "response.incomplete"):
                response_object = event_data.get("response")

        if response_object is None:
            return responses_error("Upstream returned no response", error_type="api_error", status=502)
        return jsonify(response_object)

    except ValueError as e:
        return responses_error(str(e), status=400)
    except UpstreamError as e:
        return responses_error(str(e), error_type="api_error", code=e.code, status=502)
    except Exception as e:
        logger.exception("[%s] Unhandled error", request_id)
        return responses_error(str(e), error_type="api_error", status=500)
    finally:
        elapsed = time.monotonic() - start_time
        logger.info("[%s] completed in %.2fs", request_id, elapsed)
