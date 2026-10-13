"""Traductor de identidad.

Devuelve el texto sin cambios. Es el *default* seguro cuando todavía no hay un
motor de traducción configurado: el pipeline funciona igual y se puede validar
de punta a punta.
"""

from __future__ import annotations

from app.translation.base import Translator
from app.translation.models import TranslationResult

__all__ = ["PassthroughTranslator"]


class PassthroughTranslator(Translator):
    """No traduce: copia el texto de origen al de destino."""

    def __init__(self, *, detected_language: str = "und") -> None:
        super().__init__()
        self._detected_language = detected_language

    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult:
        resolved = self._detected_language if source_language == "auto" else source_language
        return TranslationResult(
            source_text=text,
            translated_text=text,
            source_language=resolved,
            target_language=target_language,
        )
