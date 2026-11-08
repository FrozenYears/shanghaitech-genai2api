"""工作区 adapter 通过真实文件和子进程公开接口验收，不运行远端模型。"""
import json
import os
import time
import sys

import pytest

from tools.task_workspace import WorkspaceTools


def test_real_utf8_write_read_and_listing_preserve_exact_text(tmp_path):
    adapter = WorkspaceTools(tmp_path)
    text = '中文\nsecond\r\nlast\n'
    result = adapter.execute('write_file', {'path': 'nested/result.txt', 'content': text})
    assert result['is_error'] is False
    assert (tmp_path / 'nested/result.txt').read_bytes() == text.encode('utf-8')
    assert adapter.execute('read_file', {'path': 'nested/result.txt'}) == {
        'output': text, 'is_error': False,
    }
    listing = adapter.execute('list_files', {'path': 'nested'})
    assert listing['is_error'] is False
    assert json.loads(listing['output']) == [{'path': 'nested/result.txt', 'type': 'file'}]


@pytest.mark.parametrize('path', ['../outside.txt', 'nested/../file.txt', '..\\outside.txt', '/absolute.txt', 'C:\\outside.txt', 'C:relative.txt', '\\\\server\\share\\file', 'NUL', 'file.txt:stream', 'nested/.. /escape'])
def test_rejects_escape_paths_without_creating_files(tmp_path, path):
    adapter = WorkspaceTools(tmp_path)
    for name, parameters in [
        ('read_file', {'path': path}),
        ('write_file', {'path': path, 'content': 'unexpected'}),
        ('list_files', {'path': path}),
    ]:
        assert adapter.execute(name, parameters)['is_error'] is True
    assert list(tmp_path.iterdir()) == []


