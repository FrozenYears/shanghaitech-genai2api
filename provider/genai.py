import json
import logging
import re
import uuid
from datetime import datetime

import requests

from config import GENAI_URL, build_genai_headers, model_registry
from errors import UpstreamError, make_error_chunk
from provider.features import search_sources
from tools.contracts import strict_json_loads
from provider.streaming import heartbeat_events, validated_events
from tools.prompts import flatten_message_content, normalize_message_content

logger = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]")

# 上游几十万 token 的大 prompt 首 token 可能远超 60s，用长超时避免被误判为失败。
UPSTREAM_TIMEOUT = 300


def convert_messages_to_genai_format(messages):
    """取最后一条 user 消息的文本作为当前提问（chatInfo）。"""
    chat_info = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            chat_info = flatten_message_content(msg.get("content", ""))
            break
    return chat_info


def extract_content_from_genai(response_data):
    try:
        if "choices" in response_data and len(response_data["choices"]) > 0:
            delta = response_data["choices"][0].get("delta", {})
            content = delta.get("content") or None
            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or None
            return content, reasoning
    except (KeyError, IndexError, TypeError):
        pass
    return None, None


def estimate_text_tokens(text):
    if not text:
        return 0
    return len(TOKEN_PATTERN.findall(text))


def estimate_messages_tokens(messages):
    """估算整段对话的输入 token，用于填充 usage.prompt_tokens / input_tokens。"""
    return sum(
        estimate_text_tokens(flatten_message_content(msg.get("content", "")))
        for msg in messages
    )



def split_history_messages(messages):
    """规范化历史消息，并移除最后一条 user（它已经作为 chatInfo 传入）。"""
    normalized = [normalize_message_content(msg) for msg in messages]
    for index in range(len(normalized) - 1, -1, -1):
        if normalized[index].get("role") == "user":
            del normalized[index]
            break
    return normalized


def iter_genai_stream(chat_info, history_messages, model, max_tokens, config, token=None, upstream_options=None,
                      recover_empty=False):
    """增量正文/思考；仅工具请求的完全空 stop 允许一次显式继续。"""
    def source(control):
        question = chat_info
        for attempt in range(2 if recover_empty else 1):
            emitted = False
            completed = False
            for event in _iter_genai_stream(question, history_messages, model, max_tokens, config,
                                           token, upstream_options, control):
                if event["type"] == "delta" and (event.get("content") or event.get("reasoning")):
                    emitted = True
                if event["type"] == "error":
                    yield event
                    return
                if event["type"] == "done":
                    completed = True
                    if (recover_empty and not emitted and not event.get("tool_calls")
                            and event.get("finish_reason") == "stop"):
                        if attempt == 0 and not control.cancelled.is_set():
                            # 没有可见增量或调用被交付。改变提问请求“继续”，不是重放坏参数/工具。
                            question = chat_info + (
                                "\n\n上一轮生成已结束，但没有返回任何正文、思考或工具调用，"
                                "没有新的工具调用交付给客户端。请根据完整历史和当前任务继续："
                                "需要操作时给出完整合法调用，否则给出最终答复。"
                                "历史工具结果已经实际执行，不要重复已完成的操作，也不要模拟结果。"
                            )
                            logger.warning("GenAI completed empty model=%s; requesting one continuation", model)
                            break
                        yield {"type": "error", "code": "upstream_empty_response",
                               "message": "Upstream completed empty after one continuation; no tool call was delivered"}
                        return
                    yield event
                    return
                yield event
            else:
                if not completed:
                    yield {"type": "error", "message": "Upstream stream ended before completion",
                           "code": "upstream_stream_incomplete"}
                return
    yield from heartbeat_events(source)


