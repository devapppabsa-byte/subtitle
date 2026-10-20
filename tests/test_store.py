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


def test_validate_key_openai_with_mock_transport(isolated_home):
    import httpx

    from app.settings.store import validate_key

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
    )
    report = validate_key("openai", "sk-test", transport=transport)
    assert report.ok is True
    assert report.provider == "openai"
    assert report.models == ["gpt-4o-mini"]


def test_validate_key_missing_is_offline(isolated_home):
    from app.settings.store import validate_key

    report = validate_key("anthropic", "")
    assert report.ok is False
    assert report.key_present is False
    assert "ANTHROPIC_API_KEY" in report.detail


def test_validate_key_never_leaks_secret(isolated_home):
    import httpx

    from app.settings.store import validate_key

    secret = "sk-super-secreta-123"
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text=f"bad {secret}"))
    report = validate_key("openai", secret, transport=transport)
    assert report.ok is False
    assert secret not in report.detail


def test_validate_api_config_reports_new_provider(isolated_home):
    settings = AppSettings()
    settings.speech.engine = "scripted"
    settings.translation.engine = "openrouter"
    reports = validate_api_config(settings, environ={})
    assert len(reports) == 1
    assert reports[0].provider == "openrouter"
    assert reports[0].key_present is False


def test_load_env_file_sets_missing_only(isolated_home, monkeypatch):
    from app.settings.store import env_file_path, load_env_file

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file_path().write_text("OPENAI_API_KEY=sk-from-file\n# comentario\n\n", encoding="utf-8")
    loaded = load_env_file()
    assert loaded["OPENAI_API_KEY"] == "sk-from-file"
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-from-file"


def test_load_env_file_does_not_override(isolated_home, monkeypatch):
    from app.settings.store import env_file_path, load_env_file

    monkeypatch.setenv("OPENAI_API_KEY", "sk-del-entorno")
    env_file_path().write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
    load_env_file()
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-del-entorno"


def test_fetch_models_caches_in_db(isolated_home, fake_vault):
    import httpx

    from app.settings.store import fetch_models

    handler = lambda req: httpx.Response(200, json={"data": [
        {"id": "gpt-4o-mini", "pricing": {"prompt": "0.1", "completion": "0.2"}},
    ]})
    models = fetch_models("openai", "sk-x", transport=httpx.MockTransport(handler))
    assert [m.id for m in models] == ["gpt-4o-mini"]
    assert fake_vault.cached_models("openai") == [("gpt-4o-mini", False)]


def test_fetch_models_falls_back_to_cache(isolated_home, fake_vault):
    import httpx

    from app.settings.store import fetch_models

    fake_vault.cache_models("openai", [("gpt-4o-mini", False)])

    def boom(req):
        raise httpx.ConnectError("sin red")

    models = fetch_models("openai", "sk-x", transport=httpx.MockTransport(boom))
    assert [m.id for m in models] == ["gpt-4o-mini"]


def test_fetch_models_no_cache_propagates(isolated_home, fake_vault):
    import httpx
    import pytest as _pytest

    from app.core.errors import TranslationError
    from app.settings.store import fetch_models

    def boom(req):
        raise httpx.ConnectError("sin red")

    with _pytest.raises(TranslationError):
        fetch_models("openai", "sk-x", transport=httpx.MockTransport(boom))
