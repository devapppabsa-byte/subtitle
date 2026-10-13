"""Modelos de dominio del reconocimiento de voz."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Word", "RecognitionResult"]


@dataclass(frozen=True, slots=True)
class Word:
    """Palabra reconocida con su marca temporal e incertidumbre."""

    text: str
    start_time: float
    end_time: float
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """Hipótesis de transcripción, parcial o final.

    Un mismo enunciado se publica primero como varias ``is_final=False``
    (retroalimentación en vivo, se van corrigiendo) y termina con una única
    ``is_final=True``. El pipeline asigna el mismo ``cue_id`` a todas las
    hipótesis del enunciado, y el renderizador reemplaza la cue anterior.
    """

    text: str
    is_final: bool
    start_time: float
    end_time: float
    language: str | None = None
    confidence: float | None = None
    words: tuple[Word, ...] = ()
