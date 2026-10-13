"""Renderizador de subtítulos en consola.

Implementación por defecto, sin dependencias gráficas. Sirve como referencia
mínima del contrato ``SubtitleRenderer`` y para ejecutar el pipeline en
servidores o en CI.
"""

from __future__ import annotations

import sys
from typing import TextIO

from app.subtitles.base import SubtitleRenderer
from app.subtitles.models import SubtitleCue

__all__ = ["ConsoleRenderer"]


class ConsoleRenderer(SubtitleRenderer):
    """Imprime las cues finales y reescribe las parciales en la misma línea."""

    def __init__(self, *, stream: TextIO | None = None, show_partials: bool = True) -> None:
        super().__init__()
        self._stream = stream if stream is not None else sys.stdout
        self._show_partials = show_partials
        self._partial_open = False

    def _on_stop(self, timeout: float | None) -> None:
        if self._partial_open:
            self._stream.write("\n")
            self._stream.flush()
            self._partial_open = False

    def render(self, cue: SubtitleCue) -> None:
        if not cue.is_final:
            if not self._show_partials:
                return
            self._stream.write(f"\r\033[K… {cue.text}")
            self._stream.flush()
            self._partial_open = True
            return

        if self._partial_open:
            self._stream.write("\r\033[K")
            self._partial_open = False
        self._stream.write(f"▸ {cue.text}\n")
        self._stream.flush()

    def clear(self) -> None:
        if self._partial_open:
            self._stream.write("\r\033[K")
            self._stream.flush()
            self._partial_open = False
