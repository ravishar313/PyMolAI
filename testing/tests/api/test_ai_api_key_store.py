import os
import sys
from types import SimpleNamespace

import pytest

from pymol.ai import api_key_store


class _FakeKeyring:
    def __init__(self):
        self._data = {}

    def get_password(self, service, account):
        return self._data.get((service, account))

    def set_password(self, service, account, value):
        self._data[(service, account)] = value

    def delete_password(self, service, account):
        key = (service, account)
        if key not in self._data:
            raise RuntimeError("not found")
        del self._data[key]


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("PYMOL_AI_OPENROUTER_KEY_SOURCE", raising=False)


def test_env_first_precedence(monkeypatch, clean_env):
    keyring = _FakeKeyring()
    keyring.set_password(api_key_store.SERVICE_NAME, api_key_store.ACCOUNT_NAME, "saved_key_5678")
    monkeypatch.setattr(api_key_store, "_load_keyring", lambda: (keyring, True))
    monkeypatch.setenv("OPENROUTER_API_KEY", "env_key_1234")

    status = api_key_store.load_saved_key_into_env_if_needed()
    assert status.source == "env"
    assert os.getenv("OPENROUTER_API_KEY") == "env_key_1234"
    assert os.getenv("PYMOL_AI_OPENROUTER_KEY_SOURCE", "") == ""


def test_loads_saved_key_when_env_missing(monkeypatch, clean_env):
    keyring = _FakeKeyring()
    keyring.set_password(api_key_store.SERVICE_NAME, api_key_store.ACCOUNT_NAME, "saved_key_1234")
    monkeypatch.setattr(api_key_store, "_load_keyring", lambda: (keyring, True))

    status = api_key_store.load_saved_key_into_env_if_needed()
    assert status.source == "saved"
    assert os.getenv("OPENROUTER_API_KEY") == "saved_key_1234"
    assert os.getenv("PYMOL_AI_OPENROUTER_KEY_SOURCE") == "saved_keyring"


def test_save_and_clear_roundtrip(monkeypatch, clean_env):
    keyring = _FakeKeyring()
    monkeypatch.setattr(api_key_store, "_load_keyring", lambda: (keyring, True))

    api_key_store.save_key("saved_key_9999")
    assert keyring.get_password(api_key_store.SERVICE_NAME, api_key_store.ACCOUNT_NAME) == "saved_key_9999"

    monkeypatch.setenv("OPENROUTER_API_KEY", "saved_key_9999")
    monkeypatch.setenv("PYMOL_AI_OPENROUTER_KEY_SOURCE", "saved_keyring")
    env_cleared = api_key_store.clear_saved_key_and_loaded_env_if_needed()
    assert env_cleared is True
    assert keyring.get_password(api_key_store.SERVICE_NAME, api_key_store.ACCOUNT_NAME) is None
    assert os.getenv("OPENROUTER_API_KEY", "") == ""


def test_status_masking_never_leaks_full_key(monkeypatch, clean_env):
    keyring = _FakeKeyring()
    keyring.set_password(api_key_store.SERVICE_NAME, api_key_store.ACCOUNT_NAME, "sk-or-v1-secret-ABCD")
    monkeypatch.setattr(api_key_store, "_load_keyring", lambda: (keyring, True))

    status = api_key_store.get_status()
    assert status.has_key is True
    assert status.source == "saved"
    assert status.masked_key.endswith("ABCD")
    assert "secret-ABCD" not in status.masked_key
    assert status.masked_key != "sk-or-v1-secret-ABCD"


def test_keyring_backend_error_surface(monkeypatch, clean_env):
    monkeypatch.setattr(api_key_store, "_load_keyring", lambda: (None, False))

    with pytest.raises(api_key_store.ApiKeyStoreError):
        api_key_store.save_key("some_key")


def test_validate_key_live_uses_sync_client_and_closes(monkeypatch, clean_env):
    closed = {"value": False}
    captured = {"api_key": "", "base_url": "", "model": ""}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured["model"] = str(kwargs.get("model") or "")
            return {"ok": True}

    class _FakeChat:
        def __init__(self):
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, api_key, base_url, timeout):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = _FakeChat()

        def close(self):
            closed["value"] = True

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_FakeOpenAI))
    api_key_store.validate_key_live("sk-or-test-1234", "anthropic/claude-sonnet-4.6", timeout_sec=1.0)
    assert captured["api_key"] == "sk-or-test-1234"
    assert captured["base_url"]
    assert captured["model"] == "anthropic/claude-sonnet-4.6"
    assert closed["value"] is True
