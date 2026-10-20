"""Tests del almacén de claves cifradas en SQLite (Fase 9)."""

from __future__ import annotations

import secrets

import pytest

from app.settings.keyvault import (
    KeyVault,
    VaultUnavailable,
    decrypt,
    encrypt,
)
from tests.conftest import FakeLlavero


# -- cifrado puro -----------------------------------------------------------
def test_encrypt_decrypt_roundtrip():
    master = secrets.token_bytes(32)
    blob = encrypt(master, "sk-secreta-123")
    assert b"sk-secreta-123" not in blob
    assert decrypt(master, blob) == "sk-secreta-123"


def test_encrypt_nonce_makes_ciphertexts_differ():
    master = secrets.token_bytes(32)
    assert encrypt(master, "mismo") != encrypt(master, "mismo")


def test_decrypt_detects_tampering():
    master = secrets.token_bytes(32)
    blob = bytearray(encrypt(master, "dato"))
    blob[-1] ^= 0x01  # rompe el tag
    with pytest.raises(ValueError, match="tag"):
        decrypt(master, bytes(blob))


def test_decrypt_wrong_master_fails():
    blob = encrypt(secrets.token_bytes(32), "x")
    with pytest.raises(ValueError):
        decrypt(secrets.token_bytes(32), blob)


# -- KeyVault ---------------------------------------------------------------
@pytest.fixture
def vault(tmp_path):
    return KeyVault(tmp_path / "keys.db", llavero=FakeLlavero(), master=secrets.token_bytes(32))


def test_save_and_load_key(vault):
    assert vault.save_key("openai", "OPENAI_API_KEY", "sk-abc") is True
    assert vault.load_key("openai") == "sk-abc"
    assert vault.load_key("anthropic") is None


def test_db_never_holds_plaintext(vault, tmp_path):
    vault.save_key("openai", "OPENAI_API_KEY", "sk-super-secreta")
    raw = (tmp_path / "keys.db").read_bytes()
    assert b"sk-super-secreta" not in raw


def test_db_has_600_permissions(vault, tmp_path):
    import os

    vault.save_key("openai", "OPENAI_API_KEY", "sk-abc")
    mode = os.stat(tmp_path / "keys.db").st_mode & 0o777
    assert mode == 0o600


def test_update_overwrites_key(vault):
    vault.save_key("openai", "OPENAI_API_KEY", "sk-vieja")
    vault.save_key("openai", "OPENAI_API_KEY", "sk-nueva")
    assert vault.load_key("openai") == "sk-nueva"
    assert vault.providers_with_key() == ["openai"]


def test_delete_key(vault):
    vault.save_key("openai", "OPENAI_API_KEY", "sk-abc")
    vault.delete_key("openai")
    assert vault.load_key("openai") is None


def test_vault_without_llavero_does_not_persist(tmp_path):
    class _NoLlavero:
        def available(self):
            return False

    vault = KeyVault(tmp_path / "keys.db", llavero=_NoLlavero())
    assert vault.save_key("openai", "OPENAI_API_KEY", "sk-abc") is False
    assert vault.load_key("openai") is None


def test_model_cache_roundtrip(vault):
    vault.cache_models("openrouter", [("nvidia/x:free", True), ("openai/gpt-4o-mini", False)])
    cached = vault.cached_models("openrouter")
    assert ("nvidia/x:free", True) in cached
    assert ("openai/gpt-4o-mini", False) in cached


def test_load_all_into_environ(tmp_path, monkeypatch):
    vault = KeyVault(tmp_path / "keys.db", llavero=FakeLlavero(), master=secrets.token_bytes(32))
    vault.save_key("openai", "OPENAI_API_KEY", "sk-abc")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    loaded = vault.load_all_into_environ({"openai": "OPENAI_API_KEY"})
    assert loaded["OPENAI_API_KEY"] == "sk-abc"


def test_migrate_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-from-file\nGROQ_API_KEY=gsk_x\n", encoding="utf-8")
    vault = KeyVault(tmp_path / "keys.db", llavero=FakeLlavero(), master=secrets.token_bytes(32))
    migrated = vault.migrate_from_env_file(env_file)
    assert migrated == 2
    assert vault.load_key("openai") == "sk-from-file"
    assert vault.load_key("groq") == "gsk_x"
    assert not env_file.exists()  # el .env se elimina tras migrar