def _iter_genai_stream(chat_info, history_messages, model, max_tokens, config, token, upstream_options, control):
    if control.cancelled.is_set():
        return
    token = token or config.token_manager.get_token()
    root_ai_type = model_registry.get_root_ai_type(model, token)
    headers = build_genai_headers(token)

    genai_data = {
        "chatInfo": chat_info,
        "messages": history_messages,
        "type": "3",
        "stream": True,
        "aiType": model,
        "aiSecType": "1",
        "promptTokens": 0,
        "rootAiType": root_ai_type,
        "maxToken": max_tokens or 30000,
        **(upstream_options or {}),
    }

    logger.debug("=== GenAI Request ===")
    logger.debug("Model: %s, rootAiType: %s", model, root_ai_type)
    logger.debug("Messages count: %d", len(history_messages))

    response = None
    try:
        if control.cancelled.is_set():
            return
        response = requests.post(
            GENAI_URL,
            headers=headers,
            json=genai_data,
            stream=True,
            timeout=(10, UPSTREAM_TIMEOUT),
        )
        if not control.attach(response):
            return

        if response.status_code == 401:
            new_token = config.token_manager.force_refresh()
            if control.cancelled.is_set():
                return
            if new_token:
                logger.info("Token refreshed after 401, retrying request")
                headers = build_genai_headers(new_token)
                token = new_token
                response.close()
                response = requests.post(
                    GENAI_URL, headers=headers, json=genai_data, stream=True,
                    timeout=(10, UPSTREAM_TIMEOUT),
                )
                if not control.attach(response):
                    return

        if response.status_code != 200:
            logger.warning("GenAI API error %d: %s", response.status_code, response.text[:500])
            if response.status_code == 401:
                yield {"type": "error", "message": "Upstream authentication failed"}
            elif response.status_code == 429:
                yield {"type": "error", "message": "Upstream rate limit exceeded"}
            else:
                yield {"type": "error", "message": f"Upstream API error: {response.status_code}"}
            return

        finish_reason = None
        emitted_tokens = 0
        truncated = False
        stream_ended = False
        native_calls = {}
        for line in response.iter_lines(chunk_size=1):
            if control.cancelled.is_set():
                return
            if not line:
                continue

            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            line_str = line_str.strip()
            if not line_str:
                continue

            if line_str in ("[DONE]", "data:[DONE]", "data: [DONE]"):
                stream_ended = True
                finish_reason = finish_reason or "stop"
                break

            # SSE 注释行（": keep-alive"）与其它字段行不是数据，跳过。
            if line_str.startswith(":") or line_str.split(":", 1)[0] in ("event", "id", "retry"):
                continue

            if line_str.startswith("data:"):
                line_str = line_str[5:].strip()
                if not line_str:
                    continue

            try:
                genai_json = strict_json_loads(line_str)
            except ValueError:
                # 上游有时直接在 SSE 通道里写纯文本错误（worker 不可用时就是一行
                # "No available workers (all circuits open or unhealthy)"）。以前这里
                # 只记 debug 日志然后跳过，结果把“上游不可用”伪装成 HTTP 200 空回复。
                logger.warning("GenAI 上游返回非 JSON 文本: %s", line_str[:200])
                yield {"type": "error", "message": f"Upstream error: {line_str[:200]}"}
                return

            if isinstance(genai_json, dict) and genai_json.get("success") is False:
                err_msg = genai_json.get("message", "Unknown upstream error")
                logger.warning("GenAI business error: %s", err_msg)
                yield {"type": "error", "message": f"Upstream error: {err_msg}"}
                return

            if isinstance(genai_json, dict) and "choices" not in genai_json:
                error = genai_json.get("error")
                err_msg = genai_json.get("errMsg")
                if isinstance(error, dict):
                    err_msg = error.get("message") or err_msg
                if err_msg:
                    logger.warning("GenAI upstream error: %s", err_msg)
                    yield {"type": "error", "message": f"Upstream error: {err_msg}"}
                    return
                continue

            choices = genai_json.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            for fragment in delta.get("tool_calls") or []:
                index = fragment.get("index")
                if type(index) is not int or index < 0:
                    raise ValueError("Native tool call has an invalid index")
                call = native_calls.get(index)
                if call is None:
                    call = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                    native_calls[index] = call
                if fragment.get("type", "function") != "function":
                    raise ValueError("Unsupported native tool call type")
                value = fragment.get("id")
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError("Invalid native tool call ID")
                    call["id"] += value
                function = fragment.get("function") or {}
                for key in ("name", "arguments"):
                    value = function.get(key)
                    if value is not None:
                        if not isinstance(value, str):
                            raise ValueError("Invalid native tool call fragment")
                        call["function"][key] += value
                        emitted_tokens += estimate_text_tokens(value)
            if max_tokens and emitted_tokens >= max_tokens:
                truncated = True
                stream_ended = True
                break

            content, reasoning = extract_content_from_genai(genai_json)
            if content or reasoning:
                yield {"type": "delta", "content": content, "reasoning": reasoning}
                emitted_tokens += estimate_text_tokens(content or "") + estimate_text_tokens(reasoning or "")
                # 上游不执行 maxToken，长度上限只能在本地按估算 token 强制生效。
                if max_tokens and emitted_tokens >= max_tokens:
                    truncated = True
                    stream_ended = True
                    break

            if choices[0].get("finish_reason") is not None:
                finish_reason = choices[0].get("finish_reason")
                stream_ended = True
                break

        if not stream_ended:
            yield {"type": "error", "message": "Upstream stream ended before completion", "code": "upstream_stream_incomplete"}
            return
        calls = [native_calls[index] for index in sorted(native_calls)]
        if calls:
            if truncated or finish_reason == "length":
                raise ValueError("Native tool call output was truncated")
            if finish_reason not in ("tool_calls", "stop"):
                raise ValueError("Native tool calls did not complete")
            if sorted(native_calls) != list(range(len(calls))):
                raise ValueError("Native tool call indices are incomplete")
            for call in calls:
                if not call["id"] or not call["function"]["name"]:
                    raise ValueError("Native tool call is incomplete")
                if not isinstance(strict_json_loads(call["function"]["arguments"]), dict):
                    raise ValueError("Native tool call arguments must be an object")
        if control.cancelled.is_set():
            return
        if genai_data.get("netGo") and not truncated:
            try:
                sources = search_sources(genai_data, token)
                # 附加来源也受本地输出预算约束，只保留完整的链接行。
                if max_tokens and sources:
                    kept = []
                    for source_line in sources.splitlines(keepends=True):
                        count = estimate_text_tokens(source_line)
                        if emitted_tokens + count > max_tokens:
                            truncated = True
                            break
                        emitted_tokens += count
                        kept.append(source_line)
                    sources = "".join(kept)
                if sources:
                    yield {"type": "delta", "content": sources, "reasoning": None, "sources": True}
            except (requests.RequestException, UpstreamError, ValueError):
                logger.warning("补充检索列表获取失败；保留已返回的正文引用", exc_info=True)
        yield {"type": "done", "finish_reason": "length" if truncated else finish_reason, "tool_calls": calls}

    except Exception as e:
        logger.exception("Error in iter_genai_stream")
        yield {"type": "error", "message": str(e), "code": getattr(e, "code", "upstream_stream_error")}
    finally:
        if response is not None:
            response.close()


