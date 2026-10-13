"""Captura de audio en vivo: monitor del sistema y micrófono.

Fase 2: recibir el sonido que reproduce la PC sin depender del micrófono.

El contrato del pipeline no cambia: toda fuente entrega ``AudioChunk``
mono ``float32`` a ``target_rate`` Hz (16 kHz por defecto). Esta unidad se
encarga del *downmix* estéreo→mono y del remuestreo lineal 48 kHz→16 kHz
con numpy, sin dependencias nativas extra.

Tabla de correspondencia con la especificación de Fase 2::

    list_audio_devices()   → inventario sounddevice + pactl
    find_monitor_devices() → filtra fuentes ``*.monitor`` (salida del sistema)
    start_capture()        → ``SystemAudioSource.start_capture()`` (alias de ``start()``)
    read_audio_chunk()     → ``SystemAudioSource.read_audio_chunk()`` (alias de ``read()``)
    stop_capture()         → ``SystemAudioSource.stop_capture()`` (alias de ``stop()``)
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from app.audio.base import AudioSource
from app.audio.models import AudioChunk
from app.core.errors import AudioError
from app.core.logging import get_logger

__all__ = [
    "AudioDeviceInfo",
    "SystemAudioSource",
    "MicrophoneAudioSource",
    "list_audio_devices",
    "find_monitor_devices",
    "resample_mono",
]

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AudioDeviceInfo:
    """Descriptor unificado sounddevice + pactl."""

    index: int
    name: str
    kind: str = "unknown"
    """``monitor`` | ``microphone`` | ``output`` | ``input`` | ``duplex`` | ``unknown``."""

    channels_in: int = 0
    channels_out: int = 0
    default_rate: float = 0.0
    is_monitor: bool = False
    is_default: bool = False
    pactl_name: str | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------
def resample_mono(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Remuestrea un vector mono con interpolación lineal (numpy)."""
    mono = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    if src_rate == dst_rate or mono.size == 0:
        return mono.astype(np.float32, copy=False)
    if src_rate <= 0 or dst_rate <= 0:
        raise AudioError(f"Tasas inválidas: {src_rate} → {dst_rate}")
    new_len = max(1, int(round(mono.size * dst_rate / src_rate)))
    if new_len == mono.size:
        return mono
    src_pos = np.linspace(0.0, float(mono.size - 1), num=new_len)
    return np.interp(src_pos, np.arange(mono.size, dtype=np.float64), mono).astype(np.float32)


def _to_mono(frame: np.ndarray) -> np.ndarray:
    """Downmix de (n_frames, n_channels) a mono float32."""
    arr = np.asarray(frame, dtype=np.float32)
    if arr.ndim == 1:
        return np.ascontiguousarray(arr, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] == 0:
        raise AudioError(f"Trama de audio con forma inesperada: {arr.shape}")
    if arr.shape[1] == 1:
        return np.ascontiguousarray(arr[:, 0], dtype=np.float32)
    return np.ascontiguousarray(arr.mean(axis=1), dtype=np.float32)


