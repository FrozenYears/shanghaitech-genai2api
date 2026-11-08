"""工具生命周期信息通过公开HTTP入口进入最终网页请求的回归。"""
from copy import deepcopy
import json

import pytest

from app import create_app
from config import Config
from provider import genai


class Token:
    def get_token(self): return 'synthetic-token'
    def force_refresh(self): return None


@pytest.mark.parametrize('protocol', ['chat', 'messages', 'responses'])
def test_job_identity_and_exit_state_reach_school_without_opaque_details(monkeypatch, protocol):
    sent = []
    class Response:
        status_code = 200
        def iter_lines(self, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"received"},"finish_reason":null}]}'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
        def close(self): pass
    def post(*args, **kwargs):
        sent.append(deepcopy(kwargs['json']))
        return Response()
    monkeypatch.setattr(genai.requests, 'post', post)
    monkeypatch.setattr(genai.model_registry, 'get_root_ai_type', lambda *a: 'xinference')
    details = {'async': {'jobId': 'job-identity-738d', 'state': 'completed', 'type': 'bash'},
               'exitCode': 7, 'timedOut': False, 'opaque': 'PRIVATE-DETAIL-NOT-PROMPT'}
    error = {'code': 'EIO', 'type': 'execution_error', 'message': 'PRIVATE-ERROR-NOT-PROMPT'}
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    if protocol == 'chat':
        path = '/v1/chat/completions'
        body = {'messages': [{'role': 'user', 'content': 'Get job result using its metadata.'},
                            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-start', 'type': 'function', 'function': {'name': 'start_job', 'arguments': '{}'}}]},
                            {'role': 'tool', 'tool_call_id': 'call-start', 'content': 'No identity in body.', 'is_error': True, 'details': details, 'error': error}],
                'tools': [{'type': 'function', 'function': {'name': 'start_job', 'parameters': schema}}]}
    elif protocol == 'messages':
        path = '/v1/messages'
        body = {'messages': [{'role': 'user', 'content': 'Get job result using its metadata.'},
                            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call-start', 'name': 'start_job', 'input': {}}]},
                            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call-start', 'content': 'No identity in body.', 'is_error': True, 'details': details, 'error': error}]}],
                'tools': [{'name': 'start_job', 'input_schema': schema}], 'max_tokens': 1000}
    else:
        path = '/v1/responses'
        body = {'input': [{'role': 'user', 'content': 'Get job result using its metadata.'},
                          {'type': 'function_call', 'call_id': 'call-start', 'name': 'start_job', 'arguments': '{}'},
                          {'type': 'function_call_output', 'call_id': 'call-start', 'output': 'No identity in body.', 'is_error': True, 'details': details, 'error': error}],
                'tools': [{'type': 'function', 'name': 'start_job', 'parameters': schema}]}
    response = create_app(Config(Token(), 0, None, False)).test_client().post(path, json={**body, 'model': 'synthetic', 'stream': False})
    assert response.status_code == 200
    wire = json.dumps(sent[0], ensure_ascii=False)
    assert 'job-identity-738d' in wire and 'completed' in wire and 'EIO' in wire
    assert 'exitCode' in wire and '7' in wire
    assert 'PRIVATE-DETAIL-NOT-PROMPT' not in wire
    assert 'PRIVATE-ERROR-NOT-PROMPT' not in wire
