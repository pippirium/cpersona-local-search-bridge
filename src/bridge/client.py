"""Real local-search HTTP client over the ProviderPort (B-06).

Implements docs/12 §4 exactly:
- base URL validated at construction (literal IPv4 loopback, T-06);
- redirects never followed — any 3xx is a transport-class fault surfaced by
  tools as dependency_unavailable (a provider that suddenly redirects is an
  incompatibility to stop at, not a location to chase);
- no Origin / Sec-Fetch-Site headers (guard compatibility, docs/10);
- connect 2.0 s / read 10.0 s; retry: at most once, transport faults only,
  never for POST /api/search, never after any response byte;
- content fetched with Range and streamed: a declared total over the cap is
  refused before the body; actual streamed bytes are re-checked (T-11);
- provider error envelopes map by CODE; provider `message` text is discarded
  (it interpolates personal file names, T-09).
"""

from __future__ import annotations

import httpx

from bridge.config import BridgeConfig, is_literal_loopback
from bridge.errors import BridgeError, provider_error_to_bridge_code
from bridge.provider import _retry_get

_TIMEOUT = httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=5.0)


def _transport(kind: str) -> BridgeError:
    """Transport-class faults; _retry_get (shared with the Fake) maps them to
    dependency_unavailable after retries are exhausted (docs/12 §4)."""
    return BridgeError("dependency_unavailable", provider_code=kind)


