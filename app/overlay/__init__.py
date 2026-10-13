"""Implementaciones concretas de ``SubtitleRenderer``.

El renderizador Qt (``app.overlay.qt_overlay.QtOverlayRenderer``) no se importa
aquí a propósito: PySide6 es una dependencia opcional y el paquete debe poder
importarse sin ella.
"""

from app.overlay.console import ConsoleRenderer

__all__ = ["ConsoleRenderer"]
