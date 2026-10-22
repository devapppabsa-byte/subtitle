"""Captura del audio de una aplicación o pestaña concreta.

Fase 10: en vez de escuchar todo el sistema, la app puede capturar solo el
audio de un programa o de una pestaña del navegador.

El truco es el enrutado del servidor de audio (ver :mod:`app.audio.streams`):
el stream elegido se mueve a un *null sink* virtual y la app captura el
monitor de ese sink. Si el usuario quiere seguir oyéndolo, un *loopback* lo
devuelve a los parlantes.

La captura se hace con ``parec`` (PulseAudio) en lugar de con sounddevice a
propósito: el monitor del null sink virtual no siempre aparece en la lista de
dispositivos que cachea PortAudio, y ``parec`` va directo al servidor de audio.
Además, se pide el audio ya en el formato que quiere el reconocedor
(mono s16le a 16 kHz), así que no hay remuestreo.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time

import numpy as np

from app.audio.base import AudioSource
from app.audio.models import AudioChunk
from app.audio.streams import StreamRouter
from app.core.errors import AudioError
from app.core.logging import get_logger

__all__ = ["AppStreamAudioSource"]

logger = get_logger(__name__)


class AppStreamAudioSource(AudioSource):
    """Captura el audio de una app o pestaña, enrutándolo a un null sink.

    :param spec: selector del stream, ``"binario::título"`` (ver
        :attr:`app.audio.streams.AudioStreamInfo.spec`).
    :param loopback: si es ``True``, el audio sigue sonando por los parlantes.
    :param router: enrutador (inyectable para tests).
    """

    kind = "app"

    def __init__(
        self,
        spec: str | None = None,
        *,
        loopback: bool = True,
        router: StreamRouter | None = None,
        target_rate: int = 16_000,
        chunk_duration: float = 0.5,
        queue_size: int = 32,
    ) -> None:
        super().__init__()
        if target_rate <= 0:
            raise AudioError("target_rate debe ser positivo")
        if chunk_duration <= 0:
            raise AudioError("chunk_duration debe ser positivo")
        self._spec = (spec or "").strip()
        self._loopback = loopback
        self._router = router or StreamRouter()
        self._target_rate = int(target_rate)
        self._chunk_duration = float(chunk_duration)
        self._frames_per_chunk = max(1, int(round(self._target_rate * self._chunk_duration)))
        self._bytes_per_frame = 2  # s16le mono
        self._block_bytes = self._frames_per_chunk * self._bytes_per_frame
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(1, queue_size))
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._frames_emitted = 0

    # -- propiedades -------------------------------------------------------
    @property
    def sample_rate(self) -> int:
        return self._target_rate

    @property
    def spec(self) -> str:
        """Selector del stream capturado."""
        return self._spec

    @property
    def router(self) -> StreamRouter:
        """Enrutador usado para mover el stream."""
        return self._router

    # -- ciclo de vida -----------------------------------------------------
    def _on_start(self) -> None:
        router = self._router
        # Si una ejecución anterior murió a la fuerza, algún stream puede
        # seguir atrapado en el sink virtual (quedaría sin sonido).
        try:
            router.cleanup_orphans()
        except Exception:  # pragma: no cover - defensivo
            logger.exception("fallo al limpiar streams huérfanos")
        if not router.ensure_null_sink():
            raise AudioError(
                "No se pudo crear el sink virtual de captura "
                f"({router.sink_name!r}); el servidor de audio no soporta "
                "'module-null-sink'."
            )
        moved = router.route_matching(self._spec)
        if not moved:
            logger.warning(
                "ningún stream activo coincide con %r; se espera a que aparezca",
                self._spec,
            )
        if self._loopback:
            router.enable_loopback()
        router.start_watching(self._spec)
        self._start_capture()

    def _on_stop(self, timeout: float | None) -> None:
        try:
            self._stop_capture()
        finally:
            self._router.restore()

    # -- captura con parec -------------------------------------------------
    def _start_capture(self) -> None:
        if shutil.which("parec") is None:
            raise AudioError(
                "Falta 'parec' (paquete pulseaudio-utils) para capturar el "
                "audio de una aplicación."
            )
        source = self._router.capture_source
        cmd = [
            "parec",
            f"--device={source}",
            "--format=s16le",
            f"--rate={self._target_rate}",
            "--channels=1",
        ]
        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
            )
        except OSError as exc:
            raise AudioError(f"No se pudo iniciar 'parec': {exc}") from exc
        time.sleep(0.3)
        if process.poll() is not None:
            raise AudioError(
                f"'parec' no pudo capturar {source!r} (código {process.returncode})."
            )
        with self._lock:
            self._process = process
            self._frames_emitted = 0
            self._reader = threading.Thread(
                target=self._read_loop, args=(process,), name="parec-reader", daemon=True
            )
            self._reader.start()
        logger.info(
            "captura por app activa (stream=%s, %d Hz mono vía parec)",
            self._spec,
            self._target_rate,
        )

    def _read_loop(self, process: subprocess.Popen) -> None:
        stdout = process.stdout
        if stdout is None:  # pragma: no cover - defensivo
            return
        while True:
            try:
                data = stdout.read(self._block_bytes)
            except (OSError, ValueError):
                break
            if not data:
                break
            usable = len(data) - (len(data) % self._bytes_per_frame)
            if usable <= 0:
                continue
            samples = np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0
            try:
                self._queue.put(samples)
            except queue.Full:
                # El consumidor va lento: se descarta lo más viejo (igual que
                # en el resto del pipeline) para no acumular latencia.
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(samples)
                except queue.Full:  # pragma: no cover - carrera extrema
                    pass

    def _stop_capture(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            reader, self._reader = self._reader, None
        if process is not None:
            try:
                if process.stdout is not None:
                    process.stdout.close()
            except OSError:
                pass
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:  # pragma: no cover - defensivo
                    pass
        if reader is not None and reader.is_alive():
            reader.join(timeout=2.0)
        self._drain_queue()

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # -- lectura -----------------------------------------------------------
    def read(self, timeout: float | None = None) -> AudioChunk | None:
        """Devuelve el siguiente fragmento de audio mono ``float32``."""
        if not self.running:
            return None
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if deadline is not None and remaining <= 0:
                return None
            try:
                samples = self._queue.get(timeout=remaining)
            except queue.Empty:
                return None
            if samples.size == 0:
                continue
            frames = self._frames_per_chunk
            while samples.size < frames:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                extra_timeout = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                try:
                    extra = self._queue.get(timeout=extra_timeout)
                except queue.Empty:
                    break
                samples = np.concatenate((samples, extra))
            usable = (samples.size // frames) * frames
            block = samples[:usable] if usable else samples
            if block.size == 0:
                continue
            chunk = AudioChunk(
                samples=block.astype(np.float32, copy=False),
                sample_rate=self._target_rate,
                start_time=self._frames_emitted / self._target_rate,
            )
            self._frames_emitted += int(block.size)
            return chunk
