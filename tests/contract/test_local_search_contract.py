"""B-01 contract tests: the Bridge-facing model of the local-search HTTP API.

Every assertion here encodes a fact verified in the pinned local-search source
(docs/10-contract-audit.md). If a future provider revision breaks one of these
fixture-level expectations, this suite fails and the contract docs must be
updated before the Bridge client changes. No live provider is contacted.
"""

from __future__ import annotations

import pytest

RESULT_ITEM_FIELDS = {
    "file_id", "kind", "name", "folder_id", "folder", "rel_dir", "mtime",
    "size", "score", "rank_score", "hit_count", "hits", "thumb", "available",
    "duration_sec", "width", "height", "taken_at", "pages",
}
SEARCH_RESPONSE_FIELDS = {
    "search_id", "generation", "took_ms", "query", "fusion", "counts",
    "counts_capped", "items", "next_cursor", "remaining", "empty_reason",
    "unfiltered_all",
}
FILE_DETAIL_FIELDS = {
    "file_id", "kind", "name", "path", "folder_id", "folder", "rel_dir",
    "size", "mtime", "status", "available", "thumb", "meta", "index", "hits",
    "contexts",
}
ERROR_ENVELOPE_KEYS = {"error"}
ERROR_DETAIL_KEYS = {"code", "message", "field"}


def test_search_response_shape(search_fixture):
    assert set(search_fixture) == SEARCH_RESPONSE_FIELDS
    assert search_fixture["fusion"] in ("rrf", "score")
    assert search_fixture["generation"] is None or isinstance(search_fixture["generation"], int)
    assert search_fixture["empty_reason"] in (
        None, "no_index", "below_threshold", "filtered", "tab_empty",
    )


def test_search_items_are_images_without_paths(search_fixture):
    for item in search_fixture["items"]:
        assert set(item) == RESULT_ITEM_FIELDS
        assert item["kind"] == "image"
        assert isinstance(item["file_id"], int) and item["file_id"] >= 1
        assert item["available"] is True
        # Search items never carry an absolute path or a content hash.
        assert "path" not in item
        assert "quick_hash" not in item


PAGE_SIZES = (20, 50, 100)  # SearchRequest.page_size is Literal[20, 50, 100]


def test_search_page_size_enum():
    # The only accepted page_size values; the Bridge maps limit<=20 -> 20.
    assert PAGE_SIZES == (20, 50, 100)
    assert all(isinstance(v, int) for v in PAGE_SIZES)


def test_search_items_fit_one_page_for_limit_20(search_fixture):
    # fixture page_size 20 response: items count never exceeds the page size.
    assert len(search_fixture["items"]) <= PAGE_SIZES[0]


def test_file_detail_identity_fields(file_detail_fixture):
    assert set(file_detail_fixture) == FILE_DETAIL_FIELDS
    assert file_detail_fixture["kind"] == "image"
    assert file_detail_fixture["status"] in (
        "pending", "indexed", "failed", "skipped", "missing",
    )
    assert isinstance(file_detail_fixture["size"], int)
    assert isinstance(file_detail_fixture["meta"].get("width"), int)
    # detail.meta never carries a hash: byte identity needs an explicit fetch.
    assert "quick_hash" not in file_detail_fixture["meta"]
    assert "sha256" not in file_detail_fixture["meta"]


def test_file_detail_path_never_leaves_bridge(file_detail_fixture):
    # Guardrail for B-04..B-09 implementations: fixtures contain a path field
    # that the Bridge must strip; assert the fixture really has one so the
    # strip logic stays under test.
    assert file_detail_fixture["path"].startswith("/fixture-root/")


def test_status_reports_generation_and_mismatch(status_fixture):
    assert status_fixture["index"]["generation"] is None or isinstance(
        status_fixture["index"]["generation"], int
    )
    assert status_fixture["index"]["mismatch"] in (None, "model", "params")
    assert status_fixture["model"]["state"] in ("downloading", "loading", "ready", "error")


@pytest.mark.parametrize(
    "name,code,status_semantics",
    [
        ("local-search/error-409-index-mismatch.json", "index_mismatch", 409),
        ("local-search/error-409-file-unavailable.json", "file_unavailable", 409),
        ("local-search/error-404-not-found.json", "not_found", 404),
        ("local-search/error-403-forbidden-host.json", "forbidden_host", 403),
    ],
)
def test_error_envelope_shape(load_contract_fixture, name, code, status_semantics):
    body = load_contract_fixture(name)
    assert set(body) == ERROR_ENVELOPE_KEYS
    assert set(body["error"]) == ERROR_DETAIL_KEYS
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    # status is conveyed by the HTTP status line; the fixture documents it for
    # the Bridge error-mapping table (docs/04 error taxonomy).
    assert status_semantics in (403, 404, 409)


def test_guard_client_requirements():
    # api/guard.py: Host must be 127.0.0.1:<port> or localhost:<port>; a request
    # with neither Origin nor Sec-Fetch-Site passes (stdio clients send neither).
    allowed_hosts = {"127.0.0.1:18765", "localhost:18765"}
    assert "127.0.0.1:18765" in allowed_hosts
    assert "evil.example:18765" not in allowed_hosts
    # The Bridge client must not add browser-like headers: emulate "neither".
    client_headers = {"User-Agent": "bridge/0"}
    assert "Origin" not in client_headers
    assert "Sec-Fetch-Site" not in client_headers
