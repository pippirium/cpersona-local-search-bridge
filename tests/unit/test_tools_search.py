"""B-08 acceptance for search_assets / list_memory_assets (docs/06).

Same query must not return out-of-scope candidates; unlinked provider hits
must never leak (not even their names); partial coverage is disclosed and
never mistaken for exhaustive; empty index / zero hits / provider faults
all behave. Temp SQLite + synthetic fakes only.
"""

from __future__ import annotations

import pytest

from bridge.provider import FakeFailure, FakeFile, FakeProvider
from bridge.repository import open_repository, Repository
from bridge.tools_link import link_asset
from bridge.tools_search import list_memory_assets, search_assets

P = "http-127.0.0.1-18765"
SCOPE = dict(source_instance_id="", agent_id="a1", project_id="p", channel="c")
OTHER = dict(source_instance_id="", agent_id="a2", project_id="p", channel="c")


@pytest.fixture
def repo(tmp_path):
    conn = open_repository(tmp_path)
    r = Repository(conn)
    yield r
    r.close()


def fimg(fid, name=None):
    return FakeFile(file_id=fid, kind="image",
                    name=name or f"syn-{fid}.jpg", size=100 + fid,
                    mtime="2026-10-01T00:00:00Z")


def link_file(r, prov, fid, memory_ref="mem:1", **scope):
    out = link_asset(provider=prov, repo=r, provider_instance_id=P,
                     memory_ref=memory_ref, provider_file_id=fid,
                     **(scope or SCOPE))
    assert out.ok, out
    return out.asset_id


# ---- privacy: unlinked library files never leak ----------------------------------

def test_unlinked_hits_do_not_leak(repo):
    prov = FakeProvider(files={1: fimg(1), 2: fimg(2)})  # provider finds both
    link_file(repo, prov, 1)
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="anything", **SCOPE)
    assert res.ok
    ids = [e.asset_id for e in res.results]
    assert len(ids) == 1
    # file 2 was a provider hit: its name/file_id must be nowhere in output
    blob = repr(res)
    assert "syn-2" not in blob
    assert not any(e.provider_file_id == 2 for e in res.results)


def test_other_scope_link_invisible(repo):
    prov = FakeProvider(files={1: fimg(1)})
    asset_id = link_file(repo, prov, 1, **OTHER)  # linked by a2 only
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", **SCOPE)
    assert res.results == []
    # and the reverse view still sees it: proves it is a scope filter, not loss
    res2 = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                         query="q", **OTHER)
    assert [e.asset_id for e in res2.results] == [asset_id]


# ---- coverage truth -----------------------------------------------------------------

def test_coverage_exact_when_all_pages_scanned(repo):
    prov = FakeProvider(files={1: fimg(1)})
    link_file(repo, prov, 1)
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", **SCOPE)
    assert res.search_coverage == "exact"


def test_coverage_partial_when_limit_samples(repo):
    # 30 linked files: provider returns pages of 20 (2 pages, exhausted),
    # limit 5 -> a top-N sample of a larger scoped set.
    files = {i: fimg(i) for i in range(1, 31)}
    prov = FakeProvider(files=files)
    for fid in sorted(files)[:30]:
        link_file(repo, prov, fid, memory_ref=f"mem:{fid}")
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", limit=5, **SCOPE)
    assert len(res.results) == 5
    assert res.search_coverage == "partial"


def test_coverage_partial_when_provider_pages_remain(repo):
    """Provider keeps handing out next_cursor past our page bound: bounded
    scan must report partial, never pretend the remainder was scanned."""

    class PagingFake(FakeProvider):
        def search(self, q, *, page_size, folder_id=None, cursor=None):
            self.calls.append(("search", (q,), {"cursor": cursor}))
            page = int(cursor or 0)
            items = [
                {"file_id": page * 100 + i, "kind": "image",
                 "name": f"syn-{page}-{i}.jpg", "folder_id": 1,
                 "score": 0.5, "available": True}
                for i in range(page_size)
            ]
            return {"search_id": "s", "generation": 1, "took_ms": 1,
                    "items": items, "next_cursor": str(page + 1),
                    "remaining": 999, "empty_reason": None}

    prov = PagingFake(files={})
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", **SCOPE)
    assert res.ok and res.results == []
    assert res.search_coverage == "partial"  # 5 pages scanned, more existed
    n_searches = sum(1 for c in prov.calls if c[0] == "search")
    assert n_searches == 5  # bounded scan, not unbounded paging


# ---- empty / fault cases ---------------------------------------------------------------

def test_zero_hits_is_ok_exact(repo):
    prov = FakeProvider(files={})  # empty index
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="nothing", **SCOPE)
    assert res.ok and res.results == []
    assert res.search_coverage == "exact"  # fully scanned empty index


def test_empty_query_rejected(repo):
    prov = FakeProvider(files={})
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="   ", **SCOPE)
    assert res.ok is False and res.error["code"] == "invalid_request"


