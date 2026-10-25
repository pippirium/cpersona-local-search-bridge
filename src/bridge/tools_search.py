"""search_assets / list_memory_assets tools (B-08, docs/04).

The core privacy rule (docs/01/04/08): local-search indexes the user's
WHOLE library; search_assets is NOT a personal-file search endpoint. The
flow is fixed:

    provider candidates -> intersect with THIS scope's linked assets
                        -> respond

Unassociated library files never appear, not even as a count or name.

search_coverage tells the truth about how much of the scoped corpus the
answer represents:
- "exact": every page of the provider search was scanned and the scoped
  matches fit entirely within it (no truncation anywhere);
- "partial": the answer is a filtered sample of the provider's top-N per
  page — never present it as exhaustive scoped search.

Empty index, zero hits, and provider faults are all covered without
substituting other files (docs/06 acceptance).
"""

from __future__ import annotations

from dataclasses import dataclass

from bridge.errors import BridgeError, error_result
from bridge.provider import ProviderPort
from bridge.repository import Repository

MAX_LIMIT = 20  # docs/04: search_assets limit max 20
_PAGE_SIZE = 20  # smallest allowed page_size -> the most candidates per call
_MAX_PAGES = 5  # bounded scan: 5*20 = up to 100 provider candidates


@dataclass
class SearchAssetEntry:
    asset_id: str
    provider_file_id: int | None
    media_kind: str
    media_type: str | None
    size_bytes: int | None
    last_seen_name: str | None
    locator_status: str | None
    score: float | None  # provider internal score, verbatim (docs/04)
    memory_refs: list[str]  # linked memories IN THIS SCOPE for this asset


@dataclass
class SearchAssetsResult:
    ok: bool
    query: str
    results: list[SearchAssetEntry]
    search_coverage: str | None  # exact | partial
    provider_warning: str | None  # health codes, e.g. model_not_ready
    error: dict | None = None


@dataclass
class ListMemoryAssetsResult:
    ok: bool
    memory_ref: str
    assets: list[dict]
    error: dict | None = None


def search_assets(
    *,
    provider: ProviderPort,
    repo: Repository,
    provider_instance_id: str,
    source_instance_id: str,
    agent_id: str,
    project_id: str,
    channel: str,
    query: str,
    limit: int = 10,
    folder_id: int | None = None,
) -> SearchAssetsResult:
    try:
        query = str(query)
        if not query.strip():
            raise BridgeError("invalid_request")
        limit = max(1, min(int(limit), MAX_LIMIT))
        provider_warning: str | None = None
        # Best-effort health annotation on the response (docs/04: provider
        # health). Never blocks the search and never leaks more than codes.
        try:
            st = provider.status()
            state = (st.get("model") or {}).get("state")
            if st.get("index", {}).get("mismatch"):
                provider_warning = "index_mismatch"
            elif state not in ("ready", "idle"):
                provider_warning = "model_not_ready"
        except BridgeError as exc:
            # provider health unavailable -> search cannot run truthfully
            raise BridgeError("dependency_unavailable") from exc

        # ---- candidate retrieval (bounded) ---------------------------------
        candidates: list[dict] = []
        cursor: str | None = None
        pages = 0
        exhausted = False
        while pages < _MAX_PAGES:
            data = provider.search(query, page_size=_PAGE_SIZE,
                                   folder_id=folder_id, cursor=cursor)
            items = data.get("items") or []
            candidates.extend(items)
            pages += 1
            cursor = data.get("next_cursor")
            if not cursor:
                exhausted = True
                break
        if not exhausted:
            # scan truncated by our own page bound -> explicitly partial
            pass

        # ---- scope intersection BEFORE response (docs/04) --------------------
        out: list[SearchAssetEntry] = []
        seen: set[str] = set()
        for item in candidates:
            file_id = item.get("file_id")
            if file_id is None:
                continue
            asset_ids = repo.locators_by_file(provider_instance_id, int(file_id))
            for asset_id in asset_ids:
                if asset_id in seen:
                    continue
                if not repo.asset_in_scope(
                    source_instance_id=source_instance_id, agent_id=agent_id,
                    project_id=project_id, channel=channel, asset_id=asset_id,
                ):
                    continue  # linked elsewhere or unlinked: invisible (T-02)
                seen.add(asset_id)
                asset = repo.asset(asset_id, provider_instance_id)
                refs = repo.links_for_asset(
                    source_instance_id=source_instance_id, agent_id=agent_id,
                    project_id=project_id, channel=channel, asset_id=asset_id,
                )
                out.append(SearchAssetEntry(
                    asset_id=asset_id,
                    provider_file_id=(asset or {}).get("provider_file_id"),
                    media_kind=(asset or {}).get("media_kind") or "image",
                    media_type=(asset or {}).get("media_type"),
                    size_bytes=(asset or {}).get("size_bytes"),
                    last_seen_name=(asset or {}).get("last_seen_name"),
                    locator_status=(asset or {}).get("locator_status"),
                    score=item.get("score"),
                    memory_refs=refs,
                ))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break

        # ---- coverage truth ----------------------------------------------------
        # exact requires: provider pages fully exhausted AND we consumed the
        # whole candidate list without the limit cutting anything below it.
        exact = exhausted and len(out) < limit and not _candidates_remain(
            candidates, out, limit)
        coverage = "exact" if exact else "partial"
        return SearchAssetsResult(
            ok=True, query=query, results=out, search_coverage=coverage,
            provider_warning=provider_warning,
        )
    except BridgeError as exc:
        return SearchAssetsResult(ok=False, query=str(query), results=[],
                                  search_coverage=None,
                                  provider_warning=None,
                                  error=error_result(exc.code)["error"])
    except (TypeError, ValueError):
        return SearchAssetsResult(ok=False, query=str(query), results=[],
                                  search_coverage=None, provider_warning=None,
                                  error=error_result("invalid_request")["error"])


