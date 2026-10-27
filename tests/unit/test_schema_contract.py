"""B-02 schema contract tests: the frozen v1 DDL against a temporary SQLite DB.

M0 scope (docs/11 §9): this proves the *contract* (constraints, uniqueness,
scope isolation, fail-closed versioning, identifier patterns). B-05 re-runs
these through its migrator/repository and adds restart/backup/rollback tests.

No provider, no network, no CPersona data: temp files and synthetic rows only.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
ASSET_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
MEMORY_REF_RE = re.compile(r"^(mem|ep):[1-9][0-9]{0,17}$")


def migrated_db(tmp_path: Path) -> sqlite3.Connection:
    """Simulate the B-05 migrator: apply migrations in filename order in one
    transaction each, then set user_version. foreign_keys per connection, as
    the contract requires the application to do."""
    db = sqlite3.connect(tmp_path / "bridge.db")
    db.execute("PRAGMA foreign_keys = ON")
    for sql_file in [MIGRATIONS_DIR / "0001_v1.sql"]:
        script = sql_file.read_text(encoding="utf-8")
        db.executescript(script)
        version = int(re.search(r"user_version = (\d+)", script).group(1))
        db.execute(f"PRAGMA user_version = {version}")
    db.commit()
    return db


@pytest.fixture
def db(tmp_path):
    conn = migrated_db(tmp_path)
    yield conn
    conn.close()


def new_asset(conn, asset_id: str, origin="external", path=None):
    conn.execute(
        "INSERT INTO assets (asset_id, origin, media_kind, created_at, updated_at,"
        " relative_managed_path, size_bytes)"
        " VALUES (?, ?, 'image', '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z', ?, 10)",
        (asset_id, origin, path),
    )


def link(conn, scope, memory_ref, asset_id):
    s, a, p, c = scope
    conn.execute(
        "INSERT INTO asset_links (source_instance_id, agent_id, project_id, channel,"
        " memory_ref, asset_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (s, a, p, c, memory_ref, asset_id, "2026-10-26T00:00:00Z"),
    )


# ---- migrator basics ---------------------------------------------------------

def test_migration_sets_user_version(db):
    assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_tables_exist(db):
    names = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )
    }
    assert {"assets", "asset_links", "provider_locators"} <= names
    assert "uq_locator_provider_file" in names


# ---- identifier patterns -----------------------------------------------------

def test_asset_id_uuid4_canonical():
    for _ in range(5):
        assert ASSET_ID_RE.match(str(uuid.uuid4()))


def test_memory_ref_patterns():
    assert all(MEMORY_REF_RE.match(r) for r in ("mem:1", "ep:42", "mem:" + "9" * 18))
    for bad in ("", "mem:", "ep:0", "mem:01", "profile", "mem:-1", "x:1", "mem:1.0"):
        assert not MEMORY_REF_RE.match(bad), bad


# ---- link uniqueness and scope isolation -------------------------------------

def test_duplicate_identical_link_is_one_row(db):
    new_asset(db, str(uuid.uuid4()))
    asset = db.execute("SELECT asset_id FROM assets").fetchone()[0]
    scope = ("", "agent-a", "proj", "chan")
    link(db, scope, "mem:42", asset)
    with pytest.raises(sqlite3.IntegrityError):
        link(db, scope, "mem:42", asset)
    assert db.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 1


def test_same_asset_from_two_memories_and_two_scopes(db):
    asset = str(uuid.uuid4())
    new_asset(db, asset)
    link(db, ("", "agent-a", "p1", "c1"), "mem:42", asset)
    link(db, ("", "agent-a", "p1", "c1"), "mem:43", asset)
    link(db, ("", "agent-a", "p2", "c1"), "mem:42", asset)
    link(db, ("", "agent-b", "p1", "c1"), "mem:42", asset)
    assert db.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 4
    # exact-scope read never sees other scopes (B-08's join contract):
    rows = db.execute(
        "SELECT memory_ref FROM asset_links WHERE source_instance_id='' AND agent_id='agent-a'"
        " AND project_id='p1' AND channel='c1'",
    ).fetchall()
    assert sorted(r[0] for r in rows) == ["mem:42", "mem:43"]


def test_empty_scope_tuple_is_distinct_from_agent_only(db):
    asset = str(uuid.uuid4())
    new_asset(db, asset)
    link(db, ("", "", "", ""), "mem:1", asset)
    link(db, ("", "agent-a", "", ""), "mem:1", asset)
    assert db.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 2


def test_asset_fk_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        link(db, ("", "a", "", ""), "mem:1", str(uuid.uuid4()))


# ---- locator rules -----------------------------------------------------------

def locator(db, asset, provider="127.0.0.1:18765", file_id=None, status="pending"):
    conn_args = (asset, provider, file_id, status)
    db.execute(
        "INSERT INTO provider_locators (asset_id, provider_instance_id, provider_file_id,"
        " status) VALUES (?, ?, ?, ?)",
        conn_args,
    )


def test_pending_locators_with_null_file_id_coexist(db):
    a1, a2 = str(uuid.uuid4()), str(uuid.uuid4())
    new_asset(db, a1)
    new_asset(db, a2)
    locator(db, a1)
    locator(db, a2)
    assert db.execute("SELECT COUNT(*) FROM provider_locators").fetchone()[0] == 2


def test_two_resolved_locators_same_provider_file_collide(db):
    a1, a2 = str(uuid.uuid4()), str(uuid.uuid4())
    new_asset(db, a1)
    new_asset(db, a2)
    locator(db, a1, file_id=101, status="ready")
    with pytest.raises(sqlite3.IntegrityError):
        locator(db, a2, file_id=101, status="ready")


def test_provider_instance_isolates_file_ids(db):
    a1, a2 = str(uuid.uuid4()), str(uuid.uuid4())
    new_asset(db, a1)
    new_asset(db, a2)
    locator(db, a1, provider="127.0.0.1:18765", file_id=101, status="ready")
    locator(db, a2, provider="127.0.0.1:19999", file_id=101, status="ready")
    assert db.execute("SELECT COUNT(*) FROM provider_locators").fetchone()[0] == 2


def test_locator_status_enum_enforced(db):
    a = str(uuid.uuid4())
    new_asset(db, a)
    with pytest.raises(sqlite3.IntegrityError):
        locator(db, a, status="exploded")


def test_link_deletion_never_touches_asset_or_locator(db):
    # Contract §4: "delete a link" removes only the association row. The asset
    # and its provider locator must survive (D-02: unlinking never deletes the
    # user's original nor the Bridge metadata for a possible re-link).
    a = str(uuid.uuid4())
    new_asset(db, a)
    locator(db, a, file_id=7, status="ready")
    link(db, ("", "agent-a", "", ""), "mem:42", a)
    db.execute(
        "DELETE FROM asset_links WHERE agent_id='agent-a' AND memory_ref='mem:42'"
        " AND asset_id = ?",
        (a,),
    )
    assert db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM provider_locators").fetchone()[0] == 1


def test_asset_gc_cascades_locator_when_unlinked(db):
    # An asset with no remaining links may be garbage-collected (a separate
    # authorized operation); its locator row cascades with it.
    a = str(uuid.uuid4())
    new_asset(db, a)
    locator(db, a, file_id=8, status="ready")
    db.execute("DELETE FROM assets WHERE asset_id = ?", (a,))
    assert db.execute("SELECT COUNT(*) FROM provider_locators").fetchone()[0] == 0


def test_asset_with_live_link_cannot_be_silently_deleted(db):
    # FK RESTRICT default: a GC that ignores live links fails instead of
    # orphaning them.
    a = str(uuid.uuid4())
    new_asset(db, a)
    link(db, ("", "agent-a", "", ""), "mem:42", a)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM assets WHERE asset_id = ?", (a,))


# ---- CHECK constraints -------------------------------------------------------

def test_managed_requires_path_external_forbids(db):
    with pytest.raises(sqlite3.IntegrityError):
        new_asset(db, str(uuid.uuid4()), origin="managed", path=None)
    with pytest.raises(sqlite3.IntegrityError):
        new_asset(db, str(uuid.uuid4()), origin="external", path="x/y.png")


def test_negative_size_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO assets (asset_id, origin, media_kind, created_at, updated_at,"
            " size_bytes) VALUES (?, 'external', 'image', ?, ?, -1)",
            (str(uuid.uuid4()), "2026-10-26T00:00:00Z", "2026-10-26T00:00:00Z"),
        )


# ---- fail-closed versioning ----------------------------------------------------

def test_newer_user_version_refuses_open(tmp_path):
    db = migrated_db(tmp_path)
    db.execute("PRAGMA user_version = 99")
    db.commit()
    path = db.execute("PRAGMA database_list").fetchone()[2]
    db.close()
    # B-05 migrator rule: refuse when user_version > latest known (1 here).
    conn = sqlite3.connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    latest = max(
        int(re.search(r"user_version = (\d+)", f.read_text(encoding="utf-8")).group(1))
        for f in MIGRATIONS_DIR.glob("*.sql")
    )
    assert version > latest  # the refuse-open decision B-05 must implement
