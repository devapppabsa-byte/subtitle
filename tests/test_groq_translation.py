"""Fase 4: traducción Groq con cliente falsificado (sin red ni clave)."""

from __future__ import annotations

import pytest

from app.core.errors import ConfigurationError, TranslationError
from app.translation.groq import (
    ContextBuffer,
    GroqTranslator,
    build_translation_prompt,
    normalize_language,
    preserve_context,
    translate_text,
    translate_to_languages,
)


class _FakeCompletions:
    def __init__(self, parent):
        self._parent = parent

    def create(self, **kwargs):
        self._parent.calls.append(kwargs)
        target = self._parent.target_of(kwargs)
        if target in self._parent.fail_targets:
            raise RuntimeError(f"fallo simulado en {target}")
        return _FakeResponse(self._parent.answers.get(target, f"[{target}]"))


class _FakeResponse:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    """Imita client.chat.completions.create + client.models.list."""

    def __init__(self, answers=None, fail_targets=(), models=("openai/gpt-oss-20b",)):
        self.answers = dict(answers or {})
        self.fail_targets = set(fail_targets)
        self.calls: list[dict] = []
        self._models = list(models)
        self.chat = type("Chat", (), {"completions": _FakeCompletions(self)})()

    @staticmethod
    def target_of(kwargs):
        import re

        for msg in kwargs["messages"]:
            if msg["role"] == "system":
                match = re.search(r" to ([A-Za-z]+)\.", msg["content"])
                if match:
                    return match.group(1)
        return "?"

    class _M:
        def __init__(self, id):
            self.id = id

    @property
    def models(self):
        parent = self

        class _Models:
            def list(self):
                return type("L", (), {"data": [_FakeClient._M(i) for i in parent._models]})()

        return _Models()


def test_normalize_language():
    assert normalize_language("Spanish") == "es"
    assert normalize_language("EN") == "en"
    assert normalize_language(None) == "auto"
    assert normalize_language("auto") == "auto"
    assert normalize_language("de") == "de"


def test_prompt_prohibe_explicaciones_e_incluye_contexto():
    messages = build_translation_prompt(
        "hello world",
        source_language="en",
        target_language="es",
        context=[("good morning", "buenos días")],
    )
    assert messages[0]["role"] == "system"
    assert "ONLY" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "Translate: hello world"}
    assert {"role": "assistant", "content": "buenos días"} in messages


def test_translate_text_single_call():
    client = _FakeClient(answers={"Spanish": "hola mundo"})
    result = translate_text("hello world", "en", "es", client=client)
    assert result.translated_text == "hola mundo"
    assert result.source_language == "en"
    assert result.target_language == "es"
    assert len(client.calls) == 1
    assert client.calls[0]["temperature"] == 0.0


def test_translate_same_language_passthrough():
    client = _FakeClient()
    result = translate_text("hola", "es", "es", client=client)
    assert result.translated_text == "hola"
    assert client.calls == []


def test_translate_empty_raises():
    with pytest.raises(TranslationError):
        translate_text("  ", "en", "es", client=_FakeClient())


def test_translate_api_error_wraps():
    client = _FakeClient(fail_targets={"Spanish"})
    with pytest.raises(TranslationError):
        translate_text("hello", "en", "es", client=client)


def test_translate_to_languages_fan_out_with_isolation():
    client = _FakeClient(
        answers={"Spanish": "hola", "French": "bonjour", "German": "hallo"},
        fail_targets={"French"},
    )
    results = translate_to_languages("hello", "en", ["es", "fr", "de"], client=client)
    assert set(results) == {"es", "fr", "de"}
    assert results["es"].translated_text == "hola"
    assert results["de"].translated_text == "hallo"
    # El idioma fallido cae al original sin bloquear a los demás.
    assert results["fr"].translated_text == "hello"


def test_translate_many_never_reprocesses_audio():
    """El fan-out parte del MISMO texto; el audio no se toca aquí."""
    client = _FakeClient(answers={"Spanish": "hola", "French": "bonjour"})
    results = translate_to_languages("hello", "en", ["es", "fr"], client=client)
    assert len(client.calls) == 2
    assert all("hello" in c["messages"][-1]["content"] for c in client.calls)


def test_context_bounded():
    buf = ContextBuffer(max_pairs=2)
    buf.add("a", "es", "A")
    buf.add("b", "es", "B")
    buf.add("c", "es", "C")
    assert len(buf) == 2
    assert buf.for_language("es") == [("b", "B"), ("c", "C")]
    assert preserve_context(buf) is buf
    assert isinstance(preserve_context(), ContextBuffer)


def test_translator_uses_context_and_model_check():
    client = _FakeClient(
        answers={"Spanish": "hola"},
        models=("openai/gpt-oss-120b",),  # sin el 20b → debe caer al 120b
    )
    translator = GroqTranslator(api_key="fake", model="openai/gpt-oss-20b", client=client)
    translator.start()
    try:
        assert translator.model == "openai/gpt-oss-120b"
        result = translator.translate("hello", "en", "es")
    finally:
        translator.stop()
    assert result.translated_text == "hola"


