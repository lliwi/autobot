"""
Matrix audio ingest helper for Autobot worker integration.

Wire this into the real Matrix worker:
- detect Matrix audio events
- download mxc:// media with the already-authenticated worker client
- write the file atomically into the agent workspace
- invoke the agent with audio_path for matrix-audio-handler
"""

import hashlib
import mimetypes
import os
import re
import tempfile
from pathlib import Path

MAX_AUDIO_BYTES = 50 * 1024 * 1024
AUDIO_DIR = "incoming_matrix_audio"
ALLOWED_AUDIO_MIME_PREFIX = "audio/"
ALLOWED_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".webm"
}


def _content_get(event):
    if isinstance(event, dict):
        content = event.get("content") or {}
    else:
        content = getattr(event, "content", None) or {}
    return content if isinstance(content, dict) else {}


def _event_attr(event, key, default=""):
    if isinstance(event, dict):
        return str(event.get(key) or default)
    return str(getattr(event, key, default) or default)


def is_audio_event(event):
    content = _content_get(event)
    msgtype = str(content.get("msgtype") or "")
    info = content.get("info") or {}
    if not isinstance(info, dict):
        info = {}
    mimetype = str(info.get("mimetype") or "")
    return msgtype == "m.audio" or (msgtype == "m.file" and mimetype.startswith(ALLOWED_AUDIO_MIME_PREFIX))


def extract_mxc_url(event):
    content = _content_get(event)
    if content.get("file"):
        raise ValueError("encrypted Matrix media requires worker-side decryption before audio ingest")
    mxc_url = str(content.get("url") or "")
    if not mxc_url.startswith("mxc://"):
        raise ValueError("Matrix audio event does not contain a valid mxc:// url")
    return mxc_url


def infer_extension(event, mxc_url):
    content = _content_get(event)
    info = content.get("info") or {}
    if not isinstance(info, dict):
        info = {}
    filename = str(content.get("body") or "")
    ext = Path(filename).suffix.lower()
    if ext in ALLOWED_EXTENSIONS:
        return ext
    mimetype = str(info.get("mimetype") or "")
    guessed = mimetypes.guess_extension(mimetype) if mimetype else None
    if guessed:
        guessed = guessed.lower()
        if guessed == ".oga":
            return ".ogg"
        if guessed in ALLOWED_EXTENSIONS:
            return guessed
    if mxc_url.lower().endswith(".opus"):
        return ".opus"
    return ".ogg"


def safe_event_hash(event_id, mxc_url):
    raw = (str(event_id) + "|" + str(mxc_url)).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:32]


def atomic_write_bytes(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def build_agent_message(payload):
    return (
        "Audio recibido por Matrix. Procesa con matrix-audio-handler:\n"
        + "audio_path=" + payload["audio_path"] + "\n"
        + "room_id=" + payload["room_id"] + "\n"
        + "sender=" + payload["sender"] + "\n"
        + "event_id=" + payload["event_id"] + "\n"
        + "mimetype=" + payload["mimetype"] + "\n"
        + "filename=" + payload["filename"]
    )


def handle_matrix_audio_event(event, room_id, agent_slug, agent_workspace, download_callback, invoke_agent_callback):
    """Handle one Matrix event if it is audio.

    download_callback(mxc_url, max_bytes) -> bytes
    invoke_agent_callback(agent_slug, message, metadata) -> result
    """
    if not is_audio_event(event):
        return None

    event_id = _event_attr(event, "event_id")
    sender = _event_attr(event, "sender")
    content = _content_get(event)
    info = content.get("info") or {}
    if not isinstance(info, dict):
        info = {}

    mimetype = str(info.get("mimetype") or "audio/ogg")
    if not mimetype.startswith(ALLOWED_AUDIO_MIME_PREFIX):
        raise ValueError("unsupported Matrix audio mimetype: " + mimetype)

    declared_size = info.get("size")
    if isinstance(declared_size, int) and declared_size > MAX_AUDIO_BYTES:
        raise ValueError("Matrix audio is larger than the 50 MB limit")

    mxc_url = extract_mxc_url(event)
    ext = infer_extension(event, mxc_url)
    filename = str(content.get("body") or ("matrix-audio" + ext))
    if not re.match(r"^[\w .@()+\-=,\[\]{}~#]+$", filename):
        filename = "matrix-audio" + ext

    data = download_callback(mxc_url, MAX_AUDIO_BYTES)
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Matrix audio download exceeded the 50 MB limit")

    rel_path = AUDIO_DIR + "/" + safe_event_hash(event_id, mxc_url) + ext
    workspace_root = Path(agent_workspace).resolve()
    target = workspace_root / rel_path
    resolved = target.resolve()
    if not (resolved == workspace_root or workspace_root in resolved.parents):
        raise ValueError("refusing to write outside agent workspace")

    atomic_write_bytes(target, data)

    payload = {
        "audio_path": rel_path,
        "room_id": str(room_id),
        "sender": sender,
        "event_id": event_id,
        "mimetype": mimetype,
        "filename": filename,
        "size": len(data),
    }
    message = build_agent_message(payload)
    metadata = dict(payload)
    metadata["source"] = "matrix_audio"
    return invoke_agent_callback(agent_slug, message, metadata)
