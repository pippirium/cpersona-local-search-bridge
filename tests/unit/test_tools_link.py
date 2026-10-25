"""B-07 acceptance for link_asset / unlink_asset (docs/06).

Idempotence, invalid memory_ref, unknown file_id, non-image kind, no
cross-scope leakage, and no half-written DB rows when the provider fails
mid-operation. Direct function tests plus one over-the-wire MCP test.
"""

from __future__ import annotations

import uuid

import pytest

from bridge.provider import FakeFailure, FakeFile, FakeProvider
from bridge.repository import open_repository, Repository

from tests.unit.test_server_health import connected_client  # wire helper

from bridge.server import BridgeContext, create_server
from bridge.tools_link import link_asset, unlink_asset

P = "http-127.0.0.1-18765"


def field(out, name):
    """Uniform accessor for the typed LinkResult/UnlinkResult dataclasses."""
    return getattr(out, name)


def errcode(out):
    assert out.ok is False
    return out.error["code"]


@pytest.fixture
def repo(tmp_path):
    conn = open_repository(tmp_path)
    r = Repository(conn)
    yield r
    r.close()


def files(**kw):
    base = dict(file_id=101, kind="image", name="syn-a.jpg", size=1000,
                mtime="2026-10-01T00:00:00Z", available=True)
    base.update(kw)
    return {base["file_id"]: FakeFile(**base)}


def link(r, asset_id, memory_ref="mem:1", *, agent="a1", project="", channel=""):
    return dict(source_instance_id="", agent_id=agent, project_id=project,
                channel=channel, memory_ref=memory_ref, asset_id=asset_id)


SCOPE = dict(source_instance_id="", agent_id="a1", project_id="p", channel="c")


# ---- happy path + idempotence ------------------------------------------------

def test_link_creates_asset_and_ready_locator(repo):
    prov = FakeProvider(files=files())
    out = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:1", provider_file_id=101, **SCOPE)
    assert field(out, "link_status") == "ready"
    assert field(out, "media_kind") == "image"
    uuid.UUID(field(out, "asset_id"), version=4)
    asset = repo.asset(field(out, "asset_id"), P)
    assert asset["provider_file_id"] == 101
    assert asset["media_type"] == "image/jpeg"
    assert asset["last_seen_mtime"] == "2026-10-01T00:00:00Z"


def test_link_idempotent_same_scope_same_file(repo):
    prov = FakeProvider(files=files())
    a = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101, **SCOPE)
    b = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101, **SCOPE)
    assert field(a, "asset_id") == field(b, "asset_id")
    n = repo._conn.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0]
    assert n == 1  # exactly one row despite double call


def test_link_same_file_second_memory_reuses_asset(repo):
    prov = FakeProvider(files=files())
    a = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101, **SCOPE)
    b = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:2", provider_file_id=101, **SCOPE)
    assert field(a, "asset_id") == field(b, "asset_id")


# ---- failure paths: nothing half-written ---------------------------------------

def test_unknown_file_id_leaves_no_rows(repo):
    prov = FakeProvider(files={})  # every detail raises not_found
    out = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:1", provider_file_id=999, **SCOPE)
    assert errcode(out) == "asset_unavailable"
    assert repo._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    assert repo._conn.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 0


def test_non_image_kind_rejected(repo):
    prov = FakeProvider(files=files(kind="video"))
    out = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:1", provider_file_id=101, **SCOPE)
    assert errcode(out) == "unsupported_media"
    assert repo._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0


def test_invalid_memory_ref_rejected_no_rows(repo):
    prov = FakeProvider(files=files())
    out = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:0", provider_file_id=101, **SCOPE)
    assert errcode(out) == "invalid_request"
    # asset MAY exist (provider verified first) but the link must not:
    assert repo._conn.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 0


@pytest.mark.parametrize("bad", ["0", "-5", "abc", None])
def test_invalid_file_id_rejected_before_any_call(bad):
    """Non-positive / non-numeric provider ids never reach the provider."""
    calls = []

    class Spy(FakeProvider):
        def file_detail(self, file_id):
            calls.append(file_id)
            return super().file_detail(file_id)

    prov = Spy(files=files())
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        r = Repository(open_repository(Path(td)))
        out = link_asset(provider=prov, repo=r, provider_instance_id=P,
                         memory_ref="mem:1", provider_file_id=bad, **SCOPE)
        r.close()
    assert errcode(out) == "invalid_request"
    assert calls == []


