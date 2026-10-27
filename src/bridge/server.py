"""Bridge MCP server (B-04): stdio host, startup validation, `bridge_health`.

M0 ships the host and health only; the asset tools arrive with B-07..B-09 and
register on this same `create_server`. No listener, no network transport:
stdio per docs/12 §1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os

from mcp.server.mcpserver import MCPServer

from bridge.config import BridgeConfig, load_config
from bridge.errors import BridgeError, error_result
from bridge.provider import FakeProvider, ProviderPort
from bridge.repository import Repository, latest_known_version
from bridge.tools_link import LinkResult, UnlinkResult
from bridge.tools_get import GetAssetResult, get_asset as _get_impl
from bridge.tools_import import RegisterAssetResult, register_asset as _register_impl
from bridge.tools_ingest import IngestResult, ingest_image as _ingest_impl
from bridge.tools_status import IngestStatusResult, get_ingest_status as _status_impl
from bridge.tools_staging import import_staged_image as _staging_impl
from bridge.tools_discover import DiscoveryResult, DiscoveryState, discover_assets as _discover_impl
from bridge.tools_image_search import ImageSearchResult, search_assets_by_image as _image_search_impl
from bridge.audit import AuditReport, audit as _audit_impl
from bridge.audit import reconcile as _reconcile_impl
from bridge.reconcile import ResolveReport, resolve_pending as _resolve_impl
from bridge.tools_search import (
    ListMemoryAssetsResult,
    SearchAssetsResult,
    list_memory_assets as _list_impl,
    search_assets as _search_impl,
)

BRIDGE_VERSION = "0.0.0"


@dataclass
class BridgeContext:
    config: BridgeConfig
    provider: ProviderPort
    db: Repository | None = None  # repository handle (B-05)
    discovery: DiscoveryState = field(default_factory=DiscoveryState)


@dataclass
class ProviderHealth:
    reachable: bool
    provider_version: str | None
    generation: int | str | None
    model_state: str | None
    index_mismatch: str | None
    checked: bool
    fault: str | None = None


@dataclass
class StorageHealth:
    writable: bool
    schema_version: int


@dataclass
class HealthResult:
    """Structured output schema for bridge_health (MCP structuredContent)."""

    bridge_version: str
    schema_version: int
    provider_instance_id: str
    provider: ProviderHealth
    storage: StorageHealth
    warnings: list[str]
    # B-10/B-11 managed-import readiness (B-11 consumes these):
    # registered = the configured folder id exists in the provider ledger;
    # watch = that folder has the watcher on (a copy that is never scanned
    # stays pending forever). None = not probed / feature off.
    managed_enabled: bool = False
    managed_folder_registered: bool | None = None
    managed_watch: bool | None = None
    managed_root_matches: bool | None = None


def _storage_health(ctx: BridgeContext) -> StorageHealth:
    """When a repository is attached, report its ACTUAL schema version from
    PRAGMA user_version; without one (pure test hosts) report the known value."""
    if ctx.db is None:
        return StorageHealth(writable=True, schema_version=latest_known_version())
    return StorageHealth(writable=True, schema_version=ctx.db.schema_version())


def bridge_health(ctx: BridgeContext) -> HealthResult:
    """Aggregated readiness, never a raw provider passthrough. `checked` is
    true when the provider was consulted during this call."""
    provider = ProviderHealth(
        reachable=False, provider_version=None, generation=None,
        model_state=None, index_mismatch=None, checked=False,
    )
    warnings: list[str] = []
    try:
        st = ctx.provider.status()
        provider = ProviderHealth(
            reachable=True,
            provider_version=st.get("version"),
            generation=(st.get("index") or {}).get("generation"),
            model_state=(st.get("model") or {}).get("state"),
            index_mismatch=(st.get("index") or {}).get("mismatch"),
            checked=True,
        )
        if provider.index_mismatch:
            warnings.append("index_mismatch")
        if provider.model_state not in ("ready", "idle"):
            warnings.append("model_not_ready")
    except BridgeError as exc:
        provider.fault = exc.code  # Bridge taxonomy code only (T-09)
        warnings.append("provider_unreachable")
    managed_enabled = ctx.config.managed_folder_id is not None
    managed_registered: bool | None = None
    managed_watch: bool | None = None
    managed_root_matches: bool | None = None
    if managed_enabled:
        try:
            folders = ctx.provider.folders()
            match = next(
                (f for f in folders
                 if f.get("id") == ctx.config.managed_folder_id),
                None,
            )
            managed_registered = match is not None
            managed_watch = bool(match.get("watch")) if match else None
            if match is not None:
                try:
                    managed_root_matches = bool(match.get("path")) and os.path.samefile(
                        ctx.config.managed_root, match["path"])
                except OSError:
                    managed_root_matches = False
                if not managed_root_matches:
                    warnings.append("managed_folder_mismatch")
            if match is None:
                warnings.append("managed_folder_unregistered")
            elif not managed_watch:
                warnings.append("managed_folder_unwatched")
        except BridgeError as exc:
            provider.fault = provider.fault or exc.code
            warnings.append("provider_unreachable")

    return HealthResult(
        bridge_version=BRIDGE_VERSION,
        managed_enabled=managed_enabled,
        managed_folder_registered=managed_registered,
        managed_watch=managed_watch,
        managed_root_matches=managed_root_matches,
        schema_version=_storage_health(ctx).schema_version,
        provider_instance_id=ctx.config.provider_instance_id,
        provider=provider,
        storage=_storage_health(ctx),
        warnings=warnings,
    )


def create_server(ctx: BridgeContext) -> MCPServer:
    server = MCPServer(
        name="cpersona-local-search-bridge",
        instructions=(
            "Associates CPersona memories with local-search indexed assets. "
            "Scope arguments must repeat exactly across calls (exact match, "
            "no normalization). Image bytes are returned only by an explicit "
            "asset fetch."
        ),
    )

    @server.tool(
        name="bridge_health",
        description=(
            "Report Bridge, storage and provider readiness. Call before other "
            "tools after startup; warnings list readiness problems using codes."
        ),
    )
    def _bridge_health() -> HealthResult:
        return bridge_health(ctx)

    @server.tool(
        name="ingest_image",
        description="Explicitly register bounded image data in the configured managed folder. "
                    "No memory association is created; indexing occurs asynchronously.",
    )
    def _ingest_image(agent_id: str, image_base64: str, project_id: str = "",
                      channel: str = "", source_instance_id: str = "",
                      client_request_id: str | None = None) -> IngestResult:
        if ctx.db is None:
            return IngestResult(ok=False, error=error_result("storage_error")["error"])
        return _ingest_impl(provider=ctx.provider, repo=ctx.db, config=ctx.config,
                            agent_id=agent_id, image_base64=image_base64, project_id=project_id,
                            channel=channel, source_instance_id=source_instance_id,
                            client_request_id=client_request_id)

    @server.tool(name="get_ingest_status", description="Read the current scope's indexing state without resolving or modifying assets.")
    def _get_ingest_status(agent_id: str, asset_id: str, project_id: str = "",
                           channel: str = "", source_instance_id: str = "") -> IngestStatusResult:
        if ctx.db is None:
            return IngestStatusResult(ok=False, error=error_result("storage_error")["error"])
        return _status_impl(provider=ctx.provider, repo=ctx.db, config=ctx.config, asset_id=asset_id,
                            agent_id=agent_id, project_id=project_id, channel=channel,
                            source_instance_id=source_instance_id)

    @server.tool(name="import_staged_image", description="Import an explicitly staged image by opaque ID, never by path.")
    def _import_staged_image(agent_id: str, staging_id: str, project_id: str = "", channel: str = "",
                             source_instance_id: str = "", client_request_id: str | None = None) -> IngestResult:
        if ctx.db is None:
            return IngestResult(ok=False, error=error_result("storage_error")["error"])
        return _staging_impl(provider=ctx.provider, repo=ctx.db, config=ctx.config, agent_id=agent_id,
                             staging_id=staging_id, project_id=project_id, channel=channel,
                             source_instance_id=source_instance_id, client_request_id=client_request_id)

    @server.tool(name="discover_assets", description="Discover image candidates only in operator-allowed folders; no automatic links or image bytes.")
    def _discover_assets(agent_id: str, query: str, project_id: str = "", channel: str = "",
                         source_instance_id: str = "", folder_id: int | None = None,
                         limit: int = 10, cursor: str | None = None) -> DiscoveryResult:
        if ctx.db is None:
            return DiscoveryResult(ok=False, error=error_result("storage_error")["error"])
        return _discover_impl(provider=ctx.provider, repo=ctx.db, config=ctx.config, state=ctx.discovery,
            agent_id=agent_id, query=query, project_id=project_id, channel=channel,
            source_instance_id=source_instance_id, folder_id=folder_id, limit=limit, cursor=cursor)

    @server.tool(name="search_assets_by_image", description="Search linked images using an explicit image or owned/linked query asset.")
    def _search_assets_by_image(agent_id: str, query_asset_id: str | None = None, image_base64: str | None = None,
                                project_id: str = "", channel: str = "", source_instance_id: str = "",
                                folder_id: int | None = None, limit: int = 10) -> ImageSearchResult:
        if ctx.db is None:
            return ImageSearchResult(ok=False, error=error_result("storage_error")["error"])
        return _image_search_impl(provider=ctx.provider, repo=ctx.db, config=ctx.config, agent_id=agent_id,
            query_asset_id=query_asset_id, image_base64=image_base64, project_id=project_id,
            channel=channel, source_instance_id=source_instance_id, folder_id=folder_id, limit=limit)

    # ---- link tools (B-07) ----------------------------------------------------

    from bridge.tools_link import link_asset as _link_impl
    from bridge.tools_link import unlink_asset as _unlink_impl

    @server.tool(
        name="link_asset",
        description=(
            "Associate a local-search image file with one memory reference. "
            "Verifies the file with the provider before writing; re-linking "
            "the same file is idempotent. Returns a stable asset_id; never "
            "returns image bytes. Scope arguments must repeat exactly "
            "(exact match; a different scope sees nothing)."
        ),
    )
    def _link_asset(
        agent_id: str,
        memory_ref: str,
        provider_file_id: int,
        project_id: str = "",
        channel: str = "",
        source_instance_id: str = "",
    ) -> LinkResult:
        if ctx.db is None:  # pragma: no cover - hosts always attach a repo
            return LinkResult(ok=False, error=error_result("storage_error")["error"])
        return _link_impl(
            provider=ctx.provider,
            repo=ctx.db,
            provider_instance_id=ctx.config.provider_instance_id,
            source_instance_id=source_instance_id,
            agent_id=agent_id,
            project_id=project_id,
            channel=channel,
            memory_ref=memory_ref,
            provider_file_id=provider_file_id,
        )

    @server.tool(
        name="unlink_asset",
        description=(
            "Remove one memory-to-asset association within the given scope. "
            "Never deletes the local-search file or any CPersona memory."
        ),
    )
    def _unlink_asset(
        agent_id: str,
        memory_ref: str,
        asset_id: str,
        project_id: str = "",
        channel: str = "",
        source_instance_id: str = "",
    ) -> UnlinkResult:
        if ctx.db is None:  # pragma: no cover
            return UnlinkResult(ok=False, error=error_result("storage_error")["error"])
        return _unlink_impl(
            repo=ctx.db,
            source_instance_id=source_instance_id,
            agent_id=agent_id,
            project_id=project_id,
            channel=channel,
            memory_ref=memory_ref,
            asset_id=asset_id,
        )

    # ---- search tools (B-08) --------------------------------------------------

    @server.tool(
        name="search_assets",
        description=(
            "Text-search images THROUGH the provider, then intersect with the "
            "assets linked in the caller's exact scope. Unlinked library "
            "files never appear. search_coverage says whether the answer is "
            "complete ('exact') or a top-N sample ('partial') — never treat "
            "partial as exhaustive. Returns metadata only, no image bytes."
        ),
    )
    def _search_assets(
        agent_id: str,
        query: str,
        project_id: str = "",
        channel: str = "",
        source_instance_id: str = "",
        limit: int = 10,
        folder_id: int | None = None,
    ) -> SearchAssetsResult:
        if ctx.db is None:  # pragma: no cover
            return SearchAssetsResult(
                ok=False, query=query, results=[], search_coverage=None,
                provider_warning=None,
                error=error_result("storage_error")["error"],
            )
        return _search_impl(
            provider=ctx.provider, repo=ctx.db,
            provider_instance_id=ctx.config.provider_instance_id,
            source_instance_id=source_instance_id, agent_id=agent_id,
            project_id=project_id, channel=channel,
            query=query, limit=limit, folder_id=folder_id,
        )

    @server.tool(
        name="list_memory_assets",
        description=(
            "List assets linked to one memory reference in the caller's "
            "exact scope, newest link first. Metadata only (kind, label, "
            "status, asset_id); call get_asset explicitly for image bytes."
        ),
    )
    def _list_memory_assets(
        agent_id: str,
        memory_ref: str,
        project_id: str = "",
        channel: str = "",
        source_instance_id: str = "",
        limit: int = 20,
    ) -> ListMemoryAssetsResult:
        if ctx.db is None:  # pragma: no cover
            return ListMemoryAssetsResult(
                ok=False, memory_ref=memory_ref, assets=[],
                error=error_result("storage_error")["error"],
            )
        return _list_impl(
            repo=ctx.db,
            provider_instance_id=ctx.config.provider_instance_id,
            source_instance_id=source_instance_id, agent_id=agent_id,
            project_id=project_id, channel=channel,
            memory_ref=memory_ref, limit=limit,
        )

    # ---- get_asset (B-09): the only media path --------------------------------

    @server.tool(
        name="get_asset",
        description=(
            "Fetch image bytes for ONE linked asset, only when the user "
            "explicitly asked for the image. Re-validates the provider file, "
            "enforces size/pixel/MIME bounds, and never substitutes another "
            "file. Never called automatically by search or listing."
        ),
    )
    def _get_asset(
        agent_id: str,
        asset_id: str,
        project_id: str = "",
        channel: str = "",
        source_instance_id: str = "",
        memory_ref: str | None = None,
    ) -> object:
        if ctx.db is None:  # pragma: no cover
            return GetAssetResult(
                ok=False, error=error_result("storage_error")["error"]
            )
        return _get_impl(
            provider=ctx.provider, repo=ctx.db,
            provider_instance_id=ctx.config.provider_instance_id,
            source_instance_id=source_instance_id, agent_id=agent_id,
            project_id=project_id, channel=channel,
            asset_id=asset_id, memory_ref=memory_ref,
        )

    # ---- register_asset (B-10): the only copy path ----------------------------

    @server.tool(
        name="register_asset",
        description=(
            "Copy ONE explicitly named indexed image into the Bridge-managed "
            "folder (opt-in feature) and link it to a memory. The original is "
            "never modified; the copy is content-addressed and never "
            "overwritten. Requires BRIDGE_MANAGED_ROOT configuration."
        ),
    )
    def _register_asset(
        agent_id: str,
        memory_ref: str,
        provider_file_id: int,
        project_id: str = "",
        channel: str = "",
        source_instance_id: str = "",
    ) -> RegisterAssetResult:
        if ctx.db is None:  # pragma: no cover
            return RegisterAssetResult(
                ok=False, error=error_result("storage_error")["error"]
            )
        return _register_impl(
            provider=ctx.provider, repo=ctx.db, config=ctx.config,
            source_instance_id=source_instance_id, agent_id=agent_id,
            project_id=project_id, channel=channel,
            memory_ref=memory_ref, provider_file_id=provider_file_id,
        )

    # ---- resolve_pending_assets (B-11): bounded, safe to poll --------------

    @server.tool(
        name="resolve_pending_assets",
        description=(
            "Resolve pending managed imports against the provider's folder "
            "inventory (watched-folder premise verified first). Ambiguous or "
            "unseen candidates stay pending. No scope: managed storage is a "
            "single-operator concern; this tool never returns paths."
        ),
    )
    def _resolve_pending_assets() -> ResolveReport:
        if ctx.db is None:  # pragma: no cover
            return ResolveReport(
                ok=False, premise_ready=False,
                error=error_result("storage_error")["error"],
            )
        return _resolve_impl(provider=ctx.provider, repo=ctx.db,
                             config=ctx.config)

    # ---- audit_assets (B-12): read-only drift & managed-copy audit ---------

    @server.tool(
        name="audit_assets",
        description=(
            "Read-only classification of locator drift against the provider "
            "and of the managed directory against the DB (orphans, leftover "
            "scratch, foreign files). Never writes, never deletes. apply=true "
            "additionally performs ONLY frozen-graph status transitions — it "
            "never rebinds provider_file_id and never removes links."
        ),
    )
    def _audit_assets(apply: bool = False) -> AuditReport:
        if ctx.db is None:  # pragma: no cover
            return AuditReport(
                ok=False,
                error=error_result("storage_error")["error"],
            )
        if apply:
            return _reconcile_impl(provider=ctx.provider, repo=ctx.db,
                                   config=ctx.config, apply=True)
        return _audit_impl(provider=ctx.provider, repo=ctx.db,
                           config=ctx.config)

    return server


def main() -> None:  # pragma: no cover - process entrypoint
    import os
    import sys

    from bridge.config import ConfigError

    try:
        config = load_config(dict(os.environ))
    except ConfigError as exc:
        # ConfigError messages are fixed wording by construction, but stdio
        # stdout must stay protocol-clean: write to stderr only.
        sys.stderr.write("bridge: configuration error: " + str(exc) + "\n")
        raise SystemExit(2)
    from bridge.client import LocalSearchClient
    from bridge.repository import Repository, latest_known_version, open_repository

    conn = open_repository(config.data_dir)
    provider = LocalSearchClient(config)
    ctx = BridgeContext(
        config=config, provider=provider, db=Repository(conn)
    )
    try:
        create_server(ctx).run(transport="stdio")
    finally:
        provider.close()

if __name__ == "__main__":
    main()  # pragma: no cover - process entrypoint