def collect_genai_response(chat_info, messages, model, max_tokens, config, upstream_options=None,
                           tools=None, tool_choice=None, parallel_tool_calls=True):
    """收集已校验输出，返回正文、思考、结束原因和完整调用。"""
    content_parts = []
    reasoning_parts = []
    finish_reason = None
    tool_calls = []
    history = split_history_messages(messages)
    source = iter_genai_stream(chat_info, history, model, max_tokens, config, upstream_options=upstream_options,
                              recover_empty=bool(tools))
    for event in validated_events(source, tools, tool_choice, parallel_tool_calls):
        if event["type"] == "error":
            raise UpstreamError(event["message"], code=event.get("code", "upstream_error"))
        if event["type"] == "delta":
            if event.get("content"):
                content_parts.append(event["content"])
            if event.get("reasoning"):
                reasoning_parts.append(event["reasoning"])
        elif event["type"] == "done":
            finish_reason = event.get("finish_reason")
            tool_calls = event.get("tool_calls", [])
    return "".join(content_parts), "".join(reasoning_parts), finish_reason, tool_calls


def _chat_finish_reason(upstream_reason):
    """把上游 finish_reason 映射成 OpenAI Chat 的取值。"""
    return "length" if upstream_reason == "length" else "stop"


def stream_genai_response(chat_info, messages, model, max_tokens, config, upstream_options=None,
                          tools=None, tool_choice=None, parallel_tool_calls=True):
    """Chat 流先发角色，安全正文实时交付，工具整批校验后交付。"""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(datetime.now().timestamp())
    prompt_tokens = estimate_messages_tokens(messages)
    output_tokens = 0

    def chunk(delta, finish_reason=None, usage=None):
        value = {
            "id": completion_id, "object": "chat.completion.chunk", "created": created,
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            value["usage"] = usage
        return f"data: {json.dumps(value, ensure_ascii=False)}\n\n"

    yield chunk({"role": "assistant", "content": ""})
    source = iter_genai_stream(chat_info, split_history_messages(messages), model, max_tokens, config,
                               upstream_options=upstream_options, recover_empty=bool(tools))
    events = validated_events(source, tools, tool_choice, parallel_tool_calls)
    try:
        for event in events:
            if event["type"] == "heartbeat":
                yield ": keep-alive\n\n"
            elif event["type"] == "error":
                yield make_error_chunk(event["message"], code=event.get("code", "upstream_error"))
                return
            elif event["type"] == "delta":
                delta = {}
                if event.get("content"):
                    delta["content"] = event["content"]
                if event.get("reasoning"):
                    delta["reasoning_content"] = event["reasoning"]
                output_tokens += sum(estimate_text_tokens(value) for value in delta.values())
                if delta:
                    yield chunk(delta)
            elif event["type"] == "done":
                calls = event["tool_calls"]
                for index, call in enumerate(calls):
                    yield chunk({"tool_calls": [{"index": index, **call}]})
                yield chunk({}, "tool_calls" if calls else _chat_finish_reason(event.get("finish_reason")), {
                    "prompt_tokens": prompt_tokens, "completion_tokens": output_tokens,
                    "total_tokens": prompt_tokens + output_tokens,
                })
                yield "data: [DONE]\n\n"
                return
    finally:
        events.close()
