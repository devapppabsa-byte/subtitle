"""Las cuatro interfaces son contratos: no se pueden instanciar incompletas."""

from __future__ import annotations

import pytest

from app.audio.base import AudioSource
from app.speech.base import SpeechRecognizer
from app.subtitles.base import SubtitleRenderer
from app.translation.base import Translator


@pytest.mark.parametrize("interface", [AudioSource, SpeechRecognizer, Translator, SubtitleRenderer])
def test_interfaces_cannot_be_instantiated(interface):
    with pytest.raises(TypeError):
        interface()


def test_incomplete_audio_source_is_rejected():
    class Incomplete(AudioSource):
        pass

    with pytest.raises(TypeError):
        Incomplete()


def test_complete_audio_source_can_be_instantiated():
    class Complete(AudioSource):
        @property
        def sample_rate(self) -> int:
            return 16_000

        def read(self, timeout: float | None = None):
            return None

    assert Complete().sample_rate == 16_000


def test_complete_translator_can_be_instantiated():
    class Complete(Translator):
        def translate(self, text, source_language, target_language):
            raise NotImplementedError

    assert Complete().supported_languages is None


def test_complete_speech_recognizer_can_be_instantiated():
    from queue import Queue

    class Complete(SpeechRecognizer):
        def __init__(self):
            super().__init__()
            self._results = Queue()

        def configure(self, sample_rate, channels):
            pass

        def feed(self, chunk):
            pass

        def flush(self):
            pass

        @property
        def results(self):
            return self._results

    assert Complete().results.empty()


def test_complete_renderer_can_be_instantiated():
    class Complete(SubtitleRenderer):
        def render(self, cue):
            pass

        def clear(self):
            pass

    Complete()  # no lanza