def _candidates_remain(candidates: list[dict], out: list[SearchAssetEntry],
                       limit: int) -> bool:
    """True if the loop stopped on the limit while more scoped candidates
    could exist (i.e. the answer is a top-N sample of a larger scoped set)."""
    if len(out) < limit:
        return False
    return len(candidates) >= limit


def list_memory_assets(
    *,
    repo: Repository,
    provider_instance_id: str,
    source_instance_id: str,
    agent_id: str,
    project_id: str,
    channel: str,
    memory_ref: str,
    limit: int = 20,
) -> ListMemoryAssetsResult:
    """docs/04: ordered asset references for one memory — kind, display
    label (name only, never a path), status, stable asset_id. No bytes."""
    try:
        from bridge.repository import MEMORY_REF_RE

        # Validate the ref shape first: otherwise a malformed ref silently
        # matches nothing and looks like an empty memory (docs/04 invalid
        # input must be a typed error, not a disguised empty result).
        if not MEMORY_REF_RE.match(str(memory_ref)):
            raise BridgeError("invalid_request")
        limit = max(1, min(int(limit), 100))
        rows = repo.assets_for_memory(
            source_instance_id=source_instance_id, agent_id=agent_id,
            project_id=project_id, channel=channel, memory_ref=memory_ref,
            limit=limit, provider_instance_id=provider_instance_id,
        )
        assets = [{
            "asset_id": r["asset_id"],
            "media_kind": r["media_kind"],
            "media_type": r["media_type"],
            "display_label": r["last_seen_name"],
            "link_status": r["locator_status"] or "pending",
            "size_bytes": r["size_bytes"],
            "linked_at": r["linked_at"],
        } for r in rows]
        return ListMemoryAssetsResult(ok=True, memory_ref=memory_ref,
                                      assets=assets)
    except BridgeError as exc:
        return ListMemoryAssetsResult(ok=False, memory_ref=str(memory_ref),
                                      assets=[],
                                      error=error_result(exc.code)["error"])
    except (TypeError, ValueError):
        return ListMemoryAssetsResult(ok=False, memory_ref=str(memory_ref),
                                      assets=[],
                                      error=error_result("invalid_request")["error"])
