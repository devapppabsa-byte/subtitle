"""Traductor multi-proveedor (OpenAI, Anthropic, OpenRouter, Groq vía HTTP).

Reutiliza TODO lo de la Fase 4 que es independiente del proveedor —prompt,
ventana de contexto, caché LRU, reintentos y fan-out multi-idioma importados
de :mod:`app.translation.groq`— y solo aporta el transporte:
:class:`app.translation.compat.CompatClient`. ``GroqTranslator`` (SDK oficial)
sigue intacto para la ruta ``engine=groq``.

Correspondencia con la especificación de Fase 4::

    ChatTranslator.translate      → :func:`translate_text`
    ChatTranslator.translate_many → :func:`translate_to_languages`
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.errors import ConfigurationError, TranslationError
from app.core.logging import get_logger
from app.translation import compat
from app.translation.base import Translator
from app.translation.groq import (
    ContextBuffer,
    TranslationCache,
    translate_text,
    translate_to_languages,
)
from app.translation.models import TranslationResult
from app.translation.providers import Provider, provider_by_id

__all__ = ["ChatTranslator", "CompatClient"]

logger = get_logger(__name__)


class CompatClient:
    """Superficie ``client.chat.completions.create`` / ``client.models.list``.

    Permite alimentar ``translate_text``/``translate_to_languages`` con
    cualquier proveedor sin duplicar la maquinaria de la Fase 4.
    """

    def __init__(self, provider: Provider, api_key: str, *, timeout: float = 30.0) -> None:
        if not api_key:
            raise ConfigurationError(
                f"Falta la clave de {provider.label}: exportá {provider.key_env} "
                "o pegá una clave válida en el panel (Motor IA)."
            )
        self._provider = provider
        self._api_key = api_key
        self._timeout = float(timeout)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=self._list)

    @property
    def provider(self) -> Provider:
        """Proveedor asociado a este cliente."""
        return self._provider

    def _create(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float = 0.0,
        timeout: float | None = None,
        **_ignored,
    ):
        content = compat.chat_complete(
            self._provider,
            self._api_key,
            model=model,
            messages=messages,
            timeout=float(timeout or self._timeout),
            temperature=temperature,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    def _list(self):
        return compat.models_list_response(
            self._provider, self._api_key, timeout=min(self._timeout, 15.0)
        )


class ChatTranslator(Translator):
    """Traductor con contexto, caché y fan-out sobre un proveedor HTTP."""

    def __init__(
        self,
        provider: Provider | str,
        api_key: str | None,
        *,
        model: str = "",
        context_pairs: int = 3,
        request_timeout: float = 30.0,
        max_retries: int = 2,
        cache_size: int = 512,
        parallel: bool = False,
        client=None,
    ) -> None:
        super().__init__()
        self._provider = provider_by_id(provider) if isinstance(provider, str) else provider
        self._api_key = api_key
        self._preferred_model = (model or "").strip() or self._provider.default_model
        self._model = self._preferred_model
        self._context = ContextBuffer(max_pairs=context_pairs)
        self._timeout = float(request_timeout)
        self._max_retries = max(0, int(max_retries))
        self._cache = TranslationCache(max_entries=cache_size)
        self._parallel = bool(parallel)
        self._client = client
        self._verified = False

    @property
    def provider(self) -> Provider:
        """Proveedor elegido (combo del panel ``Motor IA``)."""
        return self._provider

    @property
    def cache_stats(self) -> dict[str, int]:
        """Aciertos/fallos/tamaño de la caché LRU."""
        return {"hits": self._cache.hits, "misses": self._cache.misses, "size": len(self._cache)}

    @property
    def model(self) -> str:
        """Modelo efectivo (tras verificación contra la cuenta en ``start``)."""
        return self._model

    def _resolve_model(self) -> str:
        """Comprueba el modelo contra la cuenta; cae al default del proveedor."""
        preferred = self._preferred_model
        try:
            ids = {m.id for m in self._client.models.list().data}
        except Exception as exc:
            logger.warning(
                "no se pudo listar modelos de %s, se usa %r: %s",
                self._provider.label, preferred, exc,
            )
            return preferred
        if preferred in ids:
            return preferred
        default = self._provider.default_model
        if default in ids and preferred != default:
            logger.warning("modelo %r no disponible; se usa %r", preferred, default)
            return default
        return preferred

    def _ensure_client(self):
        if self._client is None:
            self._client = CompatClient(self._provider, self._api_key, timeout=self._timeout)
        if not self._verified:
            self._model = self._resolve_model()
            self._verified = True
        logger.info(
            "traductor %s iniciado (modelo=%s)", self._provider.label, self._model
        )
        return self._client

    def _on_start(self) -> None:
        self._ensure_client()

    def translate(
        self, text: str, source_language: str, target_language: str
    ) -> TranslationResult:
        client = self._ensure_client()
        return translate_text(
            text, source_language, target_language,
            client=client, model=self._model,
            context=self._context, timeout=self._timeout,
            max_retries=self._max_retries, cache=self._cache,
            label=self._provider.label,
        )

    def translate_many(
        self,
        text: str,
        source_language: str,
        target_languages: list[str] | tuple[str, ...],
        *,
        parallel: bool | None = None,
    ) -> dict[str, TranslationResult]:
        """Varios idiomas del MISMO fragmento original, con aislamiento por idioma."""
        client = self._ensure_client()
        return translate_to_languages(
            text, source_language, target_languages,
            client=client, model=self._model,
            context=self._context, timeout=self._timeout,
            max_retries=self._max_retries, cache=self._cache,
            parallel=self._parallel if parallel is None else parallel,
            label=self._provider.label,
        )
