"""B-09 acceptance for get_asset (docs/06): explicit-only fetch, provider
revalidation, MIME/size/pixel caps, no leakage, no substitution, no source
modification. Synthetic bytes only.
"""

from __future__ import annotations

import base64

import pytest

import bridge_policy
from bridge.provider import FakeFailure, FakeFile, FakeProvider
from bridge.repository import open_repository, Repository
from bridge.tools_get import GetAssetResult, get_asset
from bridge.tools_link import link_asset

P = "http-127.0.0.1-18765"
SCOPE = dict(source_instance_id="", agent_id="a1", project_id="p", channel="c")

# a real tiny PNG header for sniffing/dimension parsing
PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
       + (120).to_bytes(4, "big") + (80).to_bytes(4, "big")
       + b"fake" * 10)


@pytest.fixture
def repo(tmp_path):
    conn = open_repository(tmp_path)
    r = Repository(conn)
    yield r
    r.close()


def png_file(fid=1, **kw):
    base = dict(file_id=fid, kind="image", name="syn.png", size=len(PNG),
                mtime="2026-10-01T00:00:00Z", content=PNG, mime="image/png",
                width=120, height=80)
    base.update(kw)
    return FakeFile(**base)


def make_link(r, prov, fid=1, memory_ref="mem:1",
              provider_generation=None, **scope):
    out = link_asset(provider=prov, repo=r, provider_instance_id=P,
                     memory_ref=memory_ref, provider_file_id=fid,
                     provider_generation=provider_generation,
                     **(scope or SCOPE))
    assert out.ok, out
    return out.asset_id


def get(repo_, prov, asset_id, scope=None, **kw):
    sc = dict(SCOPE)
    sc.update(scope or {})
    return get_asset(provider=prov, repo=repo_, provider_instance_id=P,
                     asset_id=asset_id, **sc, **kw)


# ---- happy path ---------------------------------------------------------------

def test_explicit_fetch_returns_image_bytes(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)
    res = get(repo, prov, aid)
    from mcp.server.mcpserver.utilities.types import Image

    assert isinstance(res, Image)
    assert res.data == PNG
    assert res._mime_type == "image/png"


def test_fetch_updates_verified_evidence(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)
    before = repo.asset(aid, P)["verified_at"]
    get(repo, prov, aid)
    asset = repo.asset(aid, P)
    assert asset["locator_status"] == "ready"
    assert asset["verified_at"] >= before


# ---- explicit-only: nothing else reads content -----------------------------------

def test_search_and_list_never_call_content(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)
    from bridge.tools_search import list_memory_assets, search_assets

    search_assets(provider=prov, repo=repo, provider_instance_id=P,
                  query="q", **SCOPE)
    list_memory_assets(repo=repo, provider_instance_id=P, memory_ref="mem:1",
                       **SCOPE)
    content_calls = [c for c in prov.calls if c[0] == "file_content"]
    assert content_calls == []  # the media path is get_asset alone


# ---- scope gates (T-01/T-02) ---------------------------------------------------------

def test_unknown_and_cross_scope_answer_identically(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)  # linked in SCOPE only
    other = dict(source_instance_id="", agent_id="intruder", project_id="p",
                 channel="c")
    foreign = get(repo, prov, aid, scope=other)
    bogus = get(repo, prov, "00000000-0000-4000-8000-000000000000", scope=other)
    assert isinstance(foreign, GetAssetResult) and foreign.ok is False
    assert foreign.error == bogus.error  # byte-identical: anti-enumeration
    assert foreign.error["code"] == "asset_not_found"
    # and the fetch NEVER touched the provider (no content call):
    assert [c for c in prov.calls if c[0] == "file_content"] == []


def test_memory_ref_narrowing(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov, memory_ref="mem:1")
    res = get(repo, prov, aid, memory_ref="mem:2")
    assert isinstance(res, GetAssetResult) and res.error["code"] == "asset_not_found"
    ok = get(repo, prov, aid, memory_ref="mem:1")
    from mcp.server.mcpserver.utilities.types import Image

    assert isinstance(ok, Image)


# ---- stale / substitution gates (docs/13) -------------------------------------------------

