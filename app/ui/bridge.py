"""Puente entre el pipeline (hilos de trabajo) y Qt (hilo GUI).

Fase 5: la interfaz nunca hace red ni toca los motores; solo drena la cola
``cues`` con un ``QTimer`` en el hilo GUI y reemite cada evento como señal.
Las ventanas se actualizan conectando ``cueReceived`` al renderizador.

Reglas:

* Toda llamada de red vive en los workers del pipeline, jamás aquí.
* ``render`` se invoca siempre en el hilo GUI (donde corre el event loop).
* Eventos fuera de orden (stale que escapó al pipeline) se ignoran por
  ``start_time`` monótono.
"""

from __future__ import annotations

from queue import Empty
from typing import TYPE_CHECKING

try:
    from PySide6.QtCore import QObject, QTimer, Signal
except ImportError:  # pragma: no cover - Qt opcional en CI sin GUI
    QObject = object  # type: ignore[assignment,misc]
    QTimer = None  # type: ignore[assignment]

    def Signal(*args, **kwargs):  # type: ignore[no-redef]
        raise ImportError("PySide6 requerido para el bridge Qt")

if TYPE_CHECKING:  # pragma: no cover
    from app.core.pipeline import SubtitlePipeline
    from app.subtitles.base import SubtitleRenderer
    from app.subtitles.models import SubtitleCue

__all__ = ["PipelineBridge"]


class PipelineBridge(QObject):
    """Drena ``pipeline.cues`` en el hilo GUI y lo publica como señales."""

    cueReceived = Signal(object)
    finished = Signal(str)

    def __init__(
        self,
        pipeline: "SubtitlePipeline",
        renderer: "SubtitleRenderer | None" = None,
        *,
        poll_ms: int = 100,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._pipeline = pipeline
        self._renderer = renderer
        self._router = None
        self._timer = QTimer(self)
        self._timer.setInterval(max(10, poll_ms))
        self._timer.timeout.connect(self._drain)
        self.cueReceived.connect(self._on_cue)
        self.finished.connect(self._on_finished)
        self._last_start = -1.0
        self._stale_ignored = 0
        self._finished_emitted = False

    # -- slots públicos (hilo GUI) --------------------------------------
    def set_router(self, router) -> None:
        """Registra un enrutador ``cue -> None`` (p. ej. MainWindow por idioma).

        Si hay router, ``_on_cue`` lo invoca en vez del renderizador único.
        """

        self._router = router

    def start(self) -> None:
        """Arranca pipeline, renderizador y bombeo (``start_pipeline``)."""
        self._finished_emitted = False
        self._drop_pending_finished()
        self._pipeline.start()
        if self._renderer is not None:
            self._renderer.start()
        self._timer.start()

    def stop(self) -> None:
        """Detiene bombeo, pipeline y renderizador (``stop_pipeline``)."""
        self._timer.stop()
        self._pipeline.stop()
        if self._renderer is not None:
            self._renderer.stop()

    @property
    def stale_ignored(self) -> int:
        """Eventos fuera de orden descartados en el bridge."""
        return self._stale_ignored

    # -- internos (siempre en hilo GUI) ----------------------------------
    def _drain(self) -> None:
        from app.core.events import PipelineFinished

        while True:
            try:
                event = self._pipeline.cues.get_nowait()
            except Empty:
                return
            if isinstance(event, PipelineFinished):
                if self._finished_emitted:
                    continue
                self._finished_emitted = True
                self._timer.stop()
                self._discard_finished()
                self.finished.emit(event.reason)
                return
            start = event.start_time or 0.0
            if start < self._last_start:
                self._stale_ignored += 1
                continue
            self._last_start = start
            self.cueReceived.emit(event)

    def _drop_pending_finished(self) -> None:
        """Quita los ``PipelineFinished`` que quedaron de una ejecución previa.

        Sin esto, al reiniciar (por ejemplo tras cambiar el origen de audio)
        el centinela viejo se leería como el final de la ejecución nueva y el
        panel se cerraría solo.
        """
        from app.core.events import PipelineFinished

        while True:
            try:
                event = self._pipeline.cues.get_nowait()
            except Empty:
                return
            if isinstance(event, PipelineFinished):
                continue
            self._last_start = max(self._last_start, event.start_time or 0.0)

    def _discard_finished(self) -> None:
        """Descarta ``PipelineFinished`` pendientes para no re-emitir.

        El cierre del pipeline puede publicar más de un centinela (el
        ``finally`` de la captura propaga el suyo además del normal); sin esto
        el panel veía varios ``finished`` seguidos y re-arrancaba.
        """
        from app.core.events import PipelineFinished

        while True:
            try:
                event = self._pipeline.cues.get_nowait()
            except Empty:
                return
            if isinstance(event, PipelineFinished):
                continue
            self._last_start = max(self._last_start, event.start_time or 0.0)
            self.cueReceived.emit(event)

    def _on_cue(self, cue: "SubtitleCue") -> None:
        if self._router is not None:
            self._router(cue)
        elif self._renderer is not None:
            self._renderer.render(cue)

    def _on_finished(self, reason: str) -> None:
        self._timer.stop()
