"""正式 GenAI 双跳的真实 omp 持续开发验收；不会调用官方 API。"""
import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from omp_client import OmpClient
from supervisor import feedback_budget, metrics, positive, run_checker, supervise
from workload import BASE, STAGES, STARTER, cases

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / '.pixi/envs/default/python.exe'
FIXTURE = ''.join(f'fixture-{i:04d}: 雪 \\path "quote" <think>literal</think> </tool_call> @@ACTION@@ {hashlib.sha256(str(i).encode()).hexdigest()}\n' for i in range(512))


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def agent_verifier_receipt(result, command, stage):
    """最后一次要求的验收须成功，且开始于最后一次源码修改完成之后。"""
    starts = {}
    last_mutation = last_check_start = -1
    last_check_passed, successes = False, 0
    for index, event in enumerate((result or {}).get('events', [])):
        kind = event.get('type')
        if kind == 'tool_execution_start':
            starts[event.get('toolCallId')] = (index, event.get('args') or {})
        elif kind == 'tool_execution_end':
            start, arguments = starts.get(event.get('toolCallId'), (-1, {}))
            name = event.get('toolName')
            path = arguments.get('path')
            if (name in ('write', 'edit') and not event.get('isError')
                    and isinstance(path, str) and path.lower().endswith('.py')):
                last_mutation = index
            if name != 'bash' or (arguments.get('command') or '').strip() != command.strip():
                continue
            last_check_start, last_check_passed = start, False
            output = event.get('result')
            if event.get('isError') or not isinstance(output, dict):
                continue
            text = '\n'.join(part.get('text', '') for part in output.get('content', []) if part.get('type') == 'text')
            try:
                receipt, _ = json.JSONDecoder().raw_decode(text.lstrip())
            except ValueError:
                continue
            last_check_passed = isinstance(receipt, dict) and receipt.get('stage') == stage and receipt.get('passed') is True
            successes += int(last_check_passed)
    return {'passed': last_check_passed and last_check_start > last_mutation, 'successful_receipts': successes}


def verify(workspace, stage, output=None, deadline=None):
    """逐请求运行实际业务代码；期望只由验收进程持有。"""
    failures = []
    assertions = 0
    sandbox = Path(tempfile.mkdtemp(prefix='db-', dir=workspace.parent))
    for group in cases(stage):
        db = sandbox / (group['name'].replace('/', '_') + '.sqlite')
        for step, (request, expected) in enumerate(group['steps']):
            if deadline is not None and time.monotonic() >= deadline:
                failures.append({'case': group['name'], 'error': 'verification wall_time_limit'})
                break
            request = deepcopy(request)
            if request.pop('_legacy', False):
                with sqlite3.connect(db) as connection:
                    connection.execute('CREATE TABLE transactions (id TEXT PRIMARY KEY,date TEXT,account TEXT,amount TEXT,currency TEXT,category TEXT)')
                    connection.execute('INSERT INTO transactions VALUES (?,?,?,?,?,?)', ('legacy', '2024-01-02', 'cash', '1.015', 'CNY', 'food'))
            request['db'] = str(db)
            try:
                proc = run_checker([str(PYTHON), '-B', '-m', 'ledgerlab'], workspace,
                                   input_text=json.dumps(request, ensure_ascii=False), output_limit=1048576,
                                   timeout=min(8, max(.01, deadline - time.monotonic())) if deadline is not None else 8)
                if proc.get('fatal'):
                    failures.append({'case': group['name'], 'step': step, 'error': proc['checker_error']})
                    break
                try:
                    actual = json.loads(proc['stdout'])
                except json.JSONDecodeError:
                    failures.append({'case': group['name'], 'step': step, 'error': 'Invalid checker JSON output',
                                     'exit': proc['exit'], 'timed_out': proc['timed_out'],
                                     'stdout': proc['stdout'][-700:], 'stderr': proc['stderr'][-700:]})
                    break
                assertions += 1
                if not proc['passed'] or actual != expected:
                    failures.append({'case': group['name'], 'step': step, 'request': request, 'expected': expected, 'actual': actual, 'exit': proc['exit'], 'stderr': proc['stderr'][-700:]})
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
            def progress(state):
                record.update(state)
                (run_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                if state['attempts']:
                    latest = state['attempts'][-1]
                    response, check = latest['client'], latest['verification']
                    emit({'model': args.model, 'stage': index, 'attempt': len(state['attempts']) - 1,
                          'passed': state['passed'], 'assertions': check.get('assertions', 0),
                          'tools': len(response['tools']), 'tool_errors': len(response['tool_errors']),
                          'model_requests': response['model_requests'], 'elapsed_s': response.get('elapsed_s'),
                          'automatic_feedback_count': state['automatic_feedback_count'],
                          'stagnation_count': state['stagnation_count'], 'stop_reason': state['stop_reason']})
            def check_stage(seconds):
                check = verify(workspace, index, deadline=time.monotonic() + seconds)
                receipt = agent_verifier_receipt(client.last_result, command, index)
                check.update(business_passed=check['passed'], business_assertions=check['assertions'],
                             agent_verifier=receipt, assertions=check['assertions'] + 1)
                if not receipt['passed']:
                    check['passed'] = False
                    check['failure_count'] += 1
                    check['failures'].append({'case': 'required_agent_verifier_not_executed',
                                              'error': 'No successful tool receipt for the required verifier after the final source edit',
                                              'command': command})
                return check
            state = supervise(client, prompt,
                              check_stage,
                              artifact_dir=stage_dir, max_feedback=args.max_feedback,
                              deadline=started + args.max_minutes * 60, turn_timeout=args.stage_timeout,
                              on_progress=progress)
            record.update(state)
            if not record.get('passed'):
                summary['stop_reason'] = record['stop_reason']
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
        summary['automatic_feedback_count'] = sum(s.get('automatic_feedback_count', 0) for s in summary['stages'])
        summary['human_interventions'] = 0
        summary['stagnation_count'] = sum(s.get('stagnation_count', 0) for s in summary['stages'])
        summary['stages_passed_without_feedback'] = sum(bool(s.get('passed_without_feedback')) for s in summary['stages'])
        summary['passed_without_feedback'] = summary['completed'] and summary['automatic_feedback_count'] == 0
        summary['budget'] = {'max_feedback_per_stage': args.max_feedback, 'max_minutes': args.max_minutes,
                             'turn_timeout_s': args.stage_timeout}
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
        execute.add_argument('--max-minutes', type=positive, default=45)
        execute.add_argument('--stage-timeout', type=positive, default=1200)
        execute.add_argument('--max-feedback', type=feedback_budget, default=8)
    check = sub.add_parser('verify')
    check.add_argument('--workspace', type=Path, required=True)
    check.add_argument('--stage', type=int, required=True)
    args = parser.parse_args()
    if args.mode != 'verify' and not 1 <= args.stages <= len(STAGES):
        parser.error('--stages 超出可用阶段范围')
    if args.mode == 'verify' and not 1 <= args.stage <= len(STAGES):
        parser.error('--stage 超出可用阶段范围')
    if args.mode == 'verify':
        result = verify(args.workspace.resolve(), args.stage)
        emit(result)
        return 0 if result['passed'] else 1
    return pilot(args) if args.mode == 'pilot' else run(args)


if __name__ == '__main__':
    raise SystemExit(main())
