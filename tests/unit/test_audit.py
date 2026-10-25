"""B-12 acceptance: reconcile default dry-run, no silent rebind under file-id
reuse, links preserved, disconnected->reconnected recovery, orphan managed
copies reported (never deleted), verdict-vs-apply race."""

from __future__ import annotations

import pytest

from bridge.audit import audit, reconcile
from bridge.config import validate_config
from bridge.provider import FakeFile, FakeFailure, FakeProvider
from bridge.repository import Repository, open_repository
from bridge.tools_import import register_asset
from bridge.tools_link import link_asset

P = "127.0.0.1:18765"
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


def linked(repo, prov):
    """external link (B-07 path): locator ready with file 5 evidence."""
    out = link_asset(provider=prov, repo=repo, provider_instance_id=P,
                     memory_ref="mem:9", provider_file_id=5, **SCOPE)
    assert out.ok
    return out.asset_id


def codes(rep):
    return [f["code"] for f in rep.findings]


# ---- default dry-run ---------------------------------------------------------

def test_reconcile_defaults_to_dry_run(env):
    repo, prov, config = env
    aid = linked(repo, prov)
    prov.files[5].size = len(PNG) + 7  # swap under same id
    rep = reconcile(provider=prov, repo=repo, config=config)
    assert rep.applied is False
    assert "ready_to_stale" in codes(rep)
    assert repo.locator_status(aid, P) == "ready"  # nothing written


def test_reconcile_apply_transitions_state(env):
    repo, prov, config = env
    aid = linked(repo, prov)
    prov.files[5].size = len(PNG) + 7
    rep = reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert repo.locator_status(aid, P) == "stale"
    assert codes(rep) == []  # the drift got repaired, not left reported


# ---- file-id reuse never returns a wrong image -------------------------------

def test_reused_id_staled_and_links_survive(env):
    repo, prov, config = env
    aid = linked(repo, prov)
    # rowid reuse: id 5 now serves a DIFFERENT file
    prov.files[5] = FakeFile(file_id=5, kind="image", name="reused.png",
                             content=b"\x89PNG\r\n\x1a\nX" * 3, size=9)
    rep = reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert repo.locator_status(aid, P) == "stale"
    # link = user intent: preserved (docs/11 §4)
    assert repo.links_for_asset(asset_id=aid, **SCOPE) != []
    # and never silently re-associated: provider_file_id untouched
    assert repo.asset(aid, P)["provider_file_id"] == 5


# ---- disconnected -> reconnected recovery --------------------------------------

def test_disconnect_then_reconnect_recovers(env):
    repo, prov, config = env
    aid = linked(repo, prov)
    prov.failures.append(FakeFailure("detail", "connection"))
    rep = reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert repo.locator_status(aid, P) == "ready"  # transient: untouched
    assert rep.transient_faults == 1
    prov.failures.clear()
    rep2 = reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert repo.locator_status(aid, P) == "ready"
    assert rep2.findings == []


def test_gone_then_seen_again_unchanged_returns_to_ready(env, monkeypatch):
    """missing -> ready per docs/11 §4: the provider reports the file again
    with matching evidence. The bridge NEVER invents the detail — it trusts
    the provider's FileDetail (what the remount actually shows is the
    provider's truth; the fake scripts it explicitly here)."""
    repo, prov, config = env
    aid = linked(repo, prov)
    del prov.files[5]  # drive ejected
    reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert repo.locator_status(aid, P) == "missing"

    # remount: provider now serves id 5 again with matching kind/size
    ev = repo.asset(aid, P)
    prov.files[5] = FakeFile(file_id=5, name=ev["last_seen_name"],
                             size=ev["last_seen_size"],
                             mtime="2026-09-16T00:00:00+09:00")
    reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert repo.locator_status(aid, P) == "ready"
    assert repo.links_for_asset(asset_id=aid, **SCOPE) != []


# ---- provider-wide incompatibility ----------------------------------------------

def test_index_drift_collapses_to_incompatible(env):
    repo, prov, config = env
    aid = linked(repo, prov)
    prov.failures.append(FakeFailure("detail", "index_mismatch"))  # unknown -> index_incompatible
    rep = reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert repo.locator_status(aid, P) == "incompatible"
    assert rep.provider_healthy is False


# ---- managed copy orphan audit ---------------------------------------------------

