"""Allowlisted, scoped discovery with bounded ephemeral selection evidence (IMG-05)."""
from __future__ import annotations

import copy
import hashlib
import math
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.image_input import validate_image
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError, normalize_scope
from bridge.tools_get import _mime_from_magic
from bridge_policy import ALLOWED_IMAGE_MIME

TTL_SECONDS = 300
MAX_SESSIONS = 128
MAX_TOKENS = 2048
MAX_PAGE_CALLS = 5
PAGE_SIZE = 20
MAX_LIMIT = 20
MAX_VERIFICATIONS = 20


@dataclass
class Candidate:
    candidate_token: str
    kind: str
    label: str
    score: float | None
    status: str | None


@dataclass
class DiscoveryResult:
    ok: bool
    candidates: list[Candidate] = field(default_factory=list)
    search_coverage: str | None = None
    next_cursor: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: dict | None = None


class DiscoveryState:
    """Process-local opaque handles; scope and generation checks remain mandatory."""
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.sessions: dict[str, dict] = {}
        self.candidates: dict[str, dict] = {}

    def clean(self) -> None:
        now = time.monotonic()
        for mapping in (self.sessions, self.candidates):
            for token in [key for key, value in mapping.items() if value["expires"] <= now]:
                del mapping[token]
        while len(self.sessions) > MAX_SESSIONS:
            del self.sessions[min(self.sessions, key=lambda key: self.sessions[key]["expires"])]
        while len(self.candidates) > MAX_TOKENS:
            del self.candidates[min(self.candidates, key=lambda key: self.candidates[key]["expires"])]


def _scope(source: str, agent: str, project: str, channel: str) -> tuple[str, str, str, str]:
    values = (source, agent, project, channel)
    if not all(isinstance(value, str) for value in values):
        raise BridgeError("invalid_request")
    try:
        return normalize_scope(*values)
    except StorageError:
        raise BridgeError("invalid_request") from None


def _allowed(config: BridgeConfig) -> tuple[int, ...]:
    ids = set(config.discovery_folder_ids)
    if config.managed_folder_id is not None:
        ids.add(config.managed_folder_id)
    return tuple(sorted(ids))


def _live_generation(provider: ProviderPort) -> str:
    status = provider.status()
    if not isinstance(status, dict) or not isinstance(status.get("index"), dict):
        raise BridgeError("index_incompatible")
    index = status["index"]
    value = index.get("generation")
    if type(value) not in (int, str) or not str(value) or index.get("mismatch"):
        raise BridgeError("index_incompatible")
    return str(value)


def _check_folders(provider: ProviderPort, config: BridgeConfig, folders: tuple[int, ...]) -> None:
    entries = provider.folders()
    if (not isinstance(entries, list) or any(not isinstance(item, dict) or type(item.get("id")) is not int
                                               for item in entries)):
        raise BridgeError("index_incompatible")
    available = {item["id"]: item for item in entries}
    if any(folder not in available for folder in folders):
        raise BridgeError("asset_unavailable")
    if config.managed_folder_id in folders:
        entry = available[config.managed_folder_id]
        try:
            if (config.managed_root is None or not entry.get("path")
                    or not os.path.samefile(config.managed_root, entry["path"])):
                raise BridgeError("asset_unavailable")
        except (OSError, ValueError, TypeError):
            raise BridgeError("asset_unavailable") from None


