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
    assert h.schema_version == 4


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
        assert names == {"bridge_health", "link_asset", "unlink_asset",
                    "search_assets", "list_memory_assets", "get_asset",
                    "register_asset", "resolve_pending_assets", "audit_assets", "ingest_image", "get_ingest_status", "import_staged_image", "discover_assets", "search_assets_by_image", "link_registered_asset", "set_asset_metadata", "get_asset_metadata"}
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
        assert payload["storage"]["schema_version"] == 4


@pytest.mark.anyio
async def test_ingest_image_wire_success_and_scope_bound(tmp_path):
    import base64
    import io
    from PIL import Image
    from bridge.repository import Repository, open_repository

    root = tmp_path / "managed"
    cfg = validate_config(data_dir=tmp_path / "db", provider_base_url="http://127.0.0.1:18765",
                          managed_root=root, managed_folder_id=1)
    class Provider(FakeProvider):
        def folders(self):
            return [{"id": 1, "watch": True, "path": str(root)}]
    repo = Repository(open_repository(tmp_path / "db"))
    server = create_server(BridgeContext(config=cfg, provider=Provider(), db=repo))
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, "PNG")
    async with connected_client(server) as session:
        response = await session.call_tool("ingest_image", {"agent_id": "a",
            "image_base64": base64.b64encode(buf.getvalue()).decode()})
        payload = response.structured_content
        assert payload["ok"] and payload["ingest_status"] == "pending"
        assert repo.owned_asset(scope=("", "a", "", ""), asset_id=payload["asset_id"])
        from jsonschema import Draft202012Validator
        import json
        from pathlib import Path
        spec = json.loads((Path(__file__).resolve().parents[2] / "fixtures/contracts/image-input/requests.json").read_text())
        validator = Draft202012Validator(spec["responses"]["ingest_image"])
        validator.validate(payload)
        error = await session.call_tool("ingest_image", {"agent_id": "a", "image_base64": "!bad"})
        validator.validate(error.structured_content)
        assert error.structured_content["error"]["code"] == "invalid_request"
    repo.close()


@pytest.mark.anyio
async def test_get_ingest_status_wire_contract(tmp_path):
    import json
    from jsonschema import Draft202012Validator
    from pathlib import Path
    from tests.unit.test_tools_ingest import setup, call
    cfg, provider, repo = setup(tmp_path)
    image = call(cfg, provider, repo)
    server = create_server(BridgeContext(config=cfg, provider=provider, db=repo))
    spec = json.loads((Path(__file__).resolve().parents[2] /
                       "fixtures/contracts/image-input/requests.json").read_text())
    validator = Draft202012Validator(spec["responses"]["get_ingest_status"])
    async with connected_client(server) as session:
        good = (await session.call_tool("get_ingest_status", {"agent_id": "a", "asset_id": image.asset_id})).structured_content
        validator.validate(good)
        assert good["status"] == "pending" and not good["indexed"]
        bad = (await session.call_tool("get_ingest_status", {"agent_id": "b", "asset_id": image.asset_id})).structured_content
        validator.validate(bad)
        assert bad["error"]["code"] == "asset_not_found" and bad["asset_id"] is None
    repo.close()


@pytest.mark.anyio
async def test_staged_import_wire_contract(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from tests.unit.test_staging import setup, stage
    cfg, provider, repo, original = setup(tmp_path)
    token = stage(cfg, repo)
    server = create_server(BridgeContext(config=cfg, provider=provider, db=repo))
    spec = json.loads((Path(__file__).resolve().parents[2] /
                       "fixtures/contracts/image-input/requests.json").read_text())
    validator = Draft202012Validator(spec["responses"]["import_staged_image"])
    async with connected_client(server) as session:
        args = {"agent_id": "agent", "staging_id": token}
        result = (await session.call_tool("import_staged_image", args)).structured_content
        validator.validate(result)
        assert result["ok"] and result["ingest_status"] == "pending"
        denied = (await session.call_tool("import_staged_image", {**args, "agent_id": "other"})).structured_content
        validator.validate(denied)
        assert denied["error"]["code"] == "invalid_request" and denied["asset_id"] is None
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    repo.close()


@pytest.mark.anyio
async def test_discovery_wire_is_schema_valid_and_never_contains_image_bytes(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from tests.unit.test_tools_discover import setup
    cfg, provider, repo, state = setup(tmp_path)
    server = create_server(BridgeContext(config=cfg, provider=provider, db=repo, discovery=state))
    spec = json.loads((Path(__file__).resolve().parents[2] /
                       "fixtures/contracts/image-input/requests.json").read_text())
    validator = Draft202012Validator(spec["responses"]["discover_assets"])
    async with connected_client(server) as session:
        good = await session.call_tool("discover_assets", {"agent_id": "a", "query": "synthetic", "folder_id": 1})
        validator.validate(good.structured_content)
        assert good.structured_content["candidates"]
        assert all(content.type != "image" for content in good.content)
        denied = await session.call_tool("discover_assets", {"agent_id": "a", "query": "synthetic", "folder_id": 999})
        validator.validate(denied.structured_content)
        assert denied.structured_content["error"]["code"] == "asset_not_found"
    repo.close()


@pytest.mark.anyio
async def test_image_search_wire_schema_and_no_image_response(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from tests.unit.test_tools_ingest import setup
    from tests.unit.test_tools_image_search import make_linked, encoded
    cfg, provider, repo = setup(tmp_path)
    make_linked(cfg, provider, repo)
    server = create_server(BridgeContext(config=cfg, provider=provider, db=repo))
    spec = json.loads((Path(__file__).resolve().parents[2] /
                       "fixtures/contracts/image-input/requests.json").read_text())
    validator = Draft202012Validator(spec["responses"]["search_assets_by_image"])
    async with connected_client(server) as session:
        result = await session.call_tool("search_assets_by_image", {"agent_id": "a", "image_base64": encoded()})
        validator.validate(result.structured_content)
        assert result.structured_content["ok"] and len(result.structured_content["results"]) == 1
        assert all(item.type != "image" for item in result.content)
        bad = await session.call_tool("search_assets_by_image", {"agent_id": "a"})
        validator.validate(bad.structured_content)
        assert bad.structured_content["error"]["code"] == "invalid_request"
    repo.close()


@pytest.mark.anyio
async def test_registered_link_wire_schema_and_no_image_response(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from tests.unit.test_tools_ingest import setup, call
    cfg, provider, repo = setup(tmp_path)
    asset = call(cfg, provider, repo)
    server = create_server(BridgeContext(config=cfg, provider=provider, db=repo))
    spec = json.loads((Path(__file__).resolve().parents[2] /
                       "fixtures/contracts/image-input/requests.json").read_text())
    validator = Draft202012Validator(spec["responses"]["link_registered_asset"])
    async with connected_client(server) as session:
        result = await session.call_tool("link_registered_asset", {"agent_id": "a", "asset_id": asset.asset_id, "memory_ref": "mem:1"})
        validator.validate(result.structured_content)
        assert result.structured_content["ok"] and result.structured_content["link_status"] == "pending"
        assert all(item.type != "image" for item in result.content)
        denied = await session.call_tool("link_registered_asset", {"agent_id": "b", "asset_id": asset.asset_id, "memory_ref": "mem:1"})
        validator.validate(denied.structured_content)
        assert denied.structured_content["error"]["code"] == "asset_not_found"
    repo.close()
