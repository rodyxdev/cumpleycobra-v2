"""Almacén de mockups del cliente, direccionado por contenido (sha256)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from backend.config import get_settings

MAX_BYTES = 8 * 1024 * 1024
MAX_SIDE = 8000
_FORMATS = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg")}


class MockupError(ValueError):
    pass


def _root() -> Path:
    return get_settings().work_dir / "mockups"


def save_mockup(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise MockupError(f"la imagen supera {MAX_BYTES} bytes")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt, (width, height) = img.format, img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise MockupError("no es una imagen PNG o JPEG válida") from exc
    if fmt not in _FORMATS:
        raise MockupError(f"formato {fmt} no soportado (solo PNG o JPEG)")
    if max(width, height) > MAX_SIDE:
        raise MockupError(f"la imagen supera {MAX_SIDE} px por lado")
    digest = hashlib.sha256(data).hexdigest()
    media_type, ext = _FORMATS[fmt]
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return {"sha256": f"sha256:{digest}", "media_type": media_type, "width": width, "height": height}


def mockup_path(sha256: str) -> Path | None:
    digest = sha256.removeprefix("sha256:")
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        return None
    for ext in (".png", ".jpg"):
        path = _root() / f"{digest}{ext}"
        if path.exists():
            return path
    return None
