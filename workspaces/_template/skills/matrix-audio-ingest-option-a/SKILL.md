# Matrix Audio Ingest Option A

Guía para que el worker Matrix descargue audios mxc:// al workspace del agente y active la transcripción local. Úsala al integrar mensajes de voz/audio de Matrix con Whisper local.

# Matrix audio ingest — opción A

## Objetivo

El worker Matrix debe encargarse de recibir eventos con audio, descargar el contenido `mxc://...` usando la sesión del bot y guardar el archivo dentro del workspace del agente. Después debe invocar al agente con una ruta local (`audio_path`) para que use `matrix-audio-handler` / `local-audio-transcriber`.

## Eventos soportados

Detectar eventos `m.room.message` donde:

- `content.msgtype == "m.audio"`, o
- `content.msgtype == "m.file"` y `content.info.mimetype` empieza por `audio/`, o
- `content.url` existe y `content.info.mimetype` empieza por `audio/`.

Campos útiles:

```json
{
  "event_id": "$...",
  "room_id": "!...",
  "sender": "@user:server",
  "content": {
    "msgtype": "m.audio",
    "body": "voice-message.ogg",
    "url": "mxc://server/mediaid",
    "info": {
      "mimetype": "audio/ogg",
      "size": 12345,
      "duration": 4000
    }
  }
}
```

## Descarga recomendada

1. Crear directorio en el workspace del agente:

```text
incoming_matrix_audio/
```

2. Sanear `event_id` para nombre de archivo:

```text
$abc:def → abc_def
```

3. Elegir extensión según mimetype/body:

- `audio/ogg` → `.ogg`
- `audio/opus` → `.opus`
- `audio/mpeg` → `.mp3`
- `audio/mp4` / `audio/x-m4a` → `.m4a`
- `audio/wav` → `.wav`
- `audio/webm` → `.webm`
- fallback seguro → `.ogg`

4. Guardar como:

```text
incoming_matrix_audio/<safe_event_id><ext>
```

5. Límite recomendado: rechazar audios > 50 MB antes de descargar o antes de invocar al agente.

## Payload al agente

Después de descargar, invocar al agente con un mensaje o payload equivalente a:

```text
Audio recibido por Matrix. Procesa con matrix-audio-handler:
audio_path=incoming_matrix_audio/<safe_event_id>.ogg
room_id=!room:server
sender=@user:server
event_id=$event
mimetype=audio/ogg
filename=voice-message.ogg
```

Si el runtime permite tool routing directo, llamar a:

```json
{
  "tool": "matrix-audio-handler",
  "arguments": {
    "audio_path": "incoming_matrix_audio/<safe_event_id>.ogg",
    "room_id": "!room:server",
    "sender": "@user:server",
    "event_id": "$event",
    "mimetype": "audio/ogg",
    "filename": "voice-message.ogg",
    "model": "base"
  }
}
```

## Respuesta esperada de la tool

```json
{
  "ok": true,
  "matrix": {
    "room_id": "!...",
    "sender": "@...",
    "event_id": "$...",
    "mimetype": "audio/ogg",
    "filename": "voice-message.ogg",
    "audio_path": "incoming_matrix_audio/...ogg"
  },
  "transcription": {
    "language": "es",
    "model": "base",
    "text": "..."
  },
  "agent_prompt": "Audio recibido por Matrix..."
}
```

El worker puede reenviar `agent_prompt` como mensaje de usuario al agente para que este entienda y actúe sobre la transcripción.

## Pseudocódigo Python

```python
import mimetypes
import re
from pathlib import Path

MAX_AUDIO_BYTES = 50 * 1024 * 1024

MIME_EXT = {
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "video/webm": ".webm",
}

def is_audio_event(event):
    content = event.source.get("content", {})
    msgtype = content.get("msgtype")
    mimetype = (content.get("info") or {}).get("mimetype", "")
    return bool(content.get("url")) and (
        msgtype == "m.audio" or (msgtype == "m.file" and mimetype.startswith("audio/"))
    )

def safe_id(event_id):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", event_id.strip("$"))[:120]

def extension_for(mimetype, body):
    if mimetype in MIME_EXT:
        return MIME_EXT[mimetype]
    ext = Path(body or "").suffix.lower()
    if ext in {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".mp4", ".wav", ".webm", ".flac", ".aac"}:
        return ext
    return ".ogg"

async def handle_audio_event(client, event, agent_workspace, invoke_agent):
    content = event.source.get("content", {})
    info = content.get("info") or {}
    size = info.get("size")
    if size and int(size) > MAX_AUDIO_BYTES:
        return

    mxc_url = content["url"]
    body = content.get("body") or "voice-message"
    mimetype = info.get("mimetype") or mimetypes.guess_type(body)[0] or "audio/ogg"
    ext = extension_for(mimetype, body)

    out_dir = Path(agent_workspace) / "incoming_matrix_audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"incoming_matrix_audio/{safe_id(event.event_id)}{ext}"
    abs_path = Path(agent_workspace) / rel_path

    # nio-style clients commonly expose download(mxc_url) or download(server, media_id).
    media = await client.download(mxc_url)
    data = media.body if hasattr(media, "body") else media
    if len(data) > MAX_AUDIO_BYTES:
        return
    abs_path.write_bytes(data)

    await invoke_agent(
        agent="optimus",
        message=(
            "Audio recibido por Matrix. Procesa con matrix-audio-handler:\n"
            f"audio_path={rel_path}\n"
            f"room_id={event.room_id}\n"
            f"sender={event.sender}\n"
            f"event_id={event.event_id}\n"
            f"mimetype={mimetype}\n"
            f"filename={body}"
        ),
    )
```

## Verificación

1. Worker conectado: `matrix_status` debe indicar `connected: true`.
2. Enviar audio corto por Matrix.
3. Confirmar archivo creado en `incoming_matrix_audio/`.
4. Confirmar que la tool `matrix-audio-handler` devuelve `ok: true`.
5. Confirmar que el agente responde al contenido transcrito, no solo a metadatos.

## Seguridad

- No pasar tokens Matrix al agente.
- No guardar archivos fuera del workspace.
- No aceptar rutas absolutas ni `..`.
- Límite de 50 MB.
- Evitar loggear contenido binario o credenciales.

