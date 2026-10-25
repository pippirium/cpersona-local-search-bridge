"""CP-01 (docs/14) operational sample as an executable contract.

The CPersona half of the workflow runs against the user's live server and
is exercised manually (the Agent transcript in docs/14 is synthetic). What
CAN be tested without CPersona is the Bridge half end-to-end through the
MCP surface — that is what runs here. It freezes the exact argument shapes
the documented transcript uses, so a tool-signature change breaks this
test and forces a docs/14 update (docs/06 CP-01: contract tests + 運用サンプル).
"""

from __future__ import annotations

import pytest

from bridge.config import validate_config
from bridge.provider import FakeFile, FakeProvider
from bridge.repository import Repository, open_repository
from bridge.server import BridgeContext, create_server
from tests.unit.test_server_health import connected_client

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
       + (120).to_bytes(4, "big") + (80).to_bytes(4, "big") + b"fake" * 10)


@pytest.mark.anyio
def test_workflow_a_then_b_wire_shape(tmp_path):
    """Workflow A (search -> link -> list) and B (explicit get), exactly the
    argument shapes of the docs/14 transcript."""
    import anyio

    config = validate_config(data_dir=tmp_path / "bdata",
                             provider_base_url="http://127.0.0.1:18765")
    repo = Repository(open_repository(tmp_path / "bdata"))
    prov = FakeProvider(files={218: FakeFile(file_id=218, name="IMG_0042.jpg",
                                             content=PNG, size=len(PNG),
                                             mime="image/jpeg")})
    ctx = BridgeContext(config=config, provider=prov, db=repo)

    async def go():
        async with connected_client(create_server(ctx)) as session:
            # A-2 file discovery: search_assets answers in-SCOPE assets
            # (T-02: an unlinked hit is invisible). First contact with a new
            # file id is therefore link_asset with the id the user/agent
            # pointed at (via CPersona text, a folder listing, or a prior
            # search run under a scope that could see it).
            res = await session.call_tool(
                "search_assets",
                {"agent_id": "coding", "query": "sushi"})
            sc = res.structured_content
            assert sc["ok"] is True and sc["results"] == []  # unlinked: invisible

            # A-3 link_asset(agent_id, memory_ref, provider_file_id, ...)
            res = await session.call_tool(
                "link_asset",
                {"agent_id": "coding", "memory_ref": "mem:41",
                 "provider_file_id": 218})
            sc = res.structured_content
            assert sc["ok"] is True and sc["link_status"] == "ready", sc
            asset_id = sc["asset_id"]
            # once linked, search_assets returns it WITH memory refs
            res = await session.call_tool(
                "search_assets",
                {"agent_id": "coding", "query": "sushi"})
            sc = res.structured_content
            assert [a["asset_id"] for a in sc["results"]] == [asset_id]
            assert sc["results"][0]["memory_refs"] == ["mem:41"]
            # blessed body-token format: [asset:<uuid4>]
            import re

            assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
                                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}", asset_id)

            # B: list_memory_assets is the source of truth (body token is a
            # hint only)
            res = await session.call_tool(
                "list_memory_assets",
                {"agent_id": "coding", "memory_ref": "mem:41"})
            sc = res.structured_content
            assert [a["asset_id"] for a in sc["assets"]] == [asset_id]

            # B: get_asset only on explicit request; bytes appear nowhere
            # before this call (file_content call count was 0 in A)
            assert [c for c in prov.calls if c[0] == "file_content"] == []
            res = await session.call_tool(
                "get_asset",
                {"agent_id": "coding", "asset_id": asset_id})
            blocks = res.content
            assert blocks and blocks[0].type == "image"
            assert [c for c in prov.calls if c[0] == "file_content"]

    anyio.run(go)
    repo.close()
