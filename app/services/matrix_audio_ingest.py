"""Matrix audio ingest helper.

Detects incoming Matrix audio events, downloads the mxc:// media using the
bot's authenticated nio client, and stores the file atomically in the agent's
workspace under ``incoming_matrix_audio/<hash>.<ext>``.

Security guarantees:
- Only m.audio or m.file with audio/* mimetype are accepted.
- Encrypted media (content.file) is rejected with a clear error.
- Download is capped at MAX_AUDIO_BYTES (50 MB).
- Output path is validated to stay inside the agent workspace.
- The agent receives audio_path relative to its workspace — no Matrix token is
  forwarded.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
from pathlib import Path

MAX_AUDIO_BYTES = 50 * 1024 * 1024  # 50 MB
AUDIO_DIR = "incoming_matrix_audio"
ALLOWED_EXTENSIONS = {
    ".aac", ".flac", ".m4a", ".mp3", ".oga", ".ogg", ".opus", ".wav", ".webm",
}


def is_audio_event(event) -> bool:
    """Return True if *event* is an audio message the worker should ingest."""
    content = _content(event)
    msgtype = str(content.get("msgtype") or "")
    info = content.get("info") or {}
    mimetype = str(info.get("mimetype") or "") if isinstance(info, dict) else ""
    return msgtype == "m.audio" or (
        msgtype == "m.file" and mimetype.startswith("audio/")
    )


def extract_mxc_url(event) -> str:
    content = _content(event)
    if content.get("file"):
        raise ValueError(
            "Encrypted Matrix media (content.file) requires E2EE decryption "
            "before audio ingest — not supported yet."
        )
    mxc = str(content.get("url") or "")
    if not mxc.startswith("mxc://"):
        raise ValueError(f"No valid mxc:// URL in audio event: {mxc!r}")
    return mxc


def infer_extension(event, mxc_url: str) -> str:
    content = _content(event)
    info = content.get("info") or {}
    filename = str(content.get("body") or "")
    ext = Path(filename).suffix.lower()
    if ext in ALLOWED_EXTENSIONS:
        return ext
    mimetype = str(info.get("mimetype") or "") if isinstance(info, dict) else ""
    if mimetype:
        guessed = mimetypes.guess_extension(mimetype)
        if guessed:
            guessed = guessed.lower().replace(".oga", ".ogg")
            if guessed in ALLOWED_EXTENSIONS:
                return guessed
    if mxc_url.lower().endswith(".opus"):
        return ".opus"
    return ".ogg"


def safe_filename(event_id: str, mxc_url: str, ext: str) -> str:
    digest = hashlib.sha256(
        (str(event_id) + "|" + str(mxc_url)).encode("utf-8", errors="replace")
    ).hexdigest()[:32]
    return digest + ext


def atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def build_agent_message(payload: dict) -> str:
    """Build the message text that invokes matrix-audio-handler on the agent."""
    return (
        "Audio recibido por Matrix. Procesa con matrix-audio-handler:\n"
        f"audio_path={payload['audio_path']}\n"
        f"room_id={payload['room_id']}\n"
        f"sender={payload['sender']}\n"
        f"event_id={payload['event_id']}\n"
        f"mimetype={payload['mimetype']}\n"
        f"filename={payload['filename']}"
    )


async def handle_audio_event(event, room_id: str, agent, nio_client) -> dict | None:
    """Top-level entry point called from the Matrix worker for each audio event.

    Downloads the mxc:// media, saves it to the agent workspace, then invokes
    the agent via run_agent_non_streaming and returns the result dict.
    Returns None if the event is not an audio event.
    """
    if not is_audio_event(event):
        return None

    content = _content(event)
    info = content.get("info") or {}
    info = info if isinstance(info, dict) else {}

    event_id = str(getattr(event, "event_id", "") or "")
    sender = str(getattr(event, "sender", "") or "")
    mimetype = str(info.get("mimetype") or "audio/ogg")
    declared_size = info.get("size")
    filename = str(content.get("body") or "matrix-audio.ogg")
    if not re.match(r"^[\w .@()+\-=,\[\]{}~#]+$", filename):
        filename = "matrix-audio.ogg"

    if isinstance(declared_size, int) and declared_size > MAX_AUDIO_BYTES:
        raise ValueError(
            f"Matrix audio declared size {declared_size} exceeds 50 MB limit — skipping."
        )

    mxc_url = extract_mxc_url(event)
    ext = infer_extension(event, mxc_url)

    # Download via the authenticated nio client
    from nio import DownloadResponse
    resp = await nio_client.download(mxc=mxc_url)
    if not isinstance(resp, DownloadResponse):
        raise ValueError(f"Failed to download {mxc_url}: {resp}")
    data: bytes = resp.body
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(f"Downloaded audio exceeds 50 MB limit ({len(data)} bytes).")

    # Save atomically inside workspace
    rel_path = AUDIO_DIR + "/" + safe_filename(event_id, mxc_url, ext)
    workspace_root = Path(agent.workspace_path).resolve()
    target = workspace_root / rel_path
    if workspace_root not in target.resolve().parents and target.resolve() != workspace_root:
        raise ValueError(f"Path traversal detected: {target} is outside {workspace_root}")
    atomic_write(target, data)

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

    from app.services.chat_service import run_agent_non_streaming
    result = run_agent_non_streaming(
        agent_id=agent.id,
        message=message,
        channel_type="matrix",
        trigger_type="message",
        external_chat_id=str(room_id),
        external_user_id=sender,
    )
    result["audio_payload"] = payload
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _content(event) -> dict:
    if isinstance(event, dict):
        c = event.get("content") or {}
    else:
        c = getattr(event, "source", {}).get("content") or {}
        if not c:
            # nio events expose fields directly
            c = {
                "msgtype": getattr(event, "msgtype", None),
                "url": getattr(event, "url", None),
                "body": getattr(event, "body", None),
                "info": getattr(event, "source", {}).get("content", {}).get("info"),
                "file": getattr(event, "source", {}).get("content", {}).get("file"),
            }
    return c if isinstance(c, dict) else {}
