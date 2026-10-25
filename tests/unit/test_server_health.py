"""B-04: bridge_health behavior and the MCP host over in-memory streams.

The stdio host is exercised with the SDK's low-level `Server.run` over memory
object streams — no listener, no sockets (docs/12 §1: stdio only). Failure
paths use the Fake provider's injected faults (docs/10 envelopes; docs/04
taxonomy).
"""

from __future__ import annotations

import anyio
import pytest
from contextlib import asynccontextmanager

from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams


@asynccontextmanager
async def connected_client(server):
    """Full client (initialize included) over in-memory streams — the stdio
    transport shape without touching real stdio."""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                server._lowlevel_server.run,
                server_read,
                server_write,
                server._lowlevel_server.create_initialization_options(),
                True,  # raise_exceptions: surface bugs in tests
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()

from bridge.config import validate_config
from bridge.errors import BridgeError
from bridge.provider import FakeFailure, FakeFile, FakeProvider, _retry_get
from bridge.server import BridgeContext, bridge_health, create_server


@pytest.fixture
def ctx(tmp_path):
    cfg = validate_config(data_dir=tmp_path / "bdata", provider_base_url="http://127.0.0.1:18765")
    provider = FakeProvider(files={1: FakeFile(file_id=1)})
    return BridgeContext(config=cfg, provider=provider)


# ---- bridge_health logic -------------------------------------------------------

def test_health_ok(ctx):
    h = bridge_health(ctx)
    p = h.provider
    assert (p.reachable, p.provider_version, p.generation, p.model_state,
            p.index_mismatch, p.checked) == (True, "0.1.1-fake", 1, "ready", None, True)
    assert h.warnings == []
    assert h.schema_version == 1


def test_health_provider_down(ctx):
    ctx.provider.failures.append(FakeFailure(route="status", code="connection"))
    h = bridge_health(ctx)
    assert h.provider.reachable is False
    assert h.provider.fault == "dependency_unavailable"
    assert h.warnings == ["provider_unreachable"]


def test_health_index_mismatch(ctx):
    ctx.provider.failures.append(FakeFailure(route="status", code="index_mismatch"))
    h = bridge_health(ctx)
    assert h.provider.fault == "index_incompatible"
    assert h.warnings == ["provider_unreachable"]


def test_health_model_loading(ctx):
    ctx.provider.failures.append(FakeFailure(route="status", code="model_loading"))
    h = bridge_health(ctx)
    assert h.provider.fault == "dependency_unavailable"


def test_health_model_not_ready_warns(ctx):
    ctx.provider.model_state = "loading"
    h = bridge_health(ctx)
    assert "model_not_ready" in h.warnings
    assert h.provider.reachable is True


# ---- retry policy through the port ---------------------------------------------

def test_search_never_retried_even_on_transport_fault():
    provider = FakeProvider()
    provider.failures.append(FakeFailure(route="search", code="connection"))
    with pytest.raises(BridgeError):
        provider.search("q", page_size=20)
    assert len([c for c in provider.calls if c[0] == "search"]) == 1


def test_detail_transport_fault_retried_once_then_succeeds():
    provider = FakeProvider(files={1: FakeFile(file_id=1)})
    fault = FakeFailure(route="detail", code="connection")
    provider.failures.append(fault)

    real_fail = provider._fail

    def flaky_fail(route):
        if route == "detail" and fault in provider.failures:
            provider.failures.remove(fault)  # transient: clears after first attempt
            raise BridgeError("dependency_unavailable", provider_code="connection")
        real_fail(route)

    provider._fail = flaky_fail
    detail = provider.file_detail(1)
    assert detail["file_id"] == 1
    assert len([c for c in provider.calls if c[0] == "file_detail"]) == 2  # one retry


def test_detail_http_fault_not_retried():
    provider = FakeProvider(files={1: FakeFile(file_id=1)})
    provider.failures.append(FakeFailure(route="detail", code="index_mismatch"))
    with pytest.raises(BridgeError) as ei:
        provider.file_detail(1)
    assert ei.value.code == "index_incompatible"
    assert len([c for c in provider.calls if c[0] == "file_detail"]) == 1


def test_retry_get_caps_at_one_retry():
    attempts = []

    def always_fail():
        attempts.append(1)
        raise BridgeError("dependency_unavailable", provider_code="connection")

    with pytest.raises(BridgeError):
        _retry_get(always_fail, retryable=True)
    assert len(attempts) == 2


# ---- MCP host over in-memory streams -------------------------------------------

@pytest.mark.anyio
async def test_host_lists_and_calls_health_tool(ctx):
    server = create_server(ctx)
    async with connected_client(server) as session:
        names = {t.name for t in (await session.list_tools()).tools}
        assert names == {"bridge_health"}
        result = await session.call_tool("bridge_health", {})
        payload = result.structured_content or {}
        assert payload["bridge_version"] == "0.0.0"
        assert payload["provider"]["checked"] is True


@pytest.mark.anyio
async def test_health_tool_survives_provider_fault_over_wire(ctx):
    ctx.provider.failures.append(FakeFailure(route="status", code="connection"))
    server = create_server(ctx)
    async with connected_client(server) as session:
        result = await session.call_tool("bridge_health", {})
        payload = result.structured_content or {}
        assert payload["provider"]["reachable"] is False
        assert payload["warnings"] == ["provider_unreachable"]


@pytest.mark.anyio
async def test_error_taxonomy_shapes_are_fixed_wording():
    from bridge.errors import ERRORS, error_result

    r = error_result("asset_not_found")
    assert r == {"error": {"code": "asset_not_found", "message": "No such asset in this scope."}}
    for code, spec in ERRORS.items():
        assert spec.code == code
        assert "\n" not in spec.message  # single-line fixed strings


@pytest.mark.anyio
async def test_health_reports_attached_repository_schema_version(tmp_path):
    from bridge.repository import Repository, open_repository

    cfg = validate_config(data_dir=tmp_path, provider_base_url="http://127.0.0.1:18765")
    ctx = BridgeContext(config=cfg, provider=FakeProvider(),
                        db=Repository(open_repository(tmp_path)))
    server = create_server(ctx)
    async with connected_client(server) as session:
        result = await session.call_tool("bridge_health", {})
        payload = result.structured_content or {}
        assert payload["storage"]["schema_version"] == 1
