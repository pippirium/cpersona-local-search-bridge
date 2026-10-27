"""Synthetic-only tests of IMG-02's bounded input validation."""
import base64
import io

import pytest
from PIL import Image

from bridge.errors import BridgeError
from bridge.image_input import decode_image_base64, validate_image


def image_bytes(fmt):
    buf = io.BytesIO()
    Image.new("RGB", (4, 3), (5, 9, 16)).save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize(("fmt", "mime"), [("PNG", "image/png"), ("JPEG", "image/jpeg"),
                                        ("WEBP", "image/webp"), ("GIF", "image/gif")])
def test_valid_images_round_trip_without_reencoding(fmt, mime):
    data = image_bytes(fmt)
    result = decode_image_base64(base64.b64encode(data).decode(), max_bytes=10000, max_pixels=100)
    assert result.data == data
    assert result.media_type == mime
    assert (result.width, result.height) == (4, 3)
    assert len(result.sha256) == 64


@pytest.mark.parametrize(("value", "code"), [
    ("", "invalid_request"), ("!bad", "invalid_request"), ("YQ==\n", "invalid_request"),
    ("YQ===", "invalid_request"), ("YQ==", "unsupported_media"),
    ("A" * 1000, "media_too_large"),
])
def test_base64_failures_are_typed(value, code):
    with pytest.raises(BridgeError) as exc:
        decode_image_base64(value, max_bytes=100, max_pixels=100)
    assert exc.value.code == code


def test_corruption_and_dimensions_fail_closed():
    data = image_bytes("PNG")
    for content, limit, expected in [(data[:30], 100, "unsupported_media"),
                                      (data, 11, "media_too_large")]:
        with pytest.raises(BridgeError) as exc:
            validate_image(content, max_bytes=10000, max_pixels=limit)
        assert exc.value.code == expected


def test_oversize_after_decode_and_heic_are_typed():
    data = image_bytes("PNG")
    with pytest.raises(BridgeError, match="media_too_large"):
        decode_image_base64(base64.b64encode(data).decode(), max_bytes=len(data) - 1, max_pixels=100)
    with pytest.raises(BridgeError, match="unsupported_media"):
        validate_image(b"\x00\x00\x00\x10ftypheic", max_bytes=100, max_pixels=100)


def test_animation_total_pixels_are_bounded():
    buf = io.BytesIO()
    Image.new("RGB", (4, 3), "red").save(buf, "GIF", save_all=True,
        append_images=[Image.new("RGB", (4, 3), "blue")], duration=50, loop=0)
    with pytest.raises(BridgeError, match="media_too_large"):
        validate_image(buf.getvalue(), max_bytes=10000, max_pixels=20)
