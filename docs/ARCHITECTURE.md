# Arquitectura — Fase 0

Objetivo de la fase: dejar el proyecto preparado para crecer durante las 12
fases restantes, fijando los contratos entre etapas para que **cambiar un
motor no obligue a reescribir la aplicación**.

## 1. La idea central: cuatro puertos y sus adaptadores

El flujo de datos es siempre el mismo:

```
AudioSource → SpeechRecognizer → Translator → SubtitleRenderer
  (audio)      (parcial/final)     (texto)      (cues en pantalla)
```

Cada flecha es un **contrato de datos** (una dataclass) y cada caja es una
**interfaz** (`abc.ABC`). El pipeline depende exclusivamente de las interfaces;
solo el *composition root* (`app/core/factory.py`) nombra clases concretas.

| Interfaz | Contrato de entrada | Contrato de salida | Implementaciones de Fase 0 |
|---|---|---|---|
| `AudioSource` | dispositivo/archivo | `AudioChunk` | `SyntheticAudioSource`, `WavFileAudioSource` |
| `SpeechRecognizer` | `AudioChunk` | `RecognitionResult` | `ScriptedSpeechRecognizer` |
| `Translator` | `str` | `TranslationResult` | `PassthroughTranslator`, `MappingTranslator` |
| `SubtitleRenderer` | `SubtitleCue` | pantalla | `ConsoleRenderer`, `QtOverlayRenderer` |

## 2. Decisiones tomadas

### 2.1 Concurrencia: hilos + colas

Se descarta `asyncio` en el núcleo porque la captura de audio nativa y los
modelos locales son síncronos y **bloqueantes**, y mezclarlos con async obliga
a adaptadores frágiles. En su lugar:

* El pipeline arranca tres hilos: *capture*, *dispatch* y *translation*.
* Los resultados viajan por `queue.Queue`, que da orden y contrapresión.
* Cada componente sigue siendo sincrónico y, por tanto, trivial de testear.

Si en una fase posterior se integra un proveedor cloud puramente async, se
envuelve en un adaptador: la frontera ya está aislada en la interfaz.

### 2.2 Un solo formato de audio en la frontera

Todas las fuentes entregan **mono `float32`** en `AudioChunk`. El *downmix* y la
conversión de formato son responsabilidad de la fuente, así los motores de voz
nunca conocen el dispositivo de origen.

### 2.3 Parciales y finales desde el primer día

`RecognitionResult` distingue `is_final`. El pipeline asigna a cada enunciado un
`cue_id` compartido por todas sus hipótesis, y `SubtitleRenderer` **reemplaza**
la cue del mismo `cue_id` en lugar de acumular líneas. Esto es lo que permite
retroalimentación en vivo sin parpadeos ni duplicados, y es el mismo modelo que
usarán los motores en streaming.

### 2.4 La UI nunca vive en un hilo de trabajo

El pipeline no llama al renderizador: publica eventos en `SubtitlePipeline.cues`
y **el hilo principal** los dibuja. Qt (y cualquier toolkit) exige tocar la UI
solo desde su hilo, así que esta frontera se respeta desde la Fase 0.

### 2.5 Qt es opcional

`QtOverlayRenderer` importa PySide6 de forma perezosa. El paquete `app.overlay`
se importa sin Qt instalado, y el núcleo y los tests no dependen de ninguna
librería gráfica.

### 2.6 El paquete `app/core/` es una adición

La estructura pedida no incluía una carpeta transversal. Se añadió para no
duplicar en cada subpaquete el ciclo de vida (`PipelineComponent`), la jerarquía
de errores, los eventos y el orquestador.

## 3. Contratos de concurrencia (resumen)

* `AudioSource.read` **bloquea** hasta `timeout`; devuelve `None` si no hay
  datos. Una fuente finita llama a `_mark_inactive()` al terminar, y el pipeline
  interpreta "sin datos + inactivo" como fin de stream.
* `SpeechRecognizer.feed/flush` son invocables desde el hilo de captura; los
  resultados pueden publicarse de forma asíncrona en `results`.
* `SpeechRecognizer.stop` debe **unir sus hilos** y garantizar que todo
  resultado pendiente ya está encolado.
* `Translator.translate` puede bloquear: corre en su propio hilo.
* `SubtitleRenderer.render/clear` se llaman desde el hilo principal y no deben
  bloquear.
* `start()`/`stop()` son idempotentes, y `stop()` ejecuta el hook de limpieza
  **aunque el componente ya esté inactivo** (una fuente que llegó al EOF igual
  necesita cerrar su archivo).

## 4. Cómo extender sin tocar el resto de la aplicación

### Añadir un motor de voz real (por ejemplo, faster-whisper)

1. Crear `app/speech/faster_whisper.py` implementando `SpeechRecognizer`.
2. Registrarlo en `build_recognizer` (`app/core/factory.py`).
3. Añadir su dependencia en `requirements.txt`.

El pipeline, la traducción, el renderizado y los tests no cambian.

### Añadir un origen de audio (por ejemplo, micrófono)

Igual, pero implementando `AudioSource` en `app/audio/` y registrándolo en
`build_audio_source`. `"microphone"` ya está reservado y hoy falla con un
mensaje explícito.

### Añadir un modo de presentación

Implementar `SubtitleRenderer`. La vista por consola y el overlay Qt existentes
sirven de referencia.

## 5. Estructura

```
subtitle-app/
├── app/
│   ├── core/          # transversal: ciclo de vida, errores, eventos, pipeline, factory
│   ├── audio/         # AudioSource + AudioChunk + adaptadores
│   ├── speech/        # SpeechRecognizer + RecognitionResult + simulador
│   ├── translation/   # Translator + TranslationResult + adaptadores
│   ├── subtitles/     # SubtitleCue/SubtitleStyle + SubtitleRenderer
│   ├── overlay/       # renderizadores concretos (consola, Qt)
│   ├── settings/      # AppSettings: JSON + variables de entorno
│   └── ui/            # panel de control (fases posteriores)
├── tests/
├── assets/
├── docs/
├── main.py
├── pyproject.toml
└── requirements.txt
```

## 6. Deuda asumida y qué queda para las próximas fases

* `translate_partials` existe y funciona, pero se documenta que puede retrasar
  los parciales: la traducción de parciales llega al *worker* y compite con los
  finales.
* El overlay Qt se bombea con `processEvents`; en la fase de UI se migrará a un
  `QApplication.exec()` real con panel de control.
* El `WavFileAudioSource` no remuestrea: asume que el archivo ya está a la
  frecuencia de trabajo.
* `AppSettings.to_dict()` conserva `SubtitlePosition` como enum; si se serializa
  a JSON habrá que convertirlo a `value`.
