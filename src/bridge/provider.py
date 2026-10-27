"""The provider port every tool codes against, plus the in-memory Fake with
programmable failures (docs/02 §Components: the Bridge talks HTTP to
local-search; tests substitute this port — no network, no real provider).

The shape mirrors the four audited routes only (docs/10). B-06 implements this
same port over HTTP; the retry policy (docs/12 §4: at most one retry,
connect-phase faults only, never for search) lives in the `_get` wrapper below
so both implementations share one rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from bridge.errors import BridgeError, provider_error_to_bridge_code

# Fault codes that behave like "the socket never opened": the only class the
# retry policy applies to. `bridge:`-prefixed codes are raised by the Fake's
# transport simulation (real code maps them after retries are exhausted).
TRANSPORT_CODES = ("connection", "timeout_connect")


@dataclass(frozen=True)
class FolderInventory:
    items: list[dict]
    raw_count: int
    truncated: bool


class ProviderPort(Protocol):
    def status(self) -> dict: ...

    def file_detail(self, file_id: int) -> dict: ...

    def file_content(self, file_id: int, max_bytes: int) -> tuple[bytes, str]: ...

    def search(
        self,
        q: str,
        *,
        page_size: int,
        folder_id: int | None = None,
        cursor: str | None = None,
    ) -> dict: ...

    def search_page(self, search_id: str, cursor: str, *, folder_id: int | None,
                    page_size: int) -> dict: ...

    def search_media(self, data: bytes, *, filename: str, folder_id: int | None = None,
                     page_size: int = 20) -> dict: ...

    def folders(self) -> list[dict]: ...

    def folder_entries(self, folder_id: int, *, limit: int) -> FolderInventory: ...


def _retry_get(op: Callable[[], dict], *, retryable: bool):
    """One retry, transport faults only, retryable routes only (T-07). On the
    last attempt a transport fault becomes dependency_unavailable — the same
    taxonomy the HTTP client produces when retries are exhausted."""
    attempts = 2 if retryable else 1
    for attempt in range(attempts):
        try:
            return op()
        except BridgeError as exc:
            transport = exc.provider_code in TRANSPORT_CODES
            if attempt + 1 < attempts and retryable and transport:
                continue
            if transport:
                raise BridgeError("dependency_unavailable", provider_code=exc.provider_code) from exc
            raise


# --------------------------------------------------------------------------
# Fake provider (test double; synthetic data only)
# --------------------------------------------------------------------------


@dataclass
class FakeFile:
    file_id: int
    kind: str = "image"
    name: str = "example.png"
    rel_dir: str = "photos/2026"
    folder_id: int = 1
    folder: str = "fixture-root/photos"
    size: int = 1234
    mtime: str = "2026-09-15T09:30:00+09:00"
    status: str = "indexed"
    available: bool = True
    width: int | None = 640
    height: int | None = 480
    content: bytes = b"\x89PNG\r\n\x1a\nfake-bytes"
    mime: str = "image/png"


@dataclass
class FakeFailure:
    """Injected failure for one route. Sticky until consumed; call counts let
    tests observe retry behavior."""

    route: str  # status | detail | content | search
    code: str  # provider envelope code (index_mismatch, file_unavailable,
    # connection, timeout_connect, ...; unknown codes map to index_incompatible)


@dataclass
class FakeProvider:
    files: dict[int, FakeFile] = field(default_factory=dict)
    generation: int = 1
    model_state: str = "ready"
    mismatch: str | None = None
    failures: list[FakeFailure] = field(default_factory=list)
    calls: list[tuple[str, tuple, dict]] = field(default_factory=list)
    # B-10: provider folder ledger as the Fake sees it — id -> {watch, status,
    # path}. path is absolute fake data and must never reach tool results.
    folders_data: dict[int, dict] = field(default_factory=dict)
    # B-11: unindexed folder inventory (what list_folder sees); file ids not
    # present in `files` are visible but WITHOUT detail/content availability.
    folder_files: dict[int, list[int]] = field(default_factory=dict)
    # ids the fake search refuses to return (simulates "indexed but not yet
    # searchable" — resolution must never rely on search visibility)
    invisible_in_search: set[int] = field(default_factory=set)

    def _fail(self, route: str) -> None:
        for f in self.failures:
            if f.route == route:
                if f.code not in TRANSPORT_CODES:
                    self.failures.remove(f)  # deterministic: HTTP-level faults end the call
                raise BridgeError(provider_error_to_bridge_code(f.code), provider_code=f.code)

    def status(self) -> dict:
        def op() -> dict:
            self.calls.append(("status", (), {}))
            self._fail("status")
            return {
                "version": "0.1.1-fake",
                "model": {"state": self.model_state},
                "scan": {"state": "idle"},
                "index": {"generation": self.generation, "mismatch": self.mismatch},
            }

        return _retry_get(op, retryable=True)

    def file_detail(self, file_id: int) -> dict:
        def op() -> dict:
            self.calls.append(("file_detail", (file_id,), {}))
            self._fail("detail")
            f = self.files.get(file_id)
            if f is None:
                raise BridgeError("asset_unavailable", provider_code="not_found")
            return {
                "file_id": f.file_id,
                "kind": f.kind,
                "name": f.name,
                "path": "/fixture-root/" + f.rel_dir + "/" + f.name,
                "folder_id": f.folder_id,
                "folder": f.folder,
                "rel_dir": f.rel_dir,
                "size": f.size,
                "mtime": f.mtime,
                "status": f.status,
                "available": f.available,
                "meta": {"width": f.width, "height": f.height},
            }

        return _retry_get(op, retryable=True)

    def file_content(self, file_id: int, max_bytes: int) -> tuple[bytes, str]:
        def op() -> tuple[bytes, str]:
            self.calls.append(("file_content", (file_id, max_bytes), {}))
            self._fail("content")
            f = self.files.get(file_id)
            if f is None:
                raise BridgeError("asset_unavailable", provider_code="not_found")
            if f.size > max_bytes:
                raise BridgeError("media_too_large")
            return f.content, f.mime

        # content is header-retryable only; body faults are not retried (T-07)
        result = _retry_get(lambda: {"v": op()}, retryable=True)["v"]
        return result

    def folders(self) -> list[dict]:
        def op() -> list[dict]:
            self.calls.append(("folders", (), {}))
            self._fail("folders")
            return [
                {"id": fid, "name": f"folder-{fid}", "watch": f.get("watch", True),
                 "status": f.get("status", "watching"), "kinds": ["image"],
                 "path": "/fixture-root/folder-" + str(fid)}
                for fid, f in sorted(self.folders_data.items())
            ]

        return _retry_get(lambda: {"v": op()}, retryable=True)["v"]

    def folder_entries(self, folder_id: int, *, limit: int) -> FolderInventory:
        def op() -> FolderInventory:
            self.calls.append(("folder_entries", (folder_id,), {"limit": limit}))
            self._fail("folder_entries")
            fetch = min(max(int(limit), 1), 100)
            # Model the global recent page BEFORE client-side folder filtering.
            recent = [(folder, fid) for folder, ids in self.folder_files.items()
                      for fid in ids][:fetch]
            out = []
            for folder, fid in recent:
                f = self.files.get(fid)
                if folder != folder_id:
                    continue
                if f is None:
                    out.append({"file_id": fid, "kind": "image",
                                "name": f"pending-{fid}.png", "folder_id": folder,
                                "rel_dir": "00", "size": 64,
                                "mtime": "2026-10-26T00:00:00+09:00"})
                else:
                    out.append({"file_id": fid, "kind": f.kind, "name": f.name,
                                "rel_dir": f.rel_dir, "size": f.size,
                                "folder_id": f.folder_id, "status": f.status,
                                "available": f.available, "mtime": f.mtime})
            return FolderInventory(out, len(recent), len(recent) >= fetch)

        return _retry_get(lambda: {"v": op()}, retryable=True)["v"]

    def search_media(self, data: bytes, *, filename: str, folder_id: int | None = None,
                     page_size: int = 20) -> dict:
        self.calls.append(("search_media", (), {"folder_id": folder_id, "page_size": page_size}))
        self._fail("search_media")
        return self.search("synthetic", page_size=page_size, folder_id=folder_id)

    def search_page(self, search_id: str, cursor: str, *, folder_id: int | None,
                    page_size: int) -> dict:
        self.calls.append(("search_page", (search_id, cursor), {"folder_id": folder_id, "page_size": page_size}))
        self._fail("search")
        raise BridgeError("invalid_request", provider_code="search_expired")

    def search(
        self,
        q: str,
        *,
        page_size: int,
        folder_id: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        def op() -> dict:
            self.calls.append(
                ("search", (q,), {"page_size": page_size, "folder_id": folder_id, "cursor": cursor})
            )
            self._fail("search")
            items = [
                {
                    "file_id": f.file_id,
                    "kind": f.kind,
                    "name": f.name,
                    "folder_id": f.folder_id,
                    "folder": f.folder,
                    "rel_dir": f.rel_dir,
                    "mtime": f.mtime,
                    "size": f.size,
                    "score": 0.9,
                    "hit_count": 1,
                    "hits": [],
                    "available": f.available,
                    "width": f.width,
                    "height": f.height,
                }
                for f in sorted(self.files.values(), key=lambda x: -x.file_id)
                if f.kind == "image" and f.file_id not in self.invisible_in_search
                and (folder_id is None or f.folder_id == folder_id)
            ][:page_size]
            return {
                "search_id": "fake-search-id",
                "generation": self.generation,
                "took_ms": 5,
                "items": items,
                "next_cursor": None,
                "remaining": 0,
                "empty_reason": None if items else "no_match",
            }

        return _retry_get(op, retryable=False)  # POST /api/search: never retried
