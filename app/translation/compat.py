"""Cliente HTTP multi-proveedor sobre ``httpx`` (sin SDKs por proveedor).

Expone dos operaciones —listar modelos y completar chat— con la menor
superficie posible. Las respuestas se normalizan a la forma openai (``choices``
y ``models.data``) para que la maquinaria existente de traducción funcione
sin conocer el proveedor. El estilo ``anthropic`` usa ``/messages`` con sus
headers propios (``x-api-key``, ``anthropic-version``).

Nunca se registra ni se propaga la clave: las excepciones solo llevan el
cuerpo HTTP resumido.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx

from app.core.errors import TranslationError
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.translation.providers import Provider

__all__ = ["chat_complete", "list_models"]

logger = get_logger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
#: ``max_tokens`` obligatorio en la API de Anthropic (límite corto para subtítulos).
ANTHROPIC_MAX_TOKENS = 1024


def _headers(provider: "Provider", api_key: str) -> dict[str, str]:
    if provider.style == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
    return {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}


def _summarize_error(exc: Exception) -> str:
    """Mensaje corto y sin secretos de un fallo HTTP/transporte."""
    from app.settings.store import redact_secrets

    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "").strip().replace("\n", " ")
        return redact_secrets(f"HTTP {exc.response.status_code}: {body[:160]}")
    return redact_secrets(str(exc) or type(exc).__name__)


def _request(
    provider: "Provider",
    api_key: str,
    *,
    method: str,
    path: str,
    json_body: dict | None = None,
    timeout: float = 15.0,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    url = f"{provider.base_url}/{path.lstrip('/')}"
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.request(
                method, url, headers=_headers(provider, api_key), json=json_body
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TranslationError(
            f"{provider.label}: {_summarize_error(exc)} (al llamar {path})"
        ) from exc


def list_models(
    provider: "Provider",
    api_key: str,
    *,
    timeout: float = 15.0,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Valida la clave contra la cuenta y devuelve los modelos visibles."""
    if not api_key:
        raise TranslationError(f"Falta la clave de {provider.label} ({provider.key_env}).")
    data = _request(
        provider, api_key, method="GET", path="models", timeout=timeout, transport=transport
    )
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):  # respuesta inesperada: clave válida pero forma rara
        raise TranslationError(f"{provider.label}: respuesta inesperada de /models.")
    return sorted(str(item.get("id", "")) for item in items if isinstance(item, dict) and item.get("id"))


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Anthropic no admite rol ``system`` dentro de ``messages``: lo extrae."""
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return ("\n".join(system_parts) or None), rest


def chat_complete(
    provider: "Provider",
    api_key: str,
    *,
    model: str,
    messages: list[dict],
    timeout: float = 30.0,
    temperature: float = 0.0,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Un turno de chat; devuelve SOLO el texto de la respuesta."""
    if provider.style == "anthropic":
        system, rest = _split_system(messages)
        body: dict = {
            "model": model,
            "messages": rest,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        data = _request(
            provider,
            api_key,
            method="POST",
            path="messages",
            json_body=body,
            timeout=timeout,
            transport=transport,
        )
        blocks = data.get("content") if isinstance(data, dict) else None
        text = "".join(
            block.get("text", "")
            for block in blocks or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text:
            raise TranslationError(f"{provider.label}: respuesta vacía de {model}.")
        return text

    data = _request(
        provider,
        api_key,
        method="POST",
        path="chat/completions",
        json_body={
            "model": model,
            "messages": messages,
            "temperature": temperature,
        },
        timeout=timeout,
        transport=transport,
    )
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise TranslationError(f"{provider.label}: respuesta inesperada de {model}.") from exc


def models_list_response(provider: "Provider", api_key: str, **kwargs) -> SimpleNamespace:
    """``/models`` con forma ``.data[i].id`` (compatible con ``resolve_model``)."""
    ids = list_models(provider, api_key, **kwargs)
    return SimpleNamespace(data=[SimpleNamespace(id=mid) for mid in ids])
