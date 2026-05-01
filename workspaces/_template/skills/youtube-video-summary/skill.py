#!/usr/bin/env python3
"""YouTube video summary helper skill.

This module gathers YouTube metadata and transcript text for the agent to summarize.
It prioritizes existing YouTube captions. If captions are unavailable, it can
transcribe downloaded audio locally with openai-whisper, or via the stored
`openai-whisper-api` credential when local ffmpeg is not available.

It can also publish the final summary to Notion using the stored Autobot
credential named exactly `notion`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

MAX_LOCAL_TRANSCRIBE_SECONDS = 7200
PREFERRED_TRANSCRIPT_LANGS = ["es", "es-ES", "ca", "ca-ES", "en", "en-US", "en-GB"]
OPENAI_WHISPER_CREDENTIAL_NAME = "openai-whisper-api"
NOTION_CREDENTIAL_NAME = "notion"
NOTION_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_TIMEOUT = 30


class SkillError(RuntimeError):
    pass


def _video_id(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().replace("www.", "")
    if host in {"youtu.be"}:
        vid = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
            vid = parsed.path.strip("/").split("/")[1]
        else:
            vid = ""
    else:
        raise SkillError("URL no válida: debe ser un enlace de YouTube.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or ""):
        raise SkillError("No se pudo extraer un video_id válido de la URL.")
    return vid


def _format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "desconocida"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _metadata(url: str) -> Dict[str, Any]:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "webpage_url": info.get("webpage_url") or url,
        "duration_seconds": info.get("duration"),
        "duration": _format_duration(info.get("duration")),
        "upload_date": info.get("upload_date"),
        "language": info.get("language"),
        "description": info.get("description"),
        "view_count": info.get("view_count"),
    }


def _try_youtube_transcript(video_id: str) -> Tuple[Optional[str], Dict[str, Any]]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
    except Exception as exc:
        return None, {"source": "youtube_transcript", "available": False, "error": f"Import error: {exc}"}
    try:
        api = YouTubeTranscriptApi()
        fetched = None
        lang_used = None
        try:
            fetched = api.fetch(video_id, languages=PREFERRED_TRANSCRIPT_LANGS)
            lang_used = getattr(fetched, "language_code", None)
        except TypeError:
            fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=PREFERRED_TRANSCRIPT_LANGS)
        snippets = []
        timestamps = []
        for item in fetched:
            text = item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")
            start = item.get("start") if isinstance(item, dict) else getattr(item, "start", None)
            if text:
                snippets.append(text.replace("\n", " ").strip())
                if start is not None:
                    timestamps.append({"start": start, "text": text[:160]})
        text = "\n".join(snippets).strip()
        if text:
            return text, {"source": "subtítulos/transcripción de YouTube", "available": True, "language": lang_used, "timestamps_sample": timestamps[:30]}
    except (NoTranscriptFound, TranscriptsDisabled) as exc:
        return None, {"source": "youtube_transcript", "available": False, "error": str(exc)}
    except Exception as exc:
        return None, {"source": "youtube_transcript", "available": False, "error": str(exc)}
    return None, {"source": "youtube_transcript", "available": False, "error": "Transcripción vacía."}


def _download_audio(url: str, out_dir: Path) -> Path:
    import yt_dlp

    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    opts = {"quiet": True, "no_warnings": True, "format": "bestaudio/best", "outtmpl": outtmpl, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        candidate = Path(ydl.prepare_filename(info))
    if candidate.exists():
        return candidate
    matches = list(out_dir.glob(f"{info.get('id', '*')}.*"))
    if matches:
        return matches[0]
    raise SkillError("No se pudo descargar el audio del vídeo.")


def _transcribe_local(audio_path: Path, model: str = "small") -> Tuple[str, Dict[str, Any]]:
    if not shutil.which("ffmpeg"):
        raise SkillError("ffmpeg no está disponible; Whisper local no puede decodificar audio.")
    import whisper

    whisper_model = whisper.load_model(model)
    result = whisper_model.transcribe(str(audio_path), fp16=False)
    return (result.get("text") or "").strip(), {"source": "transcripción local con openai-whisper", "available": True, "language": result.get("language"), "model": model}


def _extract_secret_value(cred: Any) -> Optional[str]:
    if cred is None:
        return None
    if isinstance(cred, str):
        return cred
    if isinstance(cred, dict):
        return cred.get("value") or cred.get("password") or cred.get("token") or cred.get("secret")
    return None


def _get_agent_credential(_agent: Any, name: str) -> Optional[str]:
    if _agent is None:
        return None
    fn = getattr(_agent, "get_credential", None)
    if callable(fn):
        try:
            value = _extract_secret_value(fn(name))
            if value:
                return value
        except Exception:
            pass
    fn = getattr(_agent, "credential", None)
    if callable(fn):
        try:
            value = _extract_secret_value(fn(name))
            if value:
                return value
        except Exception:
            pass
    if isinstance(_agent, dict):
        direct = _extract_secret_value(_agent.get(name))
        if direct:
            return direct
        for key in ("credentials", "creds", "secrets", "credential_values"):
            bucket = _agent.get(key)
            if isinstance(bucket, dict):
                value = _extract_secret_value(bucket.get(name))
                if value:
                    return value
        fn = _agent.get("get_credential")
        if callable(fn):
            try:
                value = _extract_secret_value(fn(name))
                if value:
                    return value
            except Exception:
                pass
    return None


def _transcribe_openai_api(audio_path: Path, api_key: str) -> Tuple[str, Dict[str, Any]]:
    with audio_path.open("rb") as fh:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (audio_path.name, fh, "application/octet-stream")},
            data={"model": "whisper-1", "response_format": "json"},
            timeout=600,
        )
    if resp.status_code >= 400:
        raise SkillError(f"OpenAI transcription API error: {resp.status_code} {resp.text}")
    data = resp.json()
    return (data.get("text") or "").strip(), {"source": "transcripción con API Whisper/OpenAI", "available": True, "language": data.get("language")}


def _compact_transcript(text: str, max_chars: int = 60000) -> Dict[str, Any]:
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= max_chars:
        return {"text": clean, "truncated": False, "chars": len(clean)}
    head = clean[: int(max_chars * 0.55)]
    tail = clean[-int(max_chars * 0.35) :]
    return {"text": head + "\n\n[... transcripción recortada para resumen inicial ...]\n\n" + tail, "truncated": True, "chars": len(clean)}


def _summary_prompt(metadata: Dict[str, Any], transcript_info: Dict[str, Any], transcript_text: str, output_language: str) -> str:
    return f"""Resume este vídeo de YouTube en {output_language}. Usa exactamente esta estructura:

