"""Jerarquía de excepciones de la aplicación.

Todos los errores propios del proyecto heredan de :class:`SubtitleAppError`, de
modo que la capa de orquestación puede distinguir fallos esperados (dispositivo
de audio ocupado, API sin credenciales, etc.) de errores de programación.
"""

from __future__ import annotations


class SubtitleAppError(Exception):
    """Error base de toda la aplicación."""


class ConfigurationError(SubtitleAppError):
    """Configuración inválida, incompleta o inconsistente."""


# ---------------------------------------------------------------------------
# Errores por etapa del pipeline
# ---------------------------------------------------------------------------
class AudioError(SubtitleAppError):
    """Fallo al abrir, leer o cerrar una fuente de audio."""


class SpeechError(SubtitleAppError):
    """Fallo durante el reconocimiento de voz."""


class TranslationError(SubtitleAppError):
    """Fallo durante la traducción."""


class RenderError(SubtitleAppError):
    """Fallo al dibujar los subtítulos en pantalla."""


__all__ = [
    "SubtitleAppError",
    "ConfigurationError",
    "AudioError",
    "SpeechError",
    "TranslationError",
    "RenderError",
]
