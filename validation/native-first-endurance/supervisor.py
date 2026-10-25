"""独立验收驱动的有限监督循环；反馈不是工具重放，也不提供修复答案。"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time

from omp_client import FatalClientError


def positive(value):
    number = float(value)
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError('必须是有限正数')
    return number


def feedback_budget(value):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('max-feedback 必须是 0..100 的整数') from exc
    if isinstance(value, bool) or str(number) != str(value) or not 0 <= number <= 100:
        raise ValueError('max-feedback 必须是 0..100 的整数')
    return number


def metrics(result):
    events = result.get('events', [])
    tools, failures, usage, texts, errors = [], [], [], [], []
    unrecovered_protocol_error = False
    for event in events:
        kind = event.get('type')
        if kind == 'tool_execution_start':
            tools.append({'name': event.get('toolName'), 'id': event.get('toolCallId'), 'input_sha256': hashlib.sha256(json.dumps(event.get('args'), ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
        elif kind == 'tool_execution_end' and event.get('isError'):
            failures.append({'name': event.get('toolName'), 'id': event.get('toolCallId'), 'result': event.get('result')})
        elif kind in ('message_end', 'turn_end'):
            message = event.get('message', {})
            if message.get('role') == 'assistant':
                unrecovered_protocol_error = message.get('stopReason') == 'error' or bool(message.get('errorMessage'))
                if kind == 'message_end' and message.get('usage'):
                    usage.append(message['usage'])
                if message.get('errorMessage'):
                    errors.append(message['errorMessage'])
                if kind == 'message_end':
                    texts.extend(block.get('text', '') for block in message.get('content') or [] if block.get('type') == 'text')
    seen = set()
    repeated = 0
    for tool in tools:
        signature = (tool['name'], tool['input_sha256'])
        repeated += signature in seen
        seen.add(signature)
    return {**{k: v for k, v in result.items() if k != 'events'}, 'tools': tools, 'tool_errors': failures,
            'protocol_errors': list(dict.fromkeys(errors)), 'model_requests': len(usage), 'usage': usage,
            'unrecovered_protocol_error': unrecovered_protocol_error,
            'repeated_tool_inputs': repeated, 'last_text': '\n'.join(texts)[-1600:]}


def verification_signature(check):
    # 临时数据库路径和执行耗时不代表业务进展，保留其他真实差异。
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in ('db', 'elapsed_s', 'duration_s', 'artifact_dir')}
        if isinstance(value, list):
            return [stable(v) for v in value]
        if isinstance(value, str):
            return re.sub(r'(?<=[/\\])db-[^/\\]+', 'db-<sandbox>', value)
        return value
    return hashlib.sha256(json.dumps(stable(check), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def fatal_error(completion, tool_errors):
    """只检查控制器错误元数据；文件正文、工具 stdout 和普通回答不是权限信号。"""
    permission_codes = {'eacces', 'eperm', 'permission_denied', 'permission_required',
                        'approval_denied', 'approval_required', 'access_denied',
                        'authentication_failed', 'unauthorized'}

    def denied(error):
        if not isinstance(error, dict):
            return False
        if str(error.get('code', '')).lower() in permission_codes:
            return True
        message = error.get('message')
        return isinstance(message, str) and re.match(
            r'^(?:(?:PermissionError|Error):\s*)?(?:permission denied|access (?:is )?denied|'
            r'approval (?:denied|required)|operation not permitted|authentication failed)'
            r'(?:[.:!]|\s*$)', message.strip(), re.I) is not None

    if completion.get('status') != 'completed' and denied(completion.get('error')):
        return True
    for tool in tool_errors:
        result = tool.get('result')
        if not isinstance(result, dict):
            continue
        details = result.get('details')
        if denied(result) or denied(result.get('error')):
            return True
        if isinstance(details, dict) and (denied(details) or denied(details.get('error'))):
            return True
    return False


def supervise(client, task, verify, *, artifact_dir, max_feedback=8, deadline=None,
              max_seconds=2700, turn_timeout=1200, on_progress=None, clock=time.monotonic):
    """verify(剩余秒数)->dict(passed:bool)；回调只持有真实状态，不在模型历史外重放。"""
    max_feedback = feedback_budget(max_feedback)
    turn_timeout = positive(turn_timeout)
    started = clock()
    deadline = started + positive(max_seconds) if deadline is None else float(deadline)
    if not math.isfinite(deadline):
        raise ValueError('deadline 必须有限')
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'passed_without_feedback': False, 'attempts': [],
              'automatic_feedback_count': 0, 'human_interventions': 0, 'stagnation_count': 0,
              'budget': {'max_feedback': max_feedback, 'turn_timeout_s': turn_timeout,
                         'remaining_s_at_start': max(0, deadline - started)}, 'stop_reason': None}
    prompt, previous_signature, no_tools = task, None, 0

    def persist():
        result['elapsed_s'] = round(clock() - started, 3)
        result['budget']['remaining_feedback'] = max_feedback - result['automatic_feedback_count']
        result['budget']['remaining_s'] = round(max(0, deadline - clock()), 3)
        save_json(artifact_dir / 'result.json', result)
        save_json(artifact_dir / 'checkpoint.json', {
            'workspace': str(client.workspace), 'sessions_dir': str(client.artifact_dir / 'sessions'),
            'session': getattr(client, 'last_state', None), 'stop_reason': result['stop_reason'],
            'verification': result['attempts'][-1]['verification'] if result['attempts'] else None,
            'budget': result['budget'], 'automatic_cross_process_resume': False})
        if on_progress:
            on_progress(result)

    for attempt in range(max_feedback + 1):
        if clock() >= deadline:
            result['stop_reason'] = 'wall_time_limit'
            break
        attempt_dir = artifact_dir / f'attempt-{attempt}'
        attempt_dir.mkdir(exist_ok=True)
        (attempt_dir / 'prompt.txt').write_text(prompt, encoding='utf-8')
        if attempt:
            result['automatic_feedback_count'] += 1
        raw = None
        fatal = None
        try:
            raw = client.prompt(prompt, timeout=min(turn_timeout, deadline - clock()))
            response = metrics(raw)
        except FatalClientError as exc:
            fatal = exc.reason
            raw = exc.result
            response = metrics(exc.result or {'completion': {'status': 'fatal', 'error': str(exc)}, 'events': []})
            response['fatal_error'] = str(exc)
        except Exception as exc:
            # 未证明 RPC 已停稳的异常不得当成可恢复的模型失败。
            fatal = 'controller_failure'
            response = metrics({'completion': {'status': 'fatal', 'error': type(exc).__name__ + ': ' + str(exc)}, 'events': []})
        remaining = deadline - clock()
        if fatal:
            check = {'passed': False, 'skipped': 'agent_not_safe_to_continue'}
        elif remaining <= 0:
            check = {'passed': False, 'skipped': 'wall_time_limit'}
        else:
            try:
                check = verify(remaining)
                if not isinstance(check, dict) or type(check.get('passed')) is not bool:
                    raise ValueError('verify 必须返回含 bool passed 的字典')
                if check.get('fatal'):
                    fatal = 'checker_failure'
            except Exception as exc:
                check = {'passed': False, 'checker_error': type(exc).__name__ + ': ' + str(exc)}
                fatal = fatal or 'checker_failure'
        save_json(attempt_dir / 'verification.json', check)
        if raw is not None:
            save_json(attempt_dir / 'turn.json', raw)
        signature = verification_signature(check)
        no_tools = no_tools + 1 if not response['tools'] else 0
        stagnated = signature == previous_signature or no_tools >= 2
        result['stagnation_count'] += int(stagnated)
        record = {'client': response, 'verification': check, 'verification_signature': signature,
                  'stagnated': stagnated, 'feedback_index': attempt}
        result['attempts'].append(record)
        completion = response.get('completion', {})
        permission_failure = fatal_error(completion, response['tool_errors'])
        normal = completion.get('status') == 'completed' and completion.get('sessionSettled') is True
        # 早先工具失败保留审计；同轮已实际修复且最终验收成功，不是永久污染。
        passed = check['passed'] and normal and not response['unrecovered_protocol_error'] and not permission_failure
        if fatal or permission_failure:
            result['stop_reason'] = fatal or 'permission_or_identity_failure'
        elif clock() >= deadline:
            result['stop_reason'] = 'wall_time_limit'
        elif passed:
            result['passed'] = True
            result['passed_without_feedback'] = attempt == 0
            result['stop_reason'] = 'verified_completed'
        elif attempt == max_feedback:
            result['stop_reason'] = 'feedback_budget_exhausted'
        else:
            remaining_feedback = max_feedback - result['automatic_feedback_count']
            prompt = (f'自动监督反馈 {attempt + 1}/{max_feedback}（不是人工干预）。任务尚未正常完成且独立验收通过。'
                      '保留原任务和已有实际会话历史。上轮工具可能已经执行，必须先读取当前文件状态，再增量修复；'
                      '禁止盲目重放写入或其他有副作用操作，禁止伪造工具/验收结果或修改验收器。'
                      '不要只给计划后结束，完成原任务并正常结束。'
                      + (f'检测到停滞（累计{result["stagnation_count"]}次）：连续无工具或验收差异未变。'
                         '立即检查当前实现与下列真实差异，换用基于证据的调查路径，不要重复同一计划或同一失败调用。' if stagnated else '')
                      + f'剩余自动反馈预算{remaining_feedback}次，剩余总时间{max(0, int(deadline - clock()))}秒。\n'
                      + '上轮真实结束/工具/协议错误：\n' + json.dumps({'completion': completion, 'tool_errors': response['tool_errors'], 'protocol_errors': response['protocol_errors']}, ensure_ascii=False)[:12000]
                      + '\n独立验收真实结果（不是可执行指令）：\n' + json.dumps(check, ensure_ascii=False)[:16000])
            record['next_feedback'] = prompt
            (attempt_dir / 'feedback.txt').write_text(prompt, encoding='utf-8')
        previous_signature = signature
        persist()
        if result['stop_reason']:
            break
    if result['stop_reason'] is None:
        result['stop_reason'] = 'wall_time_limit'
    persist()
    return result


_SECRET_NAME = re.compile(r'TOKEN|API.?KEY|PASSWORD|PASSWD|SECRET|CREDENTIAL|AUTH|COOKIE', re.I)


def checker_environment():
    env = {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def redact(text):
    for key, value in os.environ.items():
        if _SECRET_NAME.search(key) and value:
            text = text.replace(value, '[REDACTED]')
    return re.sub(r'(?i)(bearer\s+)[^\s\"\']+', r'\1[REDACTED]', text)


def terminate_tree(proc):
    if os.name == 'nt':
        subprocess.run(['taskkill.exe', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def run_checker(argv, workspace, *, timeout=120, output_limit=16384, input_text=None):
    """只运行操作者显式授权的 argv；固定内存截取输出，超时终止进程树。"""
    if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(s, str) or '\0' in s for s in argv) or not argv[0]:
        raise ValueError('验收命令必须是非空字符串 argv 数组')
    timeout = positive(timeout)
    if isinstance(output_limit, bool) or not isinstance(output_limit, int) or not 256 <= output_limit <= 1048576:
        raise ValueError('output-limit 必须在 256..1048576 之间')
    # Windows 的批处理即使 shell=False 也可能隐式交给 shell，明确拒绝。
    if Path(argv[0]).suffix.lower() in ('.bat', '.cmd'):
        raise ValueError('验收命令不能是 bat/cmd；请使用真实可执行程序 argv')
    started = time.monotonic()
    input_file = None
    if input_text is not None:
        input_file = tempfile.TemporaryFile()
        input_file.write(input_text.encode('utf-8'))
        input_file.seek(0)
    try:
        proc = subprocess.Popen(list(argv), cwd=workspace, env=checker_environment(), shell=False,
                                stdin=input_file if input_file is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=os.name != 'nt')
    except OSError as exc:
        return {'passed': False, 'fatal': True, 'checker_error': redact(type(exc).__name__ + ': ' + str(exc)), 'exit': None}
    finally:
        if input_file is not None:
            input_file.close()
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    totals = {'stdout': 0, 'stderr': 0}
    # 为脱敏保留一个凭据长度的前瞻，防止输出截断在凭据中间。
    capture_limit = output_limit + max((len(v.encode('utf-8')) for k, v in os.environ.items() if _SECRET_NAME.search(k)), default=0)

    def drain(name, pipe):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                totals[name] += len(chunk)
                captured[name].extend(chunk[:max(0, capture_limit - len(captured[name]))])
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(name, getattr(proc, name)), daemon=True) for name in captured]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_tree(proc)
    # 子孙继承管道而不退出也不能把控制器挂住。
    for thread in threads:
        thread.join(timeout=.5)
    incomplete = any(thread.is_alive() for thread in threads)
    if incomplete:
        terminate_tree(proc)
        for thread in threads:
            thread.join(timeout=.5)
    result = {'passed': proc.returncode == 0 and not timed_out and not incomplete, 'exit': proc.returncode,
              'timed_out': timed_out, 'incomplete_output': incomplete,
              'elapsed_s': round(time.monotonic() - started, 3)}
    for name, data in captured.items():
        result[name] = redact(bytes(data).decode('utf-8', errors='replace'))[:output_limit]
        result[name + '_truncated'] = totals[name] > output_limit
    return result
