"""Matriz reproducible de latencia y calidad (Fase 7).

Mide por separado los tres tramos:

1. captura → transcripción (ventanas de 5/8/10 s, modelos turbo/v3),
2. transcripción → traducción (gpt-oss-20b/120b, 1 idioma),
3. traducción → visualización (overhead local scripted+mapping, sin red).

Requiere ``GROQ_API_KEY`` solo para los tramos 1-2; el tramo 3 siempre corre.
Los WAV se generan con ``espeak-ng`` si está disponible; si no, se reutilizan
``/tmp/tts_es.wav`` y ``/tmp/tts_en.wav`` cuando existen.

Uso::

    GROQ_API_KEY=... python tools/measure_latency.py [--out results.json]
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = {
    "es": "hola mundo, los subtítulos están funcionando",
    "en": "hello world, the subtitles are working",
}


def make_wav(text: str, voice: str, path: Path) -> Path:
    if shutil.which("espeak-ng") is None:
        raise RuntimeError("espeak-ng no disponible para generar fixtures")
    subprocess.run(
        ["espeak-ng", "-v", voice, "-s", "150", "-w", str(path), text],
        check=True,
    )
    return path


def slice_wav(path: Path, window_s: float, rate: int = 16_000) -> list[bytes]:
    """Trocea un WAV en ventanas de ``window_s`` s, normalizado a 16 kHz mono."""
    import numpy as np

    with wave.open(str(path), "rb") as reader:
        channels = reader.getnchannels()
        src_rate = reader.getframerate()
        raw = reader.readframes(reader.getnframes())
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if src_rate != rate:
        idx = (np.linspace(0, len(audio) - 1, int(len(audio) * rate / src_rate))).astype(int)
        audio = audio[idx]
    frames = int(window_s * rate)
    chunks, offset = [], 0
    while offset < len(audio):
        part = audio[offset : offset + frames]
        if len(part) < int(1.0 * rate):
            break
        pcm16 = (part / max(1.0, abs(part).max()) * 30_000).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(pcm16.tobytes())
        chunks.append(buffer.getvalue())
        offset += frames
    return chunks


def wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate simple sobre texto normalizado."""
    ref = reference.lower().split()
    hyp = hypothesis.lower().split()
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(prev[j] + 1, current[j - 1] + 1, prev[j - 1] + (word != other)))
        prev = current
    return prev[-1] / len(ref)


def main() -> int:
    parser = argparse.ArgumentParser(description="Matriz de latencia y calidad (Fase 7).")
    parser.add_argument("--out", default="", help="JSON con los resultados.")
    parser.add_argument("--windows", default="5,8,10", help="Ventanas STT a probar.")
    args = parser.parse_args()

    import os

    api_key = os.environ.get("GROQ_API_KEY", "")
    from app.speech.groq import DEFAULT_MODEL as STT_DEFAULT
    from app.speech.groq import transcribe_audio
    from app.translation.groq import DEFAULT_MODEL as TR_DEFAULT
    from app.translation.groq import FALLBACK_MODEL as TR_FALLBACK
    from app.translation.groq import translate_text

    windows = [float(w) for w in args.windows.split(",") if w.strip()]
    results: dict = {"windows": [], "translation": [], "render_overhead_s": None}

    with tempfile.TemporaryDirectory() as tmp:
        fixtures = {}
        for lang, text in FIXTURES.items():
            existing = Path(f"/tmp/tts_{lang}.wav")
            if existing.is_file():
                fixtures[lang] = existing
            else:
                fixtures[lang] = make_wav(text, lang, Path(tmp) / f"{lang}.wav")

        if api_key:
            from groq import Groq

            client = Groq(api_key=api_key)
            for window_s in windows:
                for lang, path in fixtures.items():
                    chunks = slice_wav(path, window_s)
                    for model in (STT_DEFAULT, "whisper-large-v3"):
                        t0 = time.monotonic()
                        texts = [
                            transcribe_audio(c, client=client, model=model, language=None)
                            for c in chunks
                        ]
                        dt = time.monotonic() - t0
                        plain = " ".join(t.text for t in texts)
                        results["windows"].append(
                            {
                                "window_s": window_s,
                                "lang": lang,
                                "model": model,
                                "chunks": len(chunks),
                                "total_s": round(dt, 2),
                                "per_chunk_s": round(dt / max(1, len(chunks)), 2),
                                "wer": round(wer(FIXTURES[lang], plain), 3),
                                "text": plain,
                            }
                        )
            sample = FIXTURES["en"]
            for model in (TR_DEFAULT, TR_FALLBACK):
                t0 = time.monotonic()
                out = translate_text(sample, "en", "es", client=client, model=model)
                dt = time.monotonic() - t0
                results["translation"].append(
                    {"model": model, "latency_s": round(dt, 2), "text": out.translated_text}
                )
        else:
            print("sin GROQ_API_KEY: solo tramo 3 (overhead local)")

    # Tramo 3: overhead traducción→visualización sin red.
    from app.audio.synthetic import SyntheticAudioSource
    from app.core.pipeline import SubtitlePipeline
    from app.overlay.console import ConsoleRenderer
    from app.speech.scripted import ScriptedSpeechRecognizer, ScriptedUtterance
    from app.translation.mapping import MappingTranslator

    pipe = SubtitlePipeline(
        audio_source=SyntheticAudioSource(chunk_duration=0.5, total_duration=2.0),
        recognizer=ScriptedSpeechRecognizer(
            [ScriptedUtterance("hello world", 0.5, 1.5, language="en")]
        ),
        translator=MappingTranslator({"hello world": "hola mundo"}),
        source_language="en",
        target_language="es",
    )
    pipe.run(ConsoleRenderer(stream=io.StringIO()))
    spans = pipe.latency.summary()
    results["render_overhead_s"] = spans

    print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"guardado en {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
