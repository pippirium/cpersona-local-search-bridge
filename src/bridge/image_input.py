"""Bounded image validation shared by explicit image-input operations (IMG-02)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from bridge.errors import BridgeError
from bridge.tools_get import _mime_from_magic
from bridge_policy import ALLOWED_IMAGE_MIME

_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/gif": "GIF"}
# Bound total work even when a small GIF contains many frames.
MAX_FRAMES = 256


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    media_type: str
    sha256: str
    width: int
    height: int


def decode_image_base64(encoded: str, *, max_bytes: int, max_pixels: int) -> ValidatedImage:
    """Strict standard Base64; check encoded length before allocating decoded bytes."""
    if not isinstance(encoded, str) or not encoded:
        raise BridgeError("invalid_request")
    if len(encoded) > 4 * ((max_bytes + 2) // 3):
        raise BridgeError("media_too_large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise BridgeError("invalid_request") from None
    # Require canonical padded encoding; the decoder otherwise accepts extra padding.
    if base64.b64encode(data).decode("ascii") != encoded:
        raise BridgeError("invalid_request")
    return validate_image(data, max_bytes=max_bytes, max_pixels=max_pixels)


def validate_image(data: bytes, *, max_bytes: int, max_pixels: int) -> ValidatedImage:
    """Check magic, decoder format, dimensions, structure and bounded frame decoding."""
    if not isinstance(data, bytes) or not data:
        raise BridgeError("invalid_request")
    if len(data) > max_bytes:
        raise BridgeError("media_too_large")
    mime = _mime_from_magic(data)
    if mime not in ALLOWED_IMAGE_MIME:
        raise BridgeError("unsupported_media")
    try:
        # Pillow's global default can be lower than this operator's policy;
        # keep warnings local and enforce the configured cap before decoding.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != _FORMATS[mime]:
                    raise BridgeError("unsupported_media")
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise BridgeError("media_too_large")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                frames = getattr(image, "n_frames", 1)
                if frames > MAX_FRAMES or width * height * frames > max_pixels:
                    raise BridgeError("media_too_large")
                for frame in range(frames):
                    image.seek(frame)
                    image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BridgeError("media_too_large") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError):
        raise BridgeError("unsupported_media") from None
    return ValidatedImage(data, mime, hashlib.sha256(data).hexdigest(), width, height)
