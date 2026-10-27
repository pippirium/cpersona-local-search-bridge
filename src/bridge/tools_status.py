"""Read-only scoped status for managed inputs and linked assets (IMG-04)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.managed_store import copy_is_missing, verify_digest
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError


@dataclass
class IngestStatusResult:
    ok: bool
    asset_id: str | None = None
    status: str | None = None
    indexed: bool | None = None
    retryable: bool | None = None
    reason_code: str | None = None
    checked_at: str | None = None
    error: dict | None = None


def get_ingest_status(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                      asset_id: str, agent_id: str, source_instance_id: str = "",
                      project_id: str = "", channel: str = "") -> IngestStatusResult:
    """Probe only the requested asset; do not mutate locators or start a resolver."""
    try:
        scope = (source_instance_id, agent_id, project_id, channel)
        if not all(isinstance(x, str) for x in scope) or not agent_id or not isinstance(asset_id, str):
            raise BridgeError("invalid_request")
        try:
            owned = repo.owned_asset(scope=scope, asset_id=asset_id)
            linked = repo.asset_in_scope(source_instance_id=source_instance_id, agent_id=agent_id,
                project_id=project_id, channel=channel, asset_id=asset_id)
        except StorageError:
            raise BridgeError("invalid_request") from None
        if not (owned or linked):
            raise BridgeError("asset_not_found")
        asset = repo.asset(asset_id, config.provider_instance_id)
        if not asset or asset.get("locator_status") is None:
            raise BridgeError("asset_not_found")
        state = asset["locator_status"]
        checked = asset.get("verified_at")
        def result(status=state, *, indexed=False, retryable=True, reason=None):
            return IngestStatusResult(True, asset_id, status, indexed, retryable, reason, checked)
        if state == "incompatible":
            return result(retryable=False, reason="index_incompatible")
        if state == "missing":
            return result(reason="file_missing")
        if state == "stale":
            return result(reason="identity_mismatch")
        try:
            health = provider.status()
        except BridgeError as exc:
            if exc.code == "index_incompatible":
                return result(status="incompatible", retryable=False, reason="index_incompatible")
            return result(reason="provider_unavailable")
        index = health.get("index") or {}
        if index.get("mismatch"):
            return result(status="incompatible", retryable=False, reason="index_incompatible")
        old_generation = asset.get("provider_generation")
        generation = index.get("generation")
        if old_generation is not None and generation is not None and str(old_generation) != str(generation):
            return result(status="incompatible", retryable=False, reason="index_incompatible")
        if asset.get("origin") == "managed":
            if (config.managed_root is None or asset.get("provider_folder_id") != config.managed_folder_id):
                return result(reason="folder_not_watched")
            try:
                folder = next((x for x in provider.folders() if x.get("id") == config.managed_folder_id), None)
                folder_ok = (folder is not None and folder.get("watch") and folder.get("path")
                             and os.path.samefile(config.managed_root, folder["path"]))
            except BridgeError:
                return result(reason="provider_unavailable")
            except (OSError, TypeError, ValueError):
                folder_ok = False
            if not folder_ok:
                return result(reason="folder_not_watched")
            rel = asset.get("relative_managed_path")
            digest = asset.get("content_sha256")
            size = asset.get("size_bytes")
            if (not isinstance(rel, str) or not isinstance(digest, str)
                    or not isinstance(size, int) or size <= 0 or size > config.max_fetch_bytes):
                return result(status="stale", reason="identity_mismatch")
            if not verify_digest(config.managed_root, rel, digest, size):
                if copy_is_missing(config.managed_root, rel):
                    return result(status="missing", reason="file_missing")
                return result(status="stale", reason="identity_mismatch")
        if state == "pending":
            if asset.get("origin") != "managed":
                return result(reason="waiting_for_index")
            try:
                inventory = provider.folder_entries(config.managed_folder_id, limit=100)
            except BridgeError as exc:
                if exc.code == "index_incompatible":
                    return result(status="incompatible", retryable=False, reason="index_incompatible")
                return result(reason="provider_unavailable")
            return result(reason="page_full" if inventory.truncated else "waiting_for_index")
        file_id = asset.get("provider_file_id")
        if file_id is None:
            return result(status="stale", reason="identity_mismatch")
        try:
            detail = provider.file_detail(file_id)
        except BridgeError as exc:
            if exc.code == "asset_unavailable":
                return result(status="missing", reason="file_missing")
            if exc.code == "index_incompatible":
                return result(status="incompatible", retryable=False, reason="index_incompatible")
            return result(reason="provider_unavailable")
        if not detail.get("available", True):
            return result(status="missing", reason="file_missing")
        if detail.get("status") == "pending":
            return result(status="pending", reason="waiting_for_index")
        same_gen = old_generation is not None and generation is not None and str(old_generation) == str(generation)
        same_name = asset.get("last_seen_name") is not None and detail.get("name") == asset.get("last_seen_name")
        size_ok = asset.get("last_seen_size") is None or detail.get("size") == asset.get("last_seen_size")
        time_ok = asset.get("last_seen_mtime") is not None and detail.get("mtime") == asset.get("last_seen_mtime")
        if (detail.get("kind") != "image" or detail.get("status") not in ("indexed", "pending")
                or not same_name or not ((size_ok and time_ok) or same_gen)):
            return result(status="stale", reason="identity_mismatch")
        if asset.get("origin") == "managed":
            folder = detail.get("folder_id")
            rel = str(detail.get("rel_dir", "")) + "/" + str(detail.get("name", ""))
            if folder != config.managed_folder_id or rel != asset.get("relative_managed_path"):
                return result(status="stale", reason="identity_mismatch")
        return result(indexed=True, retryable=False)
    except BridgeError as exc:
        return IngestStatusResult(False, error=error_result(exc.code)["error"])