def _item(item: dict, folder_id: int) -> dict:
    if not isinstance(item, dict) or item.get("folder_id") != folder_id or item.get("kind") != "image":
        raise BridgeError("index_incompatible")
    file_id, name, directory = item.get("file_id"), item.get("name"), item.get("rel_dir")
    size, mtime = item.get("size"), item.get("mtime")
    score = item.get("score")
    rank = item.get("rank_score", score)
    if (type(file_id) is not int or file_id <= 0 or not isinstance(name, str) or not name
            or len(name.encode("utf-8", errors="surrogatepass")) > 255
            or any(c in name for c in ("/", "\\")) or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or not isinstance(directory, str) or directory.startswith("/") or "\\" in directory
            or len(directory.encode("utf-8", errors="surrogatepass")) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in directory)
            or any(part in (".", "..") for part in directory.split("/"))
            or type(size) is not int or size <= 0
            or not isinstance(mtime, str) or not mtime
            or type(item.get("available", True)) is not bool
            or type(rank) not in (int, float) or not math.isfinite(rank)
            or (score is not None and (type(score) not in (int, float) or not math.isfinite(score)))):
        raise BridgeError("index_incompatible")
    return {"file_id": file_id, "name": name, "rel_dir": directory, "size": size, "mtime": mtime,
            "folder_id": folder_id, "score": float(score) if score is not None else None,
            "rank": float(rank), "available": item.get("available", True)}


def _fetch_page(provider: ProviderPort, query: str, generation: str, folder_id: int, folder: dict) -> None:
    if not folder["initialized"]:
        response = provider.search(query, page_size=PAGE_SIZE, folder_id=folder_id)
    else:
        response = provider.search_page(folder["search_id"], folder["cursor"], folder_id=folder_id, page_size=PAGE_SIZE)
    if not isinstance(response, dict) or str(response.get("generation")) != generation:
        raise BridgeError("index_incompatible")
    search_id, cursor, items = response.get("search_id"), response.get("next_cursor"), response.get("items")
    capped = response.get("counts_capped") or []
    if not isinstance(capped, list) or not all(isinstance(value, str) for value in capped):
        raise BridgeError("index_incompatible")
    if (not isinstance(search_id, str) or not search_id or not isinstance(items, list)
            or len(items) > PAGE_SIZE or (cursor is not None and (not isinstance(cursor, str) or not cursor))
            or (folder["initialized"] and search_id != folder["search_id"])):
        raise BridgeError("index_incompatible")
    parsed = [_item(item, folder_id) for item in items]
    if any(parsed[i]["rank"] < parsed[i + 1]["rank"] for i in range(len(parsed) - 1)):
        raise BridgeError("index_incompatible")
    if parsed and folder["last_rank"] is not None and parsed[0]["rank"] > folder["last_rank"]:
        raise BridgeError("index_incompatible")
    if parsed:
        folder["last_rank"] = parsed[-1]["rank"]
    if not parsed and cursor is not None:
        raise BridgeError("index_incompatible")
    folder.update(initialized=True, search_id=search_id, cursor=cursor, queue=parsed,
                  capped=folder["capped"] or "image" in capped)


def _evidence(provider: ProviderPort, config: BridgeConfig, item: dict, generation: str) -> dict | None:
    if not item["available"]:
        return None
    detail = provider.file_detail(item["file_id"])
    fields = ("name", "rel_dir", "size", "mtime", "folder_id")
    if not isinstance(detail, dict):
        raise BridgeError("index_incompatible")
    if (detail.get("kind") != "image" or not detail.get("available", True)
            or detail.get("status") not in ("indexed", "pending")
            or any(detail.get(key) != item[key] for key in fields)):
        raise BridgeError("asset_unavailable")
    if item["size"] > config.max_fetch_bytes:
        return None
    data, _ = provider.file_content(item["file_id"], config.max_fetch_bytes)
    if not isinstance(data, bytes):
        raise BridgeError("index_incompatible")
    if len(data) != item["size"] or _mime_from_magic(data) not in ALLOWED_IMAGE_MIME:
        return None
    try:
        validate_image(data, max_bytes=config.max_fetch_bytes, max_pixels=config.max_pixels)
    except BridgeError as exc:
        if exc.code in ("unsupported_media", "media_too_large", "invalid_request"):
            return None
        raise
    again = provider.file_detail(item["file_id"])
    if not isinstance(again, dict):
        raise BridgeError("index_incompatible")
    if (again.get("kind") != "image" or not again.get("available", True)
            or again.get("status") not in ("indexed", "pending")
            or any(again.get(key) != item[key] for key in fields)):
        raise BridgeError("asset_unavailable")
    return {"file_id": item["file_id"], "folder_id": item["folder_id"], "name": item["name"],
            "rel_dir": item["rel_dir"], "size": item["size"], "mtime": item["mtime"],
            "generation": generation, "sha256": hashlib.sha256(data).hexdigest()}


