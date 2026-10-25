"""B-06: LocalSearchClient against contract fixtures via httpx.MockTransport.

No sockets: every response (success and fault) is served from the pinned
fixture shapes (docs/10) through the transport layer, asserting request
shape AND failure taxonomy mechanically. A localhost TCP server would add
nothing MockTransport does not, and would risk touching the real provider's
port.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bridge.client import LocalSearchClient
from bridge.config import validate_config
from bridge.errors import BridgeError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "contracts" / "local-search"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client(handler, max_bytes: int = 8_000_000, tmp_path=None) -> LocalSearchClient:
    cfg = validate_config(
        data_dir=(tmp_path or Path("/tmp")) / "bdata",
        provider_base_url="http://127.0.0.1:18765",
        max_fetch_bytes=max_bytes,
    )
    client = LocalSearchClient(cfg)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:18765",
        follow_redirects=False,
        timeout=httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=5.0),
        headers={"User-Agent": "cpersona-bridge/0"},
    )
    return client


@pytest.fixture
def tmp_data(tmp_path):
    return tmp_path


# ---- success paths (fixture-shaped) ---------------------------------------------

def test_status_ok(tmp_data):
    fixture = load("status-200.json")
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["origin"] = request.headers.get("origin")
        seen["sfs"] = request.headers.get("sec-fetch-site")
        return httpx.Response(200, json=fixture)

    c = make_client(handler, tmp_path=tmp_data)
    st = c.status()
    assert seen["path"] == "/api/status"
    # guard compatibility (docs/10): the client sends neither header.
    assert seen["origin"] is None
    assert seen["sfs"] is None
    assert st["index"]["generation"] == fixture["index"]["generation"]
    assert st["model"]["state"] == fixture["model"]["state"]


def test_search_request_shape(tmp_data):
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=load("search-image-200.json"))

    c = make_client(handler, tmp_path=tmp_data)
    out = c.search("sunset photos", page_size=50, folder_id=7)
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/search"
    assert captured["body"] == {
        "q": "sunset photos", "tab": "image", "page_size": 50, "folder_id": 7,
    }
    assert out["items"], "fixture must carry items"


def test_search_page_size_only_enum(tmp_data):
    def handler(request):
        body = json.loads(request.content)
        if body.get("page_size") not in (20, 50, 100):
            return httpx.Response(400, json={"error": {"code": "bad_request",
                                                       "message": "page_size must be 20, 50 or 100", "field": "page_size"}})
        return httpx.Response(200, json=load("search-image-200.json"))

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.search("x", page_size=25)  # not in the enum: provider rejects
    assert ei.value.code == "invalid_request"


def test_file_detail_ok(tmp_data):
    fixture = load("file-detail-image-200.json")

    def handler(request):
        assert request.url.path == "/api/files/101"
        return httpx.Response(200, json=fixture)

    c = make_client(handler, tmp_path=tmp_data)
    d = c.file_detail(101)
    assert d["file_id"] == fixture["file_id"] and d["kind"] == "image"


# ---- error envelope mapping (T-09: codes only, message dropped) ------------------

@pytest.mark.parametrize(
    "fixture_name,status,expected_code",
    [
        ("error-409-index-mismatch.json", 409, "index_incompatible"),
        ("error-409-file-unavailable.json", 409, "asset_unavailable"),
        ("error-404-not-found.json", 404, "asset_unavailable"),
        ("error-403-forbidden-host.json", 403, "index_incompatible"),
    ],
)
def test_error_envelopes_map(tmp_data, fixture_name, status, expected_code):
    fixture = load(fixture_name)

    def handler(request):
        return httpx.Response(status, json=fixture)

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.file_detail(1)
    assert ei.value.code == expected_code
    # the provider message interpolates a personal file name: it must not
    # survive anywhere on the exception (T-09).
    assert "personal" not in str(ei.value).lower()
    assert fixture["error"]["message"] not in str(ei.value)


def test_error_envelope_with_personal_name_never_leaks(tmp_data):
    payload = {"error": {"code": "file_unavailable",
                         "message": "Cannot open 田中太郎_护照扫描件.jpg", "field": None}}

    def handler(request):
        return httpx.Response(409, json=payload)

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.file_detail(3)
    assert "护照" not in str(ei.value)
    assert ei.value.code == "asset_unavailable"


def test_unknown_error_code_is_incompatible(tmp_data):
    def handler(request):
        return httpx.Response(418, json={"error": {"code": "teapot", "message": "x", "field": None}})

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.file_detail(1)
    assert ei.value.code == "index_incompatible"


def test_non_json_200_is_incompatible(tmp_data):
    def handler(request):
        return httpx.Response(200, text="<html>proxy said hi</html>")

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.status()
    assert ei.value.code == "index_incompatible"


# ---- redirects (T-06) --------------------------------------------------------------

def test_redirect_is_fatal_never_followed(tmp_data):
    followed = []

    def handler(request):
        followed.append(request.url.path)
        if request.url.path == "/api/status":
            return httpx.Response(302, headers={"Location": "http://evil.example/x"})
        raise AssertionError("redirect target must never be requested")

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.status()
    assert ei.value.provider_code == "redirect"
    assert followed == ["/api/status"]


# ---- retry policy through real exception classes (T-07) ----------------------------

def test_connect_error_retried_once_on_detail(tmp_data):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=load("file-detail-image-200.json"))

    c = make_client(handler, tmp_path=tmp_data)
    d = c.file_detail(101)
    assert d["file_id"] == 101
    assert len(calls) == 2  # exactly one retry


def test_search_connection_error_never_retried(tmp_data):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        raise httpx.ConnectError("boom")

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.search("q", page_size=20)
    assert ei.value.code == "dependency_unavailable"
    assert len(calls) == 1


def test_read_timeout_never_retried(tmp_data):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        raise httpx.ReadTimeout("slow")

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.file_detail(101)
    assert ei.value.code == "dependency_unavailable"
    assert len(calls) == 1


# ---- content bounds (T-11) ----------------------------------------------------------

def test_content_declared_total_over_cap_refused_before_body(tmp_data):
    body_reads = []

    def handler(request):
        assert request.headers["range"] == "bytes=0-99"
        return httpx.Response(
            200,
            headers={"content-length": "5000", "content-type": "image/png"},
            content=iter_body(body_reads),
        )

    def iter_body(sink):
        yield b"x" * 5000

    c = make_client(handler, max_bytes=100, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.file_content(1, 100)
    assert ei.value.code == "media_too_large"


def test_content_lying_content_length_caught_while_streaming(tmp_data):
    def handler(request):
        # declares 50 bytes but sends 5000 (a lying server)
        return httpx.Response(
            200,
            headers={"content-length": "50", "content-type": "image/png"},
            content=b"x" * 5000,
        )

    c = make_client(handler, max_bytes=100, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.file_content(1, 100)
    assert ei.value.code == "media_too_large"


def test_content_ok_within_cap(tmp_data):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100

    def handler(request):
        assert request.headers["range"].startswith("bytes=0-")
        return httpx.Response(
            206,
            headers={"content-range": f"bytes 0-{len(png) - 1}/108",
                     "content-type": "image/png"},
            content=png,
        )

    c = make_client(handler, max_bytes=1000, tmp_path=tmp_data)
    body, mime = c.file_content(1, 1000)
    assert body == png
    assert mime == "image/png"


def test_content_409_file_unavailable(tmp_data):
    def handler(request):
        return httpx.Response(409, json=load("error-409-file-unavailable.json"))

    c = make_client(handler, tmp_path=tmp_data)
    with pytest.raises(BridgeError) as ei:
        c.file_content(9, 1000)
    assert ei.value.code == "asset_unavailable"


# ---- construction guard ---------------------------------------------------------------

def test_construction_refuses_non_loopback_base(tmp_path):
    from bridge.config import BridgeConfig
    from dataclasses import replace

    cfg = validate_config(data_dir=tmp_path, provider_base_url="http://127.0.0.1:18765")
    bad = replace(cfg, provider_base_url="http://localhost:18765")
    with pytest.raises(ValueError):
        LocalSearchClient(bad)


def test_folders_dict_response_is_incompatible():
    """GET /api/folders must answer an array; a dict means provider drift."""
    import httpx

    from bridge.config import validate_config
    from bridge.client import LocalSearchClient
    from bridge.errors import BridgeError

    def handler(request):
        return httpx.Response(200, json={"folders": []})

    cfg = validate_config(data_dir=__import__("tempfile").TemporaryDirectory().name or "/tmp",
                          provider_base_url="http://127.0.0.1:18765")
    client = LocalSearchClient(cfg)
    client._client = httpx.Client(transport=httpx.MockTransport(handler),
                                  base_url=cfg.provider_base_url)
    try:
        import pytest

        with pytest.raises(BridgeError) as e:
            client.folders()
        assert e.value.code == "index_incompatible"
    finally:
        client.close()


def test_folders_array_ok():
    import httpx

    from bridge.config import validate_config
    from bridge.client import LocalSearchClient

    def handler(request):
        assert request.url.path == "/api/folders"
        return httpx.Response(200, json=[{"id": 77, "watch": True}])

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        cfg = validate_config(data_dir=Path(td) / "d",
                              provider_base_url="http://127.0.0.1:18765")
        client = LocalSearchClient(cfg)
        client._client = httpx.Client(transport=httpx.MockTransport(handler),
                                      base_url=cfg.provider_base_url)
        try:
            assert client.folders() == [{"id": 77, "watch": True}]
        finally:
            client.close()
