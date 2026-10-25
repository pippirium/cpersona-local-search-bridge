"""B-03 policy constants — the single source of truth for the rules frozen in
docs/12-threat-model-media-policy.md. B-04..B-09 import these; tests here pin
them so a silent limit change breaks CI.

Pure data only: no provider, no network, no I/O beyond reading docs.
"""

from __future__ import annotations

# §3 media limits (frozen defaults; operator-configurable downward only)
MAX_FETCH_BYTES = 8_000_000
MAX_PIXELS = 40_000_000
MIN_BYTES = 1
ALLOWED_IMAGE_MIME = ("image/jpeg", "image/png", "image/webp", "image/gif")
TYPED_REJECT_MIME = ("image/heic", "image/heif")

# §4 HTTP policy
CONNECT_TIMEOUT_S = 2.0
READ_TIMEOUT_S = 10.0
TOTAL_CONTENT_CAP_S = 30.0
MAX_RETRIES_CONNECT_PHASE = 1
RETRYABLE_ROUTES = frozenset({"status", "file_detail", "content_headers"})
NON_RETRYABLE_ROUTES = frozenset({"search"})

# §6 forbidden substrings for logs/responses/fixtures (audit list)
FORBIDDEN_LOG_MARKERS = (
    "/Users/",
    "gho_",
    "api_key",
    "Authorization:",
    "data:image/",
)


def pixels_within_cap(width: int, height: int) -> bool:
    return 0 < width and 0 < height and width * height <= MAX_PIXELS


def content_size_acceptable(size_bytes: int) -> bool:
    return MIN_BYTES <= size_bytes <= MAX_FETCH_BYTES
