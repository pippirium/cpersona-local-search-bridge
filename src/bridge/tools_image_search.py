"""Explicit image-query search over linked, scope-visible assets (IMG-06)."""
from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass, field

from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.image_input import ValidatedImage, decode_image_base64, validate_image
from bridge.managed_store import read_verified
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError
from bridge.tools_discover import _live_generation
from bridge.tools_get import GetAssetResult, get_asset
from bridge.tools_ingest import validate_scope_and_key
from bridge.tools_search import SearchAssetEntry

MAX_PAGES = 5
PAGE_SIZE = 20


@dataclass
class ImageSearchResult:
    ok: bool
    results: list[SearchAssetEntry] = field(default_factory=list)
    search_coverage: str | None = None
    provider_warning: str | None = None
    error: dict | None = None


def _query_asset(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                 scope: tuple[str, str, str, str], asset_id: str, generation: str) -> ValidatedImage:
    owned = repo.owned_asset(scope=scope, asset_id=asset_id)
    linked = repo.asset_in_scope(source_instance_id=scope[0], agent_id=scope[1],
        project_id=scope[2], channel=scope[3], asset_id=asset_id)
    if not (owned or linked):
        raise BridgeError("asset_not_found")
    asset = repo.asset(asset_id, config.provider_instance_id)
    if not asset or asset.get("locator_status") not in ("pending", "ready"):
        raise BridgeError("asset_unavailable")
    stored_generation = asset.get("provider_generation")
    if stored_generation is not None and str(stored_generation) != generation:
        raise BridgeError("index_incompatible")
    if owned and asset.get("origin") == "managed":
        if (config.managed_root is None or asset.get("provider_folder_id") != config.managed_folder_id
                or not asset.get("content_sha256") or not asset.get("relative_managed_path")):
            raise BridgeError("asset_unavailable")
        try:
            data = read_verified(config.managed_root, asset["relative_managed_path"], asset["size_bytes"],
                                 asset["content_sha256"], config.max_fetch_bytes)
        except BridgeError:
            raise BridgeError("asset_unavailable") from None
    else:
        # Existing get gates enforce link scope and fresh provider identity.
        response = get_asset(provider=provider, repo=repo, provider_instance_id=config.provider_instance_id,
            source_instance_id=scope[0], agent_id=scope[1], project_id=scope[2], channel=scope[3], asset_id=asset_id,
            max_fetch_bytes=config.max_fetch_bytes)
        if isinstance(response, GetAssetResult):
            raise BridgeError((response.error or {}).get("code", "asset_unavailable"))
        data = response.data
    return validate_image(data, max_bytes=config.max_fetch_bytes, max_pixels=config.max_pixels)


def _linked_identity(*, provider: ProviderPort, max_fetch_bytes: int, asset: dict,
                     item: dict, generation: str) -> bool:
    if asset.get("locator_status") != "ready" or asset.get("media_kind") != "image":
        return False
    stored_gen = asset.get("provider_generation")
    if stored_gen is not None and str(stored_gen) != generation:
        return False
    file_id = item["file_id"]
    try:
        detail = provider.file_detail(file_id)
    except BridgeError as exc:
        if exc.code == "asset_unavailable":
            return False
        raise
    if not isinstance(detail, dict):
        raise BridgeError("index_incompatible")
    fields = ("name", "rel_dir", "size", "mtime", "folder_id")
    if (detail.get("kind") != "image" or not detail.get("available", True)
            or detail.get("status") != "indexed" or any(detail.get(k) != item.get(k) for k in fields)):
        return False
    same_name = asset.get("last_seen_name") is not None and detail.get("name") == asset["last_seen_name"]
    same_size = asset.get("last_seen_size") is None or detail.get("size") == asset["last_seen_size"]
    same_mtime = asset.get("last_seen_mtime") is not None and detail.get("mtime") == asset["last_seen_mtime"]
    same_generation = stored_gen is not None and str(stored_gen) == generation
    if not same_name or not ((same_size and same_mtime) or same_generation):
        return False
    if asset.get("origin") == "managed":
        if (detail.get("folder_id") != asset.get("provider_folder_id")
                or str(detail.get("rel_dir", "")) + "/" + str(detail.get("name", "")) != asset.get("relative_managed_path")
                or not asset.get("content_sha256")):
            return False
    if asset.get("content_sha256"):
        try:
            data, _ = provider.file_content(file_id, max_fetch_bytes)
        except BridgeError as exc:
            if exc.code in ("asset_unavailable", "media_too_large"):
                return False
            raise
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != asset["content_sha256"]:
            return False
        again = provider.file_detail(file_id)
        if (not isinstance(again, dict) or again.get("kind") != "image" or not again.get("available", True)
                or again.get("status") != "indexed" or any(again.get(k) != detail.get(k) for k in fields)):
            return False
    return True


