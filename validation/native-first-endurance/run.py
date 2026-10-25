"""正式 GenAI 双跳的真实 omp 持续开发验收；不会调用官方 API。"""
import argparse
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from omp_client import OmpClient
from workload import BASE, STAGES, STARTER, cases

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / '.pixi/envs/default/python.exe'
FIXTURE = ''.join(f'fixture-{i:04d}: 雪 \\path "quote" <think>literal</think> </tool_call> @@ACTION@@ {hashlib.sha256(str(i).encode()).hexdigest()}\n' for i in range(512))


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(workspace, stage, output=None):
    """逐请求运行实际业务代码；期望只由验收进程持有。"""
    failures = []
    assertions = 0
    sandbox = Path(tempfile.mkdtemp(prefix='db-', dir=workspace.parent))
    child_env = {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in ('TOKEN', 'API_KEY', 'PASSWORD', 'SECRET'))}
    child_env['PYTHONDONTWRITEBYTECODE'] = '1'
    for group in cases(stage):
        db = sandbox / (group['name'].replace('/', '_') + '.sqlite')
        for step, (request, expected) in enumerate(group['steps']):
            request = deepcopy(request)
            if request.pop('_legacy', False):
                with sqlite3.connect(db) as connection:
                    connection.execute('CREATE TABLE transactions (id TEXT PRIMARY KEY,date TEXT,account TEXT,amount TEXT,currency TEXT,category TEXT)')
                    connection.execute('INSERT INTO transactions VALUES (?,?,?,?,?,?)', ('legacy', '2024-01-02', 'cash', '1.015', 'CNY', 'food'))
            request['db'] = str(db)
            try:
                proc = subprocess.run([str(PYTHON), '-B', '-m', 'ledgerlab'], input=json.dumps(request, ensure_ascii=False), cwd=workspace, env=child_env, text=True, encoding='utf-8', errors='replace', capture_output=True, timeout=8)
                actual = json.loads(proc.stdout)
                assertions += 1
                if proc.returncode != 0 or actual != expected:
                    failures.append({'case': group['name'], 'step': step, 'request': request, 'expected': expected, 'actual': actual, 'exit': proc.returncode, 'stderr': proc.stderr[-700:]})
            except Exception as exc:
                failures.append({'case': group['name'], 'step': step, 'error': type(exc).__name__ + ': ' + str(exc)})
                break
    if stage >= 16:
        modules = []
        try:
            for source in (workspace / 'ledgerlab').glob('*.py'):
                if source.name in ('__init__.py', '__main__.py'):
                    continue
                tree = ast.parse(source.read_text(encoding='utf-8-sig'))
                if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in tree.body):
                    modules.append(source.name)
            assertions += 1
            if len(modules) < 3 or len((workspace / 'ledgerlab/__main__.py').read_text(encoding='utf-8-sig').splitlines()) > 160:
                failures.append({'case': 'module_boundaries', 'modules_with_behavior': modules, 'error': 'Need >=3 real modules and __main__ <=160 lines'})
        except Exception as exc:
            failures.append({'case': 'module_boundaries', 'error': str(exc)})
    fixture = workspace / 'fixed_fixture.txt'
    if not fixture.exists() or digest(fixture) != hashlib.sha256(FIXTURE.encode()).hexdigest():
        failures.append({'case': 'fixture_integrity', 'error': 'Protected fixture missing or changed'})
    result = {'stage': stage, 'passed': not failures, 'assertions': assertions, 'failures': failures[:12], 'failure_count': len(failures)}
    if output:
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def metrics(result):
    events = result.pop('events')
    tools = []
    failures = []
    usage = []
    texts = []
    for event in events:
        kind = event.get('type')
        if kind == 'tool_execution_start':
            tools.append({'name': event.get('toolName'), 'id': event.get('toolCallId'), 'input_sha256': hashlib.sha256(json.dumps(event.get('args'), ensure_ascii=False, sort_keys=True).encode()).hexdigest()})
        elif kind == 'tool_execution_end' and event.get('isError'):
            failures.append({'name': event.get('toolName'), 'id': event.get('toolCallId'), 'result': event.get('result')})
        elif kind == 'message_end':
            message = event.get('message', {})
            if message.get('role') == 'assistant':
                if message.get('usage'):
                    usage.append(message['usage'])
                for block in message.get('content') or []:
                    if block.get('type') == 'text':
                        texts.append(block.get('text', ''))
    seen = set()
    repeated = 0
    for tool in tools:
        signature = (tool['name'], tool['input_sha256'])
        repeated += signature in seen
        seen.add(signature)
    return {**result, 'tools': tools, 'tool_errors': failures, 'model_requests': len(usage), 'usage': usage, 'repeated_tool_inputs': repeated, 'last_text': '\n'.join(texts)[-1600:]}