def test_translator_translate_many():
    client = _FakeClient(answers={"Spanish": "hola", "German": "hallo"})
    translator = GroqTranslator(api_key="fake", client=client)
    translator.start()
    try:
        results = translator.translate_many("hello", "en", ["es", "de"])
    finally:
        translator.stop()
    assert results["es"].translated_text == "hola"
    assert results["de"].translated_text == "hallo"


def test_factory_groq_translator_requires_key(monkeypatch):
    from app.core.factory import build_translator
    from app.settings.config import AppSettings

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    s = AppSettings()
    s.translation.engine = "groq"
    with pytest.raises(ConfigurationError, match="GROQ_API_KEY"):
        build_translator(s)


def test_factory_groq_translator_builds(monkeypatch):
    from app.core.factory import build_translator
    from app.settings.config import AppSettings
    from app.translation.groq import GroqTranslator as G

    monkeypatch.setenv("GROQ_API_KEY", "fake")
    s = AppSettings()
    s.translation.engine = "groq"
    assert isinstance(build_translator(s), G)


def test_pipeline_fan_out_emits_one_cue_per_language():
    from app.audio.synthetic import SyntheticAudioSource
    from app.core.pipeline import SubtitlePipeline
    from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance
    from app.translation.mapping import MappingTranslator

    from conftest import CollectingRenderer

    renderer = CollectingRenderer()
    pipeline = SubtitlePipeline(
        audio_source=SyntheticAudioSource(total_duration=1.0),
        recognizer=ScriptedSpeechRecognizer(
            [ScriptedUtterance("hello world", 0.0, 0.5, language="en")]
        ),
        translator=MappingTranslator(
            {"hello world": "hola mundo"}, source_language="en", target_language="es"
        ),
        source_language="en",
        target_languages=["es", "fr"],
    )
    assert pipeline.target_languages == ["es", "fr"]
    pipeline.run(renderer, timeout=10)
    finals = {c.cue_id: c for c in renderer.cues if c.is_final}
    assert "u0:es" in finals and "u0:fr" in finals
    assert finals["u0:es"].text == "hola mundo"
    assert finals["u0:es"].target_language == "es"
    assert finals["u0:fr"].target_language == "fr"


def test_pipeline_single_target_unchanged_ids():
    from app.audio.synthetic import SyntheticAudioSource
    from app.core.pipeline import SubtitlePipeline
    from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance
    from app.translation.mapping import MappingTranslator

    from conftest import CollectingRenderer

    renderer = CollectingRenderer()
    pipeline = SubtitlePipeline(
        audio_source=SyntheticAudioSource(total_duration=1.0),
        recognizer=ScriptedSpeechRecognizer(
            [ScriptedUtterance("hello world", 0.0, 0.5, language="en")]
        ),
        translator=MappingTranslator({"hello world": "hola mundo"}),
        source_language="en",
        target_language="es",
    )
    pipeline.run(renderer, timeout=10)
    ids = {c.cue_id for c in renderer.cues if c.is_final}
    assert ids == {"u0"}


def test_console_show_language_prefix(capsys):
    from app.overlay.console import ConsoleRenderer
    from app.subtitles.models import SubtitleCue

    renderer = ConsoleRenderer(show_language=True)
    renderer.start()
    renderer.render(
        SubtitleCue(cue_id="u0:es", text="hola", is_final=True, start_time=0.0,
                    target_language="es")
    )
    renderer.stop()
    assert "[es]" in capsys.readouterr().out


def test_settings_target_languages_env():
    from app.settings.config import load_settings

    s = load_settings(
        environ={"SUBTITLE_TARGET_LANGUAGES": "es, fr, de", "SUBTITLE_TRANSLATION_MODEL": "m"}
    )
    assert s.translation.target_languages == ["es", "fr", "de"]
    assert s.translation.model == "m"


def test_factory_openai_requires_key(monkeypatch):
    from app.core.factory import build_translator
    from app.settings.config import AppSettings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = AppSettings()
    s.translation.engine = "openai"
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        build_translator(s)


def test_factory_anthropic_builds(monkeypatch):
    from app.core.factory import build_translator
    from app.settings.config import AppSettings
    from app.translation.chat import ChatTranslator

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    s = AppSettings()
    s.translation.engine = "anthropic"
    tr = build_translator(s)
    assert isinstance(tr, ChatTranslator)
    assert tr.provider.id == "anthropic"


def test_factory_openrouter_builds_with_model(monkeypatch):
    from app.core.factory import build_translator
    from app.settings.config import AppSettings
    from app.translation.chat import ChatTranslator

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    s = AppSettings()
    s.translation.engine = "openrouter"
    s.translation.model = "meta-llama/llama-3.1-8b-instruct"
    tr = build_translator(s)
    assert isinstance(tr, ChatTranslator)
    assert tr.model == "meta-llama/llama-3.1-8b-instruct"


def test_build_translation_engine_helper(monkeypatch):
    from app.core.factory import build_translation_engine
    from app.settings.config import AppSettings
    from app.translation.chat import ChatTranslator

    s = AppSettings()
    tr = build_translation_engine("openai", api_key="sk-fake", translation=s.translation)
    assert isinstance(tr, ChatTranslator)
    assert tr.provider.id == "openai"
