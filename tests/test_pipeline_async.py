"""Fase 5: motor asíncrono (cotas, anti-stale, bridge Qt)."""

from __future__ import annotations

import threading
import types
from queue import Queue

import pytest

from app.audio.synthetic import SyntheticAudioSource
from app.core.events import PipelineFinished
from app.core.pipeline import (
    SubtitlePipeline,
    start_pipeline,
    stop_pipeline,
    submit_audio_chunk,
)
from app.speech.models import RecognitionResult
from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance
from app.subtitles.models import SubtitleCue
from app.translation.base import Translator
from app.translation.mapping import MappingTranslator
from app.translation.passthrough import PassthroughTranslator

from conftest import CollectingRenderer, make_chunk


def _final(text="hello world", start=0.0, end=0.5):
    return RecognitionResult(text=text, is_final=True, start_time=start, end_time=end,
                             language="en")


def _partial(text="hello", start=0.0, end=0.5):
    return RecognitionResult(text=text, is_final=False, start_time=start, end_time=end,
                             language="en")


def _pipeline(**kwargs):
    kwargs.setdefault("audio_source", SyntheticAudioSource(total_duration=1.0))
    kwargs.setdefault("recognizer", ScriptedSpeechRecognizer([]))
    kwargs.setdefault("translator", PassthroughTranslator())
    kwargs.setdefault("source_language", "en")
    kwargs.setdefault("target_language", "es")
    return SubtitlePipeline(**kwargs)


def _cue(cue_id, text, start, final=True, target="es"):
    return SubtitleCue(cue_id=cue_id, text=text, is_final=final, start_time=start,
                       end_time=start + 0.5, source_text=text, target_language=target)


# -- colas acotadas ----------------------------------------------------------
def test_publish_drops_oldest_and_counts():
    pipeline = _pipeline(max_cues=2)
    for i in range(5):
        pipeline._publish(_cue(f"u{i}", f"t{i}", float(i)))
    assert pipeline.cues.qsize() == 2
    assert pipeline.stats()["dropped_cues"] == 3
    assert pipeline.stats()["pending_cues"] == 2


def test_enqueue_translation_drops_oldest_and_counts():
    from app.core.pipeline import _TranslationJob

    pipeline = _pipeline(max_translation_jobs=2)
    assert pipeline.schedule_translations("u0", _final(), is_final=True) is False
    assert pipeline.schedule_translations("u1", _final(), is_final=True) is False
    assert pipeline.schedule_translations("u2", _final(), is_final=True) is True
    assert pipeline.stats()["dropped_translations"] == 1


def test_slow_translator_does_not_hang_or_grow():
    import time

    class SlowTranslator(Translator):
        def translate(self, text, source_language, target_language):
            time.sleep(0.05)
            return PassthroughTranslator().translate(text, source_language, target_language)

    renderer = CollectingRenderer()
    pipeline = SubtitlePipeline(
        audio_source=SyntheticAudioSource(chunk_duration=0.05, total_duration=1.0),
        recognizer=ScriptedSpeechRecognizer(
            [ScriptedUtterance(f"phrase {i}", i * 0.1, i * 0.1 + 0.05, language="en")
             for i in range(10)]
        ),
        translator=SlowTranslator(),
        source_language="en",
        target_language="es",
        max_cues=8,
        max_translation_jobs=4,
    )
    pipeline.run(renderer, timeout=15)
    stats = pipeline.stats()
    assert stats["pending_cues"] == 0
    assert stats["pending_translations"] == 0
    assert [c.text for c in renderer.cues if c.is_final]


# -- anti-stale --------------------------------------------------------------
def test_late_partial_does_not_overwrite_final():
    pipeline = _pipeline()
    assert pipeline.handle_transcription(_final()) == "u0"
    pipeline._cue_index = 0  # simula entrega desordenada del parcial tardío de u0
    assert pipeline.handle_transcription(_partial()) is None
    assert pipeline.stats()["stale_partials_dropped"] == 1


def test_finals_keep_monotonic_order_with_fan_out():
    renderer = CollectingRenderer()
    pipeline = SubtitlePipeline(
        audio_source=SyntheticAudioSource(total_duration=1.0),
        recognizer=ScriptedSpeechRecognizer(
            [
                ScriptedUtterance("hello world", 0.0, 0.4, language="en"),
                ScriptedUtterance("the subtitles are working", 0.5, 0.9, language="en"),
            ]
        ),
        translator=MappingTranslator(
            {"hello world": "hola mundo", "the subtitles are working": "funcionan"},
            source_language="en",
        ),
        source_language="en",
        target_languages=["es", "fr"],
    )
    pipeline.run(renderer, timeout=10)
    finals = [c for c in renderer.cues if c.is_final]
    starts = [c.start_time for c in finals]
    assert starts == sorted(starts)
    assert [c.cue_id for c in finals] == ["u0:es", "u0:fr", "u1:es", "u1:fr"]


