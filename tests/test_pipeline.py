"""El pipeline completo, y la prueba de que solo depende de las interfaces."""

from __future__ import annotations

from queue import Queue

from app.audio.synthetic import SyntheticAudioSource
from app.core.events import PipelineFinished
from app.core.pipeline import SubtitlePipeline
from app.speech.base import SpeechRecognizer
from app.speech.models import RecognitionResult
from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance
from app.translation.base import Translator
from app.translation.mapping import MappingTranslator
from app.translation.passthrough import PassthroughTranslator

from conftest import CollectingRenderer

TRANSLATIONS = {
    "hello world": "hola mundo",
    "the subtitles are working": "los subtítulos funcionan",
}


def _scripted_pipeline(translator: Translator, *, total_duration: float = 5.0) -> SubtitlePipeline:
    source = SyntheticAudioSource(chunk_duration=0.5, total_duration=total_duration)
    recognizer = ScriptedSpeechRecognizer(
        [
            ScriptedUtterance("hello world", 0.5, 2.0, language="en"),
            ScriptedUtterance("the subtitles are working", 2.5, 4.0, language="en"),
        ]
    )
    return SubtitlePipeline(
        audio_source=source,
        recognizer=recognizer,
        translator=translator,
        source_language="en",
        target_language="es",
    )


class ImmediateRecognizer(SpeechRecognizer):
    """Reconocedor de prueba: publica sus frases al hacer flush."""

    def __init__(self, phrases: list[str]) -> None:
        super().__init__()
        self._phrases = phrases
        self._results: Queue[RecognitionResult] = Queue()

    def configure(self, sample_rate: int, channels: int) -> None:
        pass

    def feed(self, chunk) -> None:
        pass

    def flush(self) -> None:
        for index, phrase in enumerate(self._phrases):
            self._results.put(
                RecognitionResult(text=phrase, is_final=True, start_time=index, end_time=index + 1, language="en")
            )

    @property
    def results(self) -> Queue[RecognitionResult]:
        return self._results


class FailingTranslator(Translator):
    def translate(self, text, source_language, target_language):
        raise RuntimeError("backend caído")


def test_pipeline_produces_partial_and_final_cues():
    renderer = CollectingRenderer()
    pipeline = _scripted_pipeline(MappingTranslator(TRANSLATIONS, source_language="en"))
    pipeline.run(renderer, timeout=10)

    cues = renderer.cues
    assert cues, "el pipeline debería haber publicado subtítulos"
    assert all(not isinstance(c, PipelineFinished) for c in cues)

    finals = [c for c in cues if c.is_final]
    assert [c.text for c in finals] == ["hola mundo", "los subtítulos funcionan"]

    # El parcial y el final del primer enunciado comparten cue_id.
    first_id = finals[0].cue_id
    partials_first = [c for c in cues if not c.is_final and c.cue_id == first_id]
    assert partials_first, "debería haber al menos un parcial para el primer enunciado"
    assert partials_first[0].text != finals[0].text  # el parcial aún no está traducido
    assert finals[0].source_text == "hello world"


def test_swapping_the_translator_does_not_touch_the_pipeline():
    passthrough = CollectingRenderer()
    _scripted_pipeline(PassthroughTranslator()).run(passthrough, timeout=10)

    mapping = CollectingRenderer()
    _scripted_pipeline(MappingTranslator(TRANSLATIONS, source_language="en")).run(mapping, timeout=10)

    assert [c.text for c in passthrough.cues if c.is_final] == [
        "hello world",
        "the subtitles are working",
    ]
    assert [c.text for c in mapping.cues if c.is_final] == [
        "hola mundo",
        "los subtítulos funcionan",
    ]


def test_pipeline_accepts_any_recognizer_implementation():
    pipeline = SubtitlePipeline(
        audio_source=SyntheticAudioSource(chunk_duration=0.5, total_duration=1.0),
        recognizer=ImmediateRecognizer(["hello world"]),
        translator=MappingTranslator(TRANSLATIONS, source_language="en"),
        source_language="en",
        target_language="es",
    )
    renderer = CollectingRenderer()
    pipeline.run(renderer, timeout=10)
    assert [c.text for c in renderer.cues if c.is_final] == ["hola mundo"]


def test_translation_failure_falls_back_to_source_text():
    renderer = CollectingRenderer()
    pipeline = _scripted_pipeline(FailingTranslator())
    pipeline.run(renderer, timeout=10)
    finals = [c.text for c in renderer.cues if c.is_final]
    assert finals == ["hello world", "the subtitles are working"]


def test_stop_is_safe_and_idempotent():
    pipeline = _scripted_pipeline(PassthroughTranslator())
    pipeline.start()
    pipeline.stop()
    pipeline.stop()
    assert pipeline.running is False