def test_provider_says_missing_status_flips_and_no_bytes(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)
    prov.files[1].available = False
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "asset_unavailable"
    assert repo.asset(aid, P)["locator_status"] == "missing"
    # content route was never attempted after detail said unavailable
    assert [c for c in prov.calls if c[0] == "file_content"] == []


def test_file_id_never_swapped_even_when_gone(repo):
    """rowid-reuse nightmare: locator points at a file_id that 404s. We must
    fail, keep the numeric id stored (never NULL/swap it), and never resolve
    a different file."""
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)
    del prov.files[1]  # provider 404s now
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "asset_unavailable"
    assert repo.asset(aid, P)["provider_file_id"] == 1  # untouched identity


def test_pending_locator_no_guessing(repo):
    """asset linked to nothing yet (pending, provider_file_id NULL):
    unavailable, no fetch."""
    prov = FakeProvider(files={1: png_file()})
    aid = str(__import__("uuid").uuid4())
    repo.upsert_asset_with_locator(
        asset_id=aid, origin="external", media_kind="image",
        provider_instance_id=P, provider_file_id=None, status="pending",
        provider_generation=None, last_seen_name=None,
    )
    repo.link(asset_id=aid, memory_ref="mem:1", **SCOPE)
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "asset_unavailable"
    assert [c for c in prov.calls if c[0] == "file_content"] == []


def test_evidence_drift_refreshes_and_serves_the_same_file(repo):
    # docs/11 §5 waiver: mtime drift (an indexed edit) is admitted ONLY by
    # generation unchanged + name match. FakeProvider default generation is
    # "1"; link with the matching hint so the waiver legitimately applies.
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov, provider_generation="1")
    prov.files[1].size = len(PNG) + 7  # provider re-measured (drift)
    prov.files[1].mtime = "2026-10-05T00:00:00Z"
    res = get(repo, prov, aid)
    from mcp.server.mcpserver.utilities.types import Image

    assert isinstance(res, Image)  # identity (same file_id, kind image) holds
    asset = repo.asset(aid, P)
    assert asset["last_seen_size"] == len(PNG) + 7  # evidence refreshed


def test_mtime_drift_without_generation_waiver_refuses(repo):
    # rowid-reuse defense (docs/13, live-measured docs/15): mtime mismatch,
    # locator never recorded a generation -> waiver unavailable -> refuse.
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)
    prov.files[1].mtime = "2026-10-05T00:00:00Z"
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "asset_unavailable"
    assert [c for c in prov.calls if c[0] == "file_content"] == []
    assert repo.locator_status(aid, P) == "stale"


def test_mtime_drift_generation_switch_refuses(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov, provider_generation="1")
    prov.files[1].mtime = "2026-10-05T00:00:00Z"
    prov.generation = 2  # rebuild happened: waiver dead even with a name match
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "asset_unavailable"


def test_mtime_drift_generation_ok_name_drift_refuses(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov, provider_generation="1")
    prov.files[1].mtime = "2026-10-05T00:00:00Z"
    prov.files[1].name = "someone-else.png"  # ledger rename != same file
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "asset_unavailable"


def test_mtime_drift_status_probe_failure_refuses(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov, provider_generation="1")
    prov.files[1].mtime = "2026-10-05T00:00:00Z"
    prov.failures.append(FakeFailure(route="status", code="model_loading"))
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "asset_unavailable"


# ---- bounds (T-11) --------------------------------------------------------------------------

def test_declared_size_over_cap_refused_before_fetch(repo):
    big = png_file(size=bridge_policy.MAX_FETCH_BYTES + 1)
    prov = FakeProvider(files={1: big})
    aid = make_link(repo, prov)
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "media_too_large"
    assert [c for c in prov.calls if c[0] == "file_content"] == []


def test_pixels_over_cap_refused(repo):
    f = png_file(width=9000, height=9000)  # 81M > 40M
    prov = FakeProvider(files={1: f})
    aid = make_link(repo, prov)
    res = get(repo, prov, aid)
    assert res.error["code"] == "media_too_large"


