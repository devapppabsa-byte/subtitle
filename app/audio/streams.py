"""Streams de audio por aplicación y enrutado selectivo (PulseAudio/PipeWire).

Fase 10: elegir *de dónde* sale el audio. El servidor de audio expone cada
aplicación o pestaña que reproduce sonido como un *sink input* (``pactl list
sink-inputs``). Para capturar solo uno de ellos se usa el mecanismo estándar:

1. se carga un **null sink** virtual (``module-null-sink``);
2. se **mueve** el stream elegido a ese sink (``pactl move-sink-input``);
3. se captura el *monitor* de ese sink, que ya contiene solo el audio deseado;
4. si el usuario quiere seguir oyendo, se devuelve el audio a los parlantes
   con un **loopback** (``module-loopback``) desde el monitor hacia el sink real.

Al terminar se deshace todo: los streams vuelven a su sink original y los
módulos se descargan, dejando el sistema como estaba.

Todo el módulo es **agnóstico del servidor**: la lista de streams y el
enrutado se hacen con ``pactl``, que en un PipeWire con ``pipewire-pulse``
habla con el mismo protocolo PulseAudio. Si ``pactl`` no está disponible, las
funciones devuelven listas vacías y registran el motivo, sin lanzar.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Sequence

from app.core.logging import get_logger

__all__ = [
    "AudioStreamInfo",
    "StreamRouter",
    "list_audio_streams",
    "list_sink_map",
    "parse_sink_inputs_json",
    "parse_sink_inputs_text",
]

logger = get_logger(__name__)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess:
    """Ejecuta un comando de ``pactl`` y devuelve el ``CompletedProcess``."""
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AudioStreamInfo:
    """Una aplicación o pestaña que está reproduciendo audio.

    :param index: índice del *sink input* (es efímero: cambia entre sesiones).
    :param app: nombre de la aplicación (``application.name``).
    :param title: título del medio (``media.title`` o ``media.name``).
    :param binary: ejecutable (``application.process.binary``).
    :param sink: índice del sink al que va el stream.
    :param sink_name: nombre del sink de destino.
    """

    index: int
    app: str = ""
    title: str = ""
    binary: str = ""
    sink: int = 0
    sink_name: str = ""

    @property
    def label(self) -> str:
        """Texto para la interfaz: ``Chromium · YouTube - Behind the Scenes``."""
        parts = [self.app or self.binary or f"stream #{self.index}"]
        if self.title and self.title != self.app:
            parts.append(self.title)
        return " · ".join(parts)

    @property
    def spec(self) -> str:
        """Identificador estable para volver a encontrar el stream más tarde.

        El índice no sirve (cambia en cada arranque), así que se usan
        aplicación y título: ``binary::title``.
        """
        return f"{self.binary or self.app}::{self.title}"

    def matches(self, spec: str) -> bool:
        """``True`` si este stream corresponde al ``spec`` dado.

        Acepta ``app::título``, ``app`` a secas o el título exacto.
        """
        spec = (spec or "").strip()
        if not spec:
            return False
        if "::" in spec:
            wanted_app, wanted_title = spec.split("::", 1)
        else:
            wanted_app, wanted_title = spec, ""
        wanted_app = wanted_app.strip().lower()
        wanted_title = wanted_title.strip().lower()
        app = (self.binary or self.app).lower()
        title = self.title.lower()
        if wanted_app and wanted_app not in {app, self.app.lower()}:
            return False
        if wanted_title and wanted_title not in title:
            return False
        return bool(wanted_app or wanted_title)


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------
def parse_sink_inputs_json(payload: str) -> list[AudioStreamInfo]:
    """Convierte la salida JSON de ``pactl list sink-inputs`` en streams.

    Acepta tanto el JSON de un array (``pactl --format=json list sink-inputs``)
    como el de un objeto suelto, y tolera datos incompletos.
    """
    try:
        data = json.loads(payload or "[]")
    except (TypeError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    streams: list[AudioStreamInfo] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        props = item.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        app = str(props.get("application.name") or "").strip()
        binary = str(props.get("application.process.binary") or "").strip()
        title = str(
            props.get("media.title") or props.get("media.name") or props.get("application.name") or ""
        ).strip()
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        try:
            sink = int(item.get("sink") or 0)
        except (TypeError, ValueError):
            sink = 0
        streams.append(
            AudioStreamInfo(
                index=index,
                app=app or binary,
                title=title,
                binary=binary,
                sink=sink,
                sink_name=str(item.get("sink_name") or "").strip(),
            )
        )
    return streams


def parse_sink_inputs_text(text: str) -> list[AudioStreamInfo]:
    """Convierte la salida de texto de ``pactl list sink-inputs`` en streams.

    Es el camino alternativo para servidores PulseAudio sin salida JSON.
    Formato típico::

        Sink Input #12
            Sink: 0
            Properties:
                application.name = "Chromium"
                media.title = "YouTube"
    """
    streams: list[AudioStreamInfo] = []
    current: dict[str, object] | None = None
    in_properties = False
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Sink Input #"):
            if current is not None:
                stream = _stream_from_props(current)
                if stream is not None:
                    streams.append(stream)
            raw_index = line.split("#", 1)[1].strip()
            try:
                int(raw_index)
            except (TypeError, ValueError):
                current = None
                continue
            current = {"index": raw_index, "props": {}}
            in_properties = False
            continue
        if current is None:
            continue
        if line.lower().startswith("properties:"):
            in_properties = True
            continue
        if in_properties:
            if "=" not in line:
                in_properties = False
                continue
            key, _, value = line.partition("=")
            current["props"][key.strip()] = value.strip().strip('"')  # type: ignore[index]
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key == "sink":
                try:
                    current["sink"] = int(value.strip())
                except ValueError:
                    pass
    if current is not None:
        stream = _stream_from_props(current)
        if stream is not None:
            streams.append(stream)
    return streams


def _stream_from_props(block: dict) -> AudioStreamInfo | None:
    """Construye un :class:`AudioStreamInfo` a partir de un bloque de texto."""
    props = block.get("props") or {}
    try:
        index = int(str(block.get("index", "0")).strip())
    except (TypeError, ValueError):
        return None
    app = str(props.get("application.name", "")).strip()
    binary = str(props.get("application.process.binary", "")).strip()
    title = str(props.get("media.title") or props.get("media.name") or "").strip()
    return AudioStreamInfo(
        index=index,
        app=app or binary,
        title=title,
        binary=binary,
        sink=int(block.get("sink") or 0),
    )


def list_sink_map(runner: Runner | None = None) -> dict[int, str]:
    """Mapa índice de sink → nombre de sink.

    ``pactl list sink-inputs`` solo da el *índice* del sink de destino, no su
    nombre, así que hace falta esta tabla para saber si un stream está en el
    sink virtual de captura y para devolverlo a su sitio.
    """
    run = runner or _default_runner
    if runner is None and shutil.which("pactl") is None:
        return {}
    try:
        out = run(["pactl", "list", "short", "sinks"])
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("no se pudo listar los sinks: %s", exc)
        return {}
    if out.returncode != 0:
        return {}
    sinks: dict[int, str] = {}
    for line in (out.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            index = int(parts[0].strip())
        except ValueError:
            continue
        name = parts[1].strip()
        if name:
            sinks[index] = name
    return sinks


def list_audio_streams(runner: Runner | None = None) -> list[AudioStreamInfo]:
    """Lista los streams de audio activos (apps/pestañas reproduciendo).

    Devuelve una lista vacía si ``pactl`` no está disponible o no hay nada
    reproduciéndose; nunca lanza.
    """
    run = runner or _default_runner
    if runner is None and shutil.which("pactl") is None:
        logger.warning("pactl no disponible: no se pueden listar streams por aplicación")
        return []
    for args in (
        ["pactl", "--format=json", "list", "sink-inputs"],
        ["pactl", "list", "sink-inputs"],
    ):
        try:
            out = run(args)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("no se pudo listar sink-inputs: %s", exc)
            return []
        if out.returncode != 0:
            continue
        streams = (
            parse_sink_inputs_json(out.stdout)
            if "--format=json" in args
            else parse_sink_inputs_text(out.stdout)
        )
        if not streams:
            # Un servidor sin salida JSON devuelve texto plano: se reintenta.
            streams = parse_sink_inputs_text(out.stdout)
        if streams:
            sinks = list_sink_map(runner=run)
            streams = [
                replace(s, sink_name=sinks.get(s.sink, s.sink_name))
                for s in streams
            ]
            return sorted(streams, key=lambda s: (s.app.lower(), s.title.lower()))
    return []


# ---------------------------------------------------------------------------
# Enrutado
# ---------------------------------------------------------------------------
class StreamRouter:
    """Mueve un stream a un null sink y lo devuelve a su sitio al terminar.

    :param sink_name: nombre del null sink virtual.
    :param runner: ejecutor de comandos (inyectable para tests).
    """

    def __init__(
        self,
        *,
        sink_name: str = "subtitle_capture",
        runner: Runner | None = None,
    ) -> None:
        self._sink_name = sink_name
        self._run = runner or _default_runner
        self._lock = threading.RLock()
        self._moved: dict[int, int] = {}
        self._module_ids: list[int] = []
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()

    # -- propiedades -------------------------------------------------------
    @property
    def sink_name(self) -> str:
        """Nombre del null sink virtual."""
        return self._sink_name

    @property
    def capture_source(self) -> str:
        """Source de PA que hay que capturar (el monitor del null sink)."""
        return f"{self._sink_name}.monitor"

    @property
    def routed_indexes(self) -> list[int]:
        """Índices de los sink inputs movidos a este null sink."""
        with self._lock:
            return sorted(self._moved)

    # -- comandos ----------------------------------------------------------
    def _pactl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["pactl", *args])

    def _load_module(self, *args: str) -> int | None:
        try:
            out = self._pactl("load-module", *args)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("no se pudo cargar el módulo %s: %s", args, exc)
            return None
        if out.returncode != 0:
            logger.warning(
                "pactl load-module %s falló (%s): %s",
                " ".join(args),
                out.returncode,
                (out.stderr or "").strip(),
            )
            return None
        raw = (out.stdout or "").strip()
        try:
            module_id = int(raw)
        except (TypeError, ValueError):
            module_id = None
        if module_id is not None:
            with self._lock:
                self._module_ids.append(module_id)
        return module_id

    def _unload_module(self, module_id: int) -> None:
        try:
            self._pactl("unload-module", str(module_id))
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("no se pudo descargar el módulo %s: %s", module_id, exc)

    # -- null sink ---------------------------------------------------------
    def ensure_null_sink(self) -> bool:
        """Crea el null sink virtual si no existe. ``True`` si está disponible."""
        if self._null_sink_exists():
            return True
        module_id = self._load_module(
            "module-null-sink",
            f"sink_name={self._sink_name}",
            "rate=48000",
            "channels=2",
        )
        if module_id is None:
            return False
        time.sleep(0.3)  # el servidor tarda un instante en publicar el sink
        return self._null_sink_exists()

    def _null_sink_exists(self) -> bool:
        try:
            out = self._pactl("list", "short", "sinks")
        except (OSError, subprocess.SubprocessError):
            return False
        return any(self._sink_name in line for line in (out.stdout or "").splitlines())

    def default_sink_name(self) -> str | None:
        """Nombre del sink de salida por defecto (para el loopback)."""
        try:
            out = self._pactl("get-default-sink")
        except (OSError, subprocess.SubprocessError):
            return None
        name = (out.stdout or "").strip()
        return name or None

    # -- enrutado ----------------------------------------------------------
    def route(self, index: int, *, sink: int | None = None) -> bool:
        """Mueve el sink input ``index`` al null sink virtual.

        :param sink: sink de destino; por defecto el null sink virtual.
        """
        target = self._sink_name if sink is None else str(sink)
        # Se recuerda el sink ORIGINAL antes de mover: sin esto, al restaurar
        # el stream volvería al sink virtual (donde ya está) en vez de al
        # dispositivo real, y el usuario se quedaría sin audio.
        original = self._original_sink(index)
        try:
            out = self._pactl("move-sink-input", str(index), target)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("no se pudo mover el stream %s: %s", index, exc)
            return False
        if out.returncode != 0:
            logger.warning(
                "no se pudo mover el stream %s a %s: %s",
                index,
                target,
                (out.stderr or "").strip(),
            )
            return False
        with self._lock:
            self._moved.setdefault(index, original)
        return True

    def route_matching(self, spec: str, streams: Iterable[AudioStreamInfo] | None = None) -> list[int]:
        """Mueve todos los streams que coinciden con ``spec``.

        Devuelve los índices movidos (vacío si no hay coincidencias).
        """
        candidates = list(streams) if streams is not None else self.list_streams()
        moved: list[int] = []
        for stream in candidates:
            if not stream.matches(spec):
                continue
            with self._lock:
                already = stream.index in self._moved
            if already:
                continue
            if self.route(stream.index):
                moved.append(stream.index)
        if moved:
            logger.info("streams enrutados al sink virtual: %s", ", ".join(map(str, moved)))
        return moved

    def list_streams(self) -> list[AudioStreamInfo]:
        """Lista los streams activos usando el ejecutor de este router."""
        return list_audio_streams(runner=self._run)

    def enable_loopback(self, sink: str | None = None, *, latency_msec: int = 20) -> bool:
        """Devuelve el audio del null sink a los parlantes.

        Sin esto, el stream capturado dejaría de oírse (está en un sink
        virtual). El audio vuelve al sink real, así que el usuario sigue
        escuchándolo mientras la app lo captura.
        """
        target = sink or self.default_sink_name()
        if not target:
            logger.warning("no hay sink de salida por defecto: el audio capturado no sonará")
            return False
        module_id = self._load_module(
            "module-loopback",
            f"source={self.capture_source}",
            f"sink={target}",
            f"latency_msec={latency_msec}",
            "source_dont_move=true",
            "sink_dont_move=true",
        )
        return module_id is not None

    def restore(self) -> None:
        """Devuelve los streams a su sink original y descarga los módulos."""
        self.stop_watching()
        with self._lock:
            moved = dict(self._moved)
            self._moved.clear()
            module_ids = list(self._module_ids)
            self._module_ids.clear()
        for index, original in moved.items():
            if original is None:
                # Sin sink original conocido: se manda al sink por defecto.
                original = self.default_sink_name()
            if not original:
                continue
            try:
                out = self._pactl("move-sink-input", str(index), str(original))
            except (OSError, subprocess.SubprocessError):
                logger.warning("no se pudo restaurar el stream %s", index)
                continue
            if out.returncode != 0:
                logger.warning(
                    "no se pudo restaurar el stream %s a %s: %s",
                    index,
                    original,
                    (out.stderr or "").strip(),
                )
        for module_id in module_ids:
            self._unload_module(module_id)

    def _original_sink(self, index: int) -> str | None:
        """Nombre del sink donde estaba el stream **antes** de moverlo."""
        sinks = list_sink_map(runner=self._run)
        sink_index = self._sink_of(index)
        if sink_index is None:
            return None
        return sinks.get(sink_index)

    def _sink_of(self, index: int) -> int | None:
        """Sink actual de un sink input (``None`` si ya no existe)."""
        try:
            out = self._pactl("--format=json", "list", "sink-inputs")
        except (OSError, subprocess.SubprocessError):
            return None
        for stream in parse_sink_inputs_json(out.stdout):
            if stream.index == index:
                return stream.sink
        return None

    def cleanup_orphans(self) -> int:
        """Saca del null sink virtual los streams que quedaron ahí.

        Si la app murió a la fuerza, algún stream puede seguir apuntando al
        sink virtual y el usuario no oiría nada. Se llama al arrancar.
        """
        try:
            out = self._pactl("--format=json", "list", "sink-inputs")
        except (OSError, subprocess.SubprocessError):
            return 0
        streams = parse_sink_inputs_json(out.stdout)
        if not streams:
            return 0
        sinks = list_sink_map(runner=self._run)
        capture_index = next(
            (idx for idx, name in sinks.items() if name == self._sink_name), None
        )
        if capture_index is None:
            return 0
        fallback = self.default_sink_name()
        restored = 0
        for stream in streams:
            if stream.sink != capture_index:
                continue
            target = fallback or sinks.get(stream.sink)
            if not target:
                continue
            try:
                moved = self._pactl("move-sink-input", str(stream.index), str(target))
            except (OSError, subprocess.SubprocessError):
                continue
            if moved.returncode != 0:
                continue
            restored += 1
        if restored:
            logger.info("streams huérfanos devueltos al sink real: %d", restored)
        return restored

    # -- vigilante ---------------------------------------------------------
    def start_watching(self, spec: str, *, interval: float = 1.5) -> None:
        """Vigila y enruta los streams nuevos que coincidan con ``spec``.

        Permite seguir capturando cuando la app o la pestaña renuevan su
        stream de audio (lo habitual en el navegador).
        """
        self.stop_watching()
        if not spec:
            return
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop, args=(spec, interval), name="stream-watcher", daemon=True
        )
        self._watch_thread.start()

    def _watch_loop(self, spec: str, interval: float) -> None:
        while not self._watch_stop.is_set():
            try:
                self.route_matching(spec)
            except Exception:  # pragma: no cover - defensivo
                logger.exception("fallo en el vigilante de streams")
            self._watch_stop.wait(interval)

    def stop_watching(self) -> None:
        """Detiene el vigilante de streams."""
        self._watch_stop.set()
        thread, self._watch_thread = self._watch_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
