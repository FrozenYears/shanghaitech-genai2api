"""只在本地执行的确定性空转实验：不访问网络或生产服务。"""

import hashlib
import json


TOOLS = [
    {"type": "function", "function": {
        "name": "read_state", "description": "读取本地状态中指定键的字符串；不存在时明确返回not_found。",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}},
                       "required": ["key"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "commit_result", "description": "精确提交字符串一次，并将提交结果保存到本地result键供读取。",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False},
    }},
]


def execute(case, name, parameters):
    """执行真实本地副作用；重复提交保留每次记录，不偷偷去重。"""
    if name not in {"read_state", "commit_result"}:
        raise ValueError(f"unknown tool: {name}")
    if not isinstance(parameters, dict):
        raise TypeError("tool parameters must be an object")
    field = "key" if name == "read_state" else "value"
    if field in parameters and not isinstance(parameters[field], str):
        raise TypeError(f"{name}.{field} must be a string")
    if set(parameters) != {field}:
        return {"output": f"invalid_parameters: exactly '{field}' is required", "is_error": True}
    value = parameters[field]
    if name == "read_state":
        if value not in case["state"]:
            return {"output": f"not_found: {value}", "is_error": True}
        return {"output": case["state"][value], "is_error": False}
    case["committed"].append(value)
    case["state"]["result"] = value
    return {"output": "committed:" + value, "is_error": False}


def _append_calls(case, calls, result_order=None):
    """历史回执也必须经过同一个执行器，不能伪造成功或失败。"""
    case["messages"].append({"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(parameters, ensure_ascii=False),
        }} for call_id, name, parameters in calls
    ]})
    for index in result_order if result_order is not None else range(len(calls)):
        call_id, name, parameters = calls[index]
        receipt = execute(case, name, parameters)
        case["messages"].append({"role": "tool", "tool_call_id": call_id,
                                 "content": receipt["output"], "is_error": receipt["is_error"]})


def make_case(name, nonce):
    """构造完整Chat历史；随机性全部由调用方传入的nonce决定。"""
    if name not in {"pending", "failed_last", "success_last", "completed", "qa", "long_pending"}:
        raise ValueError(f"unknown case: {name}")
    if not isinstance(nonce, str):
        raise TypeError("nonce must be a string")
    case = {"name": name, "messages": [], "state": {}, "expected": None,
            "committed": [], "mode": "answer" if name == "qa" else "action"}
    if name == "qa":
        case["messages"].append({"role": "user", "content": "只回答一个数字：2加2等于几？不需要使用工具。"})
    else:
        left = "L-" + hashlib.sha256(("left:" + nonce).encode()).hexdigest()[:24]
        right = "R-" + hashlib.sha256(("right:" + nonce).encode()).hexdigest()[:24]
        fallback = name in {"failed_last", "success_last"}
        case["state"] = {"left": left, "right_backup" if fallback else "right": right}
        case["expected"] = left + "|" + right
        if name == "long_pending":
            # 二十组已经结束且与当前任务无关的公开合成对话，总计约一万六千字符。
            paragraph = (
                "这是一段已经归档的虚构城市步行路线讨论：清晨从图书馆门口出发，经过蓝色长椅、"
                "小型花圃与石板路，到达公共广场后原路返回。所有地点、人物和记录均为合成内容，"
                "无需访问地图、读写文件或调用任何工具。本段只作为过去已完成的普通对话，不包含当前待办事项。"
            )
            for index in range(20):
                case["messages"].extend([
                    {"role": "user", "content": f"归档对话{index + 1}：请概括这份虚构散步记录。" + paragraph * 5},
                    {"role": "assistant", "content": "这份合成记录描述了从图书馆到公共广场再返回的短途步行，途经长椅、花圃和石板路；归档概括已完成，不涉及工具或待执行动作。"},
                ])
        case["messages"].append({"role": "user", "content": (
            "请用read_state分别读取left和right。仅当right返回not_found时，改读right_backup；"
            "不要重读已经成功读取的键。将两个成功读取的原始字符串按left+'|'+right拼接，"
            "精确调用commit_result一次，然后给出普通最终答复。提交之前任务尚未完成；"
            "如果历史已经完成提交，不要再次提交或调用无关工具，直接给出最终答复。"
        )})
        reads = [("call_read_left", "read_state", {"key": "left"}),
                 ("call_read_right", "read_state", {"key": "right"})]
        _append_calls(case, reads, [1, 0] if name == "success_last" else [0, 1])
        if name == "completed":
            _append_calls(case, [("call_commit", "commit_result", {"value": case["expected"]})])
            _append_calls(case, [("call_readback", "read_state", {"key": "result"})])
    # 保留历史结束时的真实快照，判定时重放事件不重复污染实际副作用。
    case["_initial_state"] = dict(case["state"])
    case["_initial_committed"] = list(case["committed"])
    return case


