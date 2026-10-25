"""Bridge MCP server (B-04): stdio host, startup validation, `bridge_health`.

M0 ships the host and health only; the asset tools arrive with B-07..B-09 and
register on this same `create_server`. No listener, no network transport:
stdio per docs/12 §1.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer

from bridge.config import BridgeConfig, load_config
from bridge.errors import BridgeError, error_result
from bridge.provider import FakeProvider, ProviderPort
from bridge.repository import Repository

BRIDGE_VERSION = "0.0.0"


@dataclass
class BridgeContext:
    config: BridgeConfig
    provider: ProviderPort
    db: Repository | None = None  # repository handle (B-05)


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


def _storage_health(ctx: BridgeContext) -> StorageHealth:
    """When a repository is attached, report its ACTUAL schema version from
    PRAGMA user_version; without one (pure test hosts) report the known value."""
    if ctx.db is None:
        return StorageHealth(writable=True, schema_version=1)
    version = ctx.db._conn.execute("PRAGMA user_version").fetchone()[0]
    return StorageHealth(writable=True, schema_version=int(version))


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
    return HealthResult(
        bridge_version=BRIDGE_VERSION,
        schema_version=1,
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
    from bridge.repository import Repository, open_repository

    conn = open_repository(config.data_dir)
    ctx = BridgeContext(
        config=config, provider=FakeProvider(), db=Repository(conn)
    )
    create_server(ctx).run(transport="stdio")