## Información y enlace
- Título, canal, URL, duración, fecha, idioma, fuente de transcripción.
- Ponentes/participantes identificados, solo si se infieren con base en el contenido.

## Veredicto rápido
- Recomendación: verlo / verlo parcialmente / no verlo.
- Motivo breve y para quién aporta valor.

## Resumen y puntos clave
- Temas hablados.
- Puntos clave o de interés.
- Ideas accionables y limitaciones.

## Herramientas mencionadas
- Nombre, descripción breve y enlace oficial solo si está explícito o es seguro. Si no, indica "enlace no verificado".

## Qué aporta
- Aprendizajes, novedad/profundidad, casos de uso y qué queda sin cubrir.

## Tiempo y esfuerzo
- Tiempo para verlo, segmentos de mayor valor si aparecen, esfuerzo bajo/medio/alto y recomendación de consumo.

METADATOS:
{json.dumps(metadata, ensure_ascii=False, indent=2)}

FUENTE TRANSCRIPCIÓN:
{json.dumps(transcript_info, ensure_ascii=False, indent=2)}

TRANSCRIPCIÓN:
{transcript_text}
"""


def _notion_page_id(value: str) -> str:
    """Return a canonical Notion page UUID from a raw UUID or Notion URL.

    Notion URLs often use slugs such as ``Title-<32hex>?pvs=...``.  A
    naive regex over the whole URL can accidentally consume hexadecimal
    letters from the title/slug prefix (for example ``YouTube-3120...`` can
    become ``be3120...``).  This parser therefore inspects URL path segments
    from right to left and only accepts an ID at the end of a segment, or a
    complete raw UUID/32-hex value.
    """
    raw = (value or "").strip()
    if not raw:
        raise SkillError("Falta parent_page_id o notion_parent_url para publicar en Notion.")

    def canonical(compact: str) -> str:
        compact = compact.replace("-", "").lower()
        if not re.fullmatch(r"[0-9a-f]{32}", compact):
            raise SkillError("No se pudo extraer un page_id válido de Notion.")
        return f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}"

    # Raw UUID or raw compact 32-char Notion id.
    if re.fullmatch(r"[0-9a-fA-F]{32}", raw.replace("-", "")):
        return canonical(raw)

    parsed = urlparse(raw)
    candidates: List[str] = []
    if parsed.scheme and parsed.netloc:
        for segment in reversed([s for s in parsed.path.split("/") if s]):
            # Typical public page: /Some-title-<32hex>
            m = re.search(r"([0-9a-fA-F]{32})$", segment.replace("-", ""))
            if m:
                candidates.append(m.group(1))
        # Shared/database style can carry an id query parameter.
        for key in ("p", "page_id", "id"):
            for item in parse_qs(parsed.query).get(key, []):
                if re.fullmatch(r"[0-9a-fA-F-]{32,36}", item):
                    candidates.append(item)
    else:
        # Last-resort for pasted slugs, but require the 32-hex token to be
        # preceded by a non-hex delimiter or start of string to avoid eating
        # hex letters from a title prefix.
        for m in re.finditer(r"(?:^|[^0-9a-fA-F])([0-9a-fA-F]{32})(?:$|[^0-9a-fA-F])", raw):
            candidates.append(m.group(1))

    if not candidates:
        raise SkillError("No se pudo extraer un page_id válido de Notion.")
    return canonical(candidates[0])


def _notion_token(_agent: Any) -> str:
    token = _get_agent_credential(_agent, NOTION_CREDENTIAL_NAME) or os.getenv("AUTOBOT_CRED_NOTION") or os.getenv("NOTION_TOKEN")
    if not token:
        raise SkillError("Credential `notion` no accesible para publicar en Notion.")
    return token


def _notion_headers(_agent: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {_notion_token(_agent)}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def _notion_rich_text(text: str) -> List[Dict[str, Any]]:
    """Convert a small markdown-ish inline string into Notion rich_text.

    Supports bold (**x**), inline code (`x`) and links [text](https://...).
    Keeps output inside Notion's 2000-char rich_text content limit.
    """
    text = (text or "").strip() or " "
    parts: List[Dict[str, Any]] = []
    pattern = re.compile(r"(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?://[^\s)]+)\))")
    pos = 0

    def add_piece(content: str, *, bold: bool = False, code: bool = False, href: Optional[str] = None) -> None:
        if not content:
            return
        for i in range(0, len(content), 1900):
            chunk = content[i:i+1900]
            item: Dict[str, Any] = {"type": "text", "text": {"content": chunk}}
            if href:
                item["text"]["link"] = {"url": href}
            if bold or code:
                item["annotations"] = {"bold": bold, "italic": False, "strikethrough": False, "underline": False, "code": code, "color": "default"}
            parts.append(item)

    for m in pattern.finditer(text):
        add_piece(text[pos:m.start()])
        if m.group(2) is not None:
            add_piece(m.group(2), bold=True)
        elif m.group(3) is not None:
            add_piece(m.group(3), code=True)
        elif m.group(4) is not None:
            add_piece(m.group(4), href=m.group(5))
        pos = m.end()
    add_piece(text[pos:])
    return parts or [{"type": "text", "text": {"content": " "}}]


def _strip_md_prefix(line: str) -> str:
    return re.sub(r"^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()


def _notion_block(block_type: str, text: str) -> Dict[str, Any]:
    return {"object": "block", "type": block_type, block_type: {"rich_text": _notion_rich_text(text)}}


def _notion_blocks_from_markdown(content: str, max_blocks: int = 95) -> List[Dict[str, Any]]:
    """Build real Notion blocks instead of dumping raw markdown.

    The parser intentionally supports the subset used by generated summaries:
    headings, paragraphs, bulleted/numbered lists, quotes, dividers, fenced code,
    inline bold/code/links and wrapped list continuation lines.
    """
    blocks: List[Dict[str, Any]] = []
    paragraph_lines: List[str] = []
    in_code = False
    code_lines: List[str] = []

    def append(block: Dict[str, Any]) -> None:
        if len(blocks) < max_blocks:
            blocks.append(block)

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        text = "\n".join(line.strip() for line in paragraph_lines if line.strip()).strip()
        paragraph_lines = []
        if not text:
            return
        for i in range(0, len(text), 1900):
            append(_notion_block("paragraph", text[i:i+1900]))

    def flush_code() -> None:
        nonlocal code_lines
        text = "\n".join(code_lines).strip() or " "
        code_lines = []
        for i in range(0, len(text), 1900):
            append({"object": "block", "type": "code", "code": {"rich_text": [{"type": "text", "text": {"content": text[i:i+1900]}}], "language": "plain text"}})

    for raw_line in (content or "").splitlines():
        if len(blocks) >= max_blocks:
            break
        stripped = raw_line.rstrip()
        line = stripped.strip()
        if line.startswith("```"):
            flush_paragraph()
            if in_code:
                flush_code(); in_code = False
            else:
                in_code = True; code_lines = []
            continue
        if in_code:
            code_lines.append(stripped)
            continue
        if not line:
            flush_paragraph(); continue
        if re.fullmatch(r"[-*_]{3,}", line):
            flush_paragraph(); append({"object": "block", "type": "divider", "divider": {}}); continue
        if line.startswith("### "):
            flush_paragraph(); append(_notion_block("heading_3", line[4:].strip())); continue
        if line.startswith("## "):
            flush_paragraph(); append(_notion_block("heading_2", line[3:].strip())); continue
        if line.startswith("# "):
            flush_paragraph(); append(_notion_block("heading_1", line[2:].strip())); continue
        if line.startswith("> "):
            flush_paragraph(); append(_notion_block("quote", line[2:].strip())); continue
        if re.match(r"^\s{0,3}[-*+]\s+", stripped):
            flush_paragraph(); append(_notion_block("bulleted_list_item", _strip_md_prefix(stripped))); continue
        if re.match(r"^\s{0,3}\d+[.)]\s+", stripped):
            flush_paragraph(); append(_notion_block("numbered_list_item", _strip_md_prefix(stripped))); continue
        paragraph_lines.append(line)
    if in_code:
        flush_code()
    flush_paragraph()
    return blocks[:max_blocks]


def _dated_notion_title(title: str, today: Optional[str] = None) -> str:
    clean = (title or "").strip() or "Resumen YouTube"
    if re.match(r"^\d{4}-\d{2}-\d{2}\s+-\s+", clean):
        return clean[:2000]
    return f"{today or date.today().isoformat()} - {clean}"[:2000]

def _notion_create_subpage(_agent: Any, parent_page_id: str, title: str, content: str) -> Dict[str, Any]:
    if not title:
        raise SkillError("Falta title para crear la subpágina de Notion.")
    page_id = _notion_page_id(parent_page_id)
    blocks = _notion_blocks_from_markdown(content)
    payload: Dict[str, Any] = {"parent": {"page_id": page_id}, "properties": {"title": {"title": [{"type": "text", "text": {"content": title[:2000]}}]}}}
    if blocks:
        payload["children"] = blocks
    resp = requests.post(f"{NOTION_BASE_URL}/pages", headers=_notion_headers(_agent), json=payload, timeout=NOTION_TIMEOUT)
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if resp.status_code >= 400:
        raise SkillError(f"Notion API error: {resp.status_code} {data.get('message') or resp.text}")
    return {"ok": True, "id": data.get("id"), "url": data.get("url"), "created_time": data.get("created_time"), "title": title, "blocks_sent": len(blocks), "credential_used": NOTION_CREDENTIAL_NAME}


def handler(_agent: Any = None, url: str = "", approved_long: bool = False, output_language: str = "español", whisper_model: str = "small", allow_api_fallback: bool = True, return_prompt: bool = True, publish_to_notion: bool = False, notion_parent_page_id: str = "", notion_parent_url: str = "", notion_title: str = "", summary_markdown: str = "", **kwargs: Any) -> Dict[str, Any]:
    if not url:
        raise SkillError("Falta el parámetro obligatorio: url")
    vid = _video_id(url)
    meta = _metadata(url)
    meta["id"] = meta.get("id") or vid
    transcript, transcript_info = _try_youtube_transcript(vid)
    if not transcript:
        duration = meta.get("duration_seconds")
        if duration and int(duration) > MAX_LOCAL_TRANSCRIBE_SECONDS and not approved_long:
            return {"status": "needs_approval", "reason": "El vídeo dura más de 2 horas y no hay transcripción de YouTube disponible.", "metadata": meta, "duration_seconds": duration, "duration": _format_duration(duration), "next_step": "Reintentar con approved_long=True para descargar/transcribir audio.", "transcript_attempt": transcript_info}
        with tempfile.TemporaryDirectory(prefix="yt-summary-") as tmp:
            audio = _download_audio(url, Path(tmp))
            try:
                transcript, transcript_info = _transcribe_local(audio, model=whisper_model)
            except Exception as local_exc:
                if not allow_api_fallback:
                    raise
                api_key = _get_agent_credential(_agent, OPENAI_WHISPER_CREDENTIAL_NAME) or os.getenv("OPENAI_API_KEY")
                if not api_key:
                    raise SkillError(f"Falló Whisper local ({local_exc}) y no hay credencial {OPENAI_WHISPER_CREDENTIAL_NAME} accesible.")
                transcript, transcript_info = _transcribe_openai_api(audio, api_key)
                transcript_info["local_error"] = str(local_exc)
    compact = _compact_transcript(transcript or "")
    result: Dict[str, Any] = {"status": "ok", "metadata": meta, "transcript_info": transcript_info, "transcript_compact": compact, "output_structure": ["Información y enlace", "Veredicto rápido", "Resumen y puntos clave", "Herramientas mencionadas", "Qué aporta", "Tiempo y esfuerzo"]}
    if return_prompt:
        result["summary_prompt"] = _summary_prompt(meta, transcript_info, compact["text"], output_language)
    if publish_to_notion:
        content = summary_markdown or result.get("summary_prompt", "")
        if not summary_markdown:
            result["notion_warning"] = "Se publicó el prompt/resumen preparado porque no se recibió summary_markdown final."
        parent = notion_parent_page_id or notion_parent_url
        base_title = notion_title or (meta.get("title") or meta.get("id") or vid)
        title = _dated_notion_title(base_title)
        result["notion"] = _notion_create_subpage(_agent, parent, title, content)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Prepare YouTube video transcript/summary prompt and optionally publish a final summary to Notion")
    parser.add_argument("url")
    parser.add_argument("--approved-long", action="store_true")
    parser.add_argument("--language", default="español")
    parser.add_argument("--model", default="small")
    parser.add_argument("--publish-to-notion", action="store_true")
    parser.add_argument("--notion-parent-page-id", default="")
    parser.add_argument("--notion-parent-url", default="")
    parser.add_argument("--notion-title", default="")
    parser.add_argument("--summary-markdown-file", default="")
    parser.add_argument("--no-prompt", action="store_true")
    args = parser.parse_args()
    summary = Path(args.summary_markdown_file).read_text() if args.summary_markdown_file else ""
    print(json.dumps(handler(url=args.url, approved_long=args.approved_long, output_language=args.language, whisper_model=args.model, return_prompt=not args.no_prompt, publish_to_notion=args.publish_to_notion, notion_parent_page_id=args.notion_parent_page_id, notion_parent_url=args.notion_parent_url, notion_title=args.notion_title, summary_markdown=summary), ensure_ascii=False, indent=2))