def test_detail_then_link_atomicity(repo):
    """Two failure orders around the write boundary:
    - provider detail fails -> no asset, no link;
    - provider detail succeeds but link validation fails -> asset exists,
      zero link rows (the link step is the last write; no half-links)."""
    prov = FakeProvider(files=files(),
                        failures=[FakeFailure("detail", "not_found")])
    out = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:1", provider_file_id=101, **SCOPE)
    assert errcode(out) == "asset_unavailable"
    assert repo._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0

    prov2 = FakeProvider(files=files())
    out = link_asset(provider=prov2, repo=repo, provider_instance_id=P,
                     memory_ref="mem:bad", provider_file_id=101, **SCOPE)
    assert errcode(out) == "invalid_request"
    assert repo._conn.execute(
        "SELECT COUNT(*) FROM asset_links").fetchone()[0] == 0
    assert repo._conn.execute(
        "SELECT COUNT(*) FROM assets").fetchone()[0] == 1  # verified asset kept


def test_unavailable_provider_file_links_missing(repo):
    prov = FakeProvider(files=files(available=False))
    out = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:1", provider_file_id=101, **SCOPE)
    assert field(out, "link_status") == "missing"  # truthful status, not fake ready


# ---- unlink -------------------------------------------------------------------

def test_unlink_removes_row_keeps_asset(repo):
    prov = FakeProvider(files=files())
    a = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101, **SCOPE)
    out = unlink_asset(repo=repo, memory_ref="mem:1", asset_id=field(a, "asset_id"),
                       **SCOPE)
    assert field(out, "unlinked") is True and field(out, "remaining_links") == 0
    assert repo.asset(field(a, "asset_id"), P) is not None  # asset survives
    assert repo._conn.execute(
        "SELECT COUNT(*) FROM asset_links").fetchone()[0] == 0


def test_unlink_other_scope_link_untouched_and_count_exact(repo):
    prov = FakeProvider(files=files())
    a = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101, **SCOPE)
    # same asset linked from a different channel
    link_asset(provider=prov, repo=repo, provider_instance_id=P,
               memory_ref="mem:1", provider_file_id=101,
               source_instance_id="", agent_id="a1", project_id="p",
               channel="other")
    out = unlink_asset(repo=repo, memory_ref="mem:1", asset_id=field(a, "asset_id"),
                       **SCOPE)
    assert field(out, "remaining_links") == 0  # exact scope: the other channel not counted
    assert repo.asset_in_scope(asset_id=field(a, "asset_id"), source_instance_id="",
                               agent_id="a1", project_id="p", channel="other")


def test_unlink_idempotent(repo):
    prov = FakeProvider(files=files())
    a = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101, **SCOPE)
    unlink_asset(repo=repo, memory_ref="mem:1", asset_id=field(a, "asset_id"), **SCOPE)
    out = unlink_asset(repo=repo, memory_ref="mem:1", asset_id=field(a, "asset_id"),
                       **SCOPE)
    assert field(out, "unlinked") is True and field(out, "remaining_links") == 0


# ---- cross-scope invisibility (T-02 at tool level) --------------------------------

def test_scope_is_exact_at_link_level(repo):
    prov = FakeProvider(files=files())
    a = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101,
                   source_instance_id="", agent_id="alice", project_id="p",
                   channel="c")
    # bob links the same file: same asset, separate link rows
    b = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                   memory_ref="mem:1", provider_file_id=101,
                   source_instance_id="", agent_id="bob", project_id="p",
                   channel="c")
    assert field(a, "asset_id") == field(b, "asset_id")
    assert repo.asset_in_scope(asset_id=field(a, "asset_id"), source_instance_id="",
                               agent_id="alice", project_id="p", channel="c")
    assert not repo.asset_in_scope(asset_id=field(a, "asset_id"), source_instance_id="",
                                   agent_id="alice", project_id="P", channel="c")


# ---- over-the-wire ---------------------------------------------------------------

@pytest.mark.anyio
async def test_link_tools_over_mcp_wire(tmp_path):
    conn = open_repository(tmp_path)
    r = Repository(conn)
    ctx = BridgeContext(
        config=_wire_config(tmp_path), provider=FakeProvider(files=files()),
        db=r,
    )
    async with connected_client(create_server(ctx)) as session:
        res = await session.call_tool(
            "link_asset",
            {"agent_id": "a1", "project_id": "p", "channel": "c",
             "memory_ref": "mem:1", "provider_file_id": 101},
        )
        payload = res.structured_content
        assert payload is not None and payload["link_status"] == "ready"
        res2 = await session.call_tool(
            "unlink_asset",
            {"agent_id": "a1", "project_id": "p", "channel": "c",
             "memory_ref": "mem:1", "asset_id": payload["asset_id"]},
        )
        assert (res2.structured_content or {})["unlinked"] is True
    r.close()


def _wire_config(tmp_path):
    from bridge.config import validate_config

    return validate_config(data_dir=tmp_path,
                           provider_base_url="http://127.0.0.1:18765")
