"""Narrative credentials belong to the OS vault, never project/config JSON."""
from __future__ import annotations
import os
import sys

SERVICE = 'Zucker Editor Narrative'
ACCOUNT = 'narrative-api-key'


def _vault():
    if sys.platform == 'darwin':
        from keyring.backends.macOS import Keyring
    elif sys.platform == 'win32':
        from keyring.backends.Windows import WinVaultKeyring as Keyring
    else:
        from keyring.backends.SecretService import Keyring
    return Keyring()


def saved_key() -> str:
    try:
        return _vault().get_password(SERVICE, ACCOUNT) or ''
    except Exception:
        return ''                      # Environment configuration remains usable.


def save_key(value: str) -> None:
    value = str(value).strip()
    if len(value) > 1024:
        raise ValueError('The API key is too long')
    try:
        vault = _vault()
        if value:
            vault.set_password(SERVICE, ACCOUNT, value)
        elif vault.get_password(SERVICE, ACCOUNT):
            vault.delete_password(SERVICE, ACCOUNT)
    except Exception:
        raise RuntimeError('The secure system credential store is unavailable; no key was saved') from None


def status() -> dict:
    from core.narrative_scoring import _provider_config
    config = _provider_config()
    return {'active': bool(config['key']), 'provider': config['provider'],
            'detail': 'Narrative AI: active' if config['key'] else 'Narrative AI: limited — add a key for English subtitles, storyboard and scoring'}
