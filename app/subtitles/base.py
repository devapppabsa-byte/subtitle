"""Interfaz ``SubtitleRenderer``.

Separa el "qué mostrar" (modelo :class:`SubtitleCue`) del "cómo mostrarlo"
(consola, ventana Qt, overlay nativo del sistema). Añadir un modo de
presentación nuevo no debe tocar el pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.component import PipelineComponent
from app.subtitles.models import SubtitleCue

__all__ = ["SubtitleRenderer"]


class SubtitleRenderer(PipelineComponent, ABC):
    """Dibuja subtítulos, normalmente sobre otras ventanas.

    Contrato de concurrencia
    -------------------------
    El renderizador se invoca desde el **hilo principal**, nunca desde los
    hilos del pipeline: los toolkits gráficos (Qt incluido) exigen que la UI se
    toque solo desde su propio hilo. El pipeline publica eventos y el bucle
    principal llama a ``render``.
    """

    @abstractmethod
    def render(self, cue: SubtitleCue) -> None:
        """Muestra ``cue``.

        Si ya existe una cue con el mismo ``cue_id``, debe **reemplazarla** en
        lugar de añadir una nueva. No debe bloquear.
        """

    @abstractmethod
    def clear(self) -> None:
        """Oculta todo el texto en pantalla."""