class LocalSearchClient:
    """ProviderPort implementation for the four audited routes."""

    def __init__(self, config: BridgeConfig) -> None:
        # config validated the URL at load; assert the invariant again in case
        # a BridgeConfig was hand-built in-process.
        if not is_literal_loopback(config.provider_base_url):
            raise ValueError("provider base URL must be literal IPv4 loopback")
        self._max_bytes = config.max_fetch_bytes
        self._client = httpx.Client(
            base_url=config.provider_base_url.rstrip("/"),
            timeout=_TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "cpersona-bridge/0"},
        )

    def close(self) -> None:
        self._client.close()

    # -- envelope mapping -------------------------------------------------------

    @staticmethod
    def _map(response: httpx.Response) -> BridgeError:
        """Code-only mapping; provider message text is discarded (T-09)."""
        code = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    code = str(err.get("code", ""))
        except Exception:
            code = ""
        if not code:
            code = f"http_{response.status_code}"
        return BridgeError(provider_error_to_bridge_code(code), provider_code=code)

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code in (301, 302, 303, 307, 308):
            raise _transport("redirect")
        if response.status_code == 404:
            raise BridgeError("asset_unavailable", provider_code="not_found")
        if response.status_code != 200:
            raise self._map(response)
        return response

    def _get(self, route: str, *, retryable: bool, **kw) -> httpx.Response:
        def op() -> httpx.Response:
            try:
                return self._client.get(route, **kw)
            except httpx.ConnectTimeout as exc:
                raise _transport("timeout_connect") from exc
            except httpx.ConnectError as exc:
                raise _transport("connection") from exc
            except httpx.TimeoutException as exc:
                # read timeouts are never retried (T-07: connect phase only)
                raise _transport("timeout_read") from exc
            except httpx.HTTPError as exc:  # pragma: no cover - transport detail
                raise _transport("connection") from exc

        return _retry_get(op, retryable=retryable)

    @staticmethod
    def _json_list(response: httpx.Response) -> list:
        try:
            data = response.json()
        except Exception:
            raise BridgeError("index_incompatible", provider_code="bad_json") from None
        if not isinstance(data, list):
            raise BridgeError("index_incompatible", provider_code="bad_json")
        return data

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except Exception:
            raise BridgeError("index_incompatible", provider_code="bad_json") from None
        if not isinstance(data, dict):
            raise BridgeError("index_incompatible", provider_code="bad_json")
        return data

    # -- ProviderPort ------------------------------------------------------------

    def status(self) -> dict:
        response = self._get("/api/status", retryable=True)
        self._check(response)
        return self._json(response)

    def search(
        self,
        q: str,
        *,
        page_size: int,
        folder_id: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        body: dict = {"q": q, "tab": "image", "page_size": page_size}
        if folder_id is not None:
            body["folder_id"] = folder_id
        if cursor is not None:
            body["cursor"] = cursor
        # POST /api/search is NEVER retried (T-07): a slow provider stays
        # slow, but we never double-scan the index behind a timeout.
        try:
            response = self._client.post("/api/search", json=body)
        except httpx.TimeoutException as exc:
            raise _transport("timeout_read") from exc
        except httpx.HTTPError as exc:
            raise _transport("connection") from exc
        if response.status_code == 400:
            raise BridgeError("invalid_request", provider_code="bad_request")
        self._check(response)
        return self._json(response)

    def search_media(self, data: bytes, *, filename: str,
                     folder_id: int | None = None,
                     page_size: int = 20) -> dict:
        """POST /api/search/media multipart (`file`, tab=image) — the ONLY
        image→image path (docs/10). Same never-retried rule as text search
        (it is a search). Eval-only today; kept on the client so eval never
        bypasses the audited client rules."""
        try:
            response = self._client.post(
                "/api/search/media",
                files={"file": (filename, data, "application/octet-stream")},
                data={"tab": "image", "page_size": str(page_size),
                      **({"folder_id": str(folder_id)} if folder_id is not None else {})},
            )
        except httpx.TimeoutException as exc:
            raise _transport("timeout_read") from exc
        except httpx.HTTPError as exc:
            raise _transport("connection") from exc
        if response.status_code == 400:
            raise BridgeError("invalid_request", provider_code="bad_request")
        self._check(response)
        return self._json(response)

    def folders(self) -> list[dict]:
        # GET /api/folders answers with a JSON ARRAY (contract audit docs/10);
        # a dict from it is the provider drifting from contract, which is an
        # incompatibility, not something to guess about.
        response = self._get("/api/folders", retryable=True)
        self._check(response)
        return self._json_list(response)

    def folder_entries(self, folder_id: int, *, limit: int) -> list[dict]:
        """Folder inventory via GET /api/files/recent — the audited route
        (docs/10): it answers a JSON ARRAY of ResultItems, newest-first, and
        has NO folder filter, so entries are selected here by folder_id.
        `limit` bounds the RETURNED entries; the fetch pages through up to
        _RECENT_MAX per request (server caps limit at RECENT_MAX=100)."""
        out: list[dict] = []
        fetch = min(max(int(limit), 1), 100)
        # recent is newest-first without paging; for a bounded managed folder
        # one page suffices to see fresh copies — full-history inventory is
        # not a provider capability (recorded limitation, docs/10).
        response = self._get("/api/files/recent", retryable=True,
                             params={"limit": fetch})
        self._check(response)
        items = self._json_list(response)
        for item in items:
            if item.get("folder_id") == int(folder_id):
                out.append(item)
                if len(out) >= int(limit):
                    break
        return out

    def file_detail(self, file_id: int) -> dict:
        response = self._get(f"/api/files/{int(file_id)}", retryable=True)
        self._check(response)
        return self._json(response)

    def file_content(self, file_id: int, max_bytes: int) -> tuple[bytes, str]:
        def send() -> httpx.Response:
            return self._client.send(
                self._client.build_request(
                    "GET",
                    f"/api/files/{int(file_id)}/content",
                    headers={"Range": f"bytes=0-{max_bytes - 1}"},
                ),
                stream=True,
            )

        try:
            # Only the connect phase is retryable; once send() returns, bytes
            # may have started and no retry is possible (T-07/T-11).
            response = _retry_get(send, retryable=True)
        except httpx.TimeoutException as exc:
            kind = (
                "timeout_connect"
                if isinstance(exc, httpx.ConnectTimeout)
                else "timeout_read"
            )
            raise _transport(kind) from exc
        except httpx.ConnectError as exc:
            raise _transport("connection") from exc
        except httpx.HTTPError as exc:  # pragma: no cover
            raise _transport("connection") from exc
        try:
            if response.status_code in (301, 302, 303, 307, 308):
                raise _transport("redirect")
            if response.status_code == 404:
                raise BridgeError("asset_unavailable", provider_code="not_found")
            if response.status_code == 409:
                raise self._map(response)
            if response.status_code not in (200, 206):
                raise self._map(response)
            declared_total: int | None = None
            content_range = response.headers.get("content-range")
            if content_range and "/" in content_range:
                tail = content_range.rsplit("/", 1)[-1]
                if tail.isdigit():
                    declared_total = int(tail)
            elif response.status_code == 200:
                length = response.headers.get("content-length")
                if length is not None and length.isdigit():
                    declared_total = int(length)
            if declared_total is not None and declared_total > max_bytes:
                raise BridgeError("media_too_large")
            chunks: list[bytes] = []
            received = 0
            try:
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    received += len(chunk)
                    if received > max_bytes:
                        raise BridgeError("media_too_large")
            except httpx.TimeoutException as exc:
                raise _transport("timeout_read") from exc
            return b"".join(chunks), response.headers.get("content-type", "")
        finally:
            response.close()
