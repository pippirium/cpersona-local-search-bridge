"""Explicit, bounded input of unindexed images (IMG-02)."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.image_input import decode_image_base64
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


def ingest_image(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                 agent_id: str, image_base64: str, source_instance_id: str = "",
                 project_id: str = "", channel: str = "",
                 client_request_id: str | None = None) -> IngestResult:
    try:
        if not all(isinstance(v, str) for v in (source_instance_id, agent_id, project_id, channel)):
            raise BridgeError("invalid_request")
        try:
            scope = normalize_scope(source_instance_id, agent_id, project_id, channel)
        except StorageError:
            raise BridgeError("invalid_request") from None
        if client_request_id is not None:
            if (not isinstance(client_request_id, str) or not client_request_id
                    or len(client_request_id.encode("utf-8", errors="surrogatepass")) > 128
                    or any(ord(c) < 32 or ord(c) == 127 for c in client_request_id)):
                raise BridgeError("invalid_request")
        if config.managed_root is None or config.managed_folder_id is None:
            raise BridgeError("invalid_request")
        # Do not decode unbounded client data or touch the filesystem until inputs pass.
        image = decode_image_base64(image_base64, max_bytes=config.max_fetch_bytes,
                                    max_pixels=config.max_pixels)
        if client_request_id is not None:
            try:
                repo.check_image_request(scope=scope, request_id=client_request_id, digest=image.sha256)
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
                folder_id=config.managed_folder_id, request_id=client_request_id)
        except RequestConflict:
            raise BridgeError("invalid_request") from None
        except StorageError:
            raise BridgeError("storage_error") from None
        return IngestResult(ok=True, asset_id=record["asset_id"], ingest_status=record["status"],
                            deduplicated=record["deduplicated"], media_type=image.media_type,
                            size_bytes=len(image.data))
    except BridgeError as exc:
        return IngestResult(ok=False, error=error_result(exc.code)["error"])
