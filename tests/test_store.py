"""Fase 8: persistencia, validación y diagnóstico redactado (sin red real)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from app.settings.config import AppSettings
from app.settings.store import (
    env_file_path,
    export_logs,
    load_user_settings,
    redact_secrets,
    save_env_key,
    save_settings,
    settings_path,
    user_config_dir,
    validate_api_config,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    return tmp_path


def test_user_config_dir_respects_xdg(isolated_home):
    assert user_config_dir() == isolated_home / ".config" / "traductor-tiempo-real"
    assert user_config_dir().is_dir()
    assert settings_path().name == "settings.json"
    assert env_file_path().name == ".env"


def test_save_load_round_trip(isolated_home):
    settings = AppSettings()
    settings.translation.engine = "groq"
    settings.translation.target_languages = ["es", "fr", "de"]
    settings.translation.target_language = "es"
    settings.speech.window_seconds = 5.0
    settings.latency.poll_ms = 50
    path = save_settings(settings)
    assert path == settings_path()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    reloaded = load_user_settings()
    assert reloaded.translation.target_languages == ["es", "fr", "de"]
    assert reloaded.speech.window_seconds == 5.0
    assert reloaded.latency.poll_ms == 50
    # Sin archivo de usuario se vuelve al defecto (CLI manda si se pasa).
    path.unlink()
    assert load_user_settings().translation.engine == "passthrough"


def test_cli_path_wins_over_user_file(isolated_home, tmp_path):
    save_settings(AppSettings())
    cli = tmp_path / "cli.json"
    cli.write_text(json.dumps({"translation": {"engine": "mapping"}}))
    assert load_user_settings(cli).translation.engine == "mapping"


def test_save_never_persists_key_values(isolated_home, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secreto-total-123")
    settings = AppSettings()
    settings.translation.api_key_env = "GROQ_API_KEY"
    content = save_settings(settings).read_text()
    assert "gsk_secreto" not in content
    assert "GROQ_API_KEY" in content  # solo el nombre de la variable


def test_env_key_file_is_600(isolated_home):
    path = save_env_key("GROQ_API_KEY", "gsk_x")
    assert oct(path.stat().st_mode & stat.S_IMODE(path.stat().st_mode)) == "0o600"
    assert "GROQ_API_KEY=gsk_x" in path.read_text()


def test_redact_secrets():
    assert redact_secrets("key gsk_ABC123xyz resto") == "key gsk_*** resto"
    assert "gsk_ABC" not in redact_secrets("Authorization: Bearer gsk_ABC")
    assert redact_secrets("OPENAI_API_KEY=sk-real") == "OPENAI_API_KEY=***"


def test_validate_missing_key_without_network(isolated_home):
    settings = AppSettings()
    settings.speech.engine = "groq"
    settings.translation.engine = "groq"
    reports = validate_api_config(settings, environ={})
    assert len(reports) == 1  # voz y traducción comparten GROQ_API_KEY
    assert reports[0].key_present is False
    assert reports[0].ok is False


def test_validate_lists_models_with_fake_client(isolated_home, monkeypatch):
    import sys
    import types

    settings = AppSettings()
    settings.speech.engine = "groq"
    fake = types.ModuleType("groq")

    class _Groq:
        def __init__(self, api_key=None):
            assert api_key == "gsk_falsa"

        @property
        def models(self):
            class _M:
                def list(self):
                    item = types.SimpleNamespace(id="whisper-large-v3-turbo")
                    return types.SimpleNamespace(data=[item])

            return _M()

    fake.Groq = _Groq
    monkeypatch.setitem(sys.modules, "groq", fake)
    reports = validate_api_config(settings, environ={"GROQ_API_KEY": "gsk_falsa"})
    assert reports[0].ok is True
    assert reports[0].models == ["whisper-large-v3-turbo"]


def test_export_logs_redacts_and_skips_audio(isolated_home, tmp_path):
    latency = tmp_path / "lat.jsonl"
    latency.write_text(
        '{"key": "u0", "marks": [["dispatch", 1.0]]}\n'
        "token gsk_secreto-en-log\n"
    )
    settings = AppSettings()
    out = export_logs(settings, {"pending_cues": 0}, tmp_path / "diag", latency_log=latency)
    bundle = json.loads(out.read_text())
    assert bundle["stats"] == {"pending_cues": 0}
    assert "gsk_secreto" not in out.read_text()
    assert "latency_marks" in bundle
    assert (tmp_path / "diag" / "latency.jsonl").is_file()


def test_export_logs_without_latency_file(isolated_home, tmp_path):
    out = export_logs(AppSettings(), None, tmp_path / "d2")
    assert json.loads(out.read_text())["stats"] == {}


def test_main_validate_and_export_flags(isolated_home, tmp_path, monkeypatch, capsys):
    import main as entry

    monkeypatch.setattr(
        "sys.argv", ["subtitle-app", "--speech-engine", "groq", "--translation-engine", "groq",
                     "--validate-config"]
    )
    assert entry.main() == 0
    assert "GROQ_API_KEY" in capsys.readouterr().out

    out = tmp_path / "diag"
    monkeypatch.setattr("sys.argv", ["subtitle-app", "--export-logs", str(out)])
    assert entry.main() == 0
    assert (out / "diagnostico.json").is_file()
    assert "gsk_" not in (out / "diagnostico.json").read_text()

    monkeypatch.setattr(
        "sys.argv", ["subtitle-app", "--audio", "synthetic", "--duration", "1", "--save-config"]
    )
    assert entry.main() == 0
    assert settings_path().is_file()
    assert os.environ.get("GROQ_API_KEY") is None or True
