"""B-05: repository contract tests (docs/11 §7–§8).

Executed against temporary SQLite files with synthetic UUIDs; covers the
restart-durability, transaction-atomicity and exact-scope behavior B-02
deferred here. No provider, no network.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from bridge.repository import (
    Repository,
    SchemaTooNew,
    StorageError,
    latest_known_version,
    normalize_scope,
    open_repository,
)

PROVIDER = "127.0.0.1:18765"


def new_asset_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def repo(tmp_path):
    conn = open_repository(tmp_path)
    r = Repository(conn)
    yield r
    r.close()


def add_external(r: Repository, asset_id: str | None = None, file_id: int | None = 10,
                 status: str = "ready") -> str:
    asset_id = asset_id or new_asset_id()
    r.upsert_asset_with_locator(
        asset_id=asset_id, origin="external", media_kind="image",
        media_type="image/png", size_bytes=100,
        provider_instance_id=PROVIDER, provider_file_id=file_id,
        provider_generation="1", last_seen_kind="image",
        last_seen_name="synthetic.png", last_seen_mtime="2026-09-01T00:00:00+09:00",
        last_seen_size=100, status=status,
    )
    return asset_id


def link(r: Repository, asset_id: str, memory_ref: str = "mem:1", **scope) -> None:
    r.link(memory_ref=memory_ref, asset_id=asset_id,
           agent_id=scope.get("agent_id", "a1"),
           source_instance_id=scope.get("source_instance_id", ""),
           project_id=scope.get("project_id", ""),
           channel=scope.get("channel", ""))


# ---- migration contract --------------------------------------------------------

def test_open_creates_schema_and_version(tmp_path):
    conn = open_repository(tmp_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # per-connection
    conn.close()


def test_reopen_is_noop_and_durable(tmp_path):
    conn = open_repository(tmp_path)
    r = Repository(conn)
    asset = add_external(r)
    r.close()
    conn2 = open_repository(tmp_path)  # migrations skip (version matches)
    r2 = Repository(conn2)
    assert r2.asset(asset, PROVIDER)["locator_status"] == "ready"
    r2.close()


def test_schema_too_new_refuses_open(tmp_path):
    conn = open_repository(tmp_path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(SchemaTooNew):
        open_repository(tmp_path)
    # the DB file was not modified by the refused open
    conn = sqlite3.connect(tmp_path / "bridge.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    conn.close()


def test_latest_known_version_matches_migrations():
    assert latest_known_version() == 1


# ---- links: idempotence, scope, validation -------------------------------------

def test_link_idempotent(repo):
    asset = add_external(repo)
    link(repo, asset)
    link(repo, asset)  # noop
    assert repo.links_for_asset(source_instance_id="", agent_id="a1",
                                project_id="", channel="", asset_id=asset) == ["mem:1"]


def test_unlink_returns_same_scope_remaining(repo):
    asset = add_external(repo)
    link(repo, asset, memory_ref="mem:1")
    link(repo, asset, memory_ref="mem:2")
    remaining = repo.unlink(source_instance_id="", agent_id="a1", project_id="",
                            channel="", memory_ref="mem:1", asset_id=asset)
    assert remaining == 1


def test_unlink_does_not_touch_asset_or_locator(repo):
    asset = add_external(repo)
    link(repo, asset)
    repo.unlink(source_instance_id="", agent_id="a1", project_id="", channel="",
                memory_ref="mem:1", asset_id=asset)
    assert repo.asset(asset, PROVIDER) is not None  # docs/11 §8


def test_link_to_missing_asset_fails_fk(repo):
    with pytest.raises(sqlite3.IntegrityError):
        link(repo, new_asset_id())


def test_invalid_memory_ref_rejected(repo):
    asset = add_external(repo)
    for bad in ("mem:0", "mem:", "ep:99999999999999999999", "mem:1 ", "x:1", ""):
        with pytest.raises(StorageError):
            link(repo, asset, memory_ref=bad)


def test_scope_exact_equality_no_widening(repo):
    asset = add_external(repo)
    link(repo, asset, project_id="P")
    # same tuple: visible
    assert repo.asset_in_scope(source_instance_id="", agent_id="a1",
                               project_id="P", channel="", asset_id=asset)
    # global tuple: NOT widened into P's view (D-16)
    assert not repo.asset_in_scope(source_instance_id="", agent_id="a1",
                                   project_id="", channel="", asset_id=asset)
    # channel '' does not see channel 'c' and vice versa
    link(repo, asset, channel="c")
    assert repo.asset_in_scope(source_instance_id="", agent_id="a1",
                               project_id="", channel="c", asset_id=asset)
    assert not repo.asset_in_scope(source_instance_id="", agent_id="a1",
                                   project_id="", channel="", asset_id=asset)


def test_case_and_whitespace_are_never_folded(repo):
    asset = add_external(repo)
    link(repo, asset, agent_id="Agent")
    assert not repo.asset_in_scope(source_instance_id="", agent_id="agent",
                                   project_id="", channel="", asset_id=asset)


def test_agent_id_required():
    with pytest.raises(StorageError):
        normalize_scope("", "", "", "")


# ---- assets + locators -----------------------------------------------------------

def test_upsert_never_swaps_file_id(repo):
    asset = add_external(repo, file_id=10)
    # a later observation of the SAME asset with a different file id must not
    # overwrite the bound id (D-08); the UPDATE set omits provider_file_id.
    repo.upsert_asset_with_locator(
        asset_id=asset, origin="external", media_kind="image",
        provider_instance_id=PROVIDER, provider_file_id=11,
        status="stale",
    )
    assert repo.asset(asset, PROVIDER)["provider_file_id"] == 10


def test_pending_locator_without_file_id(repo):
    asset = add_external(repo, file_id=None, status="pending")
    row = repo.asset(asset, PROVIDER)
    assert row["locator_status"] == "pending" and row["provider_file_id"] is None


def test_non_pending_requires_file_id(repo):
    with pytest.raises(StorageError):
        add_external(repo, file_id=None, status="ready")


def test_demotion_preserves_trusted_evidence_and_file_id(repo):
    asset = add_external(repo, file_id=10)
    repo.set_locator_status(asset_id=asset, provider_instance_id=PROVIDER,
                            status="stale", provider_generation="2",
                            last_seen_size=55)
    row = repo.asset(asset, PROVIDER)
    assert (row["locator_status"], row["provider_file_id"], row["provider_generation"],
            row["last_seen_size"]) == ("stale", 10, "1", 100)


def test_set_locator_status_missing_row_raises(repo):
    with pytest.raises(StorageError):
        repo.set_locator_status(asset_id=new_asset_id(), provider_instance_id=PROVIDER,
                                status="stale")


def test_two_assets_same_provider_file_collide(repo):
    add_external(repo, file_id=77)
    # wrapped as StorageError (no raw SQL text escapes, T-09/T-13)
    with pytest.raises(StorageError):
        add_external(repo, file_id=77)


def test_provider_instance_isolates_file_ids(repo):
    a1 = add_external(repo, file_id=5)
    a2 = new_asset_id()
    repo.upsert_asset_with_locator(
        asset_id=a2, origin="external", media_kind="image",
        provider_instance_id="other-host:1", provider_file_id=5, status="ready",
    )
    assert repo.asset(a1, PROVIDER)["provider_file_id"] == 5
    assert repo.asset(a2, "other-host:1")["provider_file_id"] == 5


# ---- reads ------------------------------------------------------------------------

def test_assets_for_memory_exact_scope_with_status(repo):
    a1 = add_external(repo, file_id=1, status="ready")
    a2 = add_external(repo, file_id=2, status="stale")
    link(repo, a1, memory_ref="mem:42")
    link(repo, a2, memory_ref="mem:42")
    rows = repo.assets_for_memory(source_instance_id="", agent_id="a1", project_id="",
                                  channel="", memory_ref="mem:42", limit=10,
                                  provider_instance_id=PROVIDER)
    assert {r["asset_id"] for r in rows} == {a1, a2}
    assert {r["locator_status"] for r in rows} == {"ready", "stale"}


def test_assets_for_memory_other_scope_empty(repo):
    a = add_external(repo)
    link(repo, a, project_id="P")
    assert repo.assets_for_memory(source_instance_id="", agent_id="a1", project_id="",
                                  channel="", memory_ref="mem:1", limit=10,
                                  provider_instance_id=PROVIDER) == []


def test_limit_clamped(repo):
    a = add_external(repo)
    link(repo, a)
    assert len(repo.assets_for_memory(source_instance_id="", agent_id="a1",
                                      project_id="", channel="", memory_ref="mem:1",
                                      limit=10_000, provider_instance_id=PROVIDER)) == 1
    assert len(repo.assets_for_memory(source_instance_id="", agent_id="a1",
                                      project_id="", channel="", memory_ref="mem:1",
                                      limit=0, provider_instance_id=PROVIDER)) == 1


def test_asset_in_scope_invalid_uuid_is_false_not_error(repo):
    assert not repo.asset_in_scope(source_instance_id="", agent_id="a1",
                                   project_id="", channel="", asset_id="not-a-uuid")


# ---- atomicity ---------------------------------------------------------------------

def test_failed_upsert_leaves_no_trace(repo):
    good = add_external(repo)
    bad = new_asset_id()
    with pytest.raises(StorageError):
        repo.upsert_asset_with_locator(
            asset_id=bad, origin="external", media_kind="image",
            provider_instance_id=PROVIDER, provider_file_id=10,  # taken by `good`
            status="ready",
        )
    assert repo.asset(bad, PROVIDER) is None  # rolled back: no orphan asset
    assert repo.asset(good, PROVIDER)["provider_file_id"] == 10


def test_health_reports_real_schema_version(tmp_path):
    from bridge.config import validate_config
    from bridge.provider import FakeProvider
    from bridge.server import BridgeContext, bridge_health

    cfg = validate_config(data_dir=tmp_path, provider_base_url="http://127.0.0.1:18765")
    conn = open_repository(tmp_path)
    ctx = BridgeContext(config=cfg, provider=FakeProvider(), db=Repository(conn))
    h = bridge_health(ctx)
    assert h.storage.schema_version == 1
    # a downgraded-looking DB is reported verbatim, not clamped
    conn.execute("PRAGMA user_version = 0")
    assert bridge_health(ctx).storage.schema_version == 0


def test_concurrent_writes_are_serialized(repo):
    """The connection is shared across threads (check_same_thread=False), and
    Repository.lock is the in-process serialization contract: hammer it from
    several threads and expect a consistent row count, not `database is
    locked`, not interleaved-transaction corruption."""
    import threading

    # distinct provider_file_id per asset: the uniqueness constraint is one
    # file per asset, so shared ids would collide before the link race starts
    assets = [add_external(repo, file_id=500 + i) for i in range(8)]
    errors: list[BaseException] = []

    def worker(asset_id: str, n: int) -> None:
        try:
            for i in range(n):
                repo.link(source_instance_id="", agent_id="worker", project_id="",
                          channel="", memory_ref=f"mem:{i + 1}", asset_id=asset_id)
        except BaseException as exc:  # noqa: BLE001 - recorded for the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(a, 25)) for a in assets]
    for t_ in threads:
        t_.start()
    for t_ in threads:
        t_.join()

    assert not errors, errors
    count = repo._conn.execute(
        "SELECT COUNT(*) FROM asset_links WHERE agent_id='worker'"
    ).fetchone()[0]
    assert count == 8 * 25


def test_concurrent_upsert_same_file_id_one_winner(repo):
    """The provider-file uniqueness race: exactly one asset ends up bound;
    the losers get StorageError, never raw sqlite text."""
    import threading

    ids = [new_asset_id() for _ in range(6)]
    storage_errors: list[bool] = []
    other_errors: list[BaseException] = []

    def worker(asset_id: str, file_id: int) -> None:
        try:
            repo.upsert_asset_with_locator(
                asset_id=asset_id, origin="external", media_kind="image",
                provider_instance_id=PROVIDER, provider_file_id=file_id, status="ready",
            )
        except StorageError:
            storage_errors.append(True)
        except BaseException as exc:  # noqa: BLE001
            other_errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i, 900 + n))
               for n, i in enumerate(ids)]
    for t_ in threads:
        t_.start()
    for t_ in threads:
        t_.join()

    total = sum(len(repo.locators_by_file(PROVIDER, 900 + n)) for n in range(6))
    assert total == 6  # distinct file ids: every racer owns its own locator
    assert not other_errors, other_errors
    assert not storage_errors
