"""Reconocimiento de voz con Groq API (Whisper).

Fase 3: convertir el audio capturado en texto original + idioma, en UNA sola
llamada por ventana. Groq expone ``/audio/transcriptions`` por archivo (sin
streaming real), por lo que este reconocedor acumula ventanas batch (~10 s) y
las transcribe en un *worker thread*.

Correspondencia con la especificación de Fase 3::

    transcribe_audio(audio_chunk)        → :func:`transcribe_audio` (una llamada, trae texto+idioma)
    detect_language(audio_chunk)         → :func:`detect_language` (lee el idioma de la respuesta,
                                           sin retranscribir; ver docstring)
    normalize_transcription(text)        → :func:`normalize_transcription`
    process_transcription_result(result) → :func:`process_transcription_result`
"""

from __future__ import annotations

import io
import threading
import time
import wave
from dataclasses import dataclass
from queue import Empty, Full, Queue
from typing import Any

import numpy as np

from app.audio.models import AudioChunk
from app.core.errors import ConfigurationError, SpeechError
from app.core.logging import get_logger
from app.speech.base import SpeechRecognizer
from app.speech.models import RecognitionResult, Word

__all__ = [
    "DEFAULT_MODEL",
    "FALLBACK_MODEL",
    "GroqSpeechRecognizer",
    "TranscriptionPayload",
    "detect_language",
    "encode_wav",
    "normalize_transcription",
    "process_transcription_result",
    "strip_overlap_prefix",
    "transcribe_audio",
]

logger = get_logger(__name__)

DEFAULT_MODEL = "whisper-large-v3-turbo"
FALLBACK_MODEL = "whisper-large-v3"

_INT16_MAX = 32_768.0


# ---------------------------------------------------------------------------
# Capa servicio: funciones puras / de red (especificación Fase 3)
# ---------------------------------------------------------------------------
def normalize_transcription(text: str) -> str:
    """Limpia espacios y normaliza el texto (conserva el caso original)."""
    return " ".join(str(text or "").split())


@dataclass(frozen=True, slots=True)
class TranscriptionPayload:
    """Texto + idioma + tiempos extraídos de UNA respuesta de Groq."""

    text: str
    language: str | None
    start_time: float
    end_time: float
    words: tuple[Word, ...] = ()


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def process_transcription_result(
    result: Any, *, fallback_language: str | None = None
) -> TranscriptionPayload:
    """Extrae texto, idioma y marcas de tiempo de la respuesta de Groq.

    Acepta el objeto del SDK o un dict equivalente (``verbose_json``).
    """
    text = normalize_transcription(str(_get(result, "text", default="") or ""))
    language = _get(result, "language", default=None) or fallback_language

    words: list[Word] = []
    raw_words = _get(result, "words", default=None) or ()
    try:
        for item in raw_words:
            wtext = normalize_transcription(str(_get(item, "word", "text", default="") or ""))
            if not wtext:
                continue
            start = float(_get(item, "start", "start_time", default=0.0) or 0.0)
            end = float(_get(item, "end", "end_time", default=start) or start)
            words.append(Word(text=wtext, start_time=start, end_time=end))
    except (TypeError, ValueError) as exc:
        logger.debug("palabras de Groq no parseables, se ignoran: %s", exc)

    start_time = float(_get(result, "start_time", default=0.0) or 0.0)
    end_time = float(_get(result, "end_time", "duration", default=start_time) or start_time)
    segments = _get(result, "segments", default=None) or ()
    try:
        if segments and end_time <= start_time:
            last = segments[-1] if isinstance(segments, (list, tuple)) else None
            if last is not None:
                end_time = float(_get(last, "end", "end_time", default=end_time) or end_time)
    except (TypeError, ValueError, IndexError):
        pass
    return TranscriptionPayload(
        text=text, language=language, start_time=start_time, end_time=end_time, words=tuple(words)
    )


def detect_language(result: Any, *, fallback: str | None = None) -> str | None:
    """Devuelve el idioma YA devuelto por la transcripción, sin segunda llamada.

    Recibe la **respuesta** de :func:`transcribe_audio`, no el audio: volver a
    transcribir solo para detectar el idioma duplicaría costo y latencia.
    """
    return _get(result, "language", default=None) or fallback


def strip_overlap_prefix(text: str, previous: str, *, max_words: int = 20) -> str:
    """Recorta el prefijo ya transcrito por el solape entre ventanas.

    Busca el sufijo más largo de ``previous`` (en palabras, insensible a caso)
    que coincida con el prefijo de ``text`` y lo elimina. Si todo ``text`` ya
    salió, devuelve ``""`` para no duplicar cues.
    """
    current = text.split()
    if not current or not previous.strip():
        return text
    prev = previous.split()[-max_words:]
    lowered_prev = [w.lower() for w in prev]
    lowered_cur = [w.lower() for w in current[:max_words]]
    overlap = 0
    for size in range(min(len(lowered_prev), len(lowered_cur)), 0, -1):
        if lowered_prev[-size:] == lowered_cur[:size]:
            overlap = size
            break
    return " ".join(current[overlap:]) if overlap else text


