"""显式任务的本地文件工具；路径约束不是 OS 沙箱，也不能防止恶意并发替换。"""

import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import signal
import stat
import subprocess
import sys
import threading
import time


MAX_FILE_BYTES = 1024 * 1024
MAX_LIST_ENTRIES = 1000
MAX_PROCESS_SECONDS = 120
MAX_PROCESS_OUTPUT = 16384
_SECRET_NAME = re.compile(r'TOKEN|API.?KEY|PASSWORD|PASSWD|SECRET|CREDENTIAL|AUTH|COOKIE', re.I)


def _environment():
    secrets = sorted({value for key, value in os.environ.items() if _SECRET_NAME.search(key) and value}, key=len, reverse=True)
    env = {key: value for key, value in os.environ.items() if not _SECRET_NAME.search(key)}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    return env, secrets


def _redact(text, secrets):
    for secret in secrets:
        text = text.replace(secret, '[REDACTED]')
    return re.sub(r'(?i)(bearer\s+)[^\s"\']+', r'\1[REDACTED]', text)


def _terminate(proc):
    # 最多增加有限的清理宽限；进程树终止同样不等于操作系统隔离。
    if os.name == 'nt':
        taskkill = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/taskkill.exe')
        try:
            subprocess.run([taskkill, '/PID', str(proc.pid), '/T', '/F'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=1)


def _run(argv, workspace, timeout):
    started = time.monotonic()
    result = {'passed': False, 'exit': None, 'stdout': '', 'stderr': '',
              'stdout_truncated': False, 'stderr_truncated': False,
              'timed_out': timeout <= 0, 'incomplete_output': False}
    if timeout <= 0:
        result['elapsed_s'] = 0.0
        return result
    env, secrets = _environment()
    try:
        proc = subprocess.Popen(argv, cwd=workspace, env=env, shell=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=os.name != 'nt')
    except OSError as exc:
        result.update(error=_redact(type(exc).__name__ + ': ' + str(exc), secrets),
                      elapsed_s=round(time.monotonic() - started, 3))
        return result
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    totals = {'stdout': 0, 'stderr': 0}
    failed = []
    # 凭据前瞻避免刚好在输出边界截断时泄露半个已知 secret。
    capture_limit = MAX_PROCESS_OUTPUT + max((len(value.encode('utf-8')) for value in secrets), default=0) + 4

    def drain(name, pipe):
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                totals[name] += len(chunk)
                captured[name].extend(chunk[:max(0, capture_limit - len(captured[name]))])
        except (OSError, ValueError):
            failed.append(name)
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(name, getattr(proc, name)), daemon=True) for name in captured]
    for thread in threads:
        thread.start()
    try:
        proc.wait(timeout=max(0, timeout - (time.monotonic() - started)))
    except subprocess.TimeoutExpired:
        result['timed_out'] = True
        _terminate(proc)
    for thread in threads:
        thread.join(timeout=.1)
    if any(thread.is_alive() for thread in threads):
        result['incomplete_output'] = True
        _terminate(proc)
        for thread in threads:
            thread.join(timeout=.1)
    result.update(exit=proc.returncode,
                  incomplete_output=result['incomplete_output'] or bool(failed),
                  elapsed_s=round(time.monotonic() - started, 3))
    result['passed'] = result['exit'] == 0 and not result['timed_out'] and not result['incomplete_output']
    for name in captured:
        result[name] = _redact(bytes(captured[name]).decode('utf-8', errors='replace'), secrets)[:MAX_PROCESS_OUTPUT]
        result[name + '_truncated'] = totals[name] > MAX_PROCESS_OUTPUT
    return result


