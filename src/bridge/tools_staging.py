"""Explicit import of a scoped, previously staged image (IMG-03)."""
from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.image_input import validate_image
from bridge.managed_store import read_verified
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError
from bridge.staging import stage_relpath, token_hash
from bridge.tools_ingest import IngestResult, ingest_validated, validate_scope_and_key


def import_staged_image(*, provider: ProviderPort, repo: Repository, config: BridgeConfig,
                        agent_id: str, staging_id: str, source_instance_id: str = "",
                        project_id: str = "", channel: str = "",
                        client_request_id: str | None = None) -> IngestResult:
    try:
        scope = validate_scope_and_key((source_instance_id, agent_id, project_id, channel), client_request_id)
        digest = token_hash(staging_id)
        if config.staging_root is None:
            raise BridgeError("invalid_request")
        try:
            entry = repo.staged_image(scope=scope, token_hash=digest)
        except StorageError:
            raise BridgeError("storage_error") from None
        if not entry:
            raise BridgeError("invalid_request")
        data = read_verified(config.staging_root, stage_relpath(digest), entry["size_bytes"],
                             entry["digest"], config.max_fetch_bytes,
                             (entry["stage_device"], entry["stage_inode"], entry["stage_mtime_ns"]))
        image = validate_image(data, max_bytes=config.max_fetch_bytes, max_pixels=config.max_pixels)
        if image.sha256 != entry["digest"] or image.media_type != entry["media_type"]:
            raise BridgeError("invalid_request")
        return ingest_validated(provider=provider, repo=repo, config=config, scope=scope, image=image,
                                client_request_id=client_request_id, stage_hash=digest)
    except BridgeError as exc:
        return IngestResult(ok=False, error=error_result(exc.code)["error"])
