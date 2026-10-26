"""B-11 acceptance: managed-folder premise verification, pending->ready
resolution under delay/ambiguity/disconnect, restart survival, differential
rescan. Nothing that does not exist at the provider ever becomes ready."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bridge.config import validate_config
from bridge.provider import FakeFile, FakeFailure, FakeProvider
from bridge.reconcile import revalidate, resolve_pending
from bridge.repository import Repository, open_repository
from bridge.tools_import import register_asset

P = "127.0.0.1:18765"  # validate_config default provider_instance_id = netloc
SCOPE = dict(source_instance_id="", agent_id="a1", project_id="p", channel="c")

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
       + (120).to_bytes(4, "big") + (80).to_bytes(4, "big") + b"fake" * 10)


def make_env(tmp_path):
    config = validate_config(
        data_dir=tmp_path / "bdata",
        provider_base_url="http://127.0.0.1:18765",
        managed_folder_id=77, managed_root=tmp_path / "managed")
    repo = Repository(open_repository(tmp_path / "bdata"))
    prov = FakeProvider(
        files={5: FakeFile(file_id=5, name="src.png", content=PNG,
                           size=len(PNG))},
        folders_data={77: {"watch": True}},
    )
    return repo, prov, config


@pytest.fixture
def env(tmp_path):
    repo, prov, config = make_env(tmp_path)
    yield repo, prov, config
    repo.close()


def imported(repo, prov, config):
    out = register_asset(provider=prov, repo=repo, config=config,
                         memory_ref="mem:1", provider_file_id=5, **SCOPE)
    assert out.ok
    return out.asset_id


def provider_sees_copy(prov, asset, fid=900):
    """Simulate the watcher: the copy gains a provider-visible file id in
    folder 77 with the copy's byte size."""
    prov.folder_files.setdefault(77, []).append(fid)
    prov.files[fid] = FakeFile(
        file_id=fid, kind="image",
        name=asset["relative_managed_path"].split("/")[-1],
        folder_id=77, rel_dir=asset["relative_managed_path"].rsplit("/", 1)[0],
        size=asset["size_bytes"],
        mtime="2026-10-26T08:00:00+09:00")


# ---- premise (監視されている前提の検証) -------------------------------------------------------