def test_refuses_real_symlink_reads_writes_and_directory_listing(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'target.txt'
    target.write_text('unchanged', encoding='utf-8')
    link = workspace / 'linked'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('当前用户不能创建符号链接')
    adapter = WorkspaceTools(workspace)
    assert adapter.execute('read_file', {'path': 'linked/target.txt'})['is_error']
    assert adapter.execute('write_file', {'path': 'linked/target.txt', 'content': 'bad'})['is_error']
    assert adapter.execute('list_files', {'path': 'linked'})['is_error']
    assert target.read_text(encoding='utf-8') == 'unchanged'


def test_file_read_and_listing_limits_return_explicit_errors(tmp_path):
    adapter = WorkspaceTools(tmp_path)
    (tmp_path / 'large.txt').write_bytes(b'x' * (1024 * 1024 + 1))
    result = adapter.execute('read_file', {'path': 'large.txt'})
    assert result['is_error'] and '1 MiB' in result['output']
    (tmp_path / 'invalid.txt').write_bytes(b'\xff')
    assert adapter.execute('read_file', {'path': 'invalid.txt'})['is_error']
    directory = tmp_path / 'many'
    directory.mkdir()
    for number in range(1001):
        (directory / str(number)).touch()
    result = adapter.execute('list_files', {'path': 'many'})
    assert result['is_error'] and '1000' in result['output']


@pytest.mark.parametrize('name,parameters', [
    ('unknown', {'path': '.'}),
    ('read_file', {'path': '.', 'extra': 'ignored'}),
    ('write_file', {'path': 'missing.txt'}),
    ('read_file', {'path': 12}),
    ('read_file', '{"path":"file.txt"}'),
])
def test_direct_executor_rejects_invalid_arguments(tmp_path, name, parameters):
    assert WorkspaceTools(tmp_path).execute(name, parameters)['is_error']


def test_python_execution_is_explicit_and_has_fixed_arguments(tmp_path):
    script = tmp_path / '中文.py'
    script.write_text("import sys\nfrom pathlib import Path\nassert sys.argv == [str(Path.cwd() / '中文.py')]\nassert sys.dont_write_bytecode\nprint('真实 UTF-8 输出')\nsys.exit(7)\n", encoding='utf-8')
    disabled = WorkspaceTools(tmp_path)
    assert disabled.execute('run_python', {'path': '中文.py'})['is_error']
    assert not any(tool['function']['name'] == 'run_python' for tool in disabled.tools)
    enabled = WorkspaceTools(tmp_path, allow_python=True)
    assert enabled.execute('run_python', {'path': '中文.py', 'argv': ['-c', 'bad']})['is_error']
    result = enabled.execute('run_python', {'path': '中文.py'})
    assert result['is_error'] is True
    assert result['details']['exit'] == 7
    assert result['details']['stdout'].splitlines() == ['真实 UTF-8 输出']
    assert not result['details']['passed']
    assert enabled.execute('run_python', {'path': '-c'})['is_error']
    script.write_text("from pathlib import Path\nPath('executed.txt').write_text('完成', encoding='utf-8')\n", encoding='utf-8')
    succeeded = enabled.execute('run_python', {'path': '中文.py'})
    assert succeeded['is_error'] is False and succeeded['details']['exit'] == 0
    assert (tmp_path / 'executed.txt').read_text(encoding='utf-8') == '完成'


def test_python_deadline_does_not_execute_expired_work(tmp_path):
    script = tmp_path / 'work.py'
    script.write_text("from pathlib import Path\nPath('ran').touch()\n", encoding='utf-8')
    adapter = WorkspaceTools(tmp_path, allow_python=True)
    adapter.deadline = time.monotonic() - 1
    result = adapter.execute('run_python', {'path': 'work.py'})
    assert result['is_error'] and result['details']['timed_out']
    assert not (tmp_path / 'ran').exists()


def test_independent_external_checker_uses_exit_not_stdout(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    checker = tmp_path / 'trusted_checker.py'
    checker.write_text("from pathlib import Path\nassert Path('answer.txt').read_text(encoding='utf-8') == '正确'\nprint('checked actual file')\n", encoding='utf-8')
    adapter = WorkspaceTools(workspace)
    argv = [sys.executable, '-B', str(checker)]
    initial = adapter.verify(argv, 5)
    assert initial['passed'] is False and initial['exit'] != 0
    assert adapter.execute('write_file', {'path': 'answer.txt', 'content': '正确'})['is_error'] is False
    assert adapter.verify(argv, 5)['passed'] is True
    checker.write_text("print('PASS')\nraise SystemExit(9)\n", encoding='utf-8')
    rejected = adapter.verify(argv, 5)
    assert rejected['passed'] is False and rejected['exit'] == 9
    assert adapter.execute('verify', {'argv': argv})['is_error']


def test_process_environment_filters_credentials_and_bounds_output(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVATE_API_KEY', 'credential-value-never-return')
    monkeypatch.setenv('SERVICE_AUTH', 'auth-value-never-return')
    checker = tmp_path / 'trusted_checker.py'
    checker.write_text("import os,sys\nassert 'PRIVATE_API_KEY' not in os.environ\nassert 'SERVICE_AUTH' not in os.environ\nassert os.environ['PYTHONUTF8'] == '1'\nassert os.environ['PYTHONIOENCODING'] == 'utf-8'\nprint('credential-value-never-return')\nprint('Bearer bearer-value-never-return')\nsys.stderr.write('x' * 100000)\n", encoding='utf-8')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    result = WorkspaceTools(workspace).verify([sys.executable, '-B', str(checker)], 5)
    assert result['passed']
    assert 'credential-value-never-return' not in result['stdout']
    assert 'bearer-value-never-return' not in result['stdout']
    assert '[REDACTED]' in result['stdout']
    assert len(result['stderr']) <= 16384 and result['stderr_truncated']


def test_real_process_timeout_and_expired_verification_are_failures(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    checker = tmp_path / 'slow_checker.py'
    checker.write_text("import time\ntime.sleep(60)\n", encoding='utf-8')
    adapter = WorkspaceTools(workspace)
    result = adapter.verify([sys.executable, '-B', str(checker)], .1)
    assert result['passed'] is False and result['timed_out'] is True
    assert result['exit'] != 0
    assert adapter.verify([sys.executable, '-B', str(checker)], 0)['timed_out'] is True


@pytest.mark.parametrize('argv', [[], 'python check.py', ['python', 'check.py'], ['check.bat'], [sys.executable, 'relative.py'], [sys.executable, 1]])
def test_verify_requires_operator_fixed_absolute_command(tmp_path, argv):
    with pytest.raises(ValueError):
        WorkspaceTools(tmp_path).verify(argv, 5)


def test_missing_trusted_executable_is_not_success(tmp_path):
    result = WorkspaceTools(tmp_path).verify([str(tmp_path / 'absent-executable')], 5)
    assert result['passed'] is False and result['exit'] is None
    assert result['error']


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='当前系统没有 FIFO')
def test_special_files_are_refused_without_opening_blocking_fifo(tmp_path):
    os.mkfifo(tmp_path / 'pipe')
    adapter = WorkspaceTools(tmp_path)
    assert adapter.execute('read_file', {'path': 'pipe'})['is_error']
    assert adapter.execute('write_file', {'path': 'pipe', 'content': 'blocked'})['is_error']


def test_python_execution_respects_future_deadline(tmp_path):
    (tmp_path / 'slow.py').write_text('import time\ntime.sleep(60)\n', encoding='utf-8')
    adapter = WorkspaceTools(tmp_path, allow_python=True)
    adapter.deadline = time.monotonic() + .1
    result = adapter.execute('run_python', {'path': 'slow.py'})
    assert result['is_error'] is True and result['details']['timed_out'] is True
    assert result['details']['exit'] != 0


def test_credentials_crossing_output_limit_are_not_partially_exposed(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVATE_TOKEN', 'long-secret-crosses-output-boundary')
    checker = tmp_path / 'trusted_checker.py'
    checker.write_text("print('x' * 16380 + 'long-secret-crosses-output-boundary')\n", encoding='utf-8')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    result = WorkspaceTools(workspace).verify([sys.executable, '-B', str(checker)], 5)
    assert result['passed']
    assert result['stdout_truncated']
    assert 'long' not in result['stdout']
    assert len(result['stdout']) <= 16384


def test_model_writable_checker_is_not_independent_verification(tmp_path):
    checker = tmp_path / 'fake_checker.py'
    checker.write_text("print('PASS')\n", encoding='utf-8')
    with pytest.raises(ValueError):
        WorkspaceTools(tmp_path).verify([sys.executable, '-B', str(checker)], 5)
