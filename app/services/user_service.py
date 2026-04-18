"""Profile-management helpers: password, MFA (TOTP), avatar.

Single place that owns the crypto-ish pieces so the view layer stays thin.
Every mutating helper commits so callers don't have to juggle sessions.
"""
import io
import logging
import os
import secrets
from pathlib import Path

import pyotp
import qrcode
from flask import current_app
from PIL import Image
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.user import User

logger = logging.getLogger(__name__)


# --- Password ------------------------------------------------------------


def change_password(user: User, current_password: str, new_password: str) -> tuple[bool, str | None]:
    """Validate + update ``user``'s password. Returns ``(ok, error_message)``."""
    if not user.check_password(current_password):
        return False, "Current password is incorrect."
    if not new_password or len(new_password) < 8:
        return False, "New password must be at least 8 characters."
    user.set_password(new_password)
    db.session.commit()
    return True, None


# --- MFA (TOTP) ----------------------------------------------------------


def start_mfa_setup(user: User) -> str:
    """Generate a fresh TOTP secret and stash it on the row (not yet enabled).

    The caller shows the QR/text to the user and then calls
    ``confirm_mfa_setup`` with a live code to flip ``mfa_enabled`` to True.
    """
    user.mfa_secret = pyotp.random_base32()
    user.mfa_enabled = False
    db.session.commit()
    return user.mfa_secret


def confirm_mfa_setup(user: User, code: str) -> tuple[bool, str | None]:
    if not user.mfa_secret:
        return False, "No pending MFA setup — start one first."
    if not verify_mfa_code(user, code):
        return False, "Code invalid or expired — try again."
    user.mfa_enabled = True
    db.session.commit()
    return True, None


def disable_mfa(user: User, password: str, code: str) -> tuple[bool, str | None]:
    """Turn MFA off. Requires both password and a live TOTP code so a stolen
    session alone can't disable it.
    """
    if not user.check_password(password):
        return False, "Password is incorrect."
    if not verify_mfa_code(user, code):
        return False, "MFA code invalid."
    user.mfa_secret = None
    user.mfa_enabled = False
    db.session.commit()
    return True, None


def verify_mfa_code(user: User, code: str) -> bool:
    """Verify a 6-digit TOTP code. Accepts +/- 1 window for clock skew."""
    if not user.mfa_secret or not code:
        return False
    totp = pyotp.TOTP(user.mfa_secret)
    try:
        return bool(totp.verify(code.strip().replace(" ", ""), valid_window=1))
    except Exception:
        return False


def mfa_provisioning_uri(user: User) -> str:
    if not user.mfa_secret:
        return ""
    issuer = current_app.config.get("MFA_ISSUER", "Autobot")
    return pyotp.TOTP(user.mfa_secret).provisioning_uri(name=user.email, issuer_name=issuer)


def mfa_qr_png_bytes(user: User) -> bytes:
    """Return a PNG of the provisioning URI QR code. Empty bytes if no secret."""
    uri = mfa_provisioning_uri(user)
    if not uri:
        return b""
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- Avatar --------------------------------------------------------------

_ALLOWED_AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_AVATAR_SIZE = (256, 256)


def _avatar_dir() -> Path:
    path = Path(current_app.config["AVATAR_UPLOAD_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_avatar(user: User, uploaded_file) -> tuple[bool, str | None]:
    """Persist an uploaded image as the user's avatar.

    The file is re-encoded as PNG after a center-crop resize to 256x256 to
    normalize dimensions, strip EXIF, and neutralize any smuggled payload
    (SVGs and animated GIFs are rejected by ``Image.open`` downgrading them).
    Returns ``(ok, error)``.
    """
    if uploaded_file is None or not uploaded_file.filename:
        return False, "No file uploaded."
    filename = secure_filename(uploaded_file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_AVATAR_EXTS:
        return False, f"Unsupported file type ({ext or 'unknown'})."

    max_bytes = int(current_app.config.get("AVATAR_MAX_BYTES", 2 * 1024 * 1024))
    uploaded_file.stream.seek(0, os.SEEK_END)
    size = uploaded_file.stream.tell()
    uploaded_file.stream.seek(0)
    if size > max_bytes:
        return False, f"Image too large ({size} bytes; max {max_bytes})."

    try:
        img = Image.open(uploaded_file.stream)
        img.load()
    except Exception:
        return False, "Could not decode image."

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    img = _center_crop(img, _AVATAR_SIZE)

    # Use a random suffix so stale cached avatars don't stick around.
    avatar_name = f"user_{user.id}_{secrets.token_hex(4)}.png"
    out_path = _avatar_dir() / avatar_name
    try:
        img.save(out_path, format="PNG", optimize=True)
    except Exception as e:
        logger.exception("Failed to write avatar for user %s", user.id)
        return False, f"Could not save image: {e}"

    # Drop the previous file, if any.
    old = user.avatar_filename
    user.avatar_filename = avatar_name
    db.session.commit()
    if old:
        try:
            (_avatar_dir() / old).unlink(missing_ok=True)
        except Exception:
            logger.warning("Could not remove previous avatar %s", old)
    return True, None


def remove_avatar(user: User) -> None:
    old = user.avatar_filename
    user.avatar_filename = None
    db.session.commit()
    if old:
        try:
            (_avatar_dir() / old).unlink(missing_ok=True)
        except Exception:
            logger.warning("Could not remove avatar %s", old)


def avatar_path(filename: str) -> Path | None:
    """Resolve a filename to its absolute path, or None if traversal detected."""
    if not filename:
        return None
    base = _avatar_dir().resolve()
    target = (base / filename).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target if target.exists() else None


def _center_crop(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize + center-crop to an exact size without distortion."""
    target_w, target_h = size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_size = (int(src_w * scale + 0.5), int(src_h * scale + 0.5))
    resized = img.resize(new_size, Image.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))
