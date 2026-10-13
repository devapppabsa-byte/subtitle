"""Reintentos acotados para las API en la nube (Fase 7).

Sin dependencias: backoff exponencial + jitter, solo ante errores
reintentables (429, 5xx, timeouts de red). Tras agotar el presupuesto se
propaga la última excepción y cada etapa aplica su fallback.
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from app.core.logging import get_logger

__all__ = ["is_retryable", "with_retries"]

logger = get_logger(__name__)

T = TypeVar("T")

_RETRYABLE_SNIPPETS = (
    "429",
    "rate limit",
    "rate_limit",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "connection",
    "temporary",
    "overloaded",
    "try again",
)


def is_retryable(exc: BaseException) -> bool:
    """¿Merece un reintento? Solo 429/5xx/timeout/red (nunca 400/401)."""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "401" in text or "403" in text or "400" in text:
        return False
    return any(snippet in text for snippet in _RETRYABLE_SNIPPETS)


def with_retries(
    action: Callable[[], T],
    *,
    max_retries: int = 2,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
    what: str = "llamada",
) -> T:
    """Ejecuta ``action`` con hasta ``max_retries`` reintentos con backoff."""
    attempt = 0
    while True:
        try:
            return action()
        except Exception as exc:
            if attempt >= max_retries or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2**attempt)) + random.uniform(0, 0.25)
            logger.warning("%s falló (intento %d), reintento en %.1fs: %s", what, attempt + 1, delay, exc)
            time.sleep(delay)
            attempt += 1
