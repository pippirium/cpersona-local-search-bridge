"""B-03 security-policy tests (docs/12). Executable rules for T-05/T-07/T-09/T-11/T-12.

Tool-level and end-to-end negative tests belong to B-04..B-09 and B-13; this
suite pins the policy constants and the pure predicates so later tickets
cannot quietly change them. No network, no provider, temp files only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bridge_policy as policy  # noqa: E402


# ---- T-05 / T-11: size and pixel gates ---------------------------------------

def test_pixel_cap_gate():
    assert policy.pixels_within_cap(4000, 3000)          # 12 MP: fine
    assert not policy.pixels_within_cap(100_000, 100_000)  # 10 G: bomb
    assert not policy.pixels_within_cap(0, 100)          # zero dims: refuse
    assert not policy.pixels_within_cap(-1, 10)


def test_byte_cap_boundaries():
    assert policy.content_size_acceptable(policy.MIN_BYTES)
    assert policy.content_size_acceptable(policy.MAX_FETCH_BYTES)
    assert not policy.content_size_acceptable(policy.MAX_FETCH_BYTES + 1)
    assert not policy.content_size_acceptable(0)  # T-11: empty is not a success


def test_base64_expansion_stays_under_common_client_limits():
    # docs/12 §3 basis: 8 MB raw ≈ 10.7 MB base64; assert the arithmetic the
    # decision rested on (10 MiB-ish bound commonly enforced by clients).
    encoded = policy.MAX_FETCH_BYTES * 4 // 3 + 4
    assert encoded <= 11_000_000


# ---- T-12: MIME allow-list ----------------------------------------------------

def test_m1_image_mime_allow_list():
    assert set(policy.ALLOWED_IMAGE_MIME) == {
        "image/jpeg", "image/png", "image/webp", "image/gif",
    }
    assert set(policy.TYPED_REJECT_MIME) == {"image/heic", "image/heif"}
    assert set(policy.ALLOWED_IMAGE_MIME).isdisjoint(policy.TYPED_REJECT_MIME)
    # video/text MIME must never appear in the get_asset allow list
    assert not any(m.startswith(("video/", "text/")) for m in policy.ALLOWED_IMAGE_MIME)


# ---- T-06: loopback base URL policy -------------------------------------------

def is_literal_loopback(base_url: str) -> bool:
    """The frozen rule: scheme http, host literal 127.0.0.1, explicit port."""
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    return (
        parts.scheme == "http"
        and parts.hostname == "127.0.0.1"
        and parts.path in ("", "/")
        and not parts.username
        and not parts.password
        and parts.port is not None
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:18765",
        "http://127.0.0.1:18765/",
    ],
)
def test_loopback_urls_accepted(url):
    assert is_literal_loopback(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:18765",            # name, not literal IPv4 (guard host set differs)
        "http://127.0.0.1.1:18765",          # lookalike
        "http://127.1:18765",                # short-form loopback: refuse, not normalize
        "http://evil.example:18765",
        "https://127.0.0.1:18765",           # https: provider is plain http
        "http://127.0.0.1:18765/api",        # path prefix: not accepted
        "http://user@127.0.0.1:18765",       # userinfo
        "file:///etc/passwd",
        "",
    ],
)
def test_non_literal_loopback_refused(url):
    assert not is_literal_loopback(url)


# ---- T-07: retry policy --------------------------------------------------------

def test_retry_policy():
    assert policy.MAX_RETRIES_CONNECT_PHASE == 1
    assert "search" in policy.NON_RETRYABLE_ROUTES
    assert policy.RETRYABLE_ROUTES == {"status", "file_detail", "content_headers"}
    assert set(policy.RETRYABLE_ROUTES).isdisjoint(policy.NON_RETRYABLE_ROUTES)
    assert policy.CONNECT_TIMEOUT_S < policy.READ_TIMEOUT_S < policy.TOTAL_CONTENT_CAP_S


# ---- T-09: log/fixture secrecy (CI sentinel) ----------------------------------

def test_tracked_text_files_free_of_forbidden_markers():
    """Audit-list scan over committed text files (fixtures/docs/tests/config).

    Binary files skipped. The scan's OWN definition files — the policy module
    and this test file — legitimately contain the literal markers and are
    excluded by path, never by fuzzy "does the file mention the scan" checks.
    A doc that quotes a marker (e.g. docs/12's audit list) still FAILS: only
    code that defines the scan may hold literals. Real paths must not appear
    anywhere, including docs.
    """
    repo = Path(__file__).resolve().parents[2]
    self_paths = {"bridge_policy.py", "tests/security/test_policy_rules.py"}
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    offenders = []
    for rel in tracked:
        if rel in self_paths:
            continue
        p = repo / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary
        for marker in policy.FORBIDDEN_LOG_MARKERS:
            if marker in text:
                offenders.append((rel, marker))
    assert not offenders, offenders


def test_docs_declare_the_same_caps():
    doc = (
        Path(__file__).resolve().parents[2] / "docs" / "12-threat-model-media-policy.md"
    ).read_text(encoding="utf-8")
    assert "8 × 10⁶" in doc or "8_000_000" in doc or "8 MiB" in doc
    assert "40 × 10⁶" in doc or "40 MP" in doc
