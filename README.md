# subtitle-app

Subtítulos en tiempo real con traducción. El proyecto está organizado en torno a
**cuatro interfaces intercambiables** para poder cambiar de motor (audio, voz,
traducción, presentación) sin tocar el resto de la aplicación.

> **Estado: Fase 0 — Arquitectura y decisiones técnicas.**
> Los motores reales se integran en las fases siguientes. Hoy el pipeline se
> ejecuta de punta a punta con un origen sintético, un reconocedor programado y
> traducción por diccionario, lo que permite validar todos los contratos.

## Pipeline

```
AudioSource → SpeechRecognizer → Translator → SubtitleRenderer
```

| Interfaz | Responsabilidad | Contrato de datos |
|---|---|---|
| `AudioSource` | Capturar audio mono `float32` | `AudioChunk` |
| `SpeechRecognizer` | Transcribir, con parciales y finales | `RecognitionResult` |
| `Translator` | Traducir un segmento de texto | `TranslationResult` |
| `SubtitleRenderer` | Dibujar y reemplazar cues | `SubtitleCue` |

## Puesta en marcha

```bash
python3 -m pip install -r requirements.txt

# Demo completo: fuente sintética → voz simulada → traducción → consola
python3 main.py --translation-engine mapping

# Ver los subtítulos al ritmo real
python3 main.py --translation-engine mapping --realtime

# Desde un archivo de configuración
python3 main.py --config assets/config.example.json
```

Salida esperada (los parciales se reescriben y el final llega traducido):

```
… hello
… hello world
… the subtitles
… the subtitles are
▸ hola mundo
▸ los subtítulos funcionan
```

## Tests

```bash
python3 -m pytest
```

## Estructura

```
app/
├── core/          # ciclo de vida, errores, eventos, pipeline y composition root
├── audio/         # AudioSource + AudioChunk (sintético, WAV)
├── speech/        # SpeechRecognizer + RecognitionResult (simulador)
├── translation/   # Translator + TranslationResult (passthrough, diccionario)
├── subtitles/     # SubtitleCue/SubtitleStyle + SubtitleRenderer
├── overlay/       # renderizadores concretos (consola, Qt perezoso)
├── settings/      # AppSettings (JSON + variables de entorno)
└── ui/            # panel de control (fases posteriores)
```

## Añadir un motor nuevo

1. Implementar la interfaz correspondiente en su paquete
   (`app/audio`, `app/speech`, `app/translation` o `app/subtitles`).
2. Registrarlo en `app/core/factory.py`.
3. Añadir su dependencia a `requirements.txt`.

El pipeline, la configuración y los tests no cambian.

## Documentación

Las decisiones de arquitectura (modelo de concurrencia, contratos entre etapas,
frontera con la UI) están razonadas en [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
