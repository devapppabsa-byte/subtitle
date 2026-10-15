"""Configuración centralizada de *logging*.

Cada submódulo obtiene su logger con ``get_logger(__name__)``. La aplicación
configura el formato y el nivel una sola vez desde ``main.py``.
"""

from __future__ import annotations

import logging

DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
DEFAULT_DATE_FORMAT = "%H:%M:%S"

_CONFIGURED = False


def configure_logging(level: int | str = logging.INFO) -> None:
    """Configura el logger raíz del proceso.

    Es idempotente: llamarla varias veces solo actualiza el nivel.
    """
    global _CONFIGURED
    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT))
        root.addHandler(handler)
        _CONFIGURED = True
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger con espacio de nombres dentro de la aplicación."""
    if not name.startswith("subtitle_app"):
        name = f"subtitle_app.{name}"
    return logging.getLogger(name)


__all__ = ["configure_logging", "configure_file_logging", "get_logger"]


def configure_file_logging(path: str) -> str:
    """Añade un FileHandler al logger raíz (para lanzamientos sin terminal).

    Devuelve la ruta usada. Los errores de E/S se ignoran a propósito.
    """
    try:
        from pathlib import Path

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(target), encoding="utf-8")
        handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DEFAULT_DATE_FORMAT))
        logging.getLogger().addHandler(handler)
        return str(target)
    except OSError:
        return ""
