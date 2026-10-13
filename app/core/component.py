"""Contrato de ciclo de vida común a todos los componentes del pipeline.

``PipelineComponent`` aporta la maquinaria de ``start``/``stop`` y el soporte
de *context manager* para que cada implementación (fuente de audio, motor de
voz, traductor, renderizador) solo tenga que preocuparse por sus propios
recursos.

Decisiones relevantes (Fase 0):

* El modelo de concurrencia elegido es **hilos + colas**; las etapas que lo
  necesiten arrancan sus propios *worker threads* en ``_on_start`` y los unen
  en ``_on_stop``.
* ``start`` y ``stop`` son **idempotentes**: llamarlos dos veces es seguro.
* ``_on_stop`` debe liberar recursos y, si la etapa tiene hilos, **unirlos**
  antes de volver, de forma que a la salida no queden resultados pendientes
  sin publicar.
"""

from __future__ import annotations

import threading
from abc import ABC
from types import TracebackType

__all__ = ["PipelineComponent"]


class PipelineComponent(ABC):
    """Base de todo componente con ciclo de vida.

    No declara métodos abstractos: las interfaces concretas
    (:class:`~app.audio.base.AudioSource`, etc.) son las que fijan el
    contrato de dominio. Una subclase que no implemente ninguno de esos
    contratos sigue siendo instanciable, por lo que esta clase no se usa
    sola como interfaz pública.
    """

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._running = False

    # -- estado -----------------------------------------------------------
    @property
    def running(self) -> bool:
        """``True`` mientras el componente ha sido arrancado y no parado."""
        with self._state_lock:
            return self._running

    # -- ciclo de vida ----------------------------------------------------
    def start(self) -> None:
        """Arranca el componente. Idempotente."""
        with self._state_lock:
            if self._running:
                return
            self._on_start()
            self._running = True

    def stop(self, timeout: float | None = None) -> None:
        """Detiene el componente y libera recursos.

        Idempotente y seguro ante llamadas repetidas. ``timeout`` es el tiempo
        máximo, en segundos, que se espera a los hilos internos.
        """
        with self._state_lock:
            self._running = False
        self._on_stop(timeout)

    # -- puntos de extensión ---------------------------------------------
    def _on_start(self) -> None:
        """Reserva recursos. A sobrescribir por las subclases."""

    def _on_stop(self, timeout: float | None) -> None:
        """Libera recursos y une hilos. A sobrescribir por las subclases."""

    def _mark_inactive(self) -> None:
        """Marca el componente como terminado sin liberar recursos.

        Lo usan las fuentes finitas (por ejemplo, un archivo WAV o una fuente
        sintética) para señalar que ya no producirán más datos, sin tener que
        cerrarse desde su propio hilo de lectura.
        """
        with self._state_lock:
            self._running = False

    # -- context manager --------------------------------------------------
    def __enter__(self) -> "PipelineComponent":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.stop()
        return False
