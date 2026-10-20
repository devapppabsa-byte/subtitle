"""Tests del selector de proveedores de IA (traducción multi-proveedor)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import ConfigurationError, TranslationError
from app.translation.chat import ChatTranslator, CompatClient
from app.translation.providers import (
    ENGINE_TO_PROVIDER,
    PROVIDERS,
    provider_by_id,
    provider_for_engine,
)


# -- registro ---------------------------------------------------------------
def test_registry_has_expected_providers():
    assert set(PROVIDERS) >= {"groq", "openai", "anthropic", "openrouter", "gemini"}
    assert PROVIDERS["anthropic"].style == "anthropic"
    assert PROVIDERS["openai"].key_env == "OPENAI_API_KEY"
    assert PROVIDERS["gemini"].free_tier is True
    assert PROVIDERS["gemini"].base_url.endswith("/openai")


def test_provider_for_engine():
    assert provider_for_engine("openai").id == "openai"
    assert provider_for_engine("anthropic").style == "anthropic"
    assert provider_for_engine("passThrough".lower()) is None
    assert provider_for_engine("mapping") is None


def test_provider_by_id_unknown_raises():
    with pytest.raises(ConfigurationError, match="desconocido"):
        provider_by_id("noexiste")


def test_all_registered_engines_map():
    for engine in ("groq", "openai", "anthropic", "openrouter", "gemini", "passthrough", "mapping"):
        assert engine in ENGINE_TO_PROVIDER


# -- compatible client / httpx mock -----------------------------------------
def _openai_models(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]})


def _anthropic_models(request: httpx.Request) -> httpx.Response:
    assert request.headers["x-api-key"] == "sk-ant-test"
    assert request.headers["anthropic-version"]
    return httpx.Response(200, json={"data": [{"id": "claude-3-5-haiku-latest"}]})


def test_list_models_openai_style():
    from app.translation.compat import list_models

    transport = httpx.MockTransport(_openai_models)
    provider = provider_by_id("openai")
    models = list_models(provider, "sk-test", transport=transport)
    assert models == ["gpt-4o", "gpt-4o-mini"]


def test_list_models_anthropic_sends_headers():
    from app.translation.compat import list_models

    transport = httpx.MockTransport(_anthropic_models)
    models = list_models(provider_by_id("anthropic"), "sk-ant-test", transport=transport)
    assert models == ["claude-3-5-haiku-latest"]


def test_list_models_missing_key_raises():
    from app.translation.compat import list_models

    with pytest.raises(TranslationError, match="ANTHROPIC_API_KEY"):
        list_models(provider_by_id("anthropic"), "")


def test_list_models_http_error_is_summarized():
    from app.translation.compat import list_models

    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="bad key"))
    with pytest.raises(TranslationError, match="HTTP 401"):
        list_models(provider_by_id("openai"), "sk-x", transport=transport)


def test_chat_complete_openai_shape():
    from app.translation.compat import chat_complete

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o-mini"
        assert body["messages"][0]["role"] == "system"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": " hola "}}]}
        )

    text = chat_complete(
        provider_by_id("openai"), "sk-x", model="gpt-4o-mini",
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
        transport=httpx.MockTransport(handler),
    )
    assert text == " hola "


def test_chat_complete_anthropic_extracts_text_and_system():
    from app.translation.compat import chat_complete

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["system"] == "eres traductor"
        assert body["max_tokens"] >= 1
        assert all(m["role"] != "system" for m in body["messages"])
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hola"}]})

    text = chat_complete(
        provider_by_id("anthropic"), "sk-ant", model="claude-3-5-haiku-latest",
        messages=[{"role": "system", "content": "eres traductor"},
                  {"role": "user", "content": "hi"}],
        transport=httpx.MockTransport(handler),
    )
    assert text == "hola"


# -- ChatTranslator end-to-end con transporte mock --------------------------
def _wire_mock_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    """Hace que compat._request use el MockTransport en todas las llamadas."""
    import app.translation.compat as compat

    real = compat._request

    def patched(prov, key, *, method, path, json_body=None, timeout=15.0, transport=None):
        return real(
            prov, key, method=method, path=path, json_body=json_body,
            timeout=timeout, transport=transport or _MOCK_TRANSPORT[0],
        )

    _MOCK_TRANSPORT[0] = transport
    monkeypatch.setattr(compat, "_request", patched)


_MOCK_TRANSPORT = [None]


def test_chat_translator_translates(monkeypatch):
    provider = provider_by_id("openai")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o-mini"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hola mundo"}}]})

    _wire_mock_transport(monkeypatch, httpx.MockTransport(handler))
    client = CompatClient(provider, "sk-test")
    tr = ChatTranslator(provider, "sk-test", model="gpt-4o-mini", client=client)
    result = tr.translate("hello world", "en", "es")
    assert result.translated_text == "hola mundo"
    assert tr.model == "gpt-4o-mini"


def test_chat_translator_model_falls_back_to_default(monkeypatch):
    provider = provider_by_id("openrouter")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [{"id": provider.default_model}]})
    )
    _wire_mock_transport(monkeypatch, transport)
    client = CompatClient(provider, "sk-test")
    tr = ChatTranslator(provider, "sk-test", model="modelo-inexistente", client=client)
    tr._ensure_client()
    assert tr.model == provider.default_model


def test_compat_client_requires_key():
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        CompatClient(provider_by_id("openai"), "")


def test_chat_translator_requires_key_without_client():
    tr = ChatTranslator(provider_by_id("anthropic"), None)
    with pytest.raises(ConfigurationError):
        tr._ensure_client()


# -- list_models_detailed ---------------------------------------------------
def test_list_models_detailed_marks_free():
    from app.translation.compat import list_models_detailed

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": "nvidia/x:free", "name": "X Free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "openai/gpt-4o-mini", "name": "GPT-4o mini",
             "pricing": {"prompt": "0.00000015", "completion": "0.0000006"}},
        ]})

    models = list_models_detailed(
        provider_by_id("openrouter"), "sk-or", transport=httpx.MockTransport(handler)
    )
    by_id = {m.id: m for m in models}
    assert by_id["nvidia/x:free"].free is True
    assert by_id["openai/gpt-4o-mini"].free is False
    # los gratuitos primero
    assert models[0].id == "nvidia/x:free"


def test_list_models_detailed_pricing_zero_is_free():
    from app.translation.compat import list_models_detailed

    handler = lambda req: httpx.Response(200, json={"data": [
        {"id": "modelo-gratis", "pricing": {"prompt": "0", "completion": "0"}}
    ]})
    models = list_models_detailed(
        provider_by_id("openai"), "sk-x", transport=httpx.MockTransport(handler)
    )
    assert models[0].free is True