def test_provider_fault_blocks_search_without_substitution(repo):
    prov = FakeProvider(files={1: fimg(1)},
                        failures=[FakeFailure("search", "index_mismatch")])
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", **SCOPE)
    assert res.ok is False and res.error["code"] == "index_incompatible"
    assert res.results == []  # never answers with cached/other files


def test_model_not_ready_is_annotated(repo):
    prov = FakeProvider(files={}, model_state="loading")
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", **SCOPE)
    assert res.ok
    assert res.provider_warning == "model_not_ready"


def test_limit_clamped_to_20(repo):
    prov = FakeProvider(files={})
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", limit=500, **SCOPE)
    assert res.ok  # clamped internally, not an error


# ---- folder-scoped search ---------------------------------------------------------------

def test_folder_id_filter_passthrough(repo):
    f1 = fimg(1)
    f2 = FakeFile(file_id=2, kind="image", name="syn-2.jpg", folder_id=7,
                  size=50, mtime="2026-10-01T00:00:00Z")
    prov = FakeProvider(files={1: f1, 2: f2})
    link_file(repo, prov, 1)
    link_file(repo, prov, 2)
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", folder_id=7, **SCOPE)
    ids = [e.provider_file_id for e in res.results]
    assert ids == [2]


# ---- scores / provenance ------------------------------------------------------------------

def test_score_verbatim_and_memory_refs_scoped(repo):
    prov = FakeProvider(files={1: fimg(1)})
    asset_id = link_file(repo, prov, 1, memory_ref="mem:1")
    link_file(repo, prov, 1, memory_ref="mem:2")
    res = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                        query="q", **SCOPE)
    e = res.results[0]
    assert e.score == 0.9  # provider score passed through unmodified
    assert sorted(e.memory_refs) == ["mem:1", "mem:2"]
    # no other scope's refs:
    res2 = search_assets(provider=prov, repo=repo, provider_instance_id=P,
                         query="q", **OTHER)
    assert res2.results == []


# ---- list_memory_assets ----------------------------------------------------------------------

def test_list_order_newest_first_and_fields(repo):
    prov = FakeProvider(files={1: fimg(1, name="one.jpg"), 2: fimg(2, "two.jpg")})
    a1 = link_file(repo, prov, 1, memory_ref="mem:9")
    a2 = link_file(repo, prov, 2, memory_ref="mem:9")
    res = list_memory_assets(repo=repo, provider_instance_id=P,
                             memory_ref="mem:9", **SCOPE)
    assert res.ok
    assert [a["asset_id"] for a in res.assets] == [a2, a1]  # newest link first
    a = res.assets[0]
    assert a["display_label"] == "two.jpg"  # name only, never a path
    assert "path" not in repr(res)
    assert a["link_status"] == "ready"
    assert a["linked_at"]  # non-empty timestamp
    assert a["media_kind"] == "image"


def test_list_other_scope_sees_nothing(repo):
    prov = FakeProvider(files={1: fimg(1)})
    link_file(repo, prov, 1, memory_ref="mem:9")
    res = list_memory_assets(repo=repo, provider_instance_id=P,
                             memory_ref="mem:9", **OTHER)
    assert res.ok and res.assets == []


def test_list_invalid_memory_ref(repo):
    res = list_memory_assets(repo=repo, provider_instance_id=P,
                             memory_ref="mem:0", **SCOPE)
    assert res.ok is False and res.error["code"] == "invalid_request"


def test_list_limit_clamped(repo):
    prov = FakeProvider(files={1: fimg(1)})
    link_file(repo, prov, 1, memory_ref="mem:9")
    res = list_memory_assets(repo=repo, provider_instance_id=P,
                             memory_ref="mem:9", limit=9999, **SCOPE)
    assert res.ok


@pytest.mark.anyio
def test_search_tools_over_mcp_wire(tmp_path):
    from tests.unit.test_server_health import connected_client
    from bridge.server import BridgeContext, create_server
    from bridge.config import validate_config
    import anyio

    conn = open_repository(tmp_path)
    r = Repository(conn)
    prov = FakeProvider(files={1: fimg(1)})
    cfg = validate_config(data_dir=tmp_path,
                          provider_base_url="http://127.0.0.1:18765")
    ctx = BridgeContext(config=cfg, provider=prov, db=r)

    async def go():
        async with connected_client(create_server(ctx)) as session:
            link = await session.call_tool(
                "link_asset",
                {"agent_id": "a1", "project_id": "p", "channel": "c",
                 "memory_ref": "mem:1", "provider_file_id": 1},
            )
            asset_id = link.structured_content["asset_id"]
            res = await session.call_tool(
                "search_assets",
                {"agent_id": "a1", "project_id": "p", "channel": "c",
                 "query": "q", "limit": 10},
            )
            payload = res.structured_content
            assert payload["ok"] is True
            assert payload["search_coverage"] == "exact"
            assert payload["results"][0]["asset_id"] == asset_id
            lst = await session.call_tool(
                "list_memory_assets",
                {"agent_id": "a1", "project_id": "p", "channel": "c",
                 "memory_ref": "mem:1"},
            )
            assert lst.structured_content["assets"][0]["asset_id"] == asset_id

    anyio.run(go)
    r.close()
