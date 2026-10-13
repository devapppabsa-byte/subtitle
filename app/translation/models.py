"""Modelos de dominio de la traducción."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TranslationResult"]


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """Resultado de traducir un segmento de texto."""

    source_text: str
    translated_text: str
    source_language: str
    target_language: str