def search_assets_by_image(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                           agent_id: str, source_instance_id: str = "", project_id: str = "", channel: str = "",
                           query_asset_id: str | None = None, image_base64: str | None = None,
                           limit: int = 10, folder_id: int | None = None) -> ImageSearchResult:
    try:
        scope = validate_scope_and_key((source_instance_id, agent_id, project_id, channel), None)
        if ((query_asset_id is None) == (image_base64 is None) or type(limit) is not int or not 1 <= limit <= 20
                or (query_asset_id is not None and not isinstance(query_asset_id, str))
                or (folder_id is not None and (type(folder_id) is not int or folder_id <= 0))):
            raise BridgeError("invalid_request")
        # Authorize an asset ID before making any provider call.
        if query_asset_id is not None:
            if not (repo.owned_asset(scope=scope, asset_id=query_asset_id) or repo.asset_in_scope(
                    source_instance_id=scope[0], agent_id=scope[1], project_id=scope[2],
                    channel=scope[3], asset_id=query_asset_id)):
                raise BridgeError("asset_not_found")
        if image_base64 is not None:
            image = decode_image_base64(image_base64, max_bytes=config.max_fetch_bytes, max_pixels=config.max_pixels)
        generation = _live_generation(provider)
        if query_asset_id is not None:
            image = _query_asset(provider=provider, repo=repo, config=config, scope=scope,
                                 asset_id=query_asset_id, generation=generation)
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}[image.media_type]
        response = provider.search_media(image.data, filename="query." + ext, folder_id=folder_id, page_size=PAGE_SIZE)
        output: list[SearchAssetEntry] = []
        seen: set[str] = set()
        warning = None
        pages = 0
        exhausted = False
        capped = False
        while pages < MAX_PAGES:
            if not isinstance(response, dict) or str(response.get("generation")) != generation:
                raise BridgeError("index_incompatible")
            items = response.get("items")
            search_id, cursor = response.get("search_id"), response.get("next_cursor")
            counts_capped = response.get("counts_capped") or []
            if (not isinstance(items, list) or len(items) > PAGE_SIZE or not isinstance(search_id, str) or not search_id
                    or not isinstance(counts_capped, list) or not all(isinstance(x, str) for x in counts_capped)
                    or (cursor is not None and (not isinstance(cursor, str) or not cursor))):
                raise BridgeError("index_incompatible")
            capped |= "image" in counts_capped
            pages += 1
            for item in items:
                if not isinstance(item, dict) or type(item.get("file_id")) is not int or item["file_id"] <= 0:
                    raise BridgeError("index_incompatible")
                if item.get("kind") != "image" or (folder_id is not None and item.get("folder_id") != folder_id):
                    raise BridgeError("index_incompatible")
                score = item.get("score")
                if score is not None and (type(score) not in (int, float) or not math.isfinite(score)):
                    raise BridgeError("index_incompatible")
                for asset_id in repo.locators_by_file(config.provider_instance_id, item["file_id"]):
                    if asset_id in seen or not repo.asset_in_scope(source_instance_id=scope[0], agent_id=scope[1],
                            project_id=scope[2], channel=scope[3], asset_id=asset_id):
                        continue
                    seen.add(asset_id)
                    asset = repo.asset(asset_id, config.provider_instance_id)
                    if not asset or not _linked_identity(provider=provider, max_fetch_bytes=config.max_fetch_bytes, asset=asset,
                                                         item=item, generation=generation):
                        warning = "asset_unavailable"
                        continue
                    refs = repo.links_for_asset(source_instance_id=scope[0], agent_id=scope[1],
                        project_id=scope[2], channel=scope[3], asset_id=asset_id)
                    if not refs:
                        continue
                    output.append(SearchAssetEntry(asset_id=asset_id, provider_file_id=asset["provider_file_id"],
                        media_kind=asset["media_kind"], media_type=asset["media_type"], size_bytes=asset["size_bytes"],
                        last_seen_name=asset["last_seen_name"], locator_status=asset["locator_status"],
                        score=score, memory_refs=refs))
                    if len(output) >= limit:
                        break
                if len(output) >= limit:
                    break
            if len(output) >= limit:
                break
            if cursor is None:
                exhausted = True
                break
            if pages < MAX_PAGES:
                response = provider.search_page(search_id, cursor, folder_id=folder_id, page_size=PAGE_SIZE)
                if not isinstance(response, dict) or response.get("search_id") != search_id:
                    raise BridgeError("index_incompatible")
        if _live_generation(provider) != generation:
            raise BridgeError("index_incompatible")
        coverage = "exact" if exhausted and not capped and warning is None else "partial"
        return ImageSearchResult(True, output, coverage, warning)
    except BridgeError as exc:
        return ImageSearchResult(False, error=error_result(exc.code)["error"])
    except (StorageError, sqlite3.Error, OSError):
        return ImageSearchResult(False, error=error_result("storage_error")["error"])