# -- ciclo de vida -----------------------------------------------------------
def test_procedural_aliases():
    pipeline = _pipeline()
    start_pipeline(pipeline)
    assert pipeline.running is True
    submit_audio_chunk(pipeline, make_chunk())
    stop_pipeline(pipeline)
    assert pipeline.running is False


def test_stop_joins_threads_and_keeps_last_final():
    renderer = CollectingRenderer()
    pipeline = SubtitlePipeline(
        audio_source=SyntheticAudioSource(chunk_duration=0.5, total_duration=1.0),
        recognizer=ScriptedSpeechRecognizer(
            [ScriptedUtterance("hello world", 0.0, 0.5, language="en")]
        ),
        translator=MappingTranslator({"hello world": "hola mundo"}),
        source_language="en",
        target_language="es",
    )
    pipeline.run(renderer, timeout=10)
    assert [c.text for c in renderer.cues if c.is_final] == ["hola mundo"]
    leftovers = [t for t in threading.enumerate() if t.name.startswith("pipeline-")]
    assert leftovers == []
    assert pipeline.stats()["cues_emitted"] == 1


# -- bridge Qt (sin red, sin event loop) -------------------------------------
def test_bridge_emits_in_order_and_ignores_stale():
    from app.ui.bridge import PipelineBridge

    renderer = CollectingRenderer()
    fake = types.SimpleNamespace(cues=Queue())
    bridge = PipelineBridge(fake, renderer)
    fake.cues.put(_cue("u0", "primero", 0.0))
    fake.cues.put(_cue("u1", "segundo", 2.0))
    fake.cues.put(_cue("uX", "tardío", 1.0))  # fuera de orden → se ignora
    fake.cues.put(PipelineFinished())
    bridge._drain()
    assert [c.text for c in renderer.cues] == ["primero", "segundo"]
    assert bridge.stale_ignored == 1


def test_bridge_start_stop_lifecycle():
    from app.ui.bridge import PipelineBridge

    renderer = CollectingRenderer()
    pipeline = _pipeline()
    bridge = PipelineBridge(pipeline, renderer)
    bridge.start()
    bridge.stop()
    assert pipeline.running is False


def test_bridge_emits_finished_once():
    """Dos centinelas en cola no re-emiten ``finished`` (bucle de reinicio)."""
    from app.ui.bridge import PipelineBridge

    fake = types.SimpleNamespace(cues=Queue())
    bridge = PipelineBridge(fake, None)
    seen: list[str] = []
    bridge.finished.connect(seen.append)
    fake.cues.put(_cue("u0", "hola", 0.0))
    fake.cues.put(PipelineFinished("completed"))
    fake.cues.put(PipelineFinished("completed"))  # centinela extra del cierre
    bridge._drain()
    assert seen == ["completed"]
    assert bridge._finished_emitted is True


def test_swap_translator_hot_swaps_while_running():
    from app.core.pipeline import SubtitlePipeline
    from app.audio.synthetic import SyntheticAudioSource
    from app.speech.scripted import ScriptedSpeechRecognizer
    from app.translation.passthrough import PassthroughTranslator

    pipe = SubtitlePipeline(
        audio_source=SyntheticAudioSource(total_duration=5.0),
        recognizer=ScriptedSpeechRecognizer([]),
        translator=PassthroughTranslator(),
        source_language="en",
        target_languages=["es"],
    )
    pipe.start()
    try:
        new = PassthroughTranslator()
        pipe.swap_translator(new)
        assert pipe.translator is new
        assert pipe.running is True
    finally:
        pipe.stop()


def test_swap_translator_ignores_same_instance():
    from app.core.pipeline import SubtitlePipeline
    from app.audio.synthetic import SyntheticAudioSource
    from app.speech.scripted import ScriptedSpeechRecognizer
    from app.translation.passthrough import PassthroughTranslator

    tr = PassthroughTranslator()
    pipe = SubtitlePipeline(
        audio_source=SyntheticAudioSource(total_duration=1.0),
        recognizer=ScriptedSpeechRecognizer([]),
        translator=tr,
        source_language="en",
        target_languages=["es"],
    )
    pipe.swap_translator(tr)
    assert pipe.translator is tr
