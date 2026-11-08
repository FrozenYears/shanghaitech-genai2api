"""网页用HTTP200/SSE报告过期认证时的恢复与不重放边界。"""
import json

import pytest

from app import create_app
from config import Config
from provider import genai
from tests.test_endurance_error_unit import error_from, request_body
from tests.test_tool_repair_unit import calls_from, PARAMETERS


class Tokens:
    def __init__(self, refreshed='new-token'):
        self.refreshes = 0
        self.refreshed = refreshed

    def get_token(self):
        return 'old-token'

    def force_refresh(self):
        self.refreshes += 1
        return self.refreshed


def upstream(monkeypatch, first, second=None, *, first_status=200):
    sent, closed = [], []
    class Response:
        def __init__(self, status, chunks):
            self.status_code = status
            self.chunks = chunks
            self.text = ''
        def iter_lines(self, **kwargs):
            for chunk in self.chunks:
                yield ('data: ' + json.dumps(chunk)).encode()
        def close(self):
            closed.append(self)
    def post(*args, **kwargs):
        sent.append(kwargs)
        return Response(first_status if len(sent)==1 else 200, first if len(sent)==1 else second or first)
    monkeypatch.setattr(genai.model_registry,'get_root_ai_type',lambda *a:'xinference')
    monkeypatch.setattr(genai.requests,'post',post)
    return sent,closed


EXPIRED = {'success': False, 'message': 'Token失效，请重新登录'}
GOOD = [{'choices': [{'delta': {'content': '@@ACTION@@' + json.dumps({'calls': [{'operation':'write','parameters':PARAMETERS}]}) + '@@END_ACTION@@'}, 'finish_reason':None}]},
        {'choices':[{'delta':{},'finish_reason':'stop'}]}]


@pytest.mark.parametrize('protocol',['chat','messages','responses'])
@pytest.mark.parametrize('stream',[False,True])
def test_business_auth_expiry_refreshes_before_output_without_changing_batch(monkeypatch,protocol,stream):
    tokens=Tokens()
    sent,closed=upstream(monkeypatch,[EXPIRED],GOOD)
    path,body=request_body(protocol,stream)
    response=create_app(Config(tokens,0,None,False)).test_client().post(path,json=body)
    assert calls_from(response,protocol,stream)==[PARAMETERS]
    assert tokens.refreshes==1 and len(sent)==2
    assert sent[0]['json']==sent[1]['json']
    assert sent[0]['headers']['X-Access-Token']=='old-token'
    assert sent[1]['headers']['X-Access-Token']=='new-token'
    assert len(closed)>=2


@pytest.mark.parametrize('prefix',[
    {'content':'Visible progress'}, {'reasoning_content':'Visible reasoning'},
    {'tool_calls':[{'index':0,'id':'call-pending','type':'function','function':{'name':'write','arguments':'{'}}]},
])
def test_auth_expiry_after_model_output_never_refreshes_or_replays(monkeypatch,prefix):
    tokens=Tokens()
    sent,_=upstream(monkeypatch,[{'choices':[{'delta':prefix,'finish_reason':None}]},EXPIRED],GOOD)
    path,body=request_body('chat',True)
    response=create_app(Config(tokens,0,None,False)).test_client().post(path,json=body)
    failure=error_from(response,True)
    assert failure['code']=='upstream_authentication_failed'
    assert tokens.refreshes==0 and len(sent)==1


@pytest.mark.parametrize('status',[200,401])
def test_repeated_auth_expiry_refreshes_once_then_reports_real_failure(monkeypatch,status):
    tokens=Tokens()
    sent,_=upstream(monkeypatch,[EXPIRED],first_status=status)
    path,body=request_body('chat',False)
    failure=error_from(create_app(Config(tokens,0,None,False)).test_client().post(path,json=body),False)
    assert failure['code']=='upstream_authentication_failed'
    assert tokens.refreshes==1 and len(sent)==2


def test_static_token_cannot_recover_business_auth_expiry(monkeypatch):
    tokens=Tokens(refreshed=None)
    sent,_=upstream(monkeypatch,[EXPIRED],GOOD)
    path,body=request_body('chat',False)
    failure=error_from(create_app(Config(tokens,0,None,False)).test_client().post(path,json=body),False)
    assert failure['code']=='upstream_authentication_failed'
    assert len(sent)==1


@pytest.mark.parametrize('message', ['服务暂时不可用，请稍后重试', 'No available workers (all circuits open or unhealthy)'])
def test_availability_errors_are_not_misclassified_as_authentication(monkeypatch, message):
    tokens = Tokens()
    sent, _ = upstream(monkeypatch, [{'success': False, 'message': message}], GOOD)
    path, body = request_body('chat', False)
    failure = error_from(create_app(Config(tokens, 0, None, False)).test_client().post(path, json=body), False)
    assert failure['code'] == 'upstream_error'
    assert tokens.refreshes == 0 and len(sent) == 1


@pytest.mark.parametrize('status', [200, 401])
def test_authentication_refresh_does_not_extend_generation_deadline(monkeypatch, status):
    import time
    tokens = Tokens()
    def refresh():
        tokens.refreshes += 1
        time.sleep(.1)
        return 'new-token'
    tokens.force_refresh = refresh
    sent, _ = upstream(monkeypatch, [EXPIRED], GOOD, first_status=status)
    path, body = request_body('chat', True)
    config = Config(tokens, 0, None, False, tool_request_timeout_seconds=.05)
    failure = error_from(create_app(config).test_client().post(path, json=body), True)
    assert failure['code'] == 'upstream_request_timeout'
    time.sleep(.12)
    assert len(sent) == 1