def test_lying_declared_size_caught_by_capped_fetch(repo):
    """detail declares a small size; the content route actually holds a
    giant file. The fetch must still be byte-capped and the refusal must
    surface as media_too_large — never a silent truncation."""
    from bridge.errors import BridgeError

    class LyingFake(FakeProvider):
        def file_detail(self, file_id):
            d = super().file_detail(file_id)
            d["size"] = 100  # lie: declares tiny
            return d

        def file_content(self, file_id, max_bytes):
            self.calls.append(("file_content", (file_id, max_bytes), {}))
            raise BridgeError("media_too_large")  # provider truth: too big

    prov = LyingFake(files={1: png_file()})
    aid = make_link(repo, prov)
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "media_too_large"
    calls = [c for c in prov.calls if c[0] == "file_content"]
    assert calls  # bounded fetch attempted despite the small declaration
    assert calls[0][1][1] <= bridge_policy.MAX_FETCH_BYTES


# ---- MIME gates (docs/12 §3) -------------------------------------------------------------------

def test_heic_typed_reject(repo):
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40
    f = png_file(content=heic, size=len(heic), mime="image/heic",
                 name="syn.heic")
    prov = FakeProvider(files={1: f})
    aid = make_link(repo, prov)
    res = get(repo, prov, aid)
    assert isinstance(res, GetAssetResult)
    assert res.error["code"] == "unsupported_media"


def test_lying_content_type_resolved_by_sniffing(repo):
    """header claims png; bytes are JPEG -> served as jpeg (bytes win)."""
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 60
    f = png_file(content=jpeg, mime="image/png")
    prov = FakeProvider(files={1: f})
    aid = make_link(repo, prov)
    res = get(repo, prov, aid)
    from mcp.server.mcpserver.utilities.types import Image

    assert isinstance(res, Image)
    assert res._mime_type == "image/jpeg"


def test_unknown_bytes_rejected(repo):
    junk = b"MACBKILLJARVIS" + b"\x00" * 50
    # The asset was linked while content was a valid png; the provider now
    # serves an unknown blob of the SAME size: no stored/pixel evidence
    # pre-rejects it — the MIME gate on the actual bytes must (T-11 spirit:
    # bytes decide).
    f = png_file(mime="image/png", width=None, height=None, size=len(junk))
    prov = FakeProvider(files={1: f})
    aid = make_link(repo, prov)
    prov.files[1].content = junk
    res = get(repo, prov, aid)
    assert res.error["code"] == "unsupported_media"


def test_non_image_kind_asset_rejected(repo):
    f = png_file(kind="video", name="syn.mp4")
    prov = FakeProvider(files={1: f})
    # link rejects video outright (B-07); simulate a schema-legal video asset
    aid = str(__import__("uuid").uuid4())
    repo.upsert_asset_with_locator(
        asset_id=aid, origin="external", media_kind="video",
        provider_instance_id=P, provider_file_id=1, status="ready",
        provider_generation=None, last_seen_name="syn.mp4",
    )
    repo.link(asset_id=aid, memory_ref="mem:1", **SCOPE)
    res = get(repo, prov, aid)
    assert res.error["code"] == "unsupported_media"


# ---- no leakage in errors (T-09) ------------------------------------------------------------------

def test_error_results_carry_no_paths_or_bytes(repo):
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov)
    del prov.files[1]
    res = get(repo, prov, aid)
    blob = repr(res)
    assert "fixture-root" not in blob  # provider fake path never echoes
    assert "/" not in (res.error or {}).get("message", "")


def test_provider_fault_maps(repo):
    from bridge.provider import FakeFailure

    prov = FakeProvider(files={1: png_file()},
                        failures=[FakeFailure("content", "file_unavailable")])
    aid = make_link(repo, prov)
    res = get(repo, prov, aid)
    assert res.error["code"] == "asset_unavailable"


# ---- over the wire: bytes travel as ImageContent only ------------------------------------------

@pytest.mark.anyio
def test_get_asset_wire_image_content_no_structured_echo(tmp_path):
    import anyio

    from bridge.config import validate_config
    from bridge.server import BridgeContext, create_server
    from tests.unit.test_server_health import connected_client

    conn = open_repository(tmp_path)
    r = Repository(conn)
    prov = FakeProvider(files={1: png_file()})
    cfg = validate_config(data_dir=tmp_path,
                          provider_base_url="http://127.0.0.1:18765")
    ctx = BridgeContext(config=cfg, provider=prov, db=r)
    aid_holder = {}

    async def go():
        async with connected_client(create_server(ctx)) as session:
            link = await session.call_tool(
                "link_asset",
                {"agent_id": "a1", "project_id": "p", "channel": "c",
                 "memory_ref": "mem:1", "provider_file_id": 1},
            )
            aid_holder["id"] = link.structured_content["asset_id"]
            res = await session.call_tool(
                "get_asset",
                {"agent_id": "a1", "project_id": "p", "channel": "c",
                 "asset_id": aid_holder["id"]},
            )
            kinds = [b.type for b in res.content]
            assert kinds == ["image"]
            # bytes appear ONLY as base64 image content, never structured:
            assert res.structured_content is None
            import base64 as b64

            assert b64.b64decode(res.content[0].data) == PNG

    anyio.run(go)
    r.close()