def artifacts(model):
    base = ROOT / '.validation-runtime'
    base.mkdir(exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='omp-' + model.lower() + '-', dir=base))
    workspace = run_dir / 'workspace'
    workspace.mkdir()
    return run_dir, workspace


def pilot(args):
    run_dir, workspace = artifacts(args.model)
    emit({'pilot': args.model, 'artifact_dir': str(run_dir)})
    client = OmpClient(workspace, run_dir, args.model)
    try:
        state = client.assert_model()
        emit({'actual_model': state['model'], 'available_tools': state['tools']})
        result = metrics(client.prompt('Create probe.py with exactly def add(a, b): followed by an indented return a + b, using LF newlines. Read the actual file back to verify it. Work only inside the current directory. Use tools, do not simulate results.', timeout=180))
        (run_dir / 'pilot.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        target = workspace / 'probe.py'
        actual = target.read_bytes() if target.exists() else b''
        assert actual == b'def add(a, b):\n    return a + b\n', repr(actual)
        assert any(t['name'] == 'write' for t in result['tools']) and any(t['name'] == 'read' for t in result['tools'])
        emit({'pilot_passed': True, 'actual_model': state['model'], 'tools': [t['name'] for t in result['tools']], 'elapsed_s': result['elapsed_s']})
    finally:
        client.close()
    return 0


def run(args):
    run_dir, workspace = artifacts(args.model)
    for path, content in STARTER.items():
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    (workspace / 'fixed_fixture.txt').write_bytes(FIXTURE.encode())
    command_prefix = f'"{PYTHON.as_posix()}" -B "{Path(__file__).as_posix()}" verify --workspace "{workspace.as_posix()}" --stage '
    summary = {'model': args.model, 'client': 'omp', 'relay': 'http://127.0.0.1:31110', 'workspace': str(workspace), 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'stages': [], 'completed': False}
    started = time.monotonic()
    client = None
    emit({'started': args.model, 'client': 'omp', 'artifact_dir': str(run_dir)})
    try:
        client = OmpClient(workspace, run_dir, args.model, command_pattern=command_prefix + '*')
        summary['identity'] = client.assert_model()
        for index, (name, task) in enumerate(STAGES[:args.stages], 1):
            remaining = args.max_minutes * 60 - (time.monotonic() - started)
            if remaining <= 0:
                summary['stop_reason'] = 'wall_time_limit'
                break
            stage_dir = run_dir / f'stage-{index:02d}-{name}'
            stage_dir.mkdir()
            command = command_prefix + str(index)
            prompt = (BASE.replace('Read/Write/Edit/Glob/Grep', 'read/write/edit/glob/grep').replace('Bash', 'bash') + '\n' if index == 1 else '')
            prompt += f'阶段 {index}: {name}\n{task}\n只在当前工作目录修改，保持fixed_fixture.txt字节不变。先用glob/grep/read确认已有实现，再修改。唯一获准bash命令如下，必须真实运行并据结果修复，成功后结束：\n' + command
            record = {'stage': index, 'name': name, 'attempts': []}
            summary['stages'].append(record)
            for attempt in range(args.repairs + 1):
                attempt_dir = stage_dir / f'attempt-{attempt}'
                attempt_dir.mkdir()
                (attempt_dir / 'prompt.txt').write_text(prompt, encoding='utf-8')
                remaining = args.max_minutes * 60 - (time.monotonic() - started)
                if remaining <= 0:
                    summary['stop_reason'] = 'wall_time_limit'
                    break
                response = metrics(client.prompt(prompt, timeout=min(args.stage_timeout, remaining)))
                check = verify(workspace, index, attempt_dir / 'verification.json')
                record['attempts'].append({'client': response, 'verification': check})
                (run_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                emit({'model': args.model, 'stage': index, 'attempt': attempt, 'passed': check['passed'], 'assertions': check['assertions'], 'tools': len(response['tools']), 'tool_errors': len(response['tool_errors']), 'model_requests': response['model_requests'], 'elapsed_s': response['elapsed_s']})
                if check['passed'] and response['completion'].get('status') == 'completed':
                    record['passed'] = True
                    break
                # 不是请求级自动重试：记录失败后，提交一条明确的新用户反馈，
                # 让 agent 检查已执行状态再继续；不能假定先前工具未运行。
                prompt = (
                    '本阶段尚未验收通过。上一轮结束状态为 '
                    + json.dumps(response['completion'], ensure_ascii=False)
                    + '。已有工具可能实际执行，先读取磁盘确认状态，不要盲目重复写入。'
                    + '保持全部旧接口，修复以下独立验收失败，重新运行前述验收命令并正常结束：\n'
                    + json.dumps(check, ensure_ascii=False)
                )
            if not record.get('passed'):
                summary.setdefault('stop_reason', 'stage_failed_after_repairs')
                break
        else:
            summary['completed'] = True
            summary['stop_reason'] = 'all_planned_stages_completed'
    except Exception as exc:
        summary['stop_reason'] = 'runtime_failure'
        summary['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if client:
            client.close()
        summary['elapsed_s'] = round(time.monotonic() - started, 3)
        summary['files'] = [{'path': str(path.relative_to(workspace)), 'bytes': path.stat().st_size, 'sha256': digest(path)} for path in workspace.rglob('*') if path.is_file()]
        summary['stages_passed'] = sum(bool(stage.get('passed')) for stage in summary['stages'])
        summary['tool_calls'] = sum(len(a['client']['tools']) for s in summary['stages'] for a in s['attempts'])
        summary['tool_errors'] = sum(len(a['client']['tool_errors']) for s in summary['stages'] for a in s['attempts'])
        summary['model_requests'] = sum(a['client']['model_requests'] for s in summary['stages'] for a in s['attempts'])
        summary['repeated_tool_inputs'] = sum(a['client']['repeated_tool_inputs'] for s in summary['stages'] for a in s['attempts'])
        (run_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        emit({k: summary[k] for k in ('model', 'completed', 'elapsed_s', 'stages_passed', 'tool_calls', 'tool_errors', 'model_requests', 'repeated_tool_inputs', 'stop_reason')})
        if summary.get('error'):
            emit({'error': summary['error']})
        emit({'artifact_dir': str(run_dir)})
    return 0 if summary['completed'] else 1


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    for name in ('run', 'pilot'):
        execute = sub.add_parser(name)
        execute.add_argument('--model', choices=['deepseek-pro', 'Kimi-k3'], required=True)
        execute.add_argument('--stages', type=int, default=len(STAGES))
        execute.add_argument('--max-minutes', type=float, default=45)
        execute.add_argument('--stage-timeout', type=float, default=600)
        execute.add_argument('--repairs', type=int, default=2)
    check = sub.add_parser('verify')
    check.add_argument('--workspace', type=Path, required=True)
    check.add_argument('--stage', type=int, required=True)
    args = parser.parse_args()
    if args.mode == 'verify':
        result = verify(args.workspace.resolve(), args.stage)
        emit(result)
        return 0 if result['passed'] else 1
    return pilot(args) if args.mode == 'pilot' else run(args)


if __name__ == '__main__':
    raise SystemExit(main())
