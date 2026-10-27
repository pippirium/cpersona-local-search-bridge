"""Explicit link of an owned input or a selected discovery candidate (IMG-07)."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.managed_store import verify_digest
from bridge.provider import ProviderPort
from bridge.repository import ASSET_ID_RE, MEMORY_REF_RE, AssetNotFound, IdentityConflict, Repository, RequestConflict, StorageError
from bridge.tools_discover import DiscoveryState, validate_candidate
from bridge.tools_ingest import validate_scope_and_key
from bridge.tools_status import get_ingest_status


@dataclass
class RegisteredLinkResult:
    ok: bool
    asset_id: str | None = None
    memory_ref: str | None = None
    link_status: str | None = None
    already_linked: bool | None = None
    error: dict | None = None


def _verify_owned(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                  scope: tuple[str, str, str, str], asset_id: str) -> tuple:
    if not repo.owned_asset(scope=scope, asset_id=asset_id):
        raise BridgeError("asset_not_found")
    asset = repo.asset(asset_id, config.provider_instance_id)
    if (not asset or asset.get("origin") != "managed" or asset.get("locator_status") not in ("pending", "ready")
            or config.managed_root is None or asset.get("provider_folder_id") != config.managed_folder_id):
        raise BridgeError("asset_unavailable")
    digest, path, size = asset.get("content_sha256"), asset.get("relative_managed_path"), asset.get("size_bytes")
    if (not isinstance(digest, str) or not isinstance(path, str) or not isinstance(size, int)
            or size <= 0 or size > config.max_fetch_bytes or not verify_digest(config.managed_root, path, digest, size)):
        raise BridgeError("asset_unavailable")
    if asset["locator_status"] == "ready":
        def status():
            return get_ingest_status(provider=provider, repo=repo, config=config, asset_id=asset_id,
                source_instance_id=scope[0], agent_id=scope[1], project_id=scope[2], channel=scope[3])
        before = status()
        if not before.ok or not before.indexed or before.status != "ready":
            raise BridgeError({"provider_unavailable": "dependency_unavailable",
                               "index_incompatible": "index_incompatible"}.get(before.reason_code, "asset_unavailable"))
        data, _ = provider.file_content(asset["provider_file_id"], config.max_fetch_bytes)
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != digest:
            raise BridgeError("asset_unavailable")
        after = status()
        if not after.ok or not after.indexed or after.status != "ready":
            raise BridgeError({"provider_unavailable": "dependency_unavailable",
                               "index_incompatible": "index_incompatible"}.get(after.reason_code, "asset_unavailable"))
    return (digest, path, size, asset["locator_status"], asset["provider_file_id"],
            asset["provider_generation"], asset["provider_folder_id"])


def link_registered_asset(*, provider: ProviderPort, repo: Repository, config: BridgeConfig, state: DiscoveryState,
                          agent_id: str, memory_ref: str, asset_id: str | None = None,
                          candidate_token: str | None = None, source_instance_id: str = "",
                          project_id: str = "", channel: str = "", client_request_id: str | None = None) -> RegisteredLinkResult:
    try:
        scope = validate_scope_and_key((source_instance_id, agent_id, project_id, channel), client_request_id)
        if (not isinstance(memory_ref, str) or not MEMORY_REF_RE.fullmatch(memory_ref)
                or ((asset_id is None) == (candidate_token is None))):
            raise BridgeError("invalid_request")
        if asset_id is not None:
            if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
                raise BridgeError("asset_not_found")
            expected = _verify_owned(provider=provider, repo=repo, config=config, scope=scope, asset_id=asset_id)
            result = repo.link_owned_image(scope=scope, asset_id=asset_id, memory_ref=memory_ref,
                provider_instance_id=config.provider_instance_id, expected=expected, request_id=client_request_id)
        else:
            evidence = validate_candidate(provider=provider, repo=repo, config=config, state=state,
                                          token=candidate_token, scope=scope)
            result = repo.link_discovered_image(scope=scope, memory_ref=memory_ref,
                provider_instance_id=config.provider_instance_id, evidence=evidence,
                token=candidate_token, request_id=client_request_id)
        return RegisteredLinkResult(True, result["asset_id"], memory_ref, result["status"], result["already_linked"])
    except AssetNotFound:
        return RegisteredLinkResult(False, error=error_result("asset_not_found")["error"])
    except RequestConflict:
        return RegisteredLinkResult(False, error=error_result("invalid_request")["error"])
    except IdentityConflict:
        return RegisteredLinkResult(False, error=error_result("asset_unavailable")["error"])
    except (StorageError, sqlite3.Error):
        return RegisteredLinkResult(False, error=error_result("storage_error")["error"])
    except BridgeError as exc:
        return RegisteredLinkResult(False, error=error_result(exc.code)["error"])
