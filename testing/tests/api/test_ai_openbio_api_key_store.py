import os

import pytest

from pymol.ai import openbio_api_key_store as key_store


class _FakeKeyring:
    def __init__(self):
        self._data = {}

    def get_password(self, service, account):
        return self._data.get((service, account))

    def set_password(self, service, account, value):
        self._data[(service, account)] = value

    def delete_password(self, service, account):
        ident = (service, account)
        if ident not in self._data:
            raise RuntimeError("not found")
        del self._data[ident]


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("OPENBIO_API_KEY", raising=False)
    monkeypatch.delenv("PYMOL_AI_OPENBIO_KEY_SOURCE", raising=False)


def test_env_first_precedence(monkeypatch, clean_env):
    fake = _FakeKeyring()
    fake.set_password(key_store.SERVICE_NAME, key_store.ACCOUNT_NAME, "saved_openbio_5678")
    monkeypatch.setattr(key_store, "_load_keyring", lambda: (fake, True))
    monkeypatch.setenv("OPENBIO_API_KEY", "env_openbio_1234")

    status = key_store.load_saved_key_into_env_if_needed()
    assert status.source == "env"
    assert os.getenv("OPENBIO_API_KEY") == "env_openbio_1234"
    assert os.getenv("PYMOL_AI_OPENBIO_KEY_SOURCE", "") == ""


def test_loads_saved_key_when_env_missing(monkeypatch, clean_env):
    fake = _FakeKeyring()
    fake.set_password(key_store.SERVICE_NAME, key_store.ACCOUNT_NAME, "saved_openbio_1234")
    monkeypatch.setattr(key_store, "_load_keyring", lambda: (fake, True))

    status = key_store.load_saved_key_into_env_if_needed()
    assert status.source == "saved"
    assert os.getenv("OPENBIO_API_KEY") == "saved_openbio_1234"
    assert os.getenv("PYMOL_AI_OPENBIO_KEY_SOURCE") == "saved_keyring"


def test_save_and_clear_roundtrip(monkeypatch, clean_env):
    fake = _FakeKeyring()
    monkeypatch.setattr(key_store, "_load_keyring", lambda: (fake, True))

    key_store.save_key("saved_openbio_9999")
    assert fake.get_password(key_store.SERVICE_NAME, key_store.ACCOUNT_NAME) == "saved_openbio_9999"

    monkeypatch.setenv("OPENBIO_API_KEY", "saved_openbio_9999")
    monkeypatch.setenv("PYMOL_AI_OPENBIO_KEY_SOURCE", "saved_keyring")
    env_cleared = key_store.clear_saved_key_and_loaded_env_if_needed()
    assert env_cleared is True
    assert fake.get_password(key_store.SERVICE_NAME, key_store.ACCOUNT_NAME) is None
    assert os.getenv("OPENBIO_API_KEY", "") == ""


def test_status_masking_never_leaks_full_key(monkeypatch, clean_env):
    fake = _FakeKeyring()
    fake.set_password(key_store.SERVICE_NAME, key_store.ACCOUNT_NAME, "ob-secret-ABCD")
    monkeypatch.setattr(key_store, "_load_keyring", lambda: (fake, True))

    status = key_store.get_status()
    assert status.has_key is True
    assert status.source == "saved"
    assert status.masked_key.endswith("ABCD")
    assert "secret-ABCD" not in status.masked_key
    assert status.masked_key != "ob-secret-ABCD"


def test_keyring_backend_error_surface(monkeypatch, clean_env):
    monkeypatch.setattr(key_store, "_load_keyring", lambda: (None, False))
    with pytest.raises(key_store.ApiKeyStoreError):
        key_store.save_key("key")


def test_validate_key_live_sanitizes_key_from_errors(monkeypatch, clean_env):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("401 unauthorized for OPENBIO_API_KEY=sk-test-1234")

    monkeypatch.setattr(key_store, "_validate_openbio_key_live", _raise)

    with pytest.raises(key_store.ApiKeyValidationError) as exc:
        key_store.validate_key_live("sk-test-1234")
    assert "sk-test-1234" not in str(exc.value)
    assert "***" in str(exc.value)