def test_orphan_copy_and_foreign_file_reported_not_touched(env):
    repo, prov, config = env
    aid = register_asset(provider=prov, repo=repo, config=config,
                         memory_ref="mem:1", provider_file_id=5,
                         **SCOPE).asset_id
    root = config.managed_root
    # foreign file dropped into the managed root by the operator
    (root / "holiday.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 40)
    rep = audit(provider=prov, repo=repo, config=config)
    assert any(f["code"] == "foreign_in_managed_root"
               and f["name"] == "holiday.png" for f in rep.findings)
    # nothing deleted — the foreign file survives an audit
    assert (root / "holiday.png").exists()
    # the legit copy is NOT reported missing/orphan
    asset = repo.asset(aid, P)
    rel = asset["relative_managed_path"]
    assert (root / rel).exists()
    assert not any(f["ref"] == aid and f["code"] == "managed_copy_missing"
                   for f in rep.findings)


def test_missing_copy_reported_by_asset_id(env):
    repo, prov, config = env
    aid = register_asset(provider=prov, repo=repo, config=config,
                         memory_ref="mem:1", provider_file_id=5,
                         **SCOPE).asset_id
    rel = repo.asset(aid, P)["relative_managed_path"]
    (config.managed_root / rel).unlink()  # external deletion
    rep = audit(provider=prov, repo=repo, config=config)
    hit = [f for f in rep.findings if f["ref"] == aid]
    assert hit and hit[0]["code"] == "managed_copy_missing"
    # audit never rewrites locator state as a side effect of the disk sweep
    assert repo.locator_status(aid, P) == "pending"


def test_leftover_tmp_reported(env):
    repo, prov, config = env
    register_asset(provider=prov, repo=repo, config=config,
                   memory_ref="mem:1", provider_file_id=5, **SCOPE)
    tmp = config.managed_root / ".tmp"
    tmp.mkdir(exist_ok=True)
    (tmp / "deadbeef.part").write_bytes(b"x")
    rep = audit(provider=prov, repo=repo, config=config)
    assert "leftover_tmp" in codes(rep)
    assert (tmp / "deadbeef.part").exists()  # reported, never deleted


# ---- race: stale verdict against a concurrent change ----------------------------

def test_stale_verdict_refused_after_row_moved_concurrently(env, monkeypatch):
    """A classify verdict must never clobber a concurrent change: the row is
    moved to `stale` between classify and apply; the apply path refuses
    (check-and-set under the lock) and the concurrent truth survives."""
    repo, prov, config = env
    aid = linked(repo, prov)
    prov.files[5].size = len(PNG) + 7

    fired = {"v": False}
    real_status = repo.locator_status

    def scripted_status(a, pid):
        out = real_status(a, pid)
        if not fired["v"]:
            fired["v"] = True
            repo.set_locator_status(asset_id=a, provider_instance_id=pid,
                                    status="stale")  # concurrent mover wins
        return out

    monkeypatch.setattr(repo, "locator_status", scripted_status)
    rep = reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert fired["v"] is True
    assert repo.locator_status(aid, P) == "stale"  # concurrent change kept
    assert any(f["code"] == "concurrent_change" for f in rep.findings)


# ---- report shape carries no paths ------------------------------------------------

def test_report_carries_no_absolute_paths(env):
    repo, prov, config = env
    aid = linked(repo, prov)
    prov.files[5].size = len(PNG) + 7
    rep = reconcile(provider=prov, repo=repo, config=config, apply=True)
    assert str(config.managed_root) not in repr(rep)
    assert str(config.data_dir) not in repr(rep)


@pytest.mark.anyio
def test_audit_tool_wire_default_dry_run(tmp_path):
    import anyio

    from bridge.server import BridgeContext, create_server
    from tests.unit.test_server_health import connected_client

    repo, prov, config = make_env(tmp_path)
    aid = linked(repo, prov)
    prov.files[5].size = len(PNG) + 7
    ctx = BridgeContext(config=config, provider=prov, db=repo)

    async def go():
        async with connected_client(create_server(ctx)) as session:
            res = await session.call_tool("audit_assets", {})
            sc = res.structured_content
            assert sc["ok"] is True and sc["applied"] is False
            assert any(f["code"] == "ready_to_stale" for f in sc["findings"])
            assert repo.locator_status(aid, P) == "ready"  # tool default dry
            assert str(config.managed_root) not in repr(sc)

    anyio.run(go)
    repo.close()
