# Resumen de vídeos de YouTube

Resume vídeos de YouTube priorizando transcripciones existentes y usando transcripción local cuando no existan subtítulos. También puede publicar el resultado en una subpágina de Notion usando la credencial existente `notion`.

## Objetivo
Dado un enlace de YouTube, producir un resumen útil orientado a decisión: información general, participantes/ponentes, temas tratados, puntos clave, herramientas mencionadas, aportación real, esfuerzo requerido y recomendación de visionado. Si el usuario lo pide, publicar ese resumen bajo una página padre de Notion.

## Estado operativo
Dependencias Python instaladas en el workspace:
- `youtube-transcript-api`
- `yt-dlp`
- `openai-whisper`
- `requests`

Credenciales usadas:
- `openai-whisper-api`: fallback para transcripción vía API si la transcripción local no puede ejecutarse.
- `notion`: publicación de subpáginas en Notion.

Requisitos del sistema:
- `ffmpeg` disponible en PATH para Whisper local.

## Política principal
1. Validar que la URL sea de YouTube.
2. Obtener metadatos con `yt-dlp`.
3. Intentar obtener transcripción/subtítulos existentes con `youtube-transcript-api`.
4. Si no hay transcripción disponible:
   - si el vídeo dura más de 2 horas, detenerse y solicitar aprobación explícita antes de descargar/transcribir audio;
   - si dura 2 horas o menos, descargar audio con `yt-dlp`;
   - intentar transcripción local con `openai-whisper`;
   - si falla la vía local y existe credencial, usar `openai-whisper-api` como fallback.
5. Generar un resumen final con la estructura acordada.
6. Si se solicita publicación, crear una subpágina en Notion bajo `notion_parent_page_id` o `notion_parent_url`.
7. No guardar transcripciones completas salvo que el usuario lo pida; usar temporales.
8. No inventar enlaces de herramientas: incluir enlace oficial solo si es explícito o seguro; si no, marcar como “enlace no verificado”.

## Entrada esperada
- `url`: URL del vídeo de YouTube.
- Opcional: `output_language`; por defecto: `español`.
- Opcional: `approved_long`; por defecto: `False`. Debe ser `True` para transcribir vídeos >2h sin subtítulos.
- Opcional: `whisper_model`; por defecto: `small`.
- Opcional: `publish_to_notion`; por defecto: `False`.
- Opcional: `notion_parent_page_id`: ID de página padre de Notion.
- Opcional: `notion_parent_url`: URL de página padre de Notion; la skill extrae el ID automáticamente.
- Opcional: `notion_title`: título de la subpágina.
- Opcional: `summary_markdown`: contenido markdown final a publicar. Si no se pasa, publica el prompt/resumen preparado y devuelve `notion_warning`.

## Uso desde Python

```bash
python skills/youtube-video-summary/skill.py 'https://www.youtube.com/watch?v=VIDEO_ID'
```

Para vídeos de más de 2 horas sin transcripción existente:

```bash
python skills/youtube-video-summary/skill.py 'https://www.youtube.com/watch?v=VIDEO_ID' --approved-long
```

## Uso desde `handler(...)`

Preparar transcripción y prompt:

```python
handler(url='https://www.youtube.com/watch?v=VIDEO_ID')
```

Publicar en Notion tras generar el resumen final:

```python
handler(
    _agent=agent,
    url='https://www.youtube.com/watch?v=VIDEO_ID',
    publish_to_notion=True,
    notion_parent_url='https://www.notion.so/YouTube-3120e17cc60a80c9a36af7f9b28c9368',
    notion_title='Resumen - título del vídeo',
    summary_markdown='## Información y enlace\n...'
)
```

## Salida de `skill.py`
La función `handler(...)` devuelve:
- `status`: `ok` o `needs_approval`.
- `metadata`: título, canal, URL, duración, fecha, idioma, etc.
- `transcript_info`: fuente usada y datos de idioma/modelo.
- `transcript_compact`: transcripción limpia y compactada para resumen.
- `summary_prompt`: prompt listo para generar el resumen final con la estructura acordada.
- `notion`: si `publish_to_notion=True`, resultado de creación de subpágina con `id`, `url`, `created_time` y `blocks_sent`.

Si `status = needs_approval`, no descarga ni transcribe audio. Devuelve duración detectada y el siguiente paso.

## Estructura de resumen final
Usar esta estructura:

### Información y enlace
- Título
- Canal / autor
- URL
- Duración
- Fecha de publicación, si está disponible
- Idioma detectado o estimado
- Fuente usada: subtítulos de YouTube / transcripción local / API Whisper
- Participantes o ponentes identificados

### Veredicto rápido
- Recomendación: verlo / verlo parcialmente / no verlo
- Motivo en 2–4 líneas
- Para quién aporta valor

### Resumen y puntos clave
- Resumen por temas hablados
- Puntos clave o de interés
- Ideas accionables
- Matices, advertencias o limitaciones

### Herramientas mencionadas
Para cada herramienta detectada:
- Nombre
- Descripción breve
- Enlace oficial si se puede inferir con seguridad; si no, indicar “enlace no verificado” o no incluir enlace.

### Qué aporta
- Aprendizajes principales
- Novedad o profundidad
- Casos de uso concretos
- Qué queda sin cubrir

### Tiempo y esfuerzo
- Tiempo estimado para verlo completo
- Segmentos o momentos de mayor valor si hay timestamps
- Esfuerzo requerido: bajo / medio / alto
- Recomendación de consumo: completo, por secciones, o solo resumen

## Publicación en Notion
- Usa la API oficial de Notion `2022-06-28`.
- Extrae el ID de página desde URL o acepta UUID.
- Convierte markdown básico a bloques Notion: headings, párrafos, listas con viñetas y listas numeradas.
- Limita el envío inicial a 95 bloques para evitar límites de la API.
- No expone el token `notion`.

## Criterios de aprobación para vídeos largos
Si duración > 2 horas y no hay transcripción de YouTube, responder con:
- duración detectada;
- coste/tiempo esperado aproximado;
- preguntar si continuar con transcripción/resumen completo o solo metadatos/transcripción disponible.

## Notas de seguridad y calidad
- No exponer credenciales.
- No conservar audio ni transcripciones completas salvo petición explícita.
- Declarar limitaciones: mala calidad de audio, transcripción automática, ponentes no identificables, enlaces no verificados.
- Si la transcripción se recorta para entrar en contexto, indicarlo en el resumen.
