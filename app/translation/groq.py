"""Traducción con Groq chat API (modelos multilingües).

Fase 4: recibir cada fragmento reconocido y generar sus traducciones sin
volver a procesar el audio. Un error en un idioma nunca bloquea a los demás:
cada destino tiene su propio timeout y cae al texto original si falla.

Correspondencia con la especificación de Fase 4::

    translate_text()        → :func:`translate_text` (un fragmento → un idioma)
    translate_to_languages()→ :func:`translate_to_languages` (un fragmento → N idiomas, en paralelo)
    build_translation_prompt() → :func:`build_translation_prompt`
    preserve_context()      → :class:`ContextBuffer` (ventana acotada de pares previos)
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.core.errors import ConfigurationError, TranslationError
from app.core.logging import get_logger
from app.translation.base import Translator
from app.translation.models import TranslationResult

__all__ = [
    "DEFAULT_MODEL",
    "FALLBACK_MODEL",
    "ContextBuffer",
    "GroqTranslator",
    "build_translation_prompt",
    "normalize_language",
    "preserve_context",
    "translate_text",
    "translate_to_languages",
]

logger = get_logger(__name__)

DEFAULT_MODEL = "openai/gpt-oss-20b"
FALLBACK_MODEL = "openai/gpt-oss-120b"

#: Nombres de idioma (p. ej. los que devuelve Whisper) → ISO 639-1.
_LANGUAGE_ALIASES = {
    "spanish": "es",
    "español": "es",
    "english": "en",
    "inglés": "en",
    "french": "fr",
    "francés": "fr",
    "frances": "fr",
    "german": "de",
    "alemán": "de",
    "aleman": "de",
    "portuguese": "pt",
    "portugués": "pt",
    "portugues": "pt",
    "italian": "it",
    "italiano": "it",
}

_LANGUAGE_NAMES = {
    "es": "Spanish",
    "en": "English",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
}


def normalize_language(language: str | None) -> str:
    """Normaliza nombres de idioma a ISO 639-1 en minúsculas (``auto`` se conserva)."""
    if language is None:
        return "auto"
    lowered = language.strip().lower()
    if not lowered or lowered in ("auto", "und"):
        return "auto"
    if len(lowered) == 2:
        return lowered
    return _LANGUAGE_ALIASES.get(lowered, lowered)


def language_name(iso: str) -> str:
    """Nombre inglés del idioma para el prompt (los modelos lo entienden mejor)."""
    return _LANGUAGE_NAMES.get(iso.lower(), iso)


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------
@dataclass
class ContextBuffer:
    """Ventana acotada de pares (origen → traducción) por idioma destino.

    El historial compartido del texto origen más la traducción previa en cada
    idioma da coherencia entre segmentos sin crecer sin límite.
    """

    max_pairs: int = 3
    _entries: deque = field(default_factory=deque, init=False, repr=False)

    def __post_init__(self) -> None:
        self._entries = deque(maxlen=max(1, self.max_pairs))

    def add(self, source_text: str, target_language: str, translated_text: str) -> None:
        self._entries.append((source_text, target_language, translated_text))

    def for_language(self, target_language: str) -> list[tuple[str, str]]:
        """Pares (origen, traducción) previos para ese idioma, en orden."""
        iso = normalize_language(target_language)
        return [
            (src, dst)
            for src, lang, dst in self._entries
            if normalize_language(lang) == iso
        ]

    def __len__(self) -> int:
        return len(self._entries)


def preserve_context(buffer: ContextBuffer | None = None, *, max_pairs: int = 3) -> ContextBuffer:
    """Devuelve el búfer dado o uno nuevo acotado (especificación Fase 4)."""
    return buffer if buffer is not None else ContextBuffer(max_pairs=max_pairs)


# ---------------------------------------------------------------------------
# Prompt + llamadas
# ---------------------------------------------------------------------------
def build_translation_prompt(
    text: str,
    *,
    source_language: str,
    target_language: str,
    context: list[tuple[str, str]] | None = None,
) -> list[dict]:
    """Prepara los mensajes para el modelo (traducir solo, sin explicaciones)."""
    src = normalize_language(source_language)
    dst = normalize_language(target_language)
    system = (
        f"You are a subtitle translator. Translate the user text from "
        f"{language_name(src) if src != 'auto' else 'the detected language'} "
        f"to {language_name(dst)}. Output ONLY the translation, no quotes, "
        f"no explanations, no notes. Keep the tone and preserve named entities."
    )
    messages = [{"role": "system", "content": system}]
    for prev_src, prev_dst in context or []:
        messages.append({"role": "user", "content": f"Translate: {prev_src}"})
        messages.append({"role": "assistant", "content": prev_dst})
    messages.append({"role": "user", "content": f"Translate: {text}"})
    return messages


def _create_client(api_key: str | None):
    try:
        from groq import Groq
    except ImportError as exc:
        raise TranslationError("Falta el SDK 'groq>=0.9' para el traductor Groq.") from exc
    if not api_key:
        raise ConfigurationError(
            "Falta la clave de Groq: exportá GROQ_API_KEY o configurá translation.api_key_env."
        )
    return Groq(api_key=api_key)


def resolve_model(client, preferred: str) -> str:
    """Comprueba el modelo contra la cuenta; cae al alternativo si falta."""
    try:
        ids = {m.id for m in client.models.list().data}
    except Exception as exc:
        logger.warning("no se pudo listar modelos Groq, se usa %r: %s", preferred, exc)
        return preferred
    if preferred in ids:
        return preferred
    if FALLBACK_MODEL in ids and preferred != FALLBACK_MODEL:
        logger.warning("modelo %r no disponible; se usa %r", preferred, FALLBACK_MODEL)
        return FALLBACK_MODEL
    return preferred


def _strip_outer_quotes(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"', "“", "«"):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def translate_text(
    text: str,
    source_language: str,
    target_language: str,
    *,
    client,
    model: str = DEFAULT_MODEL,
    context: ContextBuffer | None = None,
    timeout: float = 30.0,
) -> TranslationResult:
    """Traduce UN fragmento completo a UN idioma (nunca por palabra)."""
    src = normalize_language(source_language)
    dst = normalize_language(target_language)
    if not text or not text.strip():
        raise TranslationError("Texto vacío: nada que traducir.")
    if src == dst:
        return TranslationResult(
            source_text=text, translated_text=text,
            source_language=src, target_language=dst,
        )
    messages = build_translation_prompt(
        text, source_language=src, target_language=dst,
        context=context.for_language(dst) if context is not None else None,
    )
    try:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=0.0, timeout=timeout,
        )
        translated = _strip_outer_quotes(response.choices[0].message.content or "")
    except Exception as exc:
        raise TranslationError(f"Traducción Groq falló ({src}→{dst}): {exc}") from exc
    if not translated:
        raise TranslationError(f"El modelo devolvió una traducción vacía ({src}→{dst}).")
    if context is not None:
        context.add(text, dst, translated)
    return TranslationResult(
        source_text=text, translated_text=translated,
        source_language=src, target_language=dst,
    )


def translate_to_languages(
    text: str,
    source_language: str,
    target_languages: list[str] | tuple[str, ...],
    *,
    client,
    model: str = DEFAULT_MODEL,
    context: ContextBuffer | None = None,
    timeout: float = 30.0,
    parallel: bool = False,
) -> dict[str, TranslationResult]:
    """Traduce UN fragmento a VARIOS idiomas, con fallo aislado por idioma.

    Secuencial por defecto: en tiers con concurrencia limitada el servidor
    serializa igual y en paralelo sale mucho más lento (medido: 21s vs 2.7s
    para es+fr+de). ``parallel=True`` usa un hilo por idioma en cuentas con
    margen de concurrencia. En ambos modos el fallo de un idioma cae al texto
    original sin afectar a los demás.
    """
    src = normalize_language(source_language)
    targets = [normalize_language(t) for t in target_languages]
    if not targets:
        raise TranslationError("Sin idiomas destino.")

    def _one(dst: str) -> tuple[str, TranslationResult]:
        try:
            return dst, translate_text(
                text, src, dst, client=client, model=model, context=context, timeout=timeout,
            )
        except Exception as exc:  # cualquier fallo (red, API, SDK) queda aislado a su idioma
            logger.warning("traducción a %s falló, se usa el original: %s", dst, exc)
            return dst, TranslationResult(
                source_text=text, translated_text=text,
                source_language=src, target_language=dst,
            )

    if not parallel:
        return dict(_one(dst) for dst in targets)

    results: dict[str, TranslationResult] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(targets)), thread_name_prefix="groq-tr") as pool:
        for dst, result in pool.map(_one, targets):
            results[dst] = result
    return results


# ---------------------------------------------------------------------------
# Traductor para el pipeline
# ---------------------------------------------------------------------------
class GroqTranslator(Translator):
    """Traductor Groq con contexto y fan-out multi-idioma."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        context_pairs: int = 3,
        request_timeout: float = 30.0,
        client=None,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._preferred_model = model or DEFAULT_MODEL
        self._model = self._preferred_model
        self._context = ContextBuffer(max_pairs=context_pairs)
        self._timeout = float(request_timeout)
        self._client = client
        self._verified = False

    @property
    def model(self) -> str:
        """Modelo efectivo (tras verificación contra la cuenta en ``start``)."""
        return self._model

    def _ensure_client(self):
        if self._client is None:
            self._client = _create_client(self._api_key)
        if not self._verified:
            self._model = resolve_model(self._client, self._preferred_model)
            self._verified = True
            logger.info("traductor Groq iniciado (modelo=%s)", self._model)
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
        )

    def translate_many(
        self,
        text: str,
        source_language: str,
        target_languages: list[str] | tuple[str, ...],
        *,
        parallel: bool = False,
    ) -> dict[str, TranslationResult]:
        """Varios idiomas del MISMO fragmento original, con aislamiento por idioma."""
        client = self._ensure_client()
        return translate_to_languages(
            text, source_language, target_languages,
            client=client, model=self._model,
            context=self._context, timeout=self._timeout, parallel=parallel,
        )
