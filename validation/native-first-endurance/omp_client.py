"""驱动已安装的真实 omp；只使用显式 GenAI 模型，不替代 agent 工具循环。"""
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

OMP = Path.home() / 'AppData/Local/omp/omp.exe'
OVERLAY = Path(__file__).with_name('omp-isolation.yml')


class FatalClientError(RuntimeError):
    def __init__(self, message, reason='controller_failure', result=None):
        super().__init__(message)
        self.reason = reason
        self.result = result


class OmpClient:
    def __init__(self, workspace, artifact_dir, model, command_pattern=None):
        self.model = model
        self.workspace = workspace
        self.artifact_dir = artifact_dir
        self.last_state = None
        self.last_result = None
        self.unusable = False
        self.command_pattern = command_pattern
        self.queue = queue.Queue()
        self.responses = {}
        self.chunks = {}
        self.closed = False
        self.records = []
        self.started = time.monotonic()
        args = [str(OMP), '--mode', 'rpc', '--no-ui', '--model', 'GenAI/' + model,
                '--smol', 'GenAI/' + model, '--slow', 'GenAI/' + model, '--plan', 'GenAI/' + model,
                '--thinking', 'off', '--no-title', '--no-prewalk', '--no-extensions', '--no-skills', '--no-rules',
                '--no-lsp', '--no-pty', '--tools', 'read,write,edit,glob,grep' + (',bash' if command_pattern else ''),
                '--session-dir', str(artifact_dir / 'sessions'), '--cwd', str(workspace), '--config', str(OVERLAY)]
        if command_pattern:
            policy = artifact_dir / 'tool-policy.yml'
            policy.write_text(json.dumps({'bash': {'allowCompoundCommands': False, 'patterns': [
                {'match': command_pattern, 'approval': 'allow'}, {'match': '*', 'approval': 'deny'}
            ]}}, ensure_ascii=False), encoding='utf-8')
            args += ['--config', str(policy)]
        env = dict(os.environ)
        for name in ('PI_SMOL_MODEL', 'PI_SLOW_MODEL', 'PI_PLAN_MODEL', 'OMP_PROFILE', 'PI_PROFILE'):
            env.pop(name, None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        self.proc = subprocess.Popen(args, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', bufsize=1)
        self.log = (artifact_dir / 'omp-events.jsonl').open('w', encoding='utf-8')
        self.err = (artifact_dir / 'omp-stderr.log').open('w', encoding='utf-8')
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.error_reader = threading.Thread(target=self._read_error, daemon=True)
        self.error_reader.start()
        try:
            ready = self.next_event(60)
            while ready.get('type') != 'ready':
                ready = self.next_event(60)
            if 2 in ready.get('supportedProtocolVersions', []):
                self.command('negotiate_protocol', protocolVersion=2)
            self.command('set_event_filter', events=['message_start', 'message_update', 'message_end', 'tool_execution_start',
                'tool_execution_end', 'turn_start', 'turn_end', 'agent_start', 'agent_end', 'model_changed',
                'auto_retry_start', 'auto_retry_end', 'retry_fallback_applied'], messageUpdates='delta')
            self.command('set_auto_retry', enabled=False)
            self.command('set_auto_compaction', enabled=False)
            self.command('set_cache_warming', mode='off')
            self.assert_model()
        except BaseException:
            self.close()
            raise

    def _read_error(self):
        for line in self.proc.stderr:
            self.err.write(line)
            self.err.flush()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    event = json.loads(line)
                    if event.get('type') == 'rpc_chunk':
                        key = event['chunkId']
                        buf = self.chunks.setdefault(key, [])
                        if event['index'] != len(buf):
                            raise ValueError('Out-of-order RPC chunk')
                        buf.append(base64.b64decode(event['data']))
                        if len(buf) != event['count']:
                            continue
                        raw = b''.join(self.chunks.pop(key))
                        if len(raw) != event['byteLength']:
                            raise ValueError('RPC chunk size mismatch')
                        event = json.loads(raw.decode('utf-8'))
                    # get_state只记录必要身份信息，避免写入模型headers、系统提示等配置。
                    if event.get('command') == 'get_state' and event.get('success'):
                        event = {**event, 'data': self.safe_state(event['data'])}
                    self.log.write(json.dumps({'at_s': round(time.monotonic() - self.started, 3), 'event': event}, ensure_ascii=False) + '\n')
                    self.log.flush()
                    self.queue.put(event)
                except Exception as exc:
                    self.queue.put({'type': 'controller_error', 'error': str(exc)})
        finally:
            self.queue.put({'type': 'process_eof'})

    @staticmethod
    def safe_state(state):
        model = state.get('model') or {}
        return {**{k: state.get(k) for k in ('sessionId', 'sessionFile', 'messageCount', 'contextUsage', 'isSettled')},
                'model': {k: model.get(k) for k in ('provider', 'id', 'api', 'baseUrl', 'contextWindow', 'maxTokens')},
                'tools': [t.get('name') for t in state.get('dumpTools', [])]}

    def send(self, type_, **payload):
        if self.closed or self.proc.poll() is not None:
            raise FatalClientError('omp process exited', 'process_exit')
        id_ = uuid.uuid4().hex
        try:
            self.proc.stdin.write(json.dumps({'id': id_, 'type': type_, **payload}, ensure_ascii=False) + '\n')
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise FatalClientError(str(exc), 'process_exit') from exc
        return id_

    def next_event(self, timeout):
        try:
            event = self.queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError('Timed out waiting for omp RPC')
        if event.get('type') in ('process_eof', 'controller_error'):
            raise FatalClientError('omp ended or RPC failed: ' + str(event),
                                   'process_exit' if event['type'] == 'process_eof' else 'rpc_failure')
        if event.get('type') in ('model_changed', 'retry_fallback_applied'):
            self.unusable = True
            raise FatalClientError('Unexpected model switch/fallback', 'identity_failure')
        return event

    def command(self, type_, timeout=60, **payload):
        id_ = self.send(type_, **payload)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Timed out waiting for ' + type_)
            event = self.next_event(remaining)
            if event.get('type') == 'response' and event.get('id') == id_:
                if not event.get('success'):
                    raise FatalClientError(type_ + ': ' + str(event.get('error')), 'rpc_command_rejected')
                return event.get('data')
            self.records.append(event)

    def assert_model(self, timeout=60):
        state = self.command('get_state', timeout=timeout)
        self.last_state = state
        model = state['model']
        if model.get('provider') != 'GenAI' or model.get('id') != self.model:
            raise FatalClientError('Unexpected active model: ' + str(model), 'identity_failure')
        if (model.get('baseUrl') or '').rstrip('/') not in ('http://127.0.0.1:31110', 'http://127.0.0.1:31110/v1'):
            raise FatalClientError('Configured GenAI model is not the formal relay: ' + str(model), 'identity_failure')
        extra_tools = set(state['tools']) - ({'read', 'write', 'edit', 'glob', 'grep', 'bash'} if self.command_pattern else {'read', 'write', 'edit', 'glob', 'grep'})
        if extra_tools:
            raise FatalClientError('Unexpected tools discovered: ' + str(sorted(extra_tools)), 'permission_failure')
        return state

    def prompt(self, text, timeout=1200):
        if self.unusable:
            raise FatalClientError('Previous turn did not safely settle', 'session_not_settled')
        self.last_result = None
        started = time.monotonic()
        deadline = started + timeout
        state = self.assert_model(timeout=min(60, timeout))
        if state.get('isSettled') is not True:
            self.unusable = True
            raise FatalClientError('Refusing concurrent prompt on unsettled session', 'session_not_settled')
        id_ = self.send('prompt', message=text)
        events = []
        first_stream = None
        first_visible = None

        def result(completion, after):
            self.last_result = {'elapsed_s': round(time.monotonic() - started, 3),
                                'first_model_event_s': round(first_stream, 3) if first_stream is not None else None,
                                'first_text_s': round(first_visible, 3) if first_visible is not None else None,
                                'completion': completion, 'before': state, 'after': after, 'events': events}
            return self.last_result

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Prompt deadline reached')
                event = self.next_event(remaining)
                events.append(event)
                if event.get('type') == 'message_update':
                    now = time.monotonic() - started
                    if first_stream is None:
                        first_stream = now
                    delta = event.get('assistantMessageEvent') or {}
                    if delta.get('type') == 'text_delta' and first_visible is None:
                        first_visible = now
                if event.get('type') == 'prompt_result' and event.get('id') == id_:
                    if not event.get('sessionSettled'):
                        continue
                    after = self.assert_model(timeout=max(.01, min(60, deadline - time.monotonic())))
                    if after.get('isSettled') is not True:
                        raise FatalClientError('Terminal result but session is not settled', 'session_not_settled')
                    return result(event, after)
                if event.get('type') == 'response' and event.get('id') == id_ and not event.get('success'):
                    raise FatalClientError('Prompt rejected: ' + str(event.get('error')), 'prompt_rejected')
        except TimeoutError:
            # abort 的 response 不是停稳证明；必须收到本轮终结并核对真实状态。
            settle_deadline = time.monotonic() + 30
            terminal = None
            try:
                self.send('abort')
                while time.monotonic() < settle_deadline:
                    event = self.next_event(max(.01, settle_deadline - time.monotonic()))
                    events.append(event)
                    if event.get('type') == 'prompt_result' and event.get('id') == id_:
                        terminal = event
                    if terminal and (terminal.get('sessionSettled') is True or event.get('type') == 'session_settled'):
                        after = self.assert_model(timeout=max(.01, settle_deadline - time.monotonic()))
                        if after.get('isSettled') is True:
                            return result({'status': 'timed_out', 'sessionSettled': True, 'aborted_result': terminal}, after)
            except TimeoutError:
                pass
            except FatalClientError as exc:
                self.unusable = True
                exc.result = result({'status': 'fatal', 'sessionSettled': False, 'error': str(exc)}, self.last_state)
                raise
            self.unusable = True
            failure = result({'status': 'timed_out', 'sessionSettled': False}, self.last_state)
            raise FatalClientError('Abort did not prove previous prompt settled; no further prompt allowed',
                                   'session_not_settled', failure)
        except FatalClientError as exc:
            self.unusable = True
            try:
                self.send('abort')
            except Exception:
                pass
            exc.result = result({'status': 'fatal', 'error': str(exc), 'sessionSettled': False}, self.last_state)
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            subprocess.run(['taskkill.exe', '/PID', str(self.proc.pid), '/T', '/F'], capture_output=True, timeout=15)
        self.reader.join(timeout=3)
        self.error_reader.join(timeout=3)
        self.log.close()
        self.err.close()
        self.proc.stdout.close()
        self.proc.stderr.close()
