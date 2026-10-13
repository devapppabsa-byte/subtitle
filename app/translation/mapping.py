"""Traductor basado en diccionario.

Traducción real (aunque trivial) a partir de un mapa de frases. Útil para el
demo y los tests: permite comprobar que el texto que llega al renderizador es
el traducido y no el original, sin depender de un modelo.
"""

from __future__ import annotations

from app.translation.base import Translator
from app.translation.models import TranslationResult

__all__ = ["MappingTranslator"]


class MappingTranslator(Translator):
    """Traduce por coincidencia exacta de frase; si no hay, deja el original."""

    def __init__(
        self,
        mapping: dict[str, str],
        *,
        source_language: str = "en",
        target_language: str = "es",
        case_sensitive: bool = False,
    ) -> None:
        super().__init__()
        self._source_language = source_language
        self._target_language = target_language
        self._case_sensitive = case_sensitive
        self._mapping = dict(mapping) if case_sensitive else {k.lower(): v for k, v in mapping.items()}

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({self._source_language, self._target_language})

    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult:
        key = text if self._case_sensitive else text.lower()
        translated = self._mapping.get(key, text)
        return TranslationResult(
            source_text=text,
            translated_text=translated,
            source_language=self._source_language if source_language == "auto" else source_language,
            target_language=target_language or self._target_language,
        )
