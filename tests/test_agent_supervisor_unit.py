"""监督闭环用真实文件与独立子进程验收；不调用远端模型。"""
import importlib.util
import json
from pathlib import Path
import queue
import sys

import pytest


DIRECTORY = Path(__file__).resolve().parents[1] / 'validation/native-first-endurance'


def load(name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


omp_client = load('omp_client')
supervisor = load('supervisor')
supervise_cli = load('supervise')


class ScriptedClient:
    def __init__(self, workspace, actions):
        self.workspace = workspace
        self.artifact_dir = workspace.parent / 'client'
        self.last_state = {'sessionId': 'same-real-history', 'isSettled': True}
        self.actions = iter(actions)
        self.prompts = []

    def prompt(self, text, timeout):
        self.prompts.append(text)
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        return action(self.workspace) if callable(action) else action


def turn(status='completed', events=None, **completion):
    return {'elapsed_s': .1, 'completion': {'status': status, 'sessionSettled': True, **completion}, 'events': events or []}


def write_implementation(content):
    def action(workspace):
        (workspace / 'feature.py').write_text(content, encoding='utf-8')
        return turn(events=[{'type': 'tool_execution_start', 'toolName': 'write', 'args': {'path': 'feature.py', 'content': content}}])
    return action


@pytest.fixture
def project(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    checker = tmp_path / 'trusted_checker.py'
    checker.write_text("from feature import add\nassert add(4, 9) == 13, 'add(4,9) must equal 13'\nassert add(-2, 2) == 0\nprint('independent checker passed')\n", encoding='utf-8')
    # -c 保持工作目录为模块查找起点；受信验收文件不在 agent 工作目录。
    argv = [sys.executable, '-B', '-c', f"exec(compile(open({str(checker)!r}, encoding='utf-8').read(), {str(checker)!r}, 'exec'))"]
    verify = lambda remaining: supervisor.run_checker(argv, workspace, timeout=min(remaining, 5))
    return workspace, verify


def test_real_checker_failure_then_incremental_repair_and_audit(project, tmp_path):
    workspace, verify = project
    client = ScriptedClient(workspace, [write_implementation('def add(a, b):\n    return a - b\n'),
                                        write_implementation('def add(a, b):\n    return a + b\n')])
    progress = []
    result = supervisor.supervise(client, 'Implement add', verify, artifact_dir=tmp_path / 'audit', on_progress=lambda s: progress.append(s['automatic_feedback_count']))
    assert result['passed'] and not result['passed_without_feedback']
    assert result['automatic_feedback_count'] == 1 and result['human_interventions'] == 0
    assert result['attempts'][0]['verification']['exit'] != 0
    assert 'AssertionError' in client.prompts[1]
    assert '上轮工具可能已经执行' in client.prompts[1] and '增量修复' in client.prompts[1]
    assert 'def add' not in client.prompts[1]
    assert result['stop_reason'] == 'verified_completed'
    checkpoint = json.loads((tmp_path / 'audit/checkpoint.json').read_text(encoding='utf-8'))
    assert checkpoint['session']['sessionId'] == 'same-real-history'
    assert checkpoint['automatic_cross_process_resume'] is False
    assert (tmp_path / 'audit/attempt-0/feedback.txt').is_file()
    assert (tmp_path / 'audit/attempt-1/turn.json').is_file()
    assert progress


def test_repeated_plan_stops_consume_budget_instead_of_waiting(project, tmp_path):
    workspace, verify = project
    client = ScriptedClient(workspace, [turn()] * 4)
    result = supervisor.supervise(client, 'Implement add', verify, max_feedback=3, artifact_dir=tmp_path / 'audit')
    assert not result['passed'] and len(client.prompts) == 4
    assert result['automatic_feedback_count'] == 3
    assert result['stagnation_count'] == 3
    assert '检测到停滞' in client.prompts[2]
    assert result['stop_reason'] == 'feedback_budget_exhausted'
    assert result['budget']['remaining_feedback'] == 0


def test_schema_error_continues_same_session_even_when_business_check_passes(project, tmp_path):
    workspace, verify = project
    (workspace / 'feature.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    schema = "Tool 'edit' arguments fail required at parameters; missing fields: path"
    client = ScriptedClient(workspace, [turn('error', error={'message': schema}), turn()])
    result = supervisor.supervise(client, 'Maintain add', verify, artifact_dir=tmp_path / 'audit')
    assert result['passed'] and result['automatic_feedback_count'] == 1
    assert result['attempts'][0]['verification']['passed']
    assert schema in client.prompts[1]


def test_same_turn_real_checker_failure_then_repair_can_pass(project, tmp_path):
    workspace, verify = project
    intermediate = []
    def repair_in_one_turn(workspace):
        (workspace / 'feature.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        failed = verify(5)
        intermediate.append(failed)
        (workspace / 'feature.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        return turn(events=[
            {'type': 'tool_execution_end', 'isError': True, 'toolName': 'bash',
             'result': {'content': [{'type': 'text', 'text': failed['stderr']}]}},
            {'type': 'tool_execution_start', 'toolName': 'write', 'args': {'path': 'feature.py'}},
            {'type': 'tool_execution_end', 'isError': False, 'toolName': 'write'},
        ])
    client = ScriptedClient(workspace, [repair_in_one_turn])
    result = supervisor.supervise(client, 'Implement add', verify, artifact_dir=tmp_path / 'audit')
    assert not intermediate[0]['passed'] and intermediate[0]['exit'] != 0
    assert result['passed'] and result['passed_without_feedback']
    assert result['automatic_feedback_count'] == 0 and len(client.prompts) == 1
    assert len(result['attempts'][0]['client']['tool_errors']) == 1


@pytest.mark.parametrize('reason', ['identity_failure', 'process_exit', 'session_not_settled', 'permission_failure'])
def test_fatal_client_failure_never_blind_retries(project, tmp_path, reason):
    workspace, _ = project
    client = ScriptedClient(workspace, [omp_client.FatalClientError('unsafe', reason)])
    checks = []
    result = supervisor.supervise(client, 'Work', lambda _: checks.append(True), artifact_dir=tmp_path / 'audit')
    assert result['stop_reason'] == reason and len(client.prompts) == 1 and not checks
    assert not result['passed']
    assert (tmp_path / 'audit/checkpoint.json').is_file()


def test_checker_exception_saves_precise_failure(project, tmp_path):
    workspace, _ = project
    client = ScriptedClient(workspace, [turn()])
    def broken(_):
        raise PermissionError('trusted checker unreadable')
    result = supervisor.supervise(client, 'Work', broken, artifact_dir=tmp_path / 'audit')
    assert result['stop_reason'] == 'checker_failure'
    assert 'PermissionError' in result['attempts'][0]['verification']['checker_error']
    assert len(client.prompts) == 1


def test_deadline_prevents_new_prompt_and_preserves_checkpoint(project, tmp_path):
    workspace, _ = project
    now = [10.0]
    client = ScriptedClient(workspace, [turn()])
    def verify(_):
        now[0] = 13
        return {'passed': False, 'failures': ['still wrong']}
    result = supervisor.supervise(client, 'Work', verify, deadline=12, clock=lambda: now[0], artifact_dir=tmp_path / 'audit')
    assert result['stop_reason'] == 'wall_time_limit'
    assert len(client.prompts) == 1 and result['automatic_feedback_count'] == 0
    assert (tmp_path / 'audit/checkpoint.json').is_file()


def test_zero_budget_and_initial_success(project, tmp_path):
    workspace, verify = project
    client = ScriptedClient(workspace, [write_implementation('def add(a, b):\n    return a + b\n')])
    result = supervisor.supervise(client, 'Work', verify, max_feedback=0, artifact_dir=tmp_path / 'audit')
    assert result['passed_without_feedback'] and result['automatic_feedback_count'] == 0


def test_failure_signature_ignores_generated_database_and_elapsed():
    a = {'passed': False, 'failures': [{'request': {'db': '/a/db-123/c.sqlite', 'op': 'list'}, 'actual': 1}], 'elapsed_s': 1}
    b = {'passed': False, 'failures': [{'request': {'db': '/b/db-456/c.sqlite', 'op': 'list'}, 'actual': 1}], 'elapsed_s': 8}
    assert supervisor.verification_signature(a) == supervisor.verification_signature(b)
    b['failures'][0]['actual'] = 2
    assert supervisor.verification_signature(a) != supervisor.verification_signature(b)


@pytest.mark.parametrize('value', [-1, 101, True, '1.2', 2.5])
def test_feedback_budget_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        supervisor.feedback_budget(value)


@pytest.mark.parametrize('value', [0, -1, 'nan', 'inf'])
def test_timeout_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        supervisor.positive(value)


def test_checker_is_real_exitcode_based_bounded_and_filters_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVATE_API_KEY', 'credential-value-never-return')
    code = "import os,sys; assert 'PRIVATE_API_KEY' not in os.environ; print('credential-value-never-return'); sys.stderr.write('x'*100000); sys.exit(7)"
    result = supervisor.run_checker([sys.executable, '-B', '-c', code], tmp_path, output_limit=256)
    assert result['exit'] == 7 and not result['passed']
    assert 'credential-value-never-return' not in result['stdout']
    assert '[REDACTED]' in result['stdout']
    assert len(result['stderr']) <= 256 and result['stderr_truncated']
    assert supervisor.run_checker([sys.executable, '-B', '-c', 'print("not business JSON")'], tmp_path)['passed']


def test_checker_timeout_kills_process(tmp_path):
    result = supervisor.run_checker([sys.executable, '-B', '-c', 'import time; time.sleep(60)'], tmp_path, timeout=.1)
    assert not result['passed'] and result['timed_out'] and result['exit'] != 0


def test_missing_checker_is_fatal_not_model_feedback(project, tmp_path):
    workspace, _ = project
    client = ScriptedClient(workspace, [turn()])
    result = supervisor.supervise(client, 'Work', lambda _: supervisor.run_checker([str(tmp_path / 'absent.exe')], workspace), artifact_dir=tmp_path / 'audit')
    assert result['stop_reason'] == 'checker_failure' and len(client.prompts) == 1


@pytest.mark.parametrize('argv', [[], 'python check.py', ['x', 1], ['x\0'], ['check.bat'], ['check.cmd']])
def test_checker_requires_explicit_safe_argv(tmp_path, argv):
    with pytest.raises(ValueError):
        supervisor.run_checker(argv, tmp_path)


def fake_rpc(events):
    client = omp_client.OmpClient.__new__(omp_client.OmpClient)
    client.unusable = False
    client.last_state = {'isSettled': True}
    client.sent = []
    client.assert_model = lambda timeout=60: {'isSettled': True}
    def send(kind, **payload):
        client.sent.append(kind)
        return 'turn-1' if kind == 'prompt' else 'abort-1'
    client.send = send
    sequence = iter(events)
    def next_event(timeout):
        event = next(sequence)
        if isinstance(event, Exception):
            raise event
        return event
    client.next_event = next_event
    return client


def test_prompt_timeout_aborts_and_waits_for_terminal_settled_before_reuse():
    client = fake_rpc([TimeoutError(), {'type': 'response', 'id': 'abort-1', 'success': True},
                       {'type': 'prompt_result', 'id': 'turn-1', 'status': 'aborted', 'sessionSettled': True}])
    result = client.prompt('work', timeout=2)
    assert result['completion']['status'] == 'timed_out'
    assert result['completion']['sessionSettled'] is True
    assert client.sent == ['prompt', 'abort'] and not client.unusable


def test_abort_ack_alone_is_not_settled_and_next_prompt_is_refused():
    client = fake_rpc([TimeoutError(), {'type': 'response', 'id': 'abort-1', 'success': True}, TimeoutError()])
    with pytest.raises(omp_client.FatalClientError) as exc:
        client.prompt('work', timeout=2)
    assert exc.value.reason == 'session_not_settled'
    with pytest.raises(omp_client.FatalClientError):
        client.prompt('unsafe retry')
    assert client.sent == ['prompt', 'abort']


@pytest.mark.parametrize('event,reason', [({'type': 'process_eof'}, 'process_exit'),
                                        ({'type': 'model_changed'}, 'identity_failure'),
                                        ({'type': 'retry_fallback_applied'}, 'identity_failure')])
def test_rpc_fatal_events(event, reason):
    client = omp_client.OmpClient.__new__(omp_client.OmpClient)
    client.queue = queue.Queue()
    client.queue.put(event)
    with pytest.raises(omp_client.FatalClientError) as exc:
        client.next_event(1)
    assert exc.value.reason == reason


def test_cli_drives_real_checker_and_persists_final_exit(project, tmp_path, monkeypatch):
    workspace, _ = project
    task_file = tmp_path / 'task.txt'
    task_file.write_text('Implement a real add function', encoding='utf-8')
    argv_file = tmp_path / 'checker.json'
    checker = tmp_path / 'trusted_checker.py'
    argv_file.write_text(json.dumps([sys.executable, '-B', '-c', f"exec(compile(open({str(checker)!r}, encoding='utf-8').read(), {str(checker)!r}, 'exec'))"]), encoding='utf-8')
    client = ScriptedClient(workspace, [turn(), write_implementation('def add(a, b):\n    return a + b\n')])
    client.assert_model = lambda: {'model': {'provider': 'GenAI', 'id': 'Kimi-k3'}}
    client.close = lambda: None
    requested_patterns = []
    def factory(workspace, artifact_dir, model, command_pattern=None):
        requested_patterns.append(command_pattern)
        client.artifact_dir = artifact_dir
        return client
    monkeypatch.setattr(supervise_cli, 'OmpClient', factory)
    artifact_dir = tmp_path / 'cli-audit'
    exitcode = supervise_cli.main(['--model', 'Kimi-k3', '--workspace', str(workspace),
                                  '--task-file', str(task_file), '--verify-argv-file', str(argv_file),
                                  '--artifact-dir', str(artifact_dir)])
    result = json.loads((artifact_dir / 'result.json').read_text(encoding='utf-8'))
    assert exitcode == 0 and result['passed']
    assert result['automatic_feedback_count'] == 1 and requested_patterns == [None]
    assert (artifact_dir / 'invocation.json').is_file()


def test_cli_failing_checker_returns_failure_without_human_input(project, tmp_path, monkeypatch):
    workspace, _ = project
    task_file = tmp_path / 'task.txt'
    task_file.write_text('Implement add', encoding='utf-8')
    client = ScriptedClient(workspace, [turn()])
    client.assert_model = lambda: {'model': {'provider': 'GenAI', 'id': 'deepseek-pro'}}
    client.close = lambda: None
    monkeypatch.setattr(supervise_cli, 'OmpClient', lambda *a, **kw: client)
    artifact_dir = tmp_path / 'failed-cli'
    exitcode = supervise_cli.main(['--model', 'deepseek-pro', '--workspace', str(workspace),
                                  '--task-file', str(task_file), '--artifact-dir', str(artifact_dir),
                                  '--max-feedback', '0', '--verify-command', sys.executable, '-B', '-c',
                                  "import pathlib; assert (pathlib.Path.cwd() / 'feature.py').exists(), 'feature missing'"])
    result = json.loads((artifact_dir / 'result.json').read_text(encoding='utf-8'))
    assert exitcode == 1 and result['stop_reason'] == 'feedback_budget_exhausted'
    assert result['attempts'][0]['verification']['exit'] != 0


def test_checker_receives_real_stdin_without_unbounded_pipe(tmp_path):
    result = supervisor.run_checker([sys.executable, '-B', '-c',
                                    "import json,sys; data=json.load(sys.stdin); assert data['amount'] == '1.25'"],
                                   tmp_path, input_text='{"amount":"1.25"}')
    assert result['passed']


@pytest.mark.parametrize('failure', [
    turn('error', error={'message': "Schema mismatch while implementing fallback/forbidden policy"}),
    turn(events=[{'type': 'tool_execution_end', 'isError': True, 'toolName': 'bash',
                  'result': {'content': [{'type': 'text', 'text': 'AssertionError: forbidden fallback policy test failed; permission denied is an expected business value'}]}}]),
    turn(events=[{'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'stop',
                                                   'content': [{'type': 'text', 'text': 'I will implement fallback and forbidden business cases.'}]}}]),
])
def test_business_error_words_are_not_permission_failures(project, tmp_path, failure):
    workspace, verify = project
    client = ScriptedClient(workspace, [failure, write_implementation('def add(a, b):\n    return a + b\n')])
    result = supervisor.supervise(client, 'Implement add', verify, artifact_dir=tmp_path / 'audit')
    assert result['passed'] and result['automatic_feedback_count'] == 1
    assert result['attempts'][0]['verification']['exit'] != 0


@pytest.mark.parametrize('failure', [
    turn('error', error={'code': 'permission_denied', 'message': 'denied by operator'}),
    turn(events=[{'type': 'tool_execution_end', 'isError': True, 'toolName': 'write',
                  'result': {'error': {'code': 'EACCES', 'message': 'cannot access requested path'}}}]),
])
def test_explicit_permission_error_metadata_is_fatal(project, tmp_path, failure):
    workspace, verify = project
    client = ScriptedClient(workspace, [failure])
    result = supervisor.supervise(client, 'Implement add', verify, artifact_dir=tmp_path / 'audit')
    assert not result['passed'] and len(client.prompts) == 1
    assert result['stop_reason'] == 'permission_or_identity_failure'


@pytest.mark.parametrize('recovered', [False, True])
def test_only_unrecovered_protocol_error_blocks_verified_completion(project, tmp_path, recovered):
    workspace, verify = project
    (workspace / 'feature.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    events = [{'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'error', 'errorMessage': 'Tool schema invalid'}}]
    if recovered:
        events.append({'type': 'message_end', 'message': {'role': 'assistant', 'stopReason': 'stop', 'content': []}})
    client = ScriptedClient(workspace, [turn(events=events), turn()])
    result = supervisor.supervise(client, 'Maintain add', verify, artifact_dir=tmp_path / 'audit')
    assert result['passed']
    assert result['automatic_feedback_count'] == (0 if recovered else 1)
    assert result['attempts'][0]['client']['protocol_errors'] == ['Tool schema invalid']
