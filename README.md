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

### Lanzador `run.sh`

Atajo para el uso diario (crea/configura el venv y evita repetir flags):

```bash
./run.sh                              # audio del sistema (monitor) + Groq
./run.sh --audio synthetic --realtime # demo, sin red ni clave
./run.sh --list-devices               # ver monitores/dispositivos
./run.sh --list-streams               # ver apps/pestañas con audio
```

Sin argumentos arranca en modo gráfico (`--renderer qt`) sobre el audio del
sistema con Groq. Cualquier flag extra se pasa tal cual a la app. Usa el venv
`./.venv` (`VENV_DIR` es configurable) y lee las claves de la BD cifrada; no
maneja secretos.

Salida esperada (los parciales se reescriben y el final llega traducido):

```
… hello
… hello world
… the subtitles
… the subtitles are
▸ hola mundo
▸ los subtítulos funcionan
```

## Elegir de dónde sale el audio

El panel **Origen de audio** permite capturar solo lo que suena en una
aplicación o pestaña concreta, en vez de todo el sistema:

| Origen | Qué captura |
|---|---|
| Todo el sistema (monitor) | todo lo que suena |
| **Aplicación o pestaña** | solo el stream elegido |
| Micrófono | la entrada del micrófono |
| Demo (sintético) | audio de prueba, sin red |
| Archivo WAV | un archivo local |

Desde la línea de órdenes:

```bash
# Ver qué está reproduciendo audio ahora mismo
./run.sh --list-streams

# Capturar solo una app/pestaña (seguir oyéndola por los parlantes)
./run.sh --audio app --stream 'chromium::YouTube'

# Capturarla sin moverla de los parlantes
./run.sh --audio app --stream 'chromium::YouTube' --no-stream-loopback
```

**Cómo funciona.** El servidor de audio (PulseAudio o PipeWire) expone cada
reproductor como un *sink input*. La app mueve el elegido a un **sink virtual**
(`module-null-sink`), captura el monitor de ese sink con `parec` y, si querés,
lo devuelve a los parlantes con un **loopback** (`module-loopback`). Al cerrar,
los streams vuelven a su sitio y los módulos se descargan: el sistema queda
como estaba.

Requisitos: `pactl` y `parec` (paquete `pulseaudio-utils`).

> **Granularidad por pestaña.** Depende del navegador: si expone un stream por
> pestaña, se elige la pestaña; si agrupa todo el navegador en un único stream
> (Chromium suele hacerlo), se captura el navegador completo.

## Proveedores de IA y claves

El panel **Motor IA** permite elegir el proveedor de traducción y pegar su API key:

| Proveedor | Variable | Gratis |
|---|---|---|
| Groq | `GROQ_API_KEY` | sí |
| OpenAI | `OPENAI_API_KEY` | no |
| Claude (Anthropic) | `ANTHROPIC_API_KEY` | no |
| OpenRouter | `OPENROUTER_API_KEY` | sí (`:free`) |
| Google Gemini | `GEMINI_API_KEY` | sí |

Tras validar la clave, la app lista **todos los modelos** del proveedor (los
gratuitos van primero y marcados) y cambia el motor en vivo.

**Las claves se guardan cifradas** en `~/.config/traductor-tiempo-real/keys.db`
(SQLite, modo 600). La clave maestra vive en el **llavero del sistema**
(`secret-tool`/libsecret); nunca hay secretos en claro ni en la BD ni en logs.
Si el llavero no está disponible, la clave solo vive en memoria durante la sesión.
El antiguo `.env` se migra a la BD cifrada al arrancar.

## Tests

```bash
python3 -m pytest
```

## Estructura

```
app/
├── core/          # ciclo de vida, errores, eventos, pipeline y composition root
├── audio/         # AudioSource + AudioChunk (sintético, WAV, monitor, app/pestaña)
├── speech/        # SpeechRecognizer + RecognitionResult (simulador)
├── translation/   # Translator + TranslationResult (passthrough, diccionario)
├── subtitles/     # SubtitleCue/SubtitleStyle + SubtitleRenderer
├── overlay/       # renderizadores concretos (consola, Qt perezoso)
├── settings/      # AppSettings (JSON + entorno) y keyvault (claves cifradas SQLite)
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