def verdict(case, transcript_events):
    """核对实际执行、事件回执与结束状态；工具数量增长不是任务完成。"""
    replay = {"state": dict(case["_initial_state"]), "committed": list(case["_initial_committed"])}
    errors = []
    unnecessary_actions = 0
    repeated_actions = 0
    unexpected_stop = False
    stopped = False
    has_answer = False
    backup_read = False
    requires_backup = case["name"] in {"failed_last", "success_last"}
    control = case["mode"] == "answer" or case["name"] == "completed"
    seen_reads = {}
    history_calls = {}
    for message in case["messages"]:
        for call in message.get("tool_calls", []):
            history_calls[call["id"]] = call["function"]
        if message["role"] == "tool":
            function = history_calls[message["tool_call_id"]]
            if function["name"] == "read_state" and not message["is_error"]:
                seen_reads[json.loads(function["arguments"])["key"]] = message["content"]

    def task_complete():
        if case["mode"] == "answer":
            return replay["committed"] == []
        return replay["committed"] == [case["expected"]] and (not requires_backup or backup_read)

    for event in transcript_events:
        if not isinstance(event, dict):
            errors.append("invalid_event: expected an object")
            continue
        kind = event.get("kind")
        if kind == "error":
            errors.append("execution_error: " + str(event.get("message", "")))
            continue
        if kind == "stop":
            if stopped:
                errors.append("duplicate_stop")
            stopped = True
            text = event.get("text")
            has_answer = isinstance(text, str) and bool(text.strip())
            if not has_answer:
                errors.append("empty_final_answer")
            if not task_complete():
                unexpected_stop = True
            continue
        if kind != "tool":
            errors.append("invalid_event_kind: " + str(kind))
            continue
        if stopped:
            errors.append("action_after_stop")
        if control:
            unnecessary_actions += 1
        name = event.get("name")
        parameters = event.get("parameters")
        if name == "read_state" and isinstance(parameters, dict) and isinstance(parameters.get("key"), str):
            key = parameters["key"]
            if not control and not (requires_backup and key == "right_backup"):
                unnecessary_actions += 1
        if name == "commit_result" and replay["committed"]:
            repeated_actions += 1
        try:
            receipt = execute(replay, name, parameters)
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid_tool_call: {exc}")
            continue
        if type(event.get("is_error")) is not bool or event.get("is_error") != receipt["is_error"] or event.get("output") != receipt["output"]:
            errors.append("tool_receipt_mismatch: " + name)
        if receipt["is_error"]:
            errors.append("tool_failed: " + name + ": " + receipt["output"])
        elif name == "read_state":
            # 只统计成功且内容未变的重读；首次必要的备用读取不受罚。
            key = parameters["key"]
            if key in seen_reads and seen_reads[key] == receipt["output"]:
                repeated_actions += 1
            seen_reads[key] = receipt["output"]
            if key == "right_backup":
                backup_read = True
        elif name == "commit_result":
            if parameters["value"] != case["expected"]:
                errors.append("incorrect_commit")
            if requires_backup and not backup_read:
                errors.append("commit_before_required_backup_read")
    if replay["committed"] != case["committed"]:
        errors.append("actual_commit_mismatch")
    complete = task_complete()
    passed = complete and stopped and has_answer and not (
        unexpected_stop or unnecessary_actions or repeated_actions or errors
    )
    if passed:
        status = "passed"
    elif unexpected_stop:
        status = "unexpected_stop"
    elif errors or unnecessary_actions or repeated_actions:
        status = "failed"
    else:
        status = "incomplete"
    return {"passed": passed, "status": status, "committed": list(case["committed"]),
            "unexpected_stop": unexpected_stop, "unnecessary_actions": unnecessary_actions,
            "repeated_actions": repeated_actions, "errors": errors}
