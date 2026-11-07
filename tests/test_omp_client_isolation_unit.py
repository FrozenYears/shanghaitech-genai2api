"""隔离配置的真实目录、子进程环境与 RPC 边界回归；不连接远端模型。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


DIRECTORY = Path(__file__).resolve().parents[1] / 'validation/native-first-endurance'
spec = importlib.util.spec_from_file_location('omp_client_isolation', DIRECTORY / 'omp_client.py')
omp_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(omp_client)
REAL_POPEN = subprocess.Popen

# 仅替代外部 omp 可执行文件；保留真实管道、读线程、握手与身份检查。
RPC_SERVER = """
import json
import os
import sys

state = json.loads(sys.argv[1])
state['sessionId'] = os.environ.get('PI_CODING_AGENT_DIR')
print(json.dumps({'type': 'ready', 'supportedProtocolVersions': [2]}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({'type': 'response', 'id': request['id'],
                      'command': request['type'], 'success': True,
                      'data': state if request['type'] == 'get_state' else None}), flush=True)
"""


@pytest.fixture
def rpc_launch(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    artifact_dir = tmp_path / 'artifacts'
    workspace.mkdir()
    artifact_dir.mkdir()
    launches = []
    state = {}

    def popen(args, **kwargs):
        launches.append({'args': args, 'agent_dir': kwargs['env'].get('PI_CODING_AGENT_DIR')})
        return REAL_POPEN([sys.executable, '-u', '-c', RPC_SERVER, json.dumps(state)], **kwargs)

    monkeypatch.setattr(omp_client.subprocess, 'Popen', popen)

    def launch(*, reported_model=None, tools=None, **options):
        state.update({'model': reported_model or {
            'provider': 'GenAI', 'id': 'Kimi-k3', 'baseUrl': 'http://127.0.0.1:31110/v1'},
            'dumpTools': [{'name': name} for name in (tools if tools is not None else ['read', 'write', 'edit', 'glob', 'grep'])],
            'isSettled': True})
        return omp_client.OmpClient(workspace, artifact_dir, 'Kimi-k3', **options)

    return launch, launches


def model(base_url, **changes):
    return {'provider': 'GenAI', 'id': 'Kimi-k3', 'baseUrl': base_url, **changes}



@pytest.mark.parametrize('url', [
    '', '/v1', '127.0.0.1:32123', '//relay.example', 'ftp://relay.example',
    'http://', 'http://relay.example?key=value', 'http://relay.example#fragment',
    'http://relay.example?', 'http://relay.example#', ' http://relay.example',
    'http://relay.example:invalid', 'http://relay.example:65536', None,
])
def test_invalid_expected_url_is_rejected_before_process_launch(rpc_launch, url):
    launch, launches = rpc_launch
    with pytest.raises(ValueError):
        launch(relay_base_url=url)
    assert launches == []


@pytest.mark.parametrize('kind', ['missing', 'empty', 'file', 'models-directory'])
def test_agent_directory_requires_existing_model_configuration(rpc_launch, tmp_path, kind):
    launch, launches = rpc_launch
    config = tmp_path / 'invalid-agent'
    if kind == 'file':
        config.write_text('not a directory', encoding='utf-8')
    elif kind != 'missing':
        config.mkdir()
        if kind == 'models-directory':
            (config / 'models.yml').mkdir()
    with pytest.raises(ValueError, match='agent_dir'):
        launch(agent_dir=config)
    assert launches == []


@pytest.mark.parametrize('reported', [
    model('http://127.0.0.1:31110/v1'),
    model('http://127.0.0.1:32124/v1'),
    model('http://localhost:32123/v1'),
    model('https://127.0.0.1:32123/v1'),
    model('http://127.0.0.1:32123/other'),
    model('http://127.0.0.1:32123/v1?key=value'),
    model('http://127.0.0.1:32123/v1#fragment'),
    model(None),
    model('http://127.0.0.1:32123/v1', provider='Other'),
    model('http://127.0.0.1:32123/v1', id='deepseek-pro'),
])
def test_relay_or_model_identity_mismatch_still_fails(rpc_launch, reported):
    launch, launches = rpc_launch
    with pytest.raises(omp_client.FatalClientError) as exc:
        launch(relay_base_url='http://127.0.0.1:32123', reported_model=reported)
    assert exc.value.reason == 'identity_failure'
    assert len(launches) == 1


@pytest.mark.parametrize('extra_tool', ['bash', 'ssh', 'web_search'])
def test_default_tool_whitelist_is_not_relaxed(rpc_launch, extra_tool):
    launch, _ = rpc_launch
    with pytest.raises(omp_client.FatalClientError) as exc:
        launch(tools=['read', 'write', 'edit', 'glob', 'grep', extra_tool])
    assert exc.value.reason == 'permission_failure'