# ---------------------------------------------------------------------------
# Sondeo de dispositivos
# ---------------------------------------------------------------------------
def _pactl_sources() -> tuple[list[dict], str | None, str | None]:
    """Devuelve (fuentes, default_sink, default_source). Sin pactl → ([], None, None)."""
    if shutil.which("pactl") is None:
        return [], None, None
    try:
        out = subprocess.run(
            ["pactl", "list", "sources", "short"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("pactl no disponible: %s", exc)
        return [], None, None
    sources: list[dict] = []
    if out.returncode == 0:
        for line in out.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                sources.append({"name": parts[1].strip(), "raw": line.strip()})

    def _default(*args: str) -> str | None:
        try:
            res = subprocess.run(list(args), capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        return None

    return (
        sources,
        _default("pactl", "get-default-sink"),
        _default("pactl", "get-default-source"),
    )


def list_audio_devices(use_pactl: bool = True) -> list[AudioDeviceInfo]:
    """Enumera dispositivos disponibles (sounddevice + marcas de pactl).

    Nunca lanza si falta sounddevice o pactl: devuelve ``[]`` y registra el motivo.
    """
    try:
        import sounddevice as sd
    except ImportError:
        logger.warning("sounddevice no instalado; instalá 'sounddevice>=0.4'")
        return []
    try:
        raw_devices = sd.query_devices()
        default_in, default_out = sd.default.device
    except Exception as exc:  # PortAudio ausente, sin servidor de audio, etc.
        logger.warning("no se pudo consultar dispositivos: %s", exc)
        return []

    pactl_sources, default_sink, default_source = _pactl_sources() if use_pactl else ([], None, None)
    pactl_names = {s["name"] for s in pactl_sources}
    default_monitor = f"{default_sink}.monitor" if default_sink else None

    devices: list[AudioDeviceInfo] = []
    for index, dev in enumerate(raw_devices):
        name = str(dev.get("name", f"device-{index}"))
        ch_in = int(dev.get("max_input_channels", 0))
        ch_out = int(dev.get("max_output_channels", 0))
        rate = float(dev.get("default_samplerate", 0.0) or 0.0)
        lowered = name.lower()

        # Heurística de monitor: nombre JACK/ALSA de salida con entrada disponible
        # (p. ej. "... Speaker ..." con 2 in) o coincidencia con un *.monitor de pactl.
        looks_like_output = any(
            token in lowered for token in ("speaker", "hdmi", "output", "playback", "monitor")
        )
        is_monitor = looks_like_output and ch_in > 0
        kind = "unknown"
        if is_monitor:
            kind = "monitor"
        elif ch_in > 0 and ch_out > 0:
            kind = "duplex"
        elif ch_in > 0:
            kind = "microphone" if "microphone" in lowered or "mic" in lowered else "input"
        elif ch_out > 0:
            kind = "output"

        # Refuerzo con pactl: si el sink por defecto aparece en el nombre, es el monitor preferido.
        pactl_hit: str | None = None
        if default_monitor and default_monitor.lower().replace("_", " ") in lowered.replace("_", " "):
            is_monitor = True
            kind = "monitor"
            pactl_hit = default_monitor
        elif pactl_names and ".monitor" in lowered:
            is_monitor = True
            kind = "monitor"

        devices.append(
            AudioDeviceInfo(
                index=index,
                name=name,
                kind=kind,
                channels_in=ch_in,
                channels_out=ch_out,
                default_rate=rate,
                is_monitor=is_monitor,
                is_default=(index == default_out or index == default_in),
                pactl_name=pactl_hit,
                description=pactl_hit or "",
            )
        )
    return devices


def find_monitor_devices(devices: list[AudioDeviceInfo] | None = None) -> list[AudioDeviceInfo]:
    """Filtra fuentes que capturan la salida del sistema, con la preferida primero."""
    devices = devices if devices is not None else list_audio_devices()
    monitors = [d for d in devices if d.is_monitor or d.kind == "monitor"]
    # El monitor del sink por defecto (o marcado con pactl) va primero.
    monitors.sort(key=lambda d: (d.pactl_name is None, not d.is_default, d.index))
    return monitors


def _resolve_device(
    spec: str | int | None, *, want_monitor: bool, devices: list[AudioDeviceInfo] | None = None
) -> AudioDeviceInfo:
    """Resuelve ``device`` (índice, subcadena o None=auto) a un descriptor concreto."""
    devices = devices if devices is not None else list_audio_devices()
    if not devices:
        raise AudioError("No hay dispositivos de audio visibles (¿falta sounddevice/PortAudio?).")
    if spec is None:
        if want_monitor:
            monitors = find_monitor_devices(devices)
            if not monitors:
                raise AudioError(
                    "No se encontró ningún monitor del sistema. Reproducí audio y "
                    "verificá 'pactl list sources short | grep monitor'."
                )
            return monitors[0]
        inputs = [d for d in devices if d.channels_in > 0 and not d.is_monitor]
        if not inputs:
            raise AudioError("No se encontró ningún micrófono/entrada disponible.")
        inputs.sort(key=lambda d: (not d.is_default, d.index))
        return inputs[0]
    if isinstance(spec, int):
        for dev in devices:
            if dev.index == spec:
                return dev
        raise AudioError(f"Dispositivo de audio #{spec} inexistente.")
    needle = str(spec).lower()
    for dev in devices:
        if needle in dev.name.lower() or (dev.pactl_name and needle in dev.pactl_name.lower()):
            return dev
    raise AudioError(
        f"Dispositivo {spec!r} no encontrado. Usá '--list-devices' para ver los disponibles."
    )


# ---------------------------------------------------------------------------
# Fuentes en vivo
# ---------------------------------------------------------------------------
class SystemAudioSource(AudioSource):
    """Captura la salida del sistema (monitor loopback) vía sounddevice.

    :param device: índice, subcadena del nombre o ``None`` (auto: monitor preferido).
    :param target_rate: frecuencia que recibe el reconocedor (16 kHz por defecto).
    :param chunk_duration: duración de cada ``AudioChunk`` en segundos.
    """

    kind = "monitor"

    def __init__(
        self,
        device: str | int | None = None,
        *,
        target_rate: int = 16_000,
        chunk_duration: float = 0.5,
        queue_size: int = 32,
    ) -> None:
        super().__init__()
        if target_rate <= 0:
            raise AudioError("target_rate debe ser positivo")
        if chunk_duration <= 0:
            raise AudioError("chunk_duration debe ser positivo")
        self._device_spec = device
        self._target_rate = int(target_rate)
        self._chunk_duration = float(chunk_duration)
        self._frames_per_chunk = max(1, int(round(self._target_rate * self._chunk_duration)))
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(1, queue_size))
        self._pending = np.zeros(0, dtype=np.float32)
        self._stream = None
        self._native_rate = self._target_rate
        self._channels = 1
        self._frames_emitted = 0
        self._device_info: AudioDeviceInfo | None = None
        self._lock = threading.Lock()

    # -- especificación Fase 2 (alias del ciclo de vida) --------------------
    def start_capture(self) -> None:
        """Inicia la captura. Alias explícito de :meth:`start`."""
        self.start()

    def read_audio_chunk(self, timeout: float | None = None) -> AudioChunk | None:
        """Obtiene un fragmento de audio. Alias explícito de :meth:`read`."""
        return self.read(timeout)

    def stop_capture(self, timeout: float | None = None) -> None:
        """Detiene la captura de forma segura. Alias explícito de :meth:`stop`."""
        self.stop(timeout)

    # -- AudioSource --------------------------------------------------------
    @property
    def sample_rate(self) -> int:
        return self._target_rate

    @property
    def device_info(self) -> AudioDeviceInfo | None:
        """Descriptor del dispositivo elegido (``None`` antes de ``start``)."""
        return self._device_info

    def _on_start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise AudioError("Falta 'sounddevice>=0.4' para captura en vivo.") from exc
        info = _resolve_device(self._device_spec, want_monitor=(self.kind == "monitor"))
        if info.channels_in <= 0:
            raise AudioError(f"El dispositivo {info.name!r} no admite captura (0 canales de entrada).")
        channels = min(info.channels_in, 2)
        native_rate = int(info.default_rate) if info.default_rate > 0 else self._target_rate
        with self._lock:
            self._device_info = info
            self._native_rate = native_rate
            self._channels = channels
            self._pending = np.zeros(0, dtype=np.float32)
            self._frames_emitted = 0
            self._drain_queue()
        try:
            stream = sd.InputStream(
                device=info.index,
                channels=channels,
                samplerate=native_rate,
                dtype="float32",
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:
            raise AudioError(f"No se pudo abrir {info.name!r}: {exc}") from exc
        with self._lock:
            self._device_info = info
            self._stream = stream
            self._native_rate = native_rate
            self._channels = channels
        logger.info(
            "captura iniciada (%s #%d, %dch %dHz → mono %dHz)",
            info.name,
            info.index,
            channels,
            native_rate,
            self._target_rate,
        )

    def _on_stop(self, timeout: float | None) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.exception("fallo al detener el stream de audio")
            try:
                stream.close()
            except Exception:
                logger.exception("fallo al cerrar el stream de audio")
        self._drain_queue()

    def read(self, timeout: float | None = None) -> AudioChunk | None:
        if not self.running:
            return None
        # El buffer interno está en tasa nativa; se necesitan más frames nativos
        # para producir un chunk completo en tasa destino.
        ratio = self._native_rate / self._target_rate if self._target_rate else 1.0
        needed_native = max(1, int(round(self._frames_per_chunk * ratio)))
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while self._pending.size < needed_native:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if deadline is not None and remaining <= 0 and self._pending.size == 0:
                    return None
                try:
                    frame = self._queue.get(timeout=remaining)
                except queue.Empty:
                    # Timeout con parcial acumulado: se emite igual para no trabar el pipeline.
                    break
                if frame is not None and np.size(frame):
                    self._pending = np.concatenate((self._pending, _to_mono(frame)))
                if not self.running:
                    break
        except Exception as exc:
            raise AudioError(f"Fallo al leer audio en vivo: {exc}") from exc

        if self._pending.size == 0:
            return None
        take = min(self._pending.size, needed_native)
        native = self._pending[:take]
        self._pending = self._pending[take:]
        samples = resample_mono(native, self._native_rate, self._target_rate)
        chunk = AudioChunk(
            samples=np.ascontiguousarray(samples, dtype=np.float32),
            sample_rate=self._target_rate,
            start_time=self._frames_emitted / self._target_rate,
        )
        self._frames_emitted += int(chunk.samples.shape[0])
        return chunk

    # -- internos -----------------------------------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001, ANN202
        if status:
            logger.debug("estado del stream: %s", status)
        try:
            self._queue.put_nowait(np.array(indata, dtype=np.float32, copy=True))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(np.array(indata, dtype=np.float32, copy=True))
            except queue.Full:
                pass

    def _drain_queue(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass


class MicrophoneAudioSource(SystemAudioSource):
    """Captura desde micrófono/entrada (excluye monitores en modo auto)."""

    kind = "microphone"


@dataclass
class _CaptureSession:
    """Sesión funcional para quienes prefieren el estilo procedural."""

    source: SystemAudioSource = field(default_factory=SystemAudioSource)


def start_capture(device: str | int | None = None, **kwargs) -> SystemAudioSource:  # noqa: ANN003
    """Atajo procedural: crea, arranca y devuelve la fuente del sistema."""
    source = SystemAudioSource(device, **kwargs)
    source.start_capture()
    return source


def read_audio_chunk(
    source: SystemAudioSource, timeout: float | None = None
) -> AudioChunk | None:
    """Atajo procedural: lee un fragmento de una fuente ya arrancada."""
    return source.read_audio_chunk(timeout)


def stop_capture(source: SystemAudioSource, timeout: float | None = None) -> None:
    """Atajo procedural: detiene una fuente de forma segura."""
    source.stop_capture(timeout)
