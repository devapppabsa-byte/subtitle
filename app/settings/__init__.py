"""Configuración de la aplicación."""

from app.settings.config import (
    AppSettings,
    AudioSettings,
    OverlaySettings,
    SpeechSettings,
    TranslationSettings,
    load_settings,
)

__all__ = [
    "AppSettings",
    "AudioSettings",
    "SpeechSettings",
    "TranslationSettings",
    "OverlaySettings",
    "load_settings",
]