def encode_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """Codifica mono float32 [-1, 1] a WAV PCM16 en memoria."""
    mono = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate <= 0:
        raise SpeechError(f"sample_rate inválido para WAV: {sample_rate}")
    pcm16 = np.clip(mono * _INT16_MAX, -_INT16_MAX, _INT16_MAX - 1).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(int(sample_rate))
        writer.writeframes(pcm16.tobytes())
    return buffer.getvalue()


def _create_client(api_key: str | None):
    try:
        from groq import Groq
    except ImportError as exc:
        raise SpeechError("Falta el SDK 'groq>=0.9' para el motor de voz Groq.") from exc
    if not api_key:
        raise ConfigurationError(
            "Falta la clave de Groq: exportá GROQ_API_KEY o configurá speech.api_key_env."
        )
    return Groq(api_key=api_key)


def resolve_model(client: Any, preferred: str) -> str:
    """Comprueba el modelo contra la cuenta; cae al alternativo si falta."""
    try:
        ids = {m.id for m in client.models.list().data}
    except Exception as exc:
        logger.warning("no se pudo listar modelos Groq, se usa %r: %s", preferred, exc)
        return preferred
    if preferred in ids:
        return preferred
    if FALLBACK_MODEL in ids and preferred != FALLBACK_MODEL:
        logger.warning("modelo %r no disponible en la cuenta; se usa %r", preferred, FALLBACK_MODEL)
        return FALLBACK_MODEL
    if preferred not in ids:
        logger.warning("modelo %r no figura en la cuenta; se intenta igual", preferred)
    return preferred


def transcribe_audio(
    wav_bytes: bytes,
    *,
    client: Any,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    timeout: float = 60.0,
) -> Any:
    """Envía UN fragmento WAV a Groq y devuelve la respuesta (texto+idioma).

    Una sola llamada trae texto, idioma y tiempos (``verbose_json``): no
    llamar dos veces para idioma y texto.
    """
    if not wav_bytes:
        raise SpeechError("Audio vacío: nada que transcribir.")
    kwargs: dict[str, Any] = {
        "file": ("audio.wav", wav_bytes),
        "model": model,
        "response_format": "verbose_json",
        "temperature": 0.0,
        "timestamp_granularities": ["word", "segment"],
        "timeout": timeout,
    }
    if language and language != "auto":
        kwargs["language"] = language
    try:
        return client.audio.transcriptions.create(**kwargs)
    except Exception as exc:
        raise SpeechError(f"Transcripción Groq falló ({model}): {exc}") from exc


# ---------------------------------------------------------------------------
# Reconocedor para el pipeline (ventanas batch)
# ---------------------------------------------------------------------------
@dataclass
class _TranscriptionJob:
    samples: np.ndarray
    start_time: float
    duration: float


