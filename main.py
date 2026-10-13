"""Punto de entrada de la aplicación de subtítulos.

Solo compone: lee configuración, construye las implementaciones concretas a
través del *composition root* y ejecuta el bucle principal. No contiene lógica
de dominio.
"""

from __future__ import annotations

import argparse
import sys

from app.core.factory import build_pipeline, build_renderer
from app.core.logging import configure_logging, get_logger
from app.settings.config import AppSettings, load_settings

logger = get_logger("main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subtitle-app",
        description="Subtítulos en tiempo real con traducción.",
    )
    parser.add_argument("--config", help="Ruta a un archivo JSON de configuración.")
    parser.add_argument("--duration", type=float, default=None, help="Segundos de ejecución (por defecto: hasta que termine el audio).")
    parser.add_argument("--audio", choices=["synthetic", "wav", "microphone", "monitor"], help="Origen de audio.")
    parser.add_argument("--audio-path", help="Ruta del WAV cuando --audio=wav.")
    parser.add_argument("--audio-device", help="Índice o parte del nombre (None = auto).")
    parser.add_argument("--list-devices", action="store_true", help="Lista dispositivos y monitores, luego sale.")
    parser.add_argument(
        "--realtime",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Reproducir el audio al ritmo real (útil para ver los subtítulos en vivo).",
    )
    parser.add_argument("--speech-engine", help="Motor de reconocimiento de voz.")
    parser.add_argument("--translation-engine", help="Motor de traducción.")
    parser.add_argument("--source-language", help="Idioma de origen ('auto' para detectar).")
    parser.add_argument("--target-language", help="Idioma de destino.")
    parser.add_argument("--renderer", choices=["console", "qt"], help="Dónde mostrar los subtítulos.")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR.")
    return parser


def apply_cli_overrides(settings: AppSettings, args: argparse.Namespace) -> None:
    """Vuelca los argumentos presentes sobre la configuración cargada."""
    if args.audio is not None:
        settings.audio.source = args.audio
    if args.audio_path is not None:
        settings.audio.path = args.audio_path
    if args.audio_device is not None:
        settings.audio.device = args.audio_device
    if args.realtime is not None:
        settings.audio.realtime = args.realtime
    if args.speech_engine is not None:
        settings.speech.engine = args.speech_engine
    if args.translation_engine is not None:
        settings.translation.engine = args.translation_engine
    if args.source_language is not None:
        settings.translation.source_language = args.source_language
    if args.target_language is not None:
        settings.translation.target_language = args.target_language
    if args.renderer is not None:
        settings.overlay.renderer = args.renderer
    if args.log_level is not None:
        settings.log_level = args.log_level


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_devices:
        from app.audio.capture import find_monitor_devices, list_audio_devices

        devices = list_audio_devices()
        print(f"{'IDX':>3}  {'KIND':<10} IN/OUT  RATE      FLAGS  NAME")
        for dev in devices:
            flags = "".join(
                [
                    "M" if dev.is_monitor else "-",
                    "D" if dev.is_default else "-",
                ]
            )
            print(
                f"{dev.index:>3}  {dev.kind:<10} {dev.channels_in:>2}/{dev.channels_out:<2}  "
                f"{dev.default_rate:>7.0f}Hz  {flags}     {dev.name}"
                + (f"  [{dev.pactl_name}]" if dev.pactl_name else "")
            )
        monitors = find_monitor_devices(devices)
        print(f"\nMonitores del sistema: {len(monitors)}")
        for mon in monitors:
            print(f"  #{mon.index} {mon.name}")
        return 0
    settings = load_settings(args.config)
    apply_cli_overrides(settings, args)
    configure_logging(settings.log_level)

    logger.info(
        "configuración: audio=%s voz=%s traducción=%s render=%s (%s→%s)",
        settings.audio.source,
        settings.speech.engine,
        settings.translation.engine,
        settings.overlay.renderer,
        settings.translation.source_language,
        settings.translation.target_language,
    )

    pipeline = build_pipeline(settings)
    renderer = build_renderer(settings)
    try:
        with renderer:
            pipeline.run(renderer, timeout=args.duration)
    except KeyboardInterrupt:
        logger.info("interrumpido por el usuario")
    return 0


if __name__ == "__main__":
    sys.exit(main())
