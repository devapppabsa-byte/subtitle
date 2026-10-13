"""Orquestador del pipeline: audio → voz → traducción → subtítulos.

Modelo de concurrencia (decisión de Fase 0): **hilos + colas**.

* *capture thread* — lee de la ``AudioSource`` y alimenta el reconocedor.
* *dispatch thread* — consume resultados del reconocedor, asigna un
  ``cue_id`` a cada enunciado y decide qué se muestra ya y qué se traduce.
* *translation thread* — traduce los enunciados finales sin bloquear las
  etapas anteriores.

El pipeline **no** llama al renderizador: publica eventos en ``cues`` y el
hilo principal los dibuja. Esa frontera es lo que mantiene a Qt (y a cualquier
UI) fuera de los hilos de trabajo.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue

from app.audio.base import AudioSource
from app.core.events import PipelineEvent, PipelineFinished
from app.core.logging import get_logger
from app.speech.base import SpeechRecognizer
from app.speech.models import RecognitionResult
from app.subtitles.base import SubtitleRenderer
from app.subtitles.models import SubtitleCue
from app.translation.base import Translator

__all__ = ["SubtitlePipeline"]

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
        translate_partials: bool = False,
        emit_partial_cues: bool = True,
        read_timeout: float = 0.1,
    ) -> None:
        self._source = audio_source
        self._recognizer = recognizer
        self._translator = translator
        self._source_language = source_language
        self._target_language = target_language
        self._translate_partials = translate_partials
        self._emit_partial_cues = emit_partial_cues
        self._read_timeout = read_timeout

        #: Eventos para la capa de presentación (``SubtitleCue`` o ``PipelineFinished``).
        self.cues: Queue[PipelineEvent] = Queue()
        self._translation_jobs: Queue[_TranslationJob | object] = Queue()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._cue_index = 0

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

    # -- ciclo de vida ----------------------------------------------------
    def start(self) -> None:
        """Arranca etapas e hilos de trabajo. Idempotente."""
        if self._started:
            return
        self._source.start()
        self._recognizer.configure(self._source.sample_rate, 1)
        self._recognizer.start()
        self._translator.start()

        self._stop_event.clear()
        self._cue_index = 0
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
        """Detiene todo de forma ordenada y espera a los hilos. Idempotente."""
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
                chunk = self._source.read(timeout=self._read_timeout)
                if chunk is None:
                    if not self._source.running:
                        logger.info("fuente de audio agotada")
                        break
                    continue
                self._recognizer.feed(chunk)
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
                self._handle_result(item)
            except Exception:  # pragma: no cover - defensivo
                logger.exception("fallo al procesar un resultado de voz")
        self._translation_jobs.put(_SENTINEL)

    def _translation_loop(self) -> None:
        while True:
            job = self._translation_jobs.get()
            if job is _SENTINEL:
                break
            self._emit_translated(job)
        self.cues.put(PipelineFinished())

    # -- lógica de negocio ------------------------------------------------
    def _handle_result(self, result: RecognitionResult) -> None:
        cue_id = f"u{self._cue_index}"
        if result.is_final:
            self._translation_jobs.put(_TranslationJob(cue_id, result, is_final=True))
            self._cue_index += 1
            return

        if not self._emit_partial_cues:
            return
        if self._translate_partials:
            self._translation_jobs.put(_TranslationJob(cue_id, result, is_final=False))
        else:
            self.cues.put(
                self._build_cue(cue_id, result, text=result.text, is_final=False, source_language=None)
            )

    def _emit_translated(self, job: _TranslationJob) -> None:
        text = job.result.text
        source_language = job.result.language
        try:
            translation = self._translator.translate(
                job.result.text, self._source_language, self._target_language
            )
            text = translation.translated_text
            source_language = translation.source_language
        except Exception:
            logger.exception("la traducción falló; se muestra el texto original")
        self.cues.put(
            self._build_cue(job.cue_id, job.result, text=text, is_final=job.is_final, source_language=source_language)
        )

    def _build_cue(
        self,
        cue_id: str,
        result: RecognitionResult,
        *,
        text: str,
        is_final: bool,
        source_language: str | None,
    ) -> SubtitleCue:
        return SubtitleCue(
            cue_id=cue_id,
            text=text,
            is_final=is_final,
            start_time=result.start_time,
            end_time=result.end_time,
            source_text=result.text,
            source_language=source_language or result.language or self._source_language,
            target_language=self._target_language,
        )