def discover_assets(*, provider: ProviderPort, repo: Repository, config: BridgeConfig, state: DiscoveryState,
                    agent_id: str, query: str, source_instance_id: str = "", project_id: str = "",
                    channel: str = "", folder_id: int | None = None, limit: int = 10,
                    cursor: str | None = None) -> DiscoveryResult:
    try:
        scope = _scope(source_instance_id, agent_id, project_id, channel)
        if (not isinstance(query, str) or not query.strip() or len(query.encode("utf-8", errors="surrogatepass")) > 4096
                or type(limit) is not int or not 1 <= limit <= MAX_LIMIT
                or (folder_id is not None and (type(folder_id) is not int or folder_id <= 0))
                or (cursor is not None and (not isinstance(cursor, str) or len(cursor) != 43))):
            raise BridgeError("invalid_request")
        allowlist = _allowed(config)
        if not allowlist or (folder_id is not None and folder_id not in allowlist):
            raise BridgeError("asset_not_found")
        folders = (folder_id,) if folder_id is not None else allowlist
        query_hash = hashlib.sha256(query.encode("utf-8", errors="surrogatepass")).hexdigest()
        with state.lock:
            state.clean()
            if cursor is None:
                generation = _live_generation(provider)
                _check_folders(provider, config, folders)
                session = {"scope": scope, "allowlist": allowlist, "folders": folders,
                    "query_hash": query_hash, "provider_id": config.provider_instance_id,
                    "generation": generation, "expires": time.monotonic() + TTL_SECONDS,
                    "pages": {fid: {"initialized": False, "cursor": None, "queue": [],
                                    "search_id": None, "last_rank": None, "capped": False}
                              for fid in folders}}
            else:
                existing = state.sessions.get(cursor)
                if (not existing or existing["scope"] != scope or existing["allowlist"] != allowlist
                        or existing["folders"] != folders or existing["query_hash"] != query_hash
                        or existing["provider_id"] != config.provider_instance_id):
                    raise BridgeError("invalid_request")
                session = copy.deepcopy(existing)
                generation = _live_generation(provider)
                if generation != session["generation"]:
                    raise BridgeError("index_incompatible")
                _check_folders(provider, config, folders)
            calls = 0
            for fid in folders:
                page = session["pages"][fid]
                if not page["initialized"] and calls < MAX_PAGE_CALLS:
                    _fetch_page(provider, query, generation, fid, page)
                    calls += 1
            issued: list[tuple[str, dict]] = []
            results: list[Candidate] = []
            warnings: set[str] = set()
            initialized = all(page["initialized"] for page in session["pages"].values())
            verifications = 0
            while initialized and len(results) < limit:
                for fid in folders:
                    page = session["pages"][fid]
                    if not page["queue"] and page["cursor"] is not None and calls < MAX_PAGE_CALLS:
                        _fetch_page(provider, query, generation, fid, page)
                        calls += 1
                if any(not page["queue"] and page["cursor"] is not None for page in session["pages"].values()):
                    break  # cannot safely merge until all relevant heads are known
                active = [(fid, page["queue"][0]) for fid, page in session["pages"].items() if page["queue"]]
                if not active:
                    break
                fid, item = min(active, key=lambda entry: (-entry[1]["rank"], entry[0], entry[1]["file_id"]))
                rel = item["rel_dir"] + "/" + item["name"]
                if not repo.discovery_visible(scope=scope, provider_instance_id=config.provider_instance_id,
                                              file_id=item["file_id"], folder_id=fid, relative_path=rel):
                    session["pages"][fid]["queue"].pop(0)
                    continue
                if verifications >= MAX_VERIFICATIONS:
                    break
                session["pages"][fid]["queue"].pop(0)
                verifications += 1
                evidence = _evidence(provider, config, item, generation)
                if evidence is None:
                    warnings.add("candidate_unavailable")
                    continue
                if not repo.discovery_visible(scope=scope, provider_instance_id=config.provider_instance_id,
                                              file_id=item["file_id"], folder_id=fid, relative_path=rel):
                    continue
                token = secrets.token_urlsafe(32)
                evidence.update(scope=scope, allowlist=allowlist, provider_id=config.provider_instance_id,
                                expires=time.monotonic() + TTL_SECONDS)
                issued.append((token, evidence))
                results.append(Candidate(token, "image", item["name"], item["score"],
                                         "managed" if fid == config.managed_folder_id else "available"))
            if _live_generation(provider) != generation:
                raise BridgeError("index_incompatible")
            remaining = any(not page["initialized"] or page["queue"] or page["cursor"] is not None
                            for page in session["pages"].values())
            capped = any(page["capped"] for page in session["pages"].values())
            coverage = "partial" if remaining or capped else "exact"
            next_cursor = None
            if remaining:
                next_cursor = secrets.token_urlsafe(32)
                session["expires"] = time.monotonic() + TTL_SECONDS
                state.sessions[next_cursor] = session
            if cursor is not None:
                state.sessions.pop(cursor, None)
            for token, evidence in issued:
                state.candidates[token] = evidence
            state.clean()
            return DiscoveryResult(True, results, coverage, next_cursor, sorted(warnings))
    except BridgeError as exc:
        return DiscoveryResult(False, error=error_result(exc.code)["error"])
    except (StorageError, sqlite3.Error, OSError):
        return DiscoveryResult(False, error=error_result("storage_error")["error"])


