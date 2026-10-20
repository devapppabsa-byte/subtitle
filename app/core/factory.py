"""Composition root: traduce configuración en implementaciones concretas.

Aquí —y solo aquí— se nombran las clases reales. El resto de la aplicación
depende exclusivamente de las interfaces, así que añadir un motor nuevo
consiste en un ``elif`` en esta función, sin tocar el pipeline ni la UI.
"""

from __future__ import annotations

from app.audio.base import AudioSource
from app.audio.capture import MicrophoneAudioSource, SystemAudioSource
from app.audio.synthetic import SyntheticAudioSource
from app.audio.wav import WavFileAudioSource
from app.core.errors import ConfigurationError
from app.core.pipeline import SubtitlePipeline
from app.overlay.console import ConsoleRenderer
from app.settings.config import AppSettings
from app.speech.base import SpeechRecognizer
from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance
from app.subtitles.base import SubtitleRenderer
from app.translation.base import Translator
from app.translation.passthrough import PassthroughTranslator

__all__ = [
    "build_audio_source",
    "build_recognizer",
    "build_translator",
    "build_translation_engine",
    "build_renderer",
    "build_pipeline",
]


def build_audio_source(settings: AppSettings) -> AudioSource:
    audio = settings.audio
    if audio.source == "synthetic":
        return SyntheticAudioSource(
            sample_rate=audio.sample_rate,
            chunk_duration=audio.chunk_duration,
            total_duration=audio.total_duration,
            realtime=audio.realtime,
        )
    if audio.source == "wav":
        if not audio.path:
            raise ConfigurationError("audio.path es obligatorio para la fuente 'wav'")
        return WavFileAudioSource(audio.path, chunk_duration=audio.chunk_duration, realtime=audio.realtime)
    if audio.source == "monitor":
        return SystemAudioSource(
            audio.device,
            target_rate=audio.sample_rate,
            chunk_duration=audio.chunk_duration,
        )
    if audio.source == "microphone":
        return MicrophoneAudioSource(
            audio.device,
            target_rate=audio.sample_rate,
            chunk_duration=audio.chunk_duration,
        )
    raise ConfigurationError(f"Origen de audio desconocido: {audio.source!r}")


def build_recognizer(settings: AppSettings) -> SpeechRecognizer:
    speech = settings.speech
    if speech.engine == "scripted":
        return ScriptedSpeechRecognizer(
            script=_demo_script(settings),
            emit_partials=speech.emit_partials,
            language=speech.language,
        )
    if speech.engine == "groq":
        import os

        from app.speech.groq import DEFAULT_MODEL, GroqSpeechRecognizer

        key_env = speech.api_key_env or "GROQ_API_KEY"
        api_key = os.environ.get(key_env)
        if not api_key:
            raise ConfigurationError(
                f"Falta la clave de Groq: exportá {key_env} o configurá speech.api_key_env."
            )
        model = speech.model if speech.model not in ("", "base") else DEFAULT_MODEL
        language = speech.language if speech.language != "auto" else None
        return GroqSpeechRecognizer(
            api_key=api_key,
            model=model,
            language=language,
            window_seconds=speech.window_seconds,
            window_overlap_seconds=speech.window_overlap_seconds,
            request_timeout=speech.request_timeout,
            max_retries=speech.max_retries,
        )
    raise ConfigurationError(
        f"Motor de voz '{speech.engine}' no implementado todavía. "
        "Implementá SpeechRecognizer en app/speech/ y registralo aquí."
    )


