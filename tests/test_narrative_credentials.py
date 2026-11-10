import json
from core import narrative_credentials as credentials
from core.narrative_scoring import _provider_config


class Vault:
    value=''
    def get_password(self,*a):return self.value
    def set_password(self,*a):self.value=a[-1]
    def delete_password(self,*a):self.value=''


def test_key_is_saved_only_in_vault_and_never_returned_by_settings(tmp_path,monkeypatch):
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    for name in ['NARRATIVE_LLM_API_KEY','DEEPSEEK_API_KEY','OPENAI_API_KEY']:monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('NARRATIVE_LLM_PROVIDER','deepseek')
    vault=Vault();monkeypatch.setattr(credentials,'_vault',lambda:vault)
    client=create_app().test_client()
    response=client.put('/api/v1/settings/narrative',json={'key':'test-secret-value'})
    assert response.status_code==200 and response.get_json()['active']
    assert 'test-secret-value' not in response.get_data(as_text=True)
    assert _provider_config()['key']=='test-secret-value'
    assert 'test-secret-value' not in client.get('/api/v1/app/config').get_data(as_text=True)
    assert client.put('/api/v1/settings/narrative',json={'key':''}).get_json()['active'] is False
    for p in tmp_path.rglob('*.json'):assert 'test-secret-value' not in p.read_text()


def test_unavailable_secure_store_never_silently_saves_plaintext(monkeypatch):
    import pytest
    def fail():raise RuntimeError('unavailable')
    monkeypatch.setattr(credentials,'_vault',fail)
    with pytest.raises(RuntimeError,match='no key was saved'):credentials.save_key('test')