class WorkspaceTools:
    """只暴露操作者选定的工作区；Python 执行默认关闭。"""

    def __init__(self, workspace, *, allow_python=False):
        if type(allow_python) is not bool:
            raise ValueError('allow_python 必须是 bool')
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError('workspace 必须是已有目录')
        self.allow_python = allow_python
        self.deadline = None
        definitions = [
            ('read_file', '读取相对工作区的 UTF-8 文件，最多 1 MiB。', ('path',)),
            ('write_file', '精确写入 UTF-8 文本，可创建父目录，拒绝链接路径。', ('path', 'content')),
            ('list_files', '列出相对目录的直接子项，最多 1000 项和 1 MiB。', ('path',)),
        ]
        if allow_python:
            definitions.append(('run_python', '执行工作区内 .py 脚本，最长 120 秒；拥有当前用户权限，不是 OS 沙箱。', ('path',)))
        self.tools = [{
            'type': 'function',
            'function': {
                'name': name,
                'description': description,
                'parameters': {
                    'type': 'object',
                    'properties': {key: {'type': 'string'} for key in keys},
                    'required': list(keys),
                    'additionalProperties': False,
                },
            },
        } for name, description, keys in definitions]
        self._parameters = {name: frozenset(keys) for name, _, keys in definitions}

    def _path(self, value):
        if not value or '\0' in value:
            raise ValueError('path 必须是非空相对路径')
        windows = PureWindowsPath(value)
        relative = Path(value.replace('\\', '/'))
        if relative.is_absolute() or windows.drive or windows.root or '..' in relative.parts:
            raise ValueError('拒绝绝对路径或 .. 路径')
        if any(PureWindowsPath(part).is_reserved() or ':' in part or part.endswith((' ', '.')) for part in relative.parts):
            raise ValueError('拒绝 Windows 设备、数据流或模糊路径')
        candidate = self.workspace / relative
        current = self.workspace
        # 统一拒绝链接与 Windows reparse point，包括指向工作区内的链接。
        for part in relative.parts:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('拒绝符号链接或目录联接路径')
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.workspace):
            raise ValueError('路径超出工作区')
        return candidate

    def execute(self, name, parameters):
        """直接调用也严格检查参数；错误作为真实工具结果返回，不假装成功。"""
        try:
            expected = self._parameters.get(name) if isinstance(name, str) else None
            if expected is None:
                raise ValueError('工具未开放或名称未知')
            if not isinstance(parameters, dict) or set(parameters) != expected or any(not isinstance(v, str) for v in parameters.values()):
                raise ValueError('工具参数必须严格符合声明的 Schema')
            path = self._path(parameters['path'])
            if name == 'read_file':
                if not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError('只能读取普通文件')
                with path.open('rb') as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError('文件超过 1 MiB 读取上限')
                output = data.decode('utf-8')
            elif name == 'write_file':
                content = parameters['content'].encode('utf-8')
                if path.exists() and not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError('只能写入普通文件')
                path.parent.mkdir(parents=True, exist_ok=True)
                self._path(parameters['path'])
                path.write_bytes(content)
                output = '已写入 ' + str(path.relative_to(self.workspace))
            elif name == 'list_files':
                entries = []
                with os.scandir(path) as children:
                    for child in children:
                        if len(entries) >= MAX_LIST_ENTRIES:
                            raise ValueError('目录超过 1000 项列举上限')
                        checked = self._path(str(Path(child.path).relative_to(self.workspace)))
                        entries.append({'path': checked.relative_to(self.workspace).as_posix(),
                                        'type': 'directory' if child.is_dir(follow_symlinks=False) else 'file'})
                entries.sort(key=lambda item: item['path'])
                output = json.dumps(entries, ensure_ascii=False)
                if len(output.encode('utf-8')) > MAX_FILE_BYTES:
                    raise ValueError('目录列举超过 1 MiB 输出上限')
            else:
                return self._run_python(path)
            return {'output': output, 'is_error': False}
        except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            _, secrets = _environment()
            return {'output': _redact(type(exc).__name__ + ': ' + str(exc), secrets), 'is_error': True}

    def _run_python(self, path):
        if path.suffix.lower() != '.py' or not path.is_file():
            raise ValueError('run_python 只执行工作区已有的 .py 普通文件')
        timeout = MAX_PROCESS_SECONDS
        if self.deadline is not None:
            if isinstance(self.deadline, bool) or not isinstance(self.deadline, (int, float)) or not math.isfinite(self.deadline):
                raise ValueError('deadline 必须是有限 monotonic 时间')
            timeout = min(timeout, self.deadline - time.monotonic())
        result = _run([sys.executable, '-B', str(path)], self.workspace, timeout)
        return {'output': json.dumps(result, ensure_ascii=False),
                'is_error': not result['passed'], 'details': result}

    def verify(self, argv, remaining):
        """独立验收：argv 仅由操作者闭包提供，不属于模型工具参数。

        可执行文件、直接传入的 .py checker 要用绝对可信路径；checker 应放在
        工作区外且不可由任务修改。允许操作者自己的 -c 等固定参数，不猜测其语义。
        只根据退出码和完整执行状态通过；输出只做有限截取与已知凭据脱敏。
        """
        if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(arg, str) or '\0' in arg for arg in argv) or not argv[0]:
            raise ValueError('verify 必须接收操作者固定的非空字符串 argv 数组')
        command = list(argv)
        if not Path(command[0]).is_absolute() or Path(command[0]).suffix.lower() in ('.bat', '.cmd'):
            raise ValueError('验收命令必须使用绝对可执行路径，不能使用 bat/cmd')
        for argument in command[1:]:
            if argument.lower().endswith('.py') and not argument.startswith('-'):
                checker = Path(argument)
                if not checker.is_absolute() or checker.resolve().is_relative_to(self.workspace):
                    raise ValueError('验收脚本须为工作区外的绝对可信路径')
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not math.isfinite(remaining):
            raise ValueError('remaining 必须是有限秒数')
        return _run(command, self.workspace, min(MAX_PROCESS_SECONDS, remaining))
