"""Configuración de la aplicación.

Dataclasses anidadas con valores por defecto sensatos, carga desde JSON y
sobrescritura por variables de entorno. En fases posteriores esta misma
estructura alimentará la ventana de preferencias de la UI.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import ConfigurationError
from app.subtitles.models import SubtitlePosition, SubtitleStyle

__all__ = [
    "AudioSettings",
    "SpeechSettings",
    "TranslationSettings",
    "OverlaySettings",
    "AppSettings",
    "load_settings",
]

ENV_PREFIX = "SUBTITLE_"


@dataclass(slots=True)
class AudioSettings:
    """De dónde sale el audio y cómo se captura."""

    source: str = "synthetic"
    """``synthetic`` | ``wav`` | ``microphone`` | ``monitor``."""

    device: str | None = None
    """Índice, subcadena del nombre o None (auto: monitor preferido / entrada por defecto)."""
    path: str | None = None
    """Ruta del WAV cuando ``source == "wav"``."""

    sample_rate: int = 16_000
    chunk_duration: float = 0.5
    total_duration: float = 5.0
    """Duración de la fuente sintética."""

    realtime: bool = False


@dataclass(slots=True)
class SpeechSettings:
    """Motor de reconocimiento de voz."""

    engine: str = "scripted"
    """``scripted`` | ``faster-whisper`` | ``deepgram`` | ``assemblyai`` | ``openai``."""

    model: str = "base"
    language: str | None = None
    emit_partials: bool = True
    api_key_env: str | None = None
    """Nombre de la variable de entorno con la credencial del proveedor."""


@dataclass(slots=True)
class TranslationSettings:
    """Motor de traducción."""

    engine: str = "passthrough"
    """``passthrough`` | ``mapping`` | ``nllb`` | ``deepl`` | ``openai``."""

    source_language: str = "auto"
    target_language: str = "en"
    translate_partials: bool = False
    api_key_env: str | None = None


@dataclass(slots=True)
class OverlaySettings:
    """Cómo y dónde se pintan los subtítulos."""

    renderer: str = "console"
    """``console`` | ``qt``."""

    always_on_top: bool = True
    click_through: bool = True
    show_partials: bool = True
    style: SubtitleStyle = field(default_factory=SubtitleStyle)


@dataclass(slots=True)
class AppSettings:
    """Configuración completa, compuesta por la de cada etapa."""

    audio: AudioSettings = field(default_factory=AudioSettings)
    speech: SpeechSettings = field(default_factory=SpeechSettings)
    translation: TranslationSettings = field(default_factory=TranslationSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    log_level: str = "INFO"

    # -- serialización ----------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        if not isinstance(data, dict):
            raise ConfigurationError("La configuración debe ser un objeto JSON")
        return cls(
            audio=_build(AudioSettings, data.get("audio")),
            speech=_build(SpeechSettings, data.get("speech")),
            translation=_build(TranslationSettings, data.get("translation")),
            overlay=_build_overlay(data.get("overlay")),
            log_level=str(data.get("log_level", cls.log_level)),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "AppSettings":
        file_path = Path(path)
        if not file_path.is_file():
            raise ConfigurationError(f"No existe el archivo de configuración: {file_path}")
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"JSON inválido en {file_path}: {exc}") from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build(dataclass_type, data: dict | None):
    if data is None:
        return dataclass_type()
    if isinstance(data, dataclass_type):
        return data
    if not isinstance(data, dict):
        raise ConfigurationError(f"{dataclass_type.__name__} debe ser un objeto JSON")
    allowed = {f.name for f in dataclasses.fields(dataclass_type)}
    unknown = set(data) - allowed
    if unknown:
        raise ConfigurationError(
            f"Claves desconocidas en {dataclass_type.__name__}: {', '.join(sorted(unknown))}"
        )
    return dataclass_type(**data)


def _build_overlay(data: dict | None) -> OverlaySettings:
    if data is None:
        return OverlaySettings()
    if isinstance(data, OverlaySettings):
        return data
    if not isinstance(data, dict):
        raise ConfigurationError("overlay debe ser un objeto JSON")
    payload = dict(data)
    style_data = payload.pop("style", None)
    overlay = _build(OverlaySettings, payload)
    if style_data is not None:
        if isinstance(style_data, dict) and "position" in style_data:
            style_data = dict(style_data)
            style_data["position"] = SubtitlePosition(style_data["position"])
        overlay.style = _build(SubtitleStyle, style_data)
    return overlay


_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "LOG_LEVEL": ("log_level", "log_level"),
    "AUDIO_SOURCE": ("audio", "source"),
    "AUDIO_DEVICE": ("audio", "device"),
    "AUDIO_PATH": ("audio", "path"),
    "SPEECH_ENGINE": ("speech", "engine"),
    "SPEECH_MODEL": ("speech", "model"),
    "SPEECH_LANGUAGE": ("speech", "language"),
    "TRANSLATION_ENGINE": ("translation", "engine"),
    "SOURCE_LANGUAGE": ("translation", "source_language"),
    "TARGET_LANGUAGE": ("translation", "target_language"),
    "OVERLAY_RENDERER": ("overlay", "renderer"),
}


def load_settings(
    path: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> AppSettings:
    """Construye la configuración: JSON (si hay) → variables de entorno.

    La ruta puede venir del argumento ``path`` o de ``SUBTITLE_CONFIG``.
    """
    env = os.environ if environ is None else environ
    config_path = path or env.get(f"{ENV_PREFIX}CONFIG")
    settings = AppSettings.from_json_file(config_path) if config_path else AppSettings()

    for suffix, (section, attribute) in _ENV_OVERRIDES.items():
        value = env.get(f"{ENV_PREFIX}{suffix}")
        if value is None or value == "":
            continue
        target = getattr(settings, section)
        current = getattr(target, attribute)
        setattr(target, attribute, type(current)(value) if current is not None else value)

    return settings
