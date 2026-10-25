"""LS-01: executable model of the provider scenarios audited in docs/13.

A pure simulator of the *pinned* local-search ledger behavior (rebuild updates
rows in place; moves keep ids; delete removes; a re-added path or rowid reuse
yields an id that may resolve to a different file), plus the Bridge-side
revalidation decision (docs/11 §5 evidence rules) over temporary SQLite using
the real v1 schema. Synthetic data only; no provider is contacted.

This is a regression model of the audit conclusions, not proof the provider
behaves this way: the source-verified basis is docs/13 and B-13 exercises the
real three-process path.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0001_v1.sql"
PROVIDER = "127.0.0.1:18765"


@dataclass
class LedgerFile:
    file_id: int
    kind: str
    size: int
    mtime: str
    rel_dir: str
    name: str
    status: str = "indexed"
    available: bool = True


class FakeLedger:
    """Models the pinned ledger semantics (docs/13 Q1)."""

    def __init__(self):
        self.rows: dict[int, LedgerFile] = {}
        self._next_id = 1
        self.generation = "1"

    def add(self, kind="image", size=100, mtime="t1", rel_dir="d", name="f.png") -> int:
        row = LedgerFile(self._next_id, kind, size, mtime, rel_dir, name)
        self.rows[row.file_id] = row
        self._next_id += 1
        return row.file_id

    def rebuild(self):
        """Generation switch preserves ids and rows (copy_building_to_files)."""
        self.generation = str(int(self.generation) + 1)

    def move(self, file_id, new_rel_dir, new_name):
        r = self.rows[file_id]
        self.rows[file_id] = LedgerFile(file_id, r.kind, r.size, r.mtime, new_rel_dir, new_name)

    def delete(self, file_id):
        del self.rows[file_id]

    def readd_same_path(self, kind="image", size=999, mtime="t2"):
        """Same path, new row (new id; may reuse a freed rowid)."""
        return self.add(kind=kind, size=size, mtime=mtime)

    def reuse_id_on_different_file(self, file_id, size=12345):
        """Rowid-reuse hazard: the numeric id now names a different file."""
        self.rows[file_id] = LedgerFile(file_id, "image", size, "t9", "other", "evil.png")

    def disconnect(self, file_id):
        self.rows[file_id] = LedgerFile(file_id, self.rows[file_id].kind,
                                        self.rows[file_id].size, self.rows[file_id].mtime,
                                        self.rows[file_id].rel_dir, self.rows[file_id].name,
                                        status="missing", available=False)

    def reconnect(self, file_id):
        r = self.rows[file_id]
        self.rows[file_id] = LedgerFile(file_id, r.kind, r.size, r.mtime, r.rel_dir, r.name)

    def detail(self, file_id) -> LedgerFile | None:
        return self.rows.get(file_id)


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "b.db")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def bind(db, provider: FakeLedger, file_id: int):
    evidence = provider.detail(file_id)
    asset = str(uuid.uuid4())
    now = "2026-10-26T00:00:00Z"
    db.execute(
        "INSERT INTO assets (asset_id, origin, media_kind, created_at, updated_at, size_bytes)"
        " VALUES (?, 'external', ?, ?, ?, ?)",
        (asset, evidence.kind, now, now, evidence.size),
    )
    db.execute(
        "INSERT INTO provider_locators (asset_id, provider_instance_id, provider_file_id,"
        " provider_generation, last_seen_kind, last_seen_name, last_seen_mtime,"
        " last_seen_size, status, verified_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)",
        (asset, PROVIDER, file_id, provider.generation,
         evidence.kind, evidence.name, evidence.mtime, evidence.size, now),
    )
    return asset


def revalidate(db, provider: FakeLedger, asset: str) -> str:
    """Docs/11 §5 evidence rules; returns the new status (and persists it)."""
    row = db.execute(
        "SELECT provider_file_id, last_seen_kind, last_seen_size, last_seen_mtime,"
        " provider_generation FROM provider_locators WHERE asset_id=? AND provider_instance_id=?",
        (asset, PROVIDER),
    ).fetchone()
    file_id, kind, size, mtime, gen = row
    detail = provider.detail(file_id)
    if detail is None:
        status = "missing"
    elif not detail.available or detail.status == "missing":
        status = "missing"
    elif detail.kind != kind or detail.size != size:
        status = "stale"
    elif provider.generation != gen and detail.mtime != mtime:
        status = "stale"
    else:
        status = "ready"
    db.execute(
        "UPDATE provider_locators SET status=?, provider_generation=?,"
        " last_seen_mtime=?, verified_at='2026-10-26T01:00:00Z'"
        " WHERE asset_id=? AND provider_instance_id=?",
        (status, provider.generation, detail.mtime if detail else None, asset, PROVIDER),
    )
    return status


# ---- scenario table from docs/13 ----------------------------------------------

def test_rebuild_keeps_locator_ready(db):
    p = FakeLedger()
    fid = p.add()
    asset = bind(db, p, fid)
    p.rebuild()
    assert revalidate(db, p, asset) == "ready"


def test_move_rename_keeps_id_and_ready(db):
    p = FakeLedger()
    fid = p.add()
    asset = bind(db, p, fid)
    p.move(fid, "new/dir", "renamed.png")
    assert revalidate(db, p, asset) == "ready"  # same row id, size, mtime


def test_disconnect_marks_missing_reconnect_restores(db):
    p = FakeLedger()
    fid = p.add()
    asset = bind(db, p, fid)
    p.disconnect(fid)
    assert revalidate(db, p, asset) == "missing"
    p.reconnect(fid)
    assert revalidate(db, p, asset) == "ready"


def test_delete_marks_missing_and_link_survives(db):
    p = FakeLedger()
    fid = p.add()
    asset = bind(db, p, fid)
    db.execute(
        "INSERT INTO asset_links (source_instance_id, agent_id, project_id, channel,"
        " memory_ref, asset_id, created_at) VALUES ('', 'a', '', '', 'mem:1', ?, ?)",
        (asset, "2026-10-26T00:00:00Z"),
    )
    p.delete(fid)
    assert revalidate(db, p, asset) == "missing"
    # docs/03 §5: association preserved for reconciliation, never dropped:
    assert db.execute("SELECT COUNT(*) FROM asset_links").fetchone()[0] == 1


def test_readded_path_gets_new_id_old_stays_missing(db):
    p = FakeLedger()
    fid = p.add()
    asset = bind(db, p, fid)
    p.delete(fid)
    new_fid = p.readd_same_path()
    assert new_fid != fid
    assert revalidate(db, p, asset) == "missing"


def test_rowid_reuse_never_rebinds_silently(db):
    """The hazard case: stale numeric id resolves to a DIFFERENT file.
    Evidence (size mismatch) marks stale; the locator's file_id is untouched,
    so no other image is ever served under this asset."""
    p = FakeLedger()
    fid = p.add(size=100)
    asset = bind(db, p, fid)
    p.reuse_id_on_different_file(fid, size=12345)
    assert revalidate(db, p, asset) == "stale"
    stored = db.execute(
        "SELECT provider_file_id, status FROM provider_locators WHERE asset_id=?", (asset,)
    ).fetchone()
    assert stored == (fid, "stale")  # never swapped to a new binding


def test_inplace_replace_same_size_same_mtime_is_accepted_residual_risk(db):
    """docs/13 Q1 caveat: cp -p style replacement is indistinguishable by the
    evidence set. This test PINS the known limitation (not a bug claim):
    byte-level confirmation needs an explicit fetch; no auto-detection exists."""
    p = FakeLedger()
    fid = p.add(size=100, mtime="t1")
    asset = bind(db, p, fid)
    r = p.rows[fid]
    p.rows[fid] = LedgerFile(fid, r.kind, r.size, r.mtime, r.rel_dir, r.name)  # bytes changed, evidence same
    assert revalidate(db, p, asset) == "ready"  # accepted residual risk, documented


def test_generation_change_alone_with_matching_evidence_is_ready(db):
    """A rebuild with unchanged files: generation moves, evidence matches.
    Status stays ready; provider_generation is updated (never auto-staled)."""
    p = FakeLedger()
    fid = p.add()
    asset = bind(db, p, fid)
    p.rebuild()
    assert revalidate(db, p, asset) == "ready"
    assert db.execute(
        "SELECT provider_generation FROM provider_locators WHERE asset_id=?", (asset,)
    ).fetchone()[0] == "2"