def build_translator(settings: AppSettings) -> Translator:
    translation = settings.translation
    if translation.engine == "passthrough":
        return PassthroughTranslator()
    if translation.engine == "mapping":
        from app.translation.mapping import MappingTranslator

        return MappingTranslator(
            {
                "hello world": "hola mundo",
                "the subtitles are working": "los subtítulos funcionan",
                "we can swap the engine": "podemos cambiar el motor",
            },
            source_language="en",
            target_language=translation.target_language,
        )
    if translation.engine == "groq":
        import os

        from app.translation.groq import DEFAULT_MODEL, GroqTranslator

        key_env = translation.api_key_env or "GROQ_API_KEY"
        api_key = os.environ.get(key_env)
        if not api_key:
            raise ConfigurationError(
                f"Falta la clave de Groq: exportá {key_env} o configurá translation.api_key_env."
            )
        model = translation.model.strip() or DEFAULT_MODEL
        return GroqTranslator(
            api_key=api_key,
            model=model,
            request_timeout=translation.request_timeout,
            max_retries=translation.max_retries,
            cache_size=translation.cache_size,
            parallel=translation.parallel,
        )
    if translation.engine in ("openai", "anthropic", "openrouter"):
        import os

        from app.translation.chat import ChatTranslator
        from app.translation.providers import provider_for_engine

        provider = provider_for_engine(translation.engine)
        assert provider is not None  # engine registrado arriba
        key_env = translation.api_key_env or provider.key_env
        api_key = os.environ.get(key_env)
        if not api_key:
            raise ConfigurationError(
                f"Falta la clave de {provider.label}: exportá {key_env} "
                "o pegá una clave válida en el panel (Motor IA)."
            )
        return ChatTranslator(
            provider,
            api_key,
            model=translation.model,
            request_timeout=translation.request_timeout,
            max_retries=translation.max_retries,
            cache_size=translation.cache_size,
            parallel=translation.parallel,
        )
    raise ConfigurationError(
        f"Motor de traducción '{translation.engine}' no implementado todavía. "
        "Implementá Translator en app/translation/ y registralo aquí."
    )


def build_translation_engine(
    provider_id: str,
    *,
    api_key: str,
    model: str = "",
    translation,
) -> Translator:
    """Construye el traductor para un proveedor elegido en caliente (panel Motor IA).

    Reutiliza los knobs de ``settings.translation`` y devuelve el motor listo
    para ``pipeline.swap_translator``. Sin claves no construye nada.
    """
    from app.translation.chat import ChatTranslator
    from app.translation.providers import provider_by_id

    provider = provider_by_id(provider_id)
    return ChatTranslator(
        provider,
        api_key,
        model=model,
        request_timeout=translation.request_timeout,
        max_retries=translation.max_retries,
        cache_size=translation.cache_size,
        parallel=translation.parallel,
    )


def build_renderer(settings: AppSettings) -> SubtitleRenderer:
    overlay = settings.overlay
    multi = len([t for t in settings.translation.target_languages if t.strip()]) > 1
    if overlay.renderer == "console":
        return ConsoleRenderer(show_partials=overlay.show_partials, show_language=multi)
    if overlay.renderer == "qt":
        from app.overlay.qt_overlay import QtOverlayRenderer

        return QtOverlayRenderer(style=overlay.style)
    raise ConfigurationError(f"Renderizador desconocido: {overlay.renderer!r}")


def build_pipeline(settings: AppSettings) -> SubtitlePipeline:
    from app.core.latency import LatencyTracker

    targets = [t for t in settings.translation.target_languages if t.strip()]
    return SubtitlePipeline(
        audio_source=build_audio_source(settings),
        recognizer=build_recognizer(settings),
        translator=build_translator(settings),
        source_language=settings.translation.source_language,
        target_language=settings.translation.target_language,
        target_languages=targets or None,
        translate_partials=settings.translation.translate_partials,
        emit_partial_cues=settings.overlay.show_partials,
        latency=LatencyTracker(sink=settings.latency.latency_log),
    )


def _demo_script(settings: AppSettings) -> list[ScriptedUtterance]:
    """Guion del simulador: permite ver el pipeline completo sin motor real."""
    if settings.speech.engine != "scripted":
        return []
    return [
        ScriptedUtterance("hello world", 0.5, 2.0, language="en"),
        ScriptedUtterance("the subtitles are working", 2.5, 4.0, language="en"),
    ]