def test_premise_unregistered_leaves_pending(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    prov.folders_data = {}  # operator never registered the folder
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.premise_ready is False
    assert rep.bound == []
    assert repo.locator_status(aid, P) == "pending"
    assert any(w["reason"] == "premise_folder_unregistered"
               for w in rep.waiting)


def test_premise_unwatched_leaves_pending(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    prov.folders_data[77]["watch"] = False
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.premise_ready is False and rep.bound == []
    assert repo.locator_status(aid, P) == "pending"


# ---- delayed indexing (遅延索引) --------------------------------------------------------------

def test_not_indexed_yet_then_ready_after_scan(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.bound == []
    assert any(w["reason"] == "not_indexed_yet" for w in rep.waiting)
    assert repo.locator_status(aid, P) == "pending"

    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    rep2 = resolve_pending(provider=prov, repo=repo, config=config)
    assert [b["asset_id"] for b in rep2.bound] == [aid]
    assert repo.locator_status(aid, P) == "ready"
    row = repo.asset(aid, P)
    assert row["provider_file_id"] == 900
    assert row["last_seen_size"] == asset["size_bytes"]


def test_resolution_never_uses_search(env):
    """An indexed-but-not-yet-searchable file must still resolve: resolution
    uses folder inventory + detail, not search visibility."""
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    prov.invisible_in_search.add(900)
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert [b["asset_id"] for b in rep.bound] == [aid]
    assert [c for c in prov.calls if c[0] == "search"] == []


# ---- fail-closed ambiguity ---------------------------------------------------------------------

def test_ambiguous_identity_never_binds(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset, fid=900)
    # foreign file, same size, same folder: ambiguity -> refuse
    prov.folder_files[77].append(901)
    prov.files[901] = FakeFile(file_id=901, size=asset["size_bytes"],
                               folder_id=77, name=prov.files[900].name,
                               rel_dir=prov.files[900].rel_dir)
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.bound == []
    assert any(w["reason"] == "ambiguous_identity" for w in rep.waiting)
    assert repo.locator_status(aid, P) == "pending"


def test_detail_disagrees_with_listing_no_bind(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    original_detail = prov.file_detail
    def detail(fid):
        return dict(original_detail(fid), available=False)
    prov.file_detail = detail  # listing says present, detail says gone
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.bound == []
    assert any(w["reason"] == "detail_mismatch" for w in rep.waiting)


def test_foreign_folder_candidate_rejected(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    prov.files[900].folder_id = 42  # detail: not our managed folder
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.bound == []
    assert repo.locator_status(aid, P) == "pending"


# ---- disconnect / restart (切断・再起動) -----------------------------------------------------

def test_provider_disconnect_leaves_pending_and_no_degrade(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    prov.failures.append(FakeFailure("folders", "connection"))
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.premise_ready is False
    assert repo.locator_status(aid, P) == "pending"


def test_restart_rediscovers_from_db_only(tmp_path):
    """new process = new Repository over the same file: resolution completes
    purely from stored pendings (永続化検証)."""
    repo1, prov, config = make_env(tmp_path)
    aid = imported(repo1, prov, config)
    asset = repo1.asset(aid, P)
    provider_sees_copy(prov, asset)
    repo1.close()

    repo2 = Repository(open_repository(tmp_path / "bdata"))  # "restart"
    rep = resolve_pending(provider=prov, repo=repo2, config=config)
    assert [b["asset_id"] for b in rep.bound] == [aid]
    assert repo2.locator_status(aid, P) == "ready"
    repo2.close()


# ---- differential rescan: ready -> stale/missing -> ready ----------------------------------------

def test_rescan_swap_marks_stale_then_restores(tmp_path):
    repo, prov, config = make_env(tmp_path)
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    resolve_pending(provider=prov, repo=repo, config=config)

    ev = {"provider_file_id": 900, "status": "ready",
          "last_seen_kind": "image", "last_seen_size": asset["size_bytes"]}
    prov.files[900].size = asset["size_bytes"] + 5  # swap under same id
    rep = revalidate(provider=prov, repo=repo, config=config,
                     assets=[{"asset_id": aid, "evidence": ev}])
    assert rep.transitions == [{"asset_id": aid, "src": "ready",
                                "dst": "stale"}]
    prov.files[900].size = asset["size_bytes"]  # restore path (docs/13)
    ev2 = dict(ev, status="stale")
    rep2 = revalidate(provider=prov, repo=repo, config=config,
                      assets=[{"asset_id": aid, "evidence": ev2}])
    assert rep2.transitions == [{"asset_id": aid, "src": "stale",
                                 "dst": "ready"}]
    repo.close()


def test_disconnect_never_demotes_ready(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    resolve_pending(provider=prov, repo=repo, config=config)
    prov.failures.append(FakeFailure("detail", "connection"))
    ev = {"provider_file_id": 900, "status": "ready", "last_seen_kind": "image",
          "last_seen_size": asset["size_bytes"]}
    rep = revalidate(provider=prov, repo=repo, config=config,
                     assets=[{"asset_id": aid, "evidence": ev}])
    assert rep.transitions == []  # transient fault: status untouched
    assert rep.faults and rep.faults[0]["code"] == "dependency_unavailable"
    assert repo.locator_status(aid, P) == "ready"


def test_provider_gone_marks_missing(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    resolve_pending(provider=prov, repo=repo, config=config)
    del prov.files[900]
    ev = {"provider_file_id": 900, "status": "ready", "last_seen_kind": "image",
          "last_seen_size": asset["size_bytes"]}
    rep = revalidate(provider=prov, repo=repo, config=config,
                     assets=[{"asset_id": aid, "evidence": ev}])
    assert rep.transitions == [{"asset_id": aid, "src": "ready",
                                "dst": "missing"}]
    assert repo.locator_status(aid, P) == "missing"


def test_no_change_never_writes_fresh_verified_at(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    resolve_pending(provider=prov, repo=repo, config=config)
    before = repo.asset(aid, P)["verified_at"]
    ev = {"provider_file_id": 900, "status": "ready", "last_seen_kind": "image",
          "last_seen_size": asset["size_bytes"]}
    rep = revalidate(provider=prov, repo=repo, config=config,
                     assets=[{"asset_id": aid, "evidence": ev}])
    assert rep.transitions == []
    assert repo.asset(aid, P)["verified_at"] == before


# ---- bind conflict: two pendings one file ---------------------------------------------------------

def test_two_pending_one_file_binds_exactly_one(tmp_path):
    import uuid

    repo, prov, config = make_env(tmp_path)
    for _ in range(2):
        repo.upsert_asset_with_locator(
            asset_id=str(uuid.uuid4()), origin="managed",
            media_kind="image", size_bytes=len(PNG),
            relative_managed_path="00/x.png",
            provider_instance_id=P, provider_file_id=None,
            provider_folder_id=77, last_seen_kind="image",
            last_seen_size=len(PNG), status="pending")
    # second pending DIFFERENT size: only the first matches this one file
    prov.files[900] = FakeFile(file_id=900, size=len(PNG), folder_id=77,
                               name="x.png", rel_dir="00")
    prov.folder_files = {77: [900]}
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert len(rep.bound) == 1
    statuses = [s for s in (repo.locator_status(a["asset_id"], P)
                            for a in repo.folder_pending_assets(P, 77, limit=5))]
    # exactly one remains pending (the other size, not matched)
    assert statuses == ["pending"]
    repo.close()


@pytest.mark.anyio
def test_resolve_tool_wire(tmp_path):
    """the MCP surface: structured ResolveReport, no paths anywhere."""
    import anyio

    from bridge.server import BridgeContext, create_server
    from tests.unit.test_server_health import connected_client

    repo, prov, config = make_env(tmp_path)
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    ctx = BridgeContext(config=config, provider=prov, db=repo)

    async def go():
        async with connected_client(create_server(ctx)) as session:
            res = await session.call_tool("resolve_pending_assets", {})
            sc = res.structured_content
            assert sc["ok"] is True and sc["premise_ready"] is True
            assert [b["asset_id"] for b in sc["bound"]] == [aid]
            blob = repr(sc)
            assert str(config.managed_root) not in blob

    anyio.run(go)
    repo.close()


def test_report_log_shape_has_no_paths(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert str(config.managed_root) not in repr(rep)


def test_foreign_same_size_does_not_bind_but_correct_copy_does(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    prov.files[900].name = "foreign.png"
    before = len([c for c in prov.calls if c[0] == "content"])
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.bound == []
    assert repo.locator_status(aid, P) == "pending"
    provider_sees_copy(prov, asset, fid=901)
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.bound == [{"asset_id": aid, "file_id": 901}]
    assert len([c for c in prov.calls if c[0] == "content"]) == before


@pytest.mark.parametrize("field,value", [
    ("name", "foreign.png"), ("rel_dir", "wrong-shard"),
    ("folder_id", 88), ("kind", "video"), ("available", False),
    ("size", 1), ("status", "error"),
])
def test_listing_detail_race_preserves_pending(env, field, value):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    provider_sees_copy(prov, repo.asset(aid, P))
    original_detail = prov.file_detail
    def detail(fid):
        return dict(original_detail(fid), **{field: value})
    prov.file_detail = detail
    rep = resolve_pending(provider=prov, repo=repo, config=config)
    assert rep.bound == []
    assert rep.waiting == [{"asset_id": aid, "reason": "detail_mismatch"}]
    assert repo.locator_status(aid, P) == "pending"


def test_wrong_listing_shard_never_binds(env):
    repo, prov, config = env
    aid = imported(repo, prov, config)
    provider_sees_copy(prov, repo.asset(aid, P))
    prov.files[900].rel_dir = "wrong-shard"
    assert not resolve_pending(provider=prov, repo=repo, config=config).bound
    assert repo.locator_status(aid, P) == "pending"


@pytest.mark.parametrize("raw_count,managed_count,limit", [
    (100, 1, 100), (100, 0, 100), (100, 100, 100),
    (99, 1, 100), (100, 1, 1000),
])
def test_http_recent_completeness_reaches_resolver(env, raw_count, managed_count, limit):
    import httpx
    from bridge.client import LocalSearchClient

    repo, prov, config = env
    aid = imported(repo, prov, config)
    asset = repo.asset(aid, P)
    provider_sees_copy(prov, asset)
    candidate = prov.file_detail(900)
    items = [dict(candidate, file_id=900 + n) for n in range(managed_count)]
    items += [dict(candidate, file_id=2000 + n, folder_id=88)
              for n in range(raw_count - managed_count)]
    def handler(request):
        if request.url.path == "/api/folders":
            return httpx.Response(200, json=[{"id": 77, "watch": True}])
        if request.url.path == "/api/files/recent":
            assert request.url.params["limit"] == "100"
            return httpx.Response(200, json=items)
        if request.url.path == "/api/files/900":
            return httpx.Response(200, json=candidate)
        pytest.fail("unexpected provider route")
    client = LocalSearchClient(config)
    try:
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(handler),
                                    base_url=config.provider_base_url)
        inventory = client.folder_entries(77, limit=limit)
        assert inventory.raw_count == raw_count
        assert len(inventory.items) == managed_count
        assert inventory.truncated == (raw_count == 100)
        rep = resolve_pending(provider=client, repo=repo, config=config)
    finally:
        client.close()
    if raw_count == 100:
        assert rep.bound == []
        assert rep.waiting == [{"asset_id": aid, "reason": "page_full"}]
        assert repo.locator_status(aid, P) == "pending"
    else:
        assert rep.bound == [{"asset_id": aid, "file_id": 900}]


@pytest.mark.parametrize("status", ["pending", "ready", "stale", "missing", "incompatible"])
def test_duplicate_import_preserves_locator_and_links_after_restart(tmp_path, status):
    from bridge.tools_get import get_asset
    from mcp.server.mcpserver.utilities.types import Image

    repo, prov, config = make_env(tmp_path)
    aid = imported(repo, prov, config)
    if status != "pending":
        provider_sees_copy(prov, repo.asset(aid, P))
        prov.files[900].content = PNG
        resolve_pending(provider=prov, repo=repo, config=config)
        if status != "ready":
            repo.set_locator_status(asset_id=aid, provider_instance_id=P, status=status)
    before = repo.asset(aid, P)
    for memory in ("mem:2", "mem:2", "mem:1"):
        result = register_asset(provider=prov, repo=repo, config=config,
                                memory_ref=memory, provider_file_id=5, **SCOPE)
        assert result.ok and result.deduplicated
        assert result.asset_id == aid and result.link_status == status
        assert repo.asset(aid, P) == before
    assert repo.links_for_asset(asset_id=aid, **SCOPE) == ["mem:1", "mem:2"]
    repo.close()
    repo = Repository(open_repository(config.data_dir))
    assert repo.asset(aid, P) == before
    if status == "ready":
        result = get_asset(provider=prov, repo=repo, provider_instance_id=P,
                           asset_id=aid, **SCOPE)
        assert isinstance(result, Image) and result.data == PNG
    assert not repo._conn.execute(
        "SELECT 1 FROM provider_locators WHERE status='pending'"
        " AND provider_file_id IS NOT NULL").fetchall()
    repo.close()
