"""Medición de latencia por tramos (Fase 7).

``LatencyTracker`` registra marcas ``(clave, etapa, instante)`` y deriva
duraciones entre etapas consecutivas más un resumen promediado. Las claves
típicas son ``cue_id`` (pipeline) o ``w<n>`` (ventanas STT). Sin dependencias
y sin hilos propios: solo aritmética sobre ``time.monotonic``.
"""

from __future__ import annotations

import json
import threading
import time

from app.core.logging import get_logger

__all__ = ["LatencyTracker"]

logger = get_logger(__name__)


class LatencyTracker:
    """Colector de marcas por tramo con resumen y volcado JSONL opcional."""

    def __init__(self, *, sink: str = "") -> None:
        self._sink = sink
        self._lock = threading.Lock()
        self._marks: dict[str, list[tuple[str, float]]] = {}

    def mark(self, key: str, stage: str, at: float | None = None) -> None:
        """Registra una marca (``capture``, ``stt_resp``, ``rendered``, …)."""
        with self._lock:
            self._marks.setdefault(key, []).append((stage, time.monotonic() if at is None else at))

    def durations(self, key: str) -> dict[str, float]:
        """Duraciones entre etapas consecutivas para una clave."""
        with self._lock:
            marks = list(self._marks.get(key, []))
        spans: dict[str, float] = {}
        for (prev_stage, prev_t), (stage, now) in zip(marks, marks[1:]):
            spans[f"{prev_stage}->{stage}"] = max(0.0, now - prev_t)
        return spans

    def summary(self) -> dict[str, dict[str, float]]:
        """Promedio y conteo por tramo sobre todas las claves completas."""
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        with self._lock:
            keys = list(self._marks)
        for key in keys:
            for span, value in self.durations(key).items():
                totals[span] = totals.get(span, 0.0) + value
                counts[span] = counts.get(span, 0) + 1
        return {
            span: {"avg_s": totals[span] / counts[span], "n": counts[span]} for span in totals
        }

    def dump(self) -> None:
        """Vuelca las marcas a JSONL si hay ``sink`` configurado."""
        if not self._sink:
            return
        try:
            with open(self._sink, "a", encoding="utf-8") as handle:
                with self._lock:
                    items = list(self._marks.items())
                for key, marks in items:
                    handle.write(json.dumps({"key": key, "marks": marks}) + "\n")
        except OSError as exc:
            logger.warning("no se pudo escribir el log de latencia: %s", exc)
