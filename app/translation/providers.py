"""Registro de proveedores de IA para la traducción.

Un proveedor describe DÓNDE llamar (``base_url`` y estilo de API) y con qué
variable de clave se autentica; la maquinaria de prompt/caché/reintentos es
común y vive en :mod:`app.translation.groq`. Añadir un proveedor nuevo es una
entrada en ``PROVIDERS`` + una rama en la factory (y en la UI, el combo).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import ConfigurationError

__all__ = [
    "Provider",
    "PROVIDERS",
    "ENGINE_TO_PROVIDER",
    "provider_by_id",
    "provider_for_engine",
]


@dataclass(frozen=True)
class Provider:
    """Descripción mínima de un proveedor de IA para traducción."""

    id: str
    label: str
    #: Nombre de la variable de entorno donde vive la clave.
    key_env: str
    #: URL base del API (sin barra final).
    base_url: str
    #: Estilo de API: ``openai`` (chat/completions) o ``anthropic`` (messages).
    style: str
    default_model: str


#: Ids canónicos → proveedor. El orden define el orden del combo en la UI.
PROVIDERS: dict[str, Provider] = {
    "groq": Provider(
        id="groq",
        label="Groq",
        key_env="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        style="openai",
        default_model="openai/gpt-oss-20b",
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        key_env="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
        style="openai",
        default_model="gpt-4o-mini",
    ),
    "anthropic": Provider(
        id="anthropic",
        label="Claude (Anthropic)",
        key_env="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com/v1",
        style="anthropic",
        default_model="claude-3-5-haiku-latest",
    ),
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter",
        key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        style="openai",
        default_model="openai/gpt-4o-mini",
    ),
}

#: ``translation.engine`` → id de proveedor (``None`` = sin IA).
ENGINE_TO_PROVIDER: dict[str, str | None] = {
    "groq": "groq",
    "openai": "openai",
    "anthropic": "anthropic",
    "openrouter": "openrouter",
    "passthrough": None,
    "mapping": None,
}


def provider_by_id(provider_id: str) -> Provider:
    """Devuelve el proveedor o lanza ``ConfigurationError`` si no existe."""
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ConfigurationError(f"Proveedor de IA desconocido: {provider_id!r}") from exc


def provider_for_engine(engine: str) -> Provider | None:
    """Traduce un motor (``openai``, ``groq``…) a su proveedor; ``None`` si no usa IA."""
    provider_id = ENGINE_TO_PROVIDER.get(engine, None)
    if provider_id is None:
        return None
    return PROVIDERS[provider_id]