def validate_candidate(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                       state: DiscoveryState, token: str, scope: tuple[str, str, str, str]) -> dict:
    """Revalidate an explicitly selected token for the future link operation."""
    scope = _scope(*scope)
    if not isinstance(token, str) or len(token) != 43:
        raise BridgeError("invalid_request")
    with state.lock:
        state.clean()
        saved = state.candidates.get(token)
        if (not saved or saved["scope"] != scope or saved["allowlist"] != _allowed(config)
                or saved["provider_id"] != config.provider_instance_id):
            raise BridgeError("invalid_request")
        if _live_generation(provider) != saved["generation"]:
            raise BridgeError("index_incompatible")
        _check_folders(provider, config, (saved["folder_id"],))
        rel = saved["rel_dir"] + "/" + saved["name"]
        try:
            visible = repo.discovery_visible(scope=scope, provider_instance_id=config.provider_instance_id,
                file_id=saved["file_id"], folder_id=saved["folder_id"], relative_path=rel)
        except (StorageError, sqlite3.Error):
            raise BridgeError("storage_error") from None
        if not visible:
            raise BridgeError("asset_not_found")
        detail = provider.file_detail(saved["file_id"])
        if not isinstance(detail, dict):
            raise BridgeError("index_incompatible")
        if (detail.get("kind") != "image" or not detail.get("available", True)
                or detail.get("status") not in ("indexed", "pending")
                or any(detail.get(key) != saved[key] for key in ("name", "rel_dir", "size", "mtime", "folder_id"))):
            raise BridgeError("asset_unavailable")
        data, _ = provider.file_content(saved["file_id"], config.max_fetch_bytes)
        if not isinstance(data, bytes):
            raise BridgeError("index_incompatible")
        if len(data) != saved["size"] or hashlib.sha256(data).hexdigest() != saved["sha256"]:
            raise BridgeError("asset_unavailable")
        again = provider.file_detail(saved["file_id"])
        if not isinstance(again, dict):
            raise BridgeError("index_incompatible")
        if (not again.get("available", True) or again.get("kind") != "image"
                or any(again.get(key) != saved[key] for key in ("name", "rel_dir", "size", "mtime", "folder_id"))
                or _live_generation(provider) != saved["generation"]):
            raise BridgeError("asset_unavailable")
        return dict(saved)
