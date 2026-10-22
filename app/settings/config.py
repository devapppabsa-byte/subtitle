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
    "LatencySettings",
    "AppSettings",
    "load_settings",
]

ENV_PREFIX = "SUBTITLE_"


@dataclass(slots=True)
class AudioSettings:
    """De dónde sale el audio y cómo se captura."""

    source: str = "synthetic"
    """``synthetic`` | ``wav`` | ``microphone`` | ``monitor`` | ``app``."""

    device: str | None = None
    """Índice, subcadena del nombre o None (auto: monitor preferido / entrada por defecto)."""
    path: str | None = None
    """Ruta del WAV cuando ``source == "wav"``."""

    stream_spec: str | None = None
    """Stream a capturar cuando ``source == "app"``: ``"binario::título"``."""

    stream_loopback: bool = True
    """Si el audio capturado sigue sonando por los parlantes (loopback)."""

    sample_rate: int = 16_000
    chunk_duration: float = 0.5
    total_duration: float = 5.0
    """Duración de la fuente sintética."""

    realtime: bool = False


@dataclass(slots=True)
class SpeechSettings:
    """Motor de reconocimiento de voz."""

    engine: str = "scripted"
    """``scripted`` | ``groq`` | ``faster-whisper`` | ``deepgram`` | ``assemblyai`` | ``openai``."""

    model: str = "base"
    """Para ``groq``: ``whisper-large-v3-turbo`` (defecto si es ``base``) o ``whisper-large-v3``."""

    language: str | None = None
    """Código ISO o ``None``/``auto`` para autodetectar con la propia transcripción."""

    emit_partials: bool = True
    api_key_env: str | None = None
    """Nombre de la variable de entorno con la credencial (Groq: ``GROQ_API_KEY``)."""

    window_seconds: float = 8.0
    """Ventana batch de audio para STT en la nube (Fase 7: 8s + solape)."""

    window_overlap_seconds: float = 2.0
    """Solape retenido entre ventanas para no partir palabras del borde."""

    request_timeout: float = 60.0
    """Timeout por llamada a la API de voz."""

    max_retries: int = 2
    """Reintentos ante 429/5xx/timeout antes del fallback (Fase 7)."""


@dataclass(slots=True)
class TranslationSettings:
    """Motor de traducción."""

    engine: str = "passthrough"
    """``passthrough`` | ``mapping`` | ``groq`` | ``nllb`` | ``deepl`` | ``openai``."""

    model: str = ""
    """Modelo del motor (Groq: ``openai/gpt-oss-20b`` por defecto si está vacío)."""

    source_language: str = "auto"
    target_language: str = "en"
    """Destino único (compatibilidad); si ``target_languages`` no está vacío, manda esa lista."""

    target_languages: list[str] = field(default_factory=list)
    """Varios destinos del MISMO fragmento (p. ej. ``["es", "fr", "de"]``)."""

    translate_partials: bool = False
    api_key_env: str | None = None

    request_timeout: float = 30.0
    """Timeout por idioma destino."""

    max_retries: int = 2
    """Reintentos ante 429/5xx/timeout antes de caer al original (Fase 7)."""

    parallel: bool = False
    """Traducir destinos en paralelo (solo tiers con concurrencia; ver Fase 4)."""

    cache_size: int = 512
    """Entradas LRU de (texto, src, dst, modelo); 0 desactiva (Fase 7)."""


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
class LatencySettings:
    """Observabilidad y presupuesto de latencia (Fase 7)."""

    latency_log: str = ""
    """Ruta JSONL donde volcar marcas por tramo; vacío desactiva."""

    poll_ms: int = 100
    """Frecuencia de actualización del texto en la UI (bridge Qt)."""


@dataclass(slots=True)
class AppSettings:
    """Configuración completa, compuesta por la de cada etapa."""

    audio: AudioSettings = field(default_factory=AudioSettings)
    speech: SpeechSettings = field(default_factory=SpeechSettings)
    translation: TranslationSettings = field(default_factory=TranslationSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    latency: LatencySettings = field(default_factory=LatencySettings)
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
            latency=_build(LatencySettings, data.get("latency")),
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
    "TRANSLATION_MODEL": ("translation", "model"),
    "SOURCE_LANGUAGE": ("translation", "source_language"),
    "TARGET_LANGUAGE": ("translation", "target_language"),
    "TARGET_LANGUAGES": ("translation", "target_languages"),
    "OVERLAY_RENDERER": ("overlay", "renderer"),
    "LATENCY_LOG": ("latency", "latency_log"),
    "POLL_MS": ("latency", "poll_ms"),
    "SPEECH_WINDOW": ("speech", "window_seconds"),
    "SPEECH_OVERLAP": ("speech", "window_overlap_seconds"),
    "SPEECH_TIMEOUT": ("speech", "request_timeout"),
    "SPEECH_RETRIES": ("speech", "max_retries"),
    "TRANSLATION_TIMEOUT": ("translation", "request_timeout"),
    "TRANSLATION_RETRIES": ("translation", "max_retries"),
    "TRANSLATION_PARALLEL": ("translation", "parallel"),
    "TRANSLATION_CACHE": ("translation", "cache_size"),
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
        if attribute == "target_languages":
            setattr(
                target,
                attribute,
                [part.strip() for part in value.split(",") if part.strip()],
            )
            continue
        current = getattr(target, attribute)
        if isinstance(current, bool):
            setattr(target, attribute, value.strip().lower() in ("1", "true", "yes", "on"))
            continue
        setattr(target, attribute, type(current)(value) if current is not None else value)

    return settings
