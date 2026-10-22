"""Orquestador del pipeline: audio → voz → traducción → subtítulos.

Modelo de concurrencia (decisión de Fase 0, endurecido en Fase 5): **hilos + colas ACOTADAS**.

* *capture thread* — lee de la ``AudioSource`` y la entrega vía :meth:`submit`
  (``submit_audio_chunk``); nunca bloquea.
* *dispatch thread* — ``handle_transcription`` consume resultados del
  reconocedor, asigna un ``cue_id`` y descarta parciales obsoletos (stale) de
  cues ya finalizadas.
* *translation thread* — ``schedule_translations`` traduce finales (uno o
  varios idiomas) sin bloquear las etapas anteriores.

Colas acotadas con política *drop-oldest*: ante una racha lenta de red se
descarta lo más viejo (fresco > completo en vivo) y se cuenta en :meth:`stats`.

El pipeline **no** llama al renderizador: publica eventos en ``cues`` y el
hilo principal (o el bridge Qt) los dibuja. La UI nunca hace red.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from queue import Empty, Full, Queue

from app.audio.base import AudioSource
from app.audio.models import AudioChunk
from app.core.events import PipelineEvent, PipelineFinished
from app.core.latency import LatencyTracker
from app.core.logging import get_logger
from app.speech.base import SpeechRecognizer
from app.speech.models import RecognitionResult
from app.subtitles.base import SubtitleRenderer
from app.subtitles.models import SubtitleCue
from app.translation.base import Translator

__all__ = [
    "SubtitlePipeline",
    "start_pipeline",
    "submit_audio_chunk",
    "stop_pipeline",
]

logger = get_logger(__name__)

_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class _TranslationJob:
    cue_id: str
    result: RecognitionResult
    is_final: bool


class SubtitlePipeline:
    """Conecta las cuatro etapas y expone los subtítulos como eventos.

    Las dependencias se inyectan como interfaces, nunca como clases concretas,
    de modo que sustituir el motor de voz o el traductor sea transparente.
    """

    def __init__(
        self,
        *,
        audio_source: AudioSource,
        recognizer: SpeechRecognizer,
        translator: Translator,
        source_language: str = "auto",
        target_language: str = "es",
        target_languages: list[str] | tuple[str, ...] | None = None,
        translate_partials: bool = False,
        emit_partial_cues: bool = True,
        read_timeout: float = 0.1,
        max_cues: int = 64,
        max_translation_jobs: int = 16,
        latency: LatencyTracker | None = None,
    ) -> None:
        self._source = audio_source
        self._source_lock = threading.Lock()
        self._recognizer = recognizer
        self._translator = translator
        self._translator_lock = threading.Lock()
        self._source_language = source_language
        self._target_language = target_language
        multi = [t for t in (target_languages or []) if t.strip()]
        #: Destinos del MISMO fragmento; uno solo por compatibilidad.
        self._target_languages = multi or [target_language]
        self._translate_partials = translate_partials
        self._emit_partial_cues = emit_partial_cues
        self._read_timeout = read_timeout

        #: Eventos para la capa de presentación (``SubtitleCue`` o ``PipelineFinished``).
        #: Cola ACOTADA: llena → se descarta lo más viejo (ver ``_publish``).
        self.cues: Queue[PipelineEvent] = Queue(maxsize=max(1, max_cues))
        self._translation_jobs: Queue[_TranslationJob | object] = Queue(
            maxsize=max(1, max_translation_jobs)
        )
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._cue_index = 0
        self._finalized: set[str] = set()
        self._latency = latency or LatencyTracker()
        self._stats_lock = threading.Lock()
        self._dropped_cues = 0
        self._dropped_translations = 0
        self._stale_partials = 0

    # -- acceso a las dependencias ---------------------------------------
    @property
    def audio_source(self) -> AudioSource:
        return self._source

    @property
    def recognizer(self) -> SpeechRecognizer:
        return self._recognizer

    @property
    def translator(self) -> Translator:
        return self._translator

    def set_source(self, source: AudioSource) -> None:
        """Sustituye la fuente de audio (origen elegido en el panel).

        A diferencia de :meth:`swap_translator`, la fuente se lee en el bucle
        de captura, así que **debe** estar parado el pipeline al llamar esto
        (la UI hace stop → set_source → start).
        """
        if not isinstance(source, AudioSource):
            raise TypeError("set_source espera un AudioSource")
        if self.running:
            raise RuntimeError("set_source requiere el pipeline parado")
        with self._source_lock:
            self._source = source
        logger.info("fuente de audio sustituida: %s", type(source).__name__)

    def swap_translator(self, translator: Translator) -> None:
        """Sustituye el traductor en caliente (proveedor elegido en el panel).

        El hilo de traducción relee ``self._translator`` por cada job, así que
        el cambio aplica al siguiente fragmento. Si el pipeline está corriendo,
        arranca el nuevo y detiene el viejo (con LOCK para no solapar el cierre).
        """
        if not isinstance(translator, Translator):
            raise TypeError("swap_translator espera un Translator")
        with self._translator_lock:
            previous = self._translator
            if previous is translator:
                return
            if self._started:
                translator.start()
            self._translator = translator
        if self._started and previous is not translator:
            try:
                previous.stop()
            except Exception:  # pragma: no cover - cierre defensivo
                logger.exception("fallo al detener el traductor anterior")
        logger.info("traductor cambiado a %s", type(translator).__name__)

    @property
    def target_languages(self) -> list[str]:
        """Destinos configurados (uno o varios del mismo fragmento)."""
        return list(self._target_languages)

    @property
    def latency(self) -> LatencyTracker:
        """Medición por tramos (``dispatch->translating->rendered`` por cue)."""
        return self._latency

    def stats(self) -> dict:
        """Foto de backpressure: pendientes, descartes y progreso."""
        with self._stats_lock:
            dropped_cues = self._dropped_cues
            dropped_translations = self._dropped_translations
            stale_partials = self._stale_partials
        return {
            "running": self._started,
            "pending_cues": self.cues.qsize(),
            "pending_translations": self._translation_jobs.qsize(),
            "dropped_cues": dropped_cues,
            "dropped_translations": dropped_translations,
            "stale_partials_dropped": stale_partials,
            "cues_emitted": self._cue_index,
            "latency": self._latency.summary(),
        }

    # -- API de Fase 5 (especificación) -------------------------------------
    def submit(self, chunk: AudioChunk) -> None:
        """``submit_audio_chunk``: entrega audio al reconocedor sin bloquear."""
        self._recognizer.feed(chunk)

    def handle_transcription(self, result: RecognitionResult) -> str | None:
        """``handle_transcription``: distribuye el texto original.

        Devuelve el ``cue_id`` asignado, o ``None`` si el parcial llegó tarde
        (su cue ya se finalizó) y se descartó.
        """
        return self._handle_result(result)

    def schedule_translations(self, cue_id: str, result: RecognitionResult, *, is_final: bool) -> bool:
        """``schedule_translations``: encola la traducción del fragmento.

        Devuelve ``False`` si la cola estaba llena y se descartó lo más viejo
        para hacer sitio (el descarte queda en ``stats()``).
        """
        return self._enqueue_translation(_TranslationJob(cue_id, result, is_final=is_final))

    # -- ciclo de vida ----------------------------------------------------
    def start(self) -> None:
        """Arranca etapas e hilos de trabajo. Idempotente (``start_pipeline``)."""
        if self._started:
            return
        with self._source_lock:
            source = self._source
        source.start()
        self._recognizer.configure(source.sample_rate, 1)
        self._recognizer.start()
        self._translator.start()

        self._stop_event.clear()
        self._cue_index = 0
        self._finalized = set()
        self._threads = [
            self._spawn(self._capture_loop, "pipeline-capture"),
            self._spawn(self._dispatch_loop, "pipeline-dispatch"),
            self._spawn(self._translation_loop, "pipeline-translation"),
        ]
        self._started = True
        logger.info(
            "pipeline iniciado (origen=%s, reconocedor=%s, traductor=%s)",
            type(self._source).__name__,
            type(self._recognizer).__name__,
            type(self._translator).__name__,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Detiene todo de forma ordenada y espera a los hilos (``stop_pipeline``)."""
        if not self._started:
            return
        self._stop_event.set()
        try:
            self._source.stop(timeout=timeout)
        except Exception:  # pragma: no cover - cierre defensivo
            logger.exception("fallo al cerrar la fuente de audio")
        for thread in self._threads:
            thread.join(timeout=timeout)
        for component in (self._recognizer, self._translator):
            try:
                component.stop(timeout=timeout)
            except Exception:  # pragma: no cover - cierre defensivo
                logger.exception("fallo al cerrar %s", type(component).__name__)
        self._threads = []
        self._started = False
        self._latency.dump()
        logger.info("pipeline detenido")

    @property
    def running(self) -> bool:
        return self._started

    def __enter__(self) -> "SubtitlePipeline":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.stop()
        return False

    # -- bucle para consumidores headless --------------------------------
    def run(
        self,
        renderer: SubtitleRenderer | None = None,
        *,
        timeout: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        """Arranca, dibuja eventos hasta el final y detiene.

        Pensado para ejecución en consola. Una app con GUI reemplazaría este
        bucle por su propio bucle de eventos llamando a ``cues.get``.
        """
        self.start()
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                if self._stop_event.is_set():
                    break
                if stop_event is not None and stop_event.is_set():
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    logger.info("timeout del pipeline alcanzado")
                    break
                try:
                    event = self.cues.get(timeout=0.1)
                except Empty:
                    continue
                if isinstance(event, PipelineFinished):
                    break
                if renderer is not None:
                    renderer.render(event)
        finally:
            self.stop()

    # -- hilos ------------------------------------------------------------
    def _spawn(self, target, name: str) -> threading.Thread:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        return thread

    def _capture_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                with self._source_lock:
                    source = self._source
                chunk = source.read(timeout=self._read_timeout)
                if chunk is None:
                    if self._stop_event.is_set():
                        # Parada ordenada: la fuente se calló, no se agotó.
                        break
                    if not source.running:
                        logger.info("fuente de audio agotada")
                        break
                    continue
                self.submit(chunk)
        except Exception:
            logger.exception("el hilo de captura falló")
        finally:
            # Publicar los finales pendientes y garantizar que los resultados
            # ya están en la cola antes de emitir el centinela.
            try:
                self._recognizer.flush()
            except Exception:
                logger.exception("fallo al vaciar el reconocedor")
            try:
                self._recognizer.stop(timeout=self._read_timeout * 20)
            except Exception:
                logger.exception("fallo al cerrar el reconocedor")
            self._recognizer.results.put(_SENTINEL)

    def _dispatch_loop(self) -> None:
        while True:
            item = self._recognizer.results.get()
            if item is _SENTINEL:
                break
            try:
                self.handle_transcription(item)
            except Exception:  # pragma: no cover - defensivo
                logger.exception("fallo al procesar un resultado de voz")
        self._enqueue_translation(_SENTINEL)

    def _translation_loop(self) -> None:
        while True:
            job = self._translation_jobs.get()
            if job is _SENTINEL:
                break
            self._latency.mark(job.cue_id, "translating")
            self._emit_translated(job)
        self._publish(PipelineFinished())

    # -- colas acotadas ---------------------------------------------------
    def _publish(self, event: PipelineEvent) -> None:
        """Publica en ``cues``; llena → descarta lo más viejo y cuenta."""
        try:
            self.cues.put_nowait(event)
        except Full:
            try:
                self.cues.get_nowait()
            except Empty:
                pass
            with self._stats_lock:
                self._dropped_cues += 1
            try:
                self.cues.put_nowait(event)
            except Full:  # pragma: no cover - carrera extrema
                pass

    def _enqueue_translation(self, job: _TranslationJob | object) -> bool:
        """Encola trabajo de traducción; llena → descarta lo más viejo.

        Devuelve ``True`` si hubo que descartar para hacer sitio.
        """
        try:
            self._translation_jobs.put_nowait(job)
            return False
        except Full:
            try:
                self._translation_jobs.get_nowait()
            except Empty:
                pass
            with self._stats_lock:
                self._dropped_translations += 1
            try:
                self._translation_jobs.put_nowait(job)
            except Full:  # pragma: no cover - carrera extrema
                pass
            return True

    # -- lógica de negocio ------------------------------------------------
    def _handle_result(self, result: RecognitionResult) -> str | None:
        cue_id = f"u{self._cue_index}"
        if result.is_final:
            self._finalized.add(cue_id)
            self._latency.mark(cue_id, "dispatch")
            self.schedule_translations(cue_id, result, is_final=True)
            self._cue_index += 1
            return cue_id

        if cue_id in self._finalized:
            # Parcial tardío: su cue ya se publicó como final, no debe pisarla.
            with self._stats_lock:
                self._stale_partials += 1
            logger.debug("parcial obsoleto descartado (%s)", cue_id)
            return None
        if not self._emit_partial_cues:
            return None
        if self._translate_partials:
            self.schedule_translations(cue_id, result, is_final=False)
            return cue_id
        self._publish(
            self._build_cue(cue_id, result, text=result.text, is_final=False, source_language=None)
        )
        return cue_id

    def _emit_translated(self, job: _TranslationJob) -> None:
        if len(self._target_languages) == 1:
            self._emit_single(job, self._target_languages[0], job.cue_id)
            return
        try:
            translated = self._translator.translate_many(
                job.result.text, self._source_language, self._target_languages
            )
        except Exception:
            logger.exception("el fan-out de traducción falló; se muestra el original")
            translated = {}
        for target in self._target_languages:
            result = translated.get(target)
            if result is None:
                try:
                    result = self._translator.translate(job.result.text, self._source_language, target)
                except Exception:
                    logger.exception("traducción a %s falló; se muestra el original", target)
                    result = None
            self._emit_result(job, target, f"{job.cue_id}:{target}", result)

    def _emit_single(self, job: _TranslationJob, target: str, cue_id: str) -> None:
        try:
            result = self._translator.translate(job.result.text, self._source_language, target)
        except Exception:
            logger.exception("la traducción falló; se muestra el texto original")
            result = None
        self._emit_result(job, target, cue_id, result)

    def _emit_result(self, job: _TranslationJob, target: str, cue_id: str, result) -> None:
        if result is None:
            text: str = job.result.text
            source_language = job.result.language
        else:
            text = result.translated_text
            source_language = result.source_language
        self._latency.mark(cue_id, "rendered")
        self._publish(
            self._build_cue(
                cue_id, job.result, text=text, is_final=job.is_final,
                source_language=source_language, target_language=target,
            )
        )

    def _build_cue(
        self,
        cue_id: str,
        result: RecognitionResult,
        *,
        text: str,
        is_final: bool,
        source_language: str | None,
        target_language: str | None = None,
    ) -> SubtitleCue:
        return SubtitleCue(
            cue_id=cue_id,
            text=text,
            is_final=is_final,
            start_time=result.start_time,
            end_time=result.end_time,
            source_text=result.text,
            source_language=source_language or result.language or self._source_language,
            target_language=target_language or self._target_language,
        )


# ---------------------------------------------------------------------------
# Atajos procedurales (especificación Fase 5)
# ---------------------------------------------------------------------------
def start_pipeline(pipeline: "SubtitlePipeline") -> "SubtitlePipeline":
    """Inicia el procesamiento. Equivale a :meth:`SubtitlePipeline.start`."""
    pipeline.start()
    return pipeline


def submit_audio_chunk(pipeline: "SubtitlePipeline", chunk: AudioChunk) -> None:
    """Pone audio en la cola sin bloquear. Equivale a :meth:`SubtitlePipeline.submit`."""
    pipeline.submit(chunk)


def stop_pipeline(pipeline: "SubtitlePipeline", timeout: float = 5.0) -> None:
    """Cierra los procesos y libera recursos. Equivale a :meth:`SubtitlePipeline.stop`."""
    pipeline.stop(timeout=timeout)
