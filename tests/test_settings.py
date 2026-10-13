"""Configuración: valores por defecto, JSON, entorno y validación."""

from __future__ import annotations

import json

import pytest

from app.core.errors import ConfigurationError
from app.settings.config import AppSettings, load_settings
from app.subtitles.models import SubtitlePosition


def test_defaults_are_safe_and_runnable():
    settings = AppSettings()
    assert settings.audio.source == "synthetic"
    assert settings.speech.engine == "scripted"
    assert settings.translation.engine == "passthrough"
    assert settings.overlay.renderer == "console"


def test_from_dict_maps_nested_sections():
    settings = AppSettings.from_dict(
        {
            "audio": {"source": "wav", "path": "assets/demo.wav"},
            "translation": {"target_language": "pt"},
            "overlay": {"renderer": "qt", "style": {"position": "top", "font_size": 40}},
        }
    )
    assert settings.audio.source == "wav"
    assert settings.audio.path == "assets/demo.wav"
    assert settings.translation.target_language == "pt"
    assert settings.overlay.style.position is SubtitlePosition.TOP
    assert settings.overlay.style.font_size == 40


def test_unknown_key_is_rejected():
    with pytest.raises(ConfigurationError):
        AppSettings.from_dict({"audio": {"source": "synthetic", "turbo": True}})


def test_missing_config_file_raises():
    with pytest.raises(ConfigurationError):
        AppSettings.from_json_file("no-existe.json")


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ esto no es json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        AppSettings.from_json_file(path)


def test_json_round_trip(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"translation": {"target_language": "fr"}}), encoding="utf-8")
    settings = AppSettings.from_json_file(path)
    assert settings.translation.target_language == "fr"
    assert settings.to_dict()["translation"]["target_language"] == "fr"


def test_environment_overrides_are_typed():
    settings = load_settings(
        environ={
            "SUBTITLE_TARGET_LANGUAGE": "de",
            "SUBTITLE_TRANSLATION_ENGINE": "mapping",
            "SUBTITLE_AUDIO_SOURCE": "wav",
            "SUBTITLE_OVERLAY_RENDERER": "qt",
        }
    )
    assert settings.translation.target_language == "de"
    assert settings.translation.engine == "mapping"
    assert settings.audio.source == "wav"
    assert settings.overlay.renderer == "qt"
