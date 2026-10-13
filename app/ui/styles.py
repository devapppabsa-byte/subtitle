"""Hojas de estilo para las ventanas de subtítulos.

Fase 6: construcción pura (sin widgets) a partir de
:class:`app.subtitles.models.SubtitleStyle`, opacidad y tamaño de fuente.
"""

from __future__ import annotations

from app.subtitles.models import SubtitleStyle

__all__ = ["subtitle_stylesheet", "badge_stylesheet", "button_stylesheet", "LANGUAGE_COLORS"]

#: Acento por idioma para la etiqueta identificadora.
LANGUAGE_COLORS = {
    "original": "#8AB4F8",
    "es": "#F28B82",
    "fr": "#81C995",
    "de": "#FDD663",
    "pt": "#C58AF9",
    "it": "#78D9EC",
    "en": "#8AB4F8",
}


def _rgba(background: str, opacity: float) -> str:
    color = background.lstrip("#")
    if len(color) == 6:
        red, green, blue = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    else:  # pragma: no cover - defensivo ante colores no hex
        red, green, blue = 0, 0, 0
    alpha = max(0, min(255, int(opacity * 255)))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def subtitle_stylesheet(style: SubtitleStyle, *, font_size: int | None = None, dimmed: bool = False) -> str:
    """Estilo del texto del subtítulo; ``dimmed`` atenúa los parciales."""
    size = font_size or style.font_size
    opacity = style.background_opacity * (0.55 if dimmed else 1.0)
    return (
        f"color: {style.text_color};"
        f"background-color: {_rgba(style.background_color, opacity)};"
        f"font-size: {size}px;"
        "padding: 10px 14px; border-radius: 8px;"
    )


def badge_stylesheet(language: str) -> str:
    """Etiqueta con el idioma de la ventana."""
    accent = LANGUAGE_COLORS.get((language or "original").lower(), "#8AB4F8")
    return (
        f"color: {accent}; background-color: rgba(0, 0, 0, 160);"
        "font-size: 11px; font-weight: bold; padding: 2px 8px; border-radius: 6px;"
    )


def button_stylesheet() -> str:
    """Botones pequeños de la barra de la ventana flotante."""
    return (
        "QToolButton { color: #FFFFFF; background-color: rgba(0, 0, 0, 140);"
        " border: none; border-radius: 6px; padding: 2px 6px; font-size: 12px; }"
        "QToolButton:hover { background-color: rgba(60, 60, 60, 180); }"
        "QToolButton:checked { background-color: rgba(138, 180, 248, 200); color: #000000; }"
    )