class GroqSpeechRecognizer(SpeechRecognizer):
    """Acumula ventanas de audio y las transcribe con Groq en un worker.

    Emite solo resultados finales (``is_final=True``); los parciales en vivo
    no existen en la API por archivo. ``feed`` nunca bloquea.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        language: str | None = None,
        window_seconds: float = 10.0,
        window_overlap_seconds: float = 0.0,
        min_seconds: float = 3.0,
        request_timeout: float = 60.0,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        super().__init__()
        if window_seconds <= 0 or min_seconds <= 0:
            raise ConfigurationError("window_seconds y min_seconds deben ser positivos")
        if window_overlap_seconds < 0 or window_overlap_seconds >= window_seconds:
            raise ConfigurationError("window_overlap_seconds debe estar en [0, window_seconds)")
        self._api_key = api_key
        self._preferred_model = model or DEFAULT_MODEL
        self._model = self._preferred_model
        self._language = language
        self._window_seconds = float(window_seconds)
        self._overlap_seconds = float(window_overlap_seconds)
        self._min_seconds = float(min_seconds)
        self._request_timeout = float(request_timeout)
        self._max_retries = max(0, int(max_retries))
        self._client = client
        self._results: Queue[RecognitionResult] = Queue(maxsize=32)
        self._jobs: Queue[_TranscriptionJob | object] = Queue(maxsize=8)
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._sample_rate = 16_000
        self._buffer = np.zeros(0, dtype=np.float32)
        self._buffer_start = 0.0
        self._stream_time = 0.0
        self._dropped_windows = 0
        self._last_text = ""
        self._lock = threading.Lock()

    # -- SpeechRecognizer -------------------------------------------------
    @property
    def results(self) -> Queue[RecognitionResult]:
        return self._results

    @property
    def model(self) -> str:
        """Modelo efectivo (tras verificación contra la cuenta en ``start``)."""
        return self._model

    def configure(self, sample_rate: int, channels: int) -> None:
        self._sample_rate = int(sample_rate)

    def feed(self, chunk: AudioChunk) -> None:
        job: _TranscriptionJob | None = None
        with self._lock:
            if self._buffer.size == 0:
                self._buffer_start = chunk.start_time
            self._buffer = np.concatenate((self._buffer, chunk.samples.astype(np.float32)))
            self._stream_time = chunk.start_time + chunk.duration
            buffered = float(self._buffer.size / self._sample_rate)
            if buffered >= self._window_seconds:
                job = _TranscriptionJob(
                    samples=self._buffer,
                    start_time=self._buffer_start,
                    duration=buffered,
                )
                # Solape: se retiene la cola para no partir palabras del borde.
                keep = int(round(self._overlap_seconds * self._sample_rate))
                if 0 < keep < self._buffer.size:
                    self._buffer = self._buffer[-keep:]
                    self._buffer_start = self._buffer_start + buffered - self._overlap_seconds
                else:
                    self._buffer = np.zeros(0, dtype=np.float32)
        if job is not None:
            self._submit_job(job)

    def _submit_job(self, job: _TranscriptionJob | object, *, critical: bool = False) -> None:
        """Encola sin bloquear jamás (ni siquiera en ``stop``).

        Llena → descarta lo más viejo. El centinela (``critical``) siempre
        entra porque puede desalojar datos: garantiza la terminación.
        """
        while True:
            try:
                self._jobs.put_nowait(job)
                return
            except Full:
                try:
                    dropped = self._jobs.get_nowait()
                    if dropped is _SENTINEL:
                        # Nunca perder un centinela previo: se repone y reintenta.
                        self._jobs.put_nowait(dropped)
                        continue
                except Empty:
                    pass
                if not critical:
                    self._dropped_windows += 1
                    logger.warning("ventana de voz descartada por backpressure")
                    return
                # critical: insiste hasta que el centinela entre.

    def _drain_queue(self) -> None:
        try:
            while True:
                self._jobs.get_nowait()
        except Empty:
            pass

    def flush(self) -> None:
        with self._lock:
            buffered = float(self._buffer.size / self._sample_rate)
            if self._buffer.size == 0 or buffered < self._min_seconds:
                return
            job = _TranscriptionJob(
                samples=self._buffer, start_time=self._buffer_start, duration=buffered
            )
            self._buffer = np.zeros(0, dtype=np.float32)
        self._submit_job(job)

    # -- ciclo de vida ----------------------------------------------------
    def _on_start(self) -> None:
        if self._client is None:
            self._client = _create_client(self._api_key)
        self._model = resolve_model(self._client, self._preferred_model)
        with self._lock:
            # Sesión limpia: sin restos de audio ni dedupe de una corrida previa.
            self._buffer = np.zeros(0, dtype=np.float32)
            self._buffer_start = 0.0
            self._stream_time = 0.0
            self._last_text = ""
            self._drain_queue()
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop, name="groq-speech", daemon=True)
        self._worker.start()
        logger.info("reconocedor Groq iniciado (modelo=%s, ventana=%.0fs)", self._model, self._window_seconds)

    def _on_stop(self, timeout: float | None) -> None:
        self.flush()
        self._submit_job(_SENTINEL, critical=True)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.join(timeout=timeout)
        self._stop_event.set()

    # -- worker -----------------------------------------------------------
    def _worker_loop(self) -> None:
        while True:
            try:
                job = self._jobs.get(timeout=0.1)
            except Empty:
                if self._stop_event.is_set():
                    return
                continue
            if job is _SENTINEL:
                return
            assert isinstance(job, _TranscriptionJob)
            self._transcribe_job(job)

    def _transcribe_job(self, job: _TranscriptionJob) -> None:
        from app.core.retry import with_retries

        try:
            wav_bytes = encode_wav(job.samples, self._sample_rate)
        except SpeechError:
            logger.exception("no se pudo codificar la ventana de audio")
            return
        started = time.monotonic()
        try:
            response = with_retries(
                lambda: transcribe_audio(
                    wav_bytes,
                    client=self._client,
                    model=self._model,
                    language=self._language,
                    timeout=self._request_timeout,
                ),
                max_retries=self._max_retries,
                what="transcripción",
            )
        except SpeechError:
            logger.exception("ventana %.1fs descartada por fallo de API", job.duration)
            return
        latency = time.monotonic() - started
        payload = process_transcription_result(response, fallback_language=self._language)
        language = detect_language(response, fallback=self._language)
        if not payload.text:
            logger.info("ventana %.1fs sin voz (%.2fs de API)", job.duration, latency)
            return
        text = strip_overlap_prefix(payload.text, self._last_text)
        self._last_text = payload.text
        if not text:
            logger.info("ventana %.1fs totalmente solapada, sin texto nuevo", job.duration)
            return
        logger.info(
            "transcript %.1fs → %r [%s] (latencia %.2fs)",
            job.duration,
            text,
            language,
            latency,
        )
        self._results.put(
            RecognitionResult(
                text=text,
                is_final=True,
                start_time=job.start_time,
                end_time=job.start_time + job.duration,
                language=language or payload.language,
                words=payload.words,
            )
        )


_SENTINEL: Any = object()
