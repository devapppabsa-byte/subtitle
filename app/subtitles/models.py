"""Modelos de dominio de los subtítulos."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["SubtitlePosition", "SubtitleCue", "SubtitleStyle"]


class SubtitlePosition(str, Enum):
    """Anclaje vertical del subtítulo en pantalla."""

    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    """Línea de subtítulo lista para pintar.

    ``cue_id`` es la clave de actualización: todas las hipótesis de un mismo
    enunciado comparten identificador, así que una cue parcial es reemplazada
    por su versión final en vez de acumularse.
    """

    cue_id: str
    text: str
    is_final: bool
    start_time: float
    end_time: float | None = None
    source_text: str | None = None
    source_language: str | None = None
    target_language: str | None = None


@dataclass(frozen=True, slots=True)
class SubtitleStyle:
    """Estilo visual del subtítulo. Independiente del motor de renderizado."""

    font_family: str = "sans-serif"
    font_size: int = 32
    text_color: str = "#FFFFFF"
    background_color: str = "#000000"
    background_opacity: float = 0.6
    position: SubtitlePosition = SubtitlePosition.BOTTOM
    max_lines: int = 2
    margin_pixels: int = 64
