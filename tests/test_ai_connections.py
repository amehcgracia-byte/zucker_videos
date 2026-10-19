import io,json
from types import SimpleNamespace
import pytest
from flask import Flask
from core import ai_connections,narrative_credentials,narrative_scoring
from server import ai_connections as routes


class Vault:
    def __init__(self):self.values={}
    def get_password(self,service,account):return self.values.get((service,account))
    def set_password(self,service,account,value):self.values[service,account]=value
    def delete_password(self,service,account):self.values.pop((service,account),None)


@pytest.fixture
def vault(tmp_path,monkeypatch):
    v=Vault();monkeypatch.setattr(narrative_credentials,'_vault',lambda:v);monkeypatch.setattr(routes,'_vault',lambda:v)
    monkeypatch.setattr(ai_connections,'preferences_path',lambda:tmp_path/'global-ai.json')
    for name in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','DEEPSEEK_API_KEY','HF_TOKEN','NARRATIVE_LLM_API_KEY','NARRATIVE_LLM_PROVIDER','NARRATIVE_LLM_MODEL','NARRATIVE_LLM_BASE_URL']:monkeypatch.delenv(name,raising=False)
    # No test reads the developer's existing Hugging Face login.
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub,'get_token',lambda:None)
    return v


def test_keys_are_separate_masked_and_not_in_global_json(vault,tmp_path):
    ai_connections.save_key('openai','openai-secret-1234');ai_connections.save_key('anthropic','claude-secret-5678')
    doc=ai_connections.status();assert 'openai-secret' not in json.dumps(doc)
    assert next(p for p in doc['providers'] if p['id']=='openai')['masked']=='••••1234'
    ai_connections.select('openai','gpt-5-mini')
    assert narrative_scoring._provider_config()['key']=='openai-secret-1234'
    assert narrative_scoring._provider_config('anthropic')['key']=='claude-secret-5678'
    assert 'secret' not in (tmp_path/'global-ai.json').read_text()
    ai_connections.save_key('openai','');assert not ai_connections.api_key('openai')
    with pytest.raises(ValueError):ai_connections.select('openai')


def test_generic_deepseek_key_is_never_sent_to_another_provider(vault,monkeypatch):
    monkeypatch.setenv('NARRATIVE_LLM_API_KEY','legacy-deepseek-secret')
    assert narrative_scoring._provider_config('deepseek')['key']=='legacy-deepseek-secret'
    assert narrative_scoring._provider_config('openai')['key']==''
    assert narrative_scoring._provider_config('anthropic')['key']==''


def test_hf_router_uses_global_token_and_does_not_claim_face_swap(vault,monkeypatch):
    ai_connections.save_key('huggingface','hf_test-secret-1234');calls=[]
    class Response(io.BytesIO):headers={'Content-Type':'application/json'}
    def opened(req,timeout):
        calls.append(req);assert timeout==90
        return Response(b'{"labels":["test"]}')
    monkeypatch.setattr(ai_connections,'urlopen',opened)
    assert ai_connections._hf_inference('owner/model',{'inputs':'test'})=={'labels':['test']}
    assert calls[0].full_url=='https://router.huggingface.co/hf-inference/models/owner/model'
    assert calls[0].get_header('Authorization')=='Bearer hf_test-secret-1234'
    with pytest.raises(ValueError):ai_connections._hf_inference('https://untrusted/host',{})


def test_claude_compatibility_omits_unsupported_sampling_and_validates_json_in_callers(vault):
    calls=[];client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:calls.append(kw))))
    narrative_scoring._completion(client,{'provider':'anthropic'},model='claude-sonnet-4-6',temperature=.1,response_format={'type':'json_object'},max_tokens=100,messages=[])
    assert 'temperature' not in calls[0] and 'response_format' not in calls[0]


def test_external_control_needs_local_bearer_and_current_project(vault):
    app=Flask(__name__);called=[]
    state=SimpleNamespace(project=SimpleNamespace(folder='/test/project'),wizard=SimpleNamespace(status=lambda:{'status':'idle'}))
    routes.register_ai_connection_routes(app,state,lambda:(called.append('show') or {'started':True},202),lambda:({'started':True},202))
    client=app.test_client()
    assert client.get('/api/remote/status').status_code==401
    token=client.post('/api/v1/connectors/remote-token',json={}).get_json()['token']
    headers={'Authorization':'Bearer '+token}
    assert client.get('/api/remote/status',headers=headers).get_json()['project_id']=='/test/project'
    assert client.get('/api/remote/status',headers={**headers,'Origin':'https://foreign.example'}).status_code==401
    assert client.get('/api/remote/status',headers=headers,environ_overrides={'REMOTE_ADDR':'192.168.1.3'}).status_code==401
    assert client.get('/api/remote/status',headers=headers,base_url='http://foreign.example').status_code==401
    assert client.post('/api/remote/render',headers=headers,json={'project_id':'wrong','mode':'show'}).status_code==409
    assert called==[]
    assert client.post('/api/remote/render',headers=headers,json={'project_id':'/test/project','mode':'show'}).status_code==202
    assert called==['show']
    assert token not in client.get('/api/remote/status',headers=headers).get_data(as_text=True)
    assert client.put('/api/v1/connectors/api/openai',headers={'Origin':'https://foreign.example'},json={'key':'test'}).status_code==401


def test_model_selected_in_ui_overrides_old_environment_provider(vault,monkeypatch):
    monkeypatch.setenv('NARRATIVE_LLM_PROVIDER','deepseek')
    ai_connections.save_key('openai','test-openai-key')
    ai_connections.select('openai','gpt-5-mini')
    assert narrative_scoring._provider_config()['provider']=='openai'


def test_openai_reasoning_models_use_their_completion_token_parameter(vault):
    calls=[];client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:calls.append(kw))))
    narrative_scoring._completion(client,{'provider':'openai'},model='gpt-5-mini',temperature=.1,max_tokens=3000,messages=[])
    assert calls[0]['max_completion_tokens']==3000
    assert 'max_tokens' not in calls[0] and 'temperature' not in calls[0]
