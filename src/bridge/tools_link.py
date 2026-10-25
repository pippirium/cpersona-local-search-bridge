"""link_asset / unlink_asset tools (B-07, docs/04).

Rules enforced here (docs/08/12):
- provider file identity is verified BEFORE any write (kind must be `image`
  in M1; unknown file_id / unavailable asset / non-image kinds are typed
  errors);
- asset_id is a fresh UUIDv4 for a new binding, reused when the same
  (provider_instance, provider_file_id) is already bound (idempotence);
- the provider check happens before any DB write: a failure there means no
  link row exists afterwards (B-07 acceptance: no half-written links);
- responses never include image bytes or absolute paths (T-09);
- unlink removes the association row only — never the asset, the locator,
  the user's file, or anything in CPersona;
- StorageError (invalid id shapes) becomes invalid_request, never raw SQL
  text (T-13).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from bridge.errors import BridgeError, error_result
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError


@dataclass
class LinkResult:
    """Structured content for link_asset. Exactly one shape applies per call:
    success fills asset_id/link_status/media_kind and leaves error None;
    failure fills error={"code","message"} and leaves the rest None. A typed
    dataclass (not a plain dict) because the MCP SDK only emits
    structuredContent for annotated result types."""

    ok: bool
    asset_id: str | None = None
    link_status: str | None = None
    media_kind: str | None = None
    error: dict | None = None


@dataclass
class UnlinkResult:
    """Structured content for unlink_asset; same success/failure convention."""

    ok: bool
    unlinked: bool | None = None
    remaining_links: int | None = None
    error: dict | None = None


def _check_provider(provider: ProviderPort, provider_file_id: int) -> dict:
    detail = provider.file_detail(provider_file_id)
    if detail.get("kind") != "image":
        # M1 links images only; other kinds would silently link the wrong
        # media to a memory (docs/04 rules).
        raise BridgeError("unsupported_media")
    return detail


def link_asset(
    *,
    provider: ProviderPort,
    repo: Repository,
    provider_instance_id: str,
    source_instance_id: str,
    agent_id: str,
    project_id: str,
    channel: str,
    memory_ref: str,
    provider_file_id: int | str,
    provider_generation: str | None = None,
) -> LinkResult:
    try:
        # docs/04: positive integer. Non-numeric / non-positive is rejected
        # before anything is read or written.
        try:
            provider_file_id = int(provider_file_id)
        except (TypeError, ValueError):
            raise BridgeError("invalid_request") from None
        if provider_file_id <= 0:
            raise BridgeError("invalid_request")
        try:
            detail = _check_provider(provider, provider_file_id)
        except StorageError:
            raise BridgeError("invalid_request") from None
        # Reuse an existing binding for this provider file (idempotence across
        # different memories AND repeats of the same call).
        existing = repo.locators_by_file(provider_instance_id, provider_file_id)
        if existing:
            asset_id = existing[0]
            asset = repo.asset(asset_id, provider_instance_id)
            if asset is None:  # pragma: no cover - locator row without asset
                raise BridgeError("storage_error")
            media_kind = asset["media_kind"]
            if media_kind != "image":  # defensive: schema allows video/text
                raise BridgeError("unsupported_media")
        else:
            asset_id = str(uuid.uuid4())
            media_kind = "image"
            # media_type comes from the provider-served name's extension only
            # — never from a path, and only recognized image extensions
            # (docs/12 §3). Unknown extensions stay None (unknown, not wrong).
            ext = str(detail.get("name", "")).rsplit(".", 1)[-1].lower()
            media_type = {
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
                "webp": "image/webp",
                "gif": "image/gif",
            }.get(ext)
            status = "ready" if detail.get("available", True) else "missing"
            try:
                repo.upsert_asset_with_locator(
                    asset_id=asset_id,
                    origin="external",
                    media_kind=media_kind,
                    media_type=media_type,
                    size_bytes=detail.get("size"),
                    provider_instance_id=provider_instance_id,
                    provider_file_id=provider_file_id,
                    provider_generation=provider_generation,
                    provider_folder_id=detail.get("folder_id"),
                    last_seen_kind=detail.get("kind"),
                    last_seen_name=detail.get("name"),
                    last_seen_mtime=detail.get("mtime"),
                    last_seen_size=detail.get("size"),
                    status=status,
                )
            except StorageError:
                # locator/file-id uniqueness lost to a concurrent bind: the
                # fresh uuid asset rolled back with it; reuse is impossible
                # here, so report the provider-side conflict truthfully.
                raise BridgeError("asset_unavailable") from None
        # Link write AFTER the provider check succeeded: any provider failure
        # above means this line never ran (no half-written link).
        try:
            repo.link(
                source_instance_id=source_instance_id,
                agent_id=agent_id,
                project_id=project_id,
                channel=channel,
                memory_ref=memory_ref,
                asset_id=asset_id,
            )
        except StorageError:
            raise BridgeError("invalid_request") from None
        asset = repo.asset(asset_id, provider_instance_id)
        status = (asset or {}).get("locator_status") or "pending"
        return LinkResult(ok=True, asset_id=asset_id, link_status=status,
                          media_kind=media_kind)
    except BridgeError as exc:
        return LinkResult(ok=False, error=error_result(exc.code)["error"])


def unlink_asset(
    *,
    repo: Repository,
    source_instance_id: str,
    agent_id: str,
    project_id: str,
    channel: str,
    memory_ref: str,
    asset_id: str,
) -> UnlinkResult:
    """docs/04: unlinked + remaining count within the SAME exact scope.
    Removes only the association row; the asset and locator survive for any
    other link."""
    try:
        try:
            remaining = repo.unlink(
                source_instance_id=source_instance_id,
                agent_id=agent_id,
                project_id=project_id,
                channel=channel,
                memory_ref=memory_ref,
                asset_id=asset_id,
            )
        except StorageError:
            raise BridgeError("invalid_request") from None
        return UnlinkResult(ok=True, unlinked=True, remaining_links=remaining)
    except BridgeError as exc:
        return UnlinkResult(ok=False, error=error_result(exc.code)["error"])