def test_mtime_drift_generation_ok_name_match_serves(repo):
    # full waiver: same generation + same audited name -> indexed edit,
    # serve and refresh (the gate-3b counterpart of the refusal tests)
    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov, provider_generation="1")
    prov.files[1].mtime = "2026-10-05T00:00:00Z"  # name stays "syn.png"
    res = get(repo, prov, aid)
    from mcp.server.mcpserver.utilities.types import Image

    assert isinstance(res, Image)


def test_rejected_evidence_survives_retries_revalidation_and_restart(tmp_path):
    from bridge.config import validate_config
    from bridge.reconcile import revalidate
    from bridge.audit import reconcile
    from mcp.server.mcpserver.utilities.types import Image

    prov = FakeProvider(files={1: png_file()})
    r = Repository(open_repository(tmp_path))
    aid = make_link(r, prov, provider_generation="1")
    original = r.asset(aid, P)
    prov.generation = 2
    prov.files[1].mtime = "2026-10-05T00:00:00Z"
    prov.files[1].name = "replacement.png"
    config = validate_config(data_dir=tmp_path,
                             provider_base_url="http://127.0.0.1:18765")
    for attempt in range(3):
        assert get(r, prov, aid).error["code"] == "asset_unavailable"
        if attempt == 0:
            r.close()
            r = Repository(open_repository(tmp_path))
        ev = r.locator_evidence_for_file(P, 1)[0]
        revalidate(provider=prov, repo=r, config=config,
                   assets=[{"asset_id": aid, "evidence": ev}])
        reconcile(provider=prov, repo=r, config=config, apply=True)
        make_link(r, prov, provider_generation="2")
        for key in ("last_seen_mtime", "last_seen_size", "last_seen_name",
                    "provider_generation", "provider_file_id"):
            assert r.asset(aid, P)[key] == original[key]
    assert not [c for c in prov.calls if c[0] == "content"]
    prov.files[1] = png_file()
    prov.generation = 1
    assert isinstance(get(r, prov, aid), Image)
    r.set_locator_status(asset_id=aid, provider_instance_id=P, status="incompatible")
    assert get(r, prov, aid).error["code"] == "asset_unavailable"
    assert r.asset(aid, P)["locator_status"] == "incompatible"
    r.close()


@pytest.mark.parametrize("generation,mtime,size_delta,name,allowed", [
    (2, "same", 5, "replacement.png", False),
    (2, "same", 0, "syn.png", False),
    (None, "same", 5, "syn.png", False),
    (None, None, 5, "syn.png", False),
    (None, None, 0, "syn.png", False),
    (None, "same", 0, "syn.png", True),
    (1, "same", 5, "syn.png", True),
    (1, None, 5, "syn.png", True),
    (1, "same", 0, "replacement.png", False),
])
def test_fetch_identity_dimensions_independent(repo, generation, mtime,
                                                size_delta, name, allowed):
    from mcp.server.mcpserver.utilities.types import Image

    prov = FakeProvider(files={1: png_file()})
    aid = make_link(repo, prov, provider_generation="1")
    original = repo.asset(aid, P)
    prov.generation = generation
    if mtime != "same":
        prov.files[1].mtime = mtime
    prov.files[1].size += size_delta
    prov.files[1].name = name
    for _ in range(3):
        result = get(repo, prov, aid)
        if allowed:
            assert isinstance(result, Image)
        else:
            assert result.error["code"] == "asset_unavailable"
            for key in ("last_seen_mtime", "last_seen_size", "last_seen_name"):
                assert repo.asset(aid, P)[key] == original[key]
    if not allowed:
        assert not [c for c in prov.calls if c[0] == "content"]
