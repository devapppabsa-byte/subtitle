"""Reconocedor de voz programado (simulador de desarrollo).

No transcribe nada: reproduce un *script* de enunciados a medida que consume
audio, respetando la duración real de cada uno. Permite construir y testear
todo el pipeline —parciales, finales, traducción, renderizado— antes de
integrar un motor de voz real.

Como la emisión depende de **la duración de audio consumida** y no del reloj de
pared, los tests son deterministas e instantáneos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from queue import Queue

from app.audio.models import AudioChunk
from app.core.component import PipelineComponent
from app.speech.base import SpeechRecognizer
from app.speech.models import RecognitionResult

__all__ = ["ScriptedUtterance", "ScriptedSpeechRecognizer"]


@dataclass(frozen=True, slots=True)
class ScriptedUtterance:
    """Enunciado que el simulador "reconocerá" en una ventana de tiempo."""

    text: str
    start_time: float
    end_time: float
    language: str | None = None


class ScriptedSpeechRecognizer(SpeechRecognizer):
    """Emite parciales progresivos y un final por cada enunciado del guion."""

    def __init__(
        self,
        script: list[ScriptedUtterance] | tuple[ScriptedUtterance, ...],
        *,
        emit_partials: bool = True,
        language: str | None = None,
    ) -> None:
        super().__init__()
        self._script = tuple(sorted(script, key=lambda u: u.start_time))
        self._emit_partials = emit_partials
        self._language = language
        self._results: Queue[RecognitionResult] = Queue()
        self._processed = 0.0
        self._finalized: set[int] = set()
        self._last_partial: dict[int, str] = {}

    # -- SpeechRecognizer -------------------------------------------------
    @property
    def results(self) -> Queue[RecognitionResult]:
        return self._results

    def configure(self, sample_rate: int, channels: int) -> None:
        self._sample_rate = sample_rate

    def feed(self, chunk: AudioChunk) -> None:
        if not self._script:
            return
        self._processed += chunk.duration
        if self._emit_partials:
            self._emit_partial()
        self._emit_due_finals()

    def flush(self) -> None:
        for index, utterance in enumerate(self._script):
            if index in self._finalized:
                continue
            self._finalized.add(index)
            self._results.put(self._build_result(utterance, is_final=True, text=utterance.text))

    # -- helpers ----------------------------------------------------------
    def _emit_partial(self) -> None:
        index, utterance = self._active_utterance()
        if utterance is None:
            return
        words = utterance.text.split()
        if not words:
            return
        span = max(utterance.end_time - utterance.start_time, 1e-6)
        progress = min(1.0, max(0.0, (self._processed - utterance.start_time) / span))
        visible = max(1, math.ceil(progress * len(words)))
        text = " ".join(words[:visible]) if visible < len(words) else utterance.text
        if self._last_partial.get(index) == text:
            return
        self._last_partial[index] = text
        self._results.put(self._build_result(utterance, is_final=False, text=text))

    def _emit_due_finals(self) -> None:
        for index, utterance in enumerate(self._script):
            if index in self._finalized or self._processed < utterance.end_time:
                continue
            self._finalized.add(index)
            self._results.put(self._build_result(utterance, is_final=True, text=utterance.text))

    def _active_utterance(self) -> tuple[int, ScriptedUtterance | None]:
        candidate: tuple[int, ScriptedUtterance | None] = (-1, None)
        for index, utterance in enumerate(self._script):
            if utterance.start_time <= self._processed < utterance.end_time:
                candidate = (index, utterance)
        return candidate

    def _build_result(
        self, utterance: ScriptedUtterance, *, is_final: bool, text: str
    ) -> RecognitionResult:
        return RecognitionResult(
            text=text,
            is_final=is_final,
            start_time=utterance.start_time,
            end_time=utterance.end_time,
            language=utterance.language or self._language,
        )
