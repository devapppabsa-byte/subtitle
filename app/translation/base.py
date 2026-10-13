"""Interfaz ``Translator``.

Modelo petición/respuesta, deliberadamente simple: el pipeline la invoca desde
un *worker* dedicado para que un backend lento (API en la nube, modelo local
grande) no bloquee la captura ni el dibujado.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.component import PipelineComponent
from app.core.logging import get_logger
from app.translation.models import TranslationResult

__all__ = ["Translator"]

logger = get_logger(__name__)


class Translator(PipelineComponent, ABC):
    """Traduce texto de un idioma a otro."""

    @abstractmethod
    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult:
        """Traduce ``text``.

        Puede bloquear: el pipeline lo ejecuta en un hilo aparte.

        :param source_language: idioma de origen, o ``"auto"`` para detección
            automática si el backend lo soporta.
        :param target_language: idioma de destino.
        """

    @property
    def supported_languages(self) -> frozenset[str] | None:
        """Idiomas soportados, o ``None`` si el backend no puede enumerarlos."""
        return None

    def translate_many(
        self, text: str, source_language: str, target_languages: list[str] | tuple[str, ...]
    ) -> dict[str, TranslationResult]:
        """Traduce un fragmento a varios idiomas, con fallo aislado por idioma."""
        results: dict[str, TranslationResult] = {}
        for target in target_languages:
            try:
                results[target] = self.translate(text, source_language, target)
            except Exception:
                logger.exception("traducción a %s falló; se conserva el original", target)
                results[target] = TranslationResult(
                    source_text=text,
                    translated_text=text,
                    source_language=source_language,
                    target_language=target,
                )
        return results
