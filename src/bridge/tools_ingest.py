"""Explicit, bounded input of unindexed images (IMG-02/IMG-03)."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.image_input import ValidatedImage, decode_image_base64
from bridge.managed_store import publish
from bridge.provider import ProviderPort
from bridge.repository import Repository, RequestConflict, StorageError, normalize_scope
from bridge.tools_import import _managed_relpath


@dataclass
class IngestResult:
    ok: bool
    asset_id: str | None = None
    ingest_status: str | None = None
    deduplicated: bool | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    error: dict | None = None


def validate_scope_and_key(scope: tuple[str, str, str, str], key: str | None) -> tuple[str, str, str, str]:
    if not all(isinstance(v, str) for v in scope):
        raise BridgeError("invalid_request")
    try:
        scope = normalize_scope(*scope)
    except StorageError:
        raise BridgeError("invalid_request") from None
    if key is not None and (not isinstance(key, str) or not key
            or len(key.encode("utf-8", errors="surrogatepass")) > 128
            or any(ord(c) < 32 or ord(c) == 127 for c in key)):
        raise BridgeError("invalid_request")
    return scope


def ingest_validated(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                     scope: tuple[str, str, str, str], image: ValidatedImage,
                     client_request_id: str | None, stage_hash: str | None = None) -> IngestResult:
    if config.managed_root is None or config.managed_folder_id is None:
        raise BridgeError("invalid_request")
    if client_request_id is not None:
        try:
            repo.check_image_request(scope=scope, request_id=client_request_id, digest=image.sha256,
                provider_instance_id=config.provider_instance_id, folder_id=config.managed_folder_id,
                stage_hash=stage_hash)
        except RequestConflict:
            raise BridgeError("invalid_request") from None
        except StorageError:
            raise BridgeError("storage_error") from None
    folder = next((f for f in provider.folders() if f.get("id") == config.managed_folder_id), None)
    if folder is None or not folder.get("watch") or not folder.get("path"):
        raise BridgeError("invalid_request")
    try:
        if not os.path.samefile(config.managed_root, folder["path"]):
            raise BridgeError("invalid_request")
    except (OSError, TypeError, ValueError):
        raise BridgeError("invalid_request") from None
    rel = _managed_relpath(image.sha256, image.media_type)
    publish(config.managed_root, rel, image.data)
    try:
        record = repo.create_owned_image(scope=scope, asset_id=str(uuid.uuid4()),
            digest=image.sha256, relative_path=rel, media_type=image.media_type,
            size_bytes=len(image.data), provider_instance_id=config.provider_instance_id,
            folder_id=config.managed_folder_id, request_id=client_request_id, stage_hash=stage_hash)
    except RequestConflict:
        raise BridgeError("invalid_request") from None
    except StorageError:
        raise BridgeError("storage_error") from None
    return IngestResult(ok=True, asset_id=record["asset_id"], ingest_status=record["status"],
                        deduplicated=record["deduplicated"], media_type=image.media_type,
                        size_bytes=len(image.data))


def ingest_image(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                 agent_id: str, image_base64: str, source_instance_id: str = "",
                 project_id: str = "", channel: str = "",
                 client_request_id: str | None = None) -> IngestResult:
    try:
        scope = validate_scope_and_key((source_instance_id, agent_id, project_id, channel), client_request_id)
        if config.managed_root is None or config.managed_folder_id is None:
            raise BridgeError("invalid_request")
        image = decode_image_base64(image_base64, max_bytes=config.max_fetch_bytes,
                                    max_pixels=config.max_pixels)
        return ingest_validated(provider=provider, repo=repo, config=config, scope=scope,
                                image=image, client_request_id=client_request_id)
    except BridgeError as exc:
        return IngestResult(ok=False, error=error_result(exc.code)["error"])
