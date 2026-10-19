"""Optional API connections; keys stay in the OS vault, preferences stay global."""
from __future__ import annotations
import json
import os
import re
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from core import narrative_credentials
from core.project import atomic_write_json

SERVICE='Zucker Editor AI APIs'
LOCK=threading.RLock()
PROVIDERS={
    'deepseek':{'name':'DeepSeek','model':'deepseek-v4-pro','base_url':'https://api.deepseek.com','env':'DEEPSEEK_API_KEY'},
    'openai':{'name':'OpenAI','model':'gpt-5-mini','base_url':'https://api.openai.com/v1','env':'OPENAI_API_KEY'},
    'anthropic':{'name':'Claude (Anthropic)','model':'claude-sonnet-4-6','base_url':'https://api.anthropic.com/v1','env':'ANTHROPIC_API_KEY'},
    'huggingface':{'name':'Hugging Face','env':'HF_TOKEN'},
    'jamendo':{'name':'Jamendo client ID','env':'JAMENDO_CLIENT_ID'},
    'freesound':{'name':'Freesound API key','env':'FREESOUND_API_KEY'},
}

def preferences_path():
    from server.inbox import app_home
    return app_home()/'ai-connections.json'

def preferences():
    try:return json.loads(preferences_path().read_text())
    except (OSError,ValueError):return {}

def stored_key(provider):
    if provider not in PROVIDERS:raise ValueError('Unknown AI provider')
    try:return narrative_credentials._vault().get_password(SERVICE,provider) or ''
    except Exception:return ''

def api_key(provider):
    generic=os.environ.get('NARRATIVE_LLM_API_KEY') if provider==(os.environ.get('NARRATIVE_LLM_PROVIDER') or 'deepseek') else ''
    value=os.environ.get(PROVIDERS[provider]['env']) or generic or stored_key(provider)
    if value:return value
    if provider=='deepseek':return narrative_credentials.saved_key()
    if provider=='huggingface':
        try:
            from huggingface_hub import get_token
            return get_token() or ''
        except Exception:return ''
    return ''

def save_key(provider,value):
    if provider not in PROVIDERS:raise ValueError('Unknown AI provider')
    value=str(value).strip()
    if len(value)>1024 or any(c in value for c in '\r\n'):raise ValueError('Enter a valid API key')
    if provider=='huggingface' and value and not value.startswith('hf_'):raise ValueError('A Hugging Face token starts with hf_')
    try:
        vault=narrative_credentials._vault()
        if value:vault.set_password(SERVICE,provider,value)
        elif vault.get_password(SERVICE,provider):vault.delete_password(SERVICE,provider)
    except Exception:raise RuntimeError('The secure system credential store is unavailable; no key was saved') from None

def select(provider,model=None):
    if provider not in ('deepseek','openai','anthropic'):raise ValueError('Choose a narrative model provider')
    if not api_key(provider):raise ValueError('Add your API key in Connectors first')
    model=str(model or PROVIDERS[provider]['model']).strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}',model):raise ValueError('Enter a valid model ID')
    with LOCK:
        doc=preferences();doc.update(provider=provider,model=model)
        atomic_write_json(preferences_path(),doc)
    return doc

def status():
    config=preferences()
    return {'provider':config.get('provider','deepseek'),'model':config.get('model','deepseek-v4-pro'),
            'providers':[{'id':name,'name':item['name'],'active':bool(key:=api_key(name)),
                          'masked':('••••'+key[-4:]) if key else '',
                          'environment':bool(os.environ.get(item['env'])),'default_model':item.get('model')}
                         for name,item in PROVIDERS.items()]}

def _hf_inference(model_id,payload):
    """A configured, supported HF task. No guessed face-swap service or uploads."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',str(model_id)):
        raise ValueError('Choose a Hugging Face owner/model ID')
    token=api_key('huggingface')
    if not token:raise ValueError('Add your Hugging Face token in Connectors')
    request=Request('https://router.huggingface.co/hf-inference/models/'+model_id,
                    data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(request,timeout=90) as response:
            body=response.read(32*1024*1024+1)
            if len(body)>32*1024*1024:raise ValueError('The inference result is too large')
            return json.loads(body) if 'json' in response.headers.get('Content-Type','') else body
    except HTTPError as exc:
        raise RuntimeError(f'Hugging Face inference failed (HTTP {exc.code}). Check model availability, token permissions and provider allowance.') from None
