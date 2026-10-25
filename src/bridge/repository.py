"""Bridge SQLite repository (B-05, docs/11 §7–§8).

- one connection per Bridge process; `PRAGMA foreign_keys=ON` per connection
  (SQLite defaults it OFF — docs/11 §7);
- migrations applied in filename order, `PRAGMA user_version` set per script;
  a NEWER stored version than the code knows refuses to open (fail-closed);
- scope reads/writes are exact-equality over the four normalized strings
  (D-16); every query binds parameters — no interpolation, ever (T-13);
- every write is one transaction; callers get atomicity or an exception;
- Concurrency contract: the connection is shared across the MCP SDK's worker
  threads (open_repository sets check_same_thread=False), so EVERY public
  read/write takes `self.lock` (an RLock) as its first statement. New public
  methods must start with `with self.lock:`;
  tests/unit/test_repository.py::test_all_public_methods_take_the_lock audits
  this mechanically.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

MEMORY_REF_RE = re.compile(r"^(mem|ep):[1-9][0-9]{0,17}$")
ASSET_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
LOCATOR_STATUSES = ("pending", "ready", "stale", "missing", "incompatible")

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_VERSION_FILE_RE = re.compile(r"^(\d{4})_")


class StorageError(Exception):
    """Repository-level failure; carries no paths or SQL text."""


class SchemaTooNew(StorageError):
    """Stored user_version exceeds what this code knows: refuse to open."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_scope(source_instance_id, agent_id, project_id, channel) -> tuple[str, str, str, str]:
    """D-16: None -> "" only; no case/trim/Unicode folding. agent_id required."""
    out = ["" if v is None else v for v in (source_instance_id, agent_id, project_id, channel)]
    source, agent, project, chan = out
    if not agent:
        raise StorageError("agent_id is required for every scope")
    return (source, agent, project, chan)


def migration_files(directory: Path = _MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    out = []
    for p in sorted(directory.glob("*.sql")):
        m = _VERSION_FILE_RE.match(p.name)
        if not m:
            raise StorageError("migration filename must start with a 4-digit version")
        out.append((int(m.group(1)), p))
    versions = [v for v, _ in out]
    if versions != sorted(set(versions)):
        raise StorageError("duplicate migration versions")
    return out


def latest_known_version(directory: Path = _MIGRATIONS_DIR) -> int:
    files = migration_files(directory)
    return files[-1][0] if files else 0


def open_repository(data_dir: Path, migrations_dir: Path = _MIGRATIONS_DIR) -> sqlite3.Connection:
    """Open (creating if needed) the Bridge DB with the fail-closed migration
    contract of docs/11 §7. foreign_keys is set per connection (SQLite's own
    default is OFF); check_same_thread is off because the SDK runs sync tools
    in a worker thread — Repository.lock serializes actual use."""
    db_path = Path(data_dir) / "bridge.db"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    known = latest_known_version(migrations_dir)
    if current > known:
        conn.close()
        raise SchemaTooNew("Bridge database schema is newer than this Bridge version")
    for version, path in migration_files(migrations_dir):
        if version <= current:
            continue
        with conn:  # one transaction per migration script
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version = {version}")
    return conn


_ASSET_KEYS = ("asset_id", "origin", "media_kind", "content_sha256",
               "relative_managed_path", "media_type", "size_bytes",
               "created_at", "updated_at")
_LOCATOR_KEYS = ("provider_file_id", "provider_generation", "provider_folder_id",
                 "last_seen_kind", "last_seen_name", "last_seen_mtime",
                 "last_seen_size", "locator_status", "verified_at")


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.lock = threading.RLock()

    def close(self) -> None:
        with self.lock:
            self._conn.close()

    def schema_version(self) -> int:
        """Stored user_version, for health reporting without exposing _conn."""
        with self.lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    # ---- links (exact-scope only, docs/11 §8) --------------------------------

    def link(
        self,
        *,
        source_instance_id: str,
        agent_id: str,
        project_id: str,
        channel: str,
        memory_ref: str,
        asset_id: str,
    ) -> None:
        with self.lock:
            scope = normalize_scope(source_instance_id, agent_id, project_id, channel)
            if not MEMORY_REF_RE.match(memory_ref):
                raise StorageError("invalid memory_ref")
            if not ASSET_ID_RE.match(asset_id):
                raise StorageError("invalid asset_id")
            # Idempotent insert-or-noop (docs/11 §8). The asset must EXIST in
            # this DB (FK enforced per-connection); links never touch locators.
            with self._conn:
                self._conn.execute(
                    "INSERT INTO asset_links (source_instance_id, agent_id,"
                    " project_id, channel, memory_ref, asset_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (source_instance_id, agent_id, project_id,"
                    " channel, memory_ref, asset_id) DO NOTHING",
                    (*scope, memory_ref, asset_id, utc_now()),
                )

    def unlink(
        self,
        *,
        source_instance_id: str,
        agent_id: str,
        project_id: str,
        channel: str,
        memory_ref: str,
        asset_id: str,
    ) -> int:
        """Delete the link row only; returns remaining links for the asset
        within the SAME exact scope (docs/11 §8)."""
        with self.lock:
            scope = normalize_scope(source_instance_id, agent_id, project_id, channel)
            if not MEMORY_REF_RE.match(memory_ref):
                raise StorageError("invalid memory_ref")
            if not ASSET_ID_RE.match(asset_id):
                raise StorageError("invalid asset_id")
            with self._conn:
                self._conn.execute(
                    "DELETE FROM asset_links WHERE source_instance_id=? AND agent_id=?"
                    " AND project_id=? AND channel=? AND memory_ref=? AND asset_id=?",
                    (*scope, memory_ref, asset_id),
                )
            row = self._conn.execute(
                "SELECT COUNT(*) FROM asset_links WHERE source_instance_id=?"
                " AND agent_id=? AND project_id=? AND channel=? AND asset_id=?",
                (*scope, asset_id),
            ).fetchone()
            return int(row[0])

    # ---- reads ---------------------------------------------------------------

    def assets_for_memory(
        self,
        *,
        source_instance_id: str,
        agent_id: str,
        project_id: str,
        channel: str,
        memory_ref: str,
        limit: int,
        provider_instance_id: str,
    ) -> list[dict]:
        with self.lock:
            scope = normalize_scope(source_instance_id, agent_id, project_id, channel)
            rows = self._conn.execute(
                "SELECT a.asset_id, a.origin, a.media_kind, a.media_type, a.size_bytes,"
                " l.status, l.provider_file_id, l.provider_generation, l.last_seen_name,"
                " l.last_seen_mtime, l.last_seen_size, l.verified_at, k.created_at"
                " FROM asset_links k"
                " JOIN assets a ON a.asset_id = k.asset_id"
                " LEFT JOIN provider_locators l"
                "   ON l.asset_id = a.asset_id AND l.provider_instance_id = ?"
                " WHERE k.source_instance_id=? AND k.agent_id=? AND k.project_id=?"
                " AND k.channel=? AND k.memory_ref=?"
                " ORDER BY k.rowid DESC LIMIT ?",
                (provider_instance_id, *scope, memory_ref,
                 max(1, min(int(limit), 100))),
            ).fetchall()
            keys = ("asset_id", "origin", "media_kind", "media_type", "size_bytes",
                    "locator_status", "provider_file_id", "provider_generation",
                    "last_seen_name", "last_seen_mtime", "last_seen_size",
                    "verified_at", "linked_at")
            return [dict(zip(keys, r)) for r in rows]

    def links_for_asset(
        self,
        *,
        source_instance_id: str,
        agent_id: str,
        project_id: str,
        channel: str,
        asset_id: str,
    ) -> list[str]:
        with self.lock:
            scope = normalize_scope(source_instance_id, agent_id, project_id, channel)
            rows = self._conn.execute(
                "SELECT memory_ref FROM asset_links WHERE source_instance_id=?"
                " AND agent_id=? AND project_id=? AND channel=? AND asset_id=?"
                " ORDER BY rowid",
                (*scope, asset_id),
            ).fetchall()
            return [r[0] for r in rows]

    def asset(self, asset_id: str, provider_instance_id: str) -> dict | None:
        with self.lock:
            if not ASSET_ID_RE.match(asset_id):
                return None
            row = self._conn.execute(
                "SELECT asset_id, origin, media_kind, content_sha256,"
                " relative_managed_path, media_type, size_bytes, created_at,"
                " updated_at FROM assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                return None
            out = dict(zip(_ASSET_KEYS, row))
            loc = self._conn.execute(
                "SELECT provider_file_id, provider_generation, provider_folder_id,"
                " last_seen_kind, last_seen_name, last_seen_mtime, last_seen_size,"
                " status, verified_at FROM provider_locators"
                " WHERE asset_id = ? AND provider_instance_id = ?",
                (asset_id, provider_instance_id),
            ).fetchone()
            if loc is not None:
                out.update(dict(zip(_LOCATOR_KEYS, loc)))
            else:
                out.update({k: None for k in _LOCATOR_KEYS})
            return out

    def asset_in_scope(
        self,
        *,
        source_instance_id: str,
        agent_id: str,
        project_id: str,
        channel: str,
        asset_id: str,
    ) -> bool:
        """Exact-scope membership test used by get_asset before returning any
        locator evidence (T-01/T-02): an asset that exists but lives in another
        scope answers exactly like an unknown id."""
        with self.lock:
            scope = normalize_scope(source_instance_id, agent_id, project_id, channel)
            if not ASSET_ID_RE.match(asset_id):
                return False
            row = self._conn.execute(
                "SELECT 1 FROM asset_links WHERE source_instance_id=? AND agent_id=?"
                " AND project_id=? AND channel=? AND asset_id=? LIMIT 1",
                (*scope, asset_id),
            ).fetchone()
            return row is not None

    def locator_evidence_for_file(
        self, provider_instance_id: str, provider_file_id: int
    ) -> list[dict]:
        """Full evidence rows for a provider file binding (B-10 source-staleness
        check, B-12 reconciliation). asset_id + last_seen_* only — never paths."""
        with self.lock:
            rows = self._conn.execute(
                "SELECT asset_id, last_seen_kind, last_seen_name,"
                " last_seen_mtime, last_seen_size, status,"
                " provider_generation, provider_folder_id"
                " FROM provider_locators WHERE provider_instance_id=?"
                " AND provider_file_id=?",
                (provider_instance_id, int(provider_file_id)),
            ).fetchall()
        return [
            {"asset_id": r[0], "last_seen_kind": r[1], "last_seen_name": r[2],
             "last_seen_mtime": r[3], "last_seen_size": r[4], "status": r[5],
             "provider_generation": r[6], "provider_folder_id": r[7]}
            for r in rows
        ]

    def locators_by_file(
        self, provider_instance_id: str, provider_file_id: int
    ) -> list[str]:
        with self.lock:
            rows = self._conn.execute(
                "SELECT asset_id FROM provider_locators WHERE provider_instance_id=?"
                " AND provider_file_id=?",
                (provider_instance_id, int(provider_file_id)),
            ).fetchall()
            return [r[0] for r in rows]

    # ---- assets + locators (one transaction, docs/11 §8) ----------------------

    def upsert_asset_with_locator(
        self,
        *,
        asset_id: str,
        origin: str,
        media_kind: str,
        media_type: str | None = None,
        size_bytes: int | None = None,
        relative_managed_path: str | None = None,
        provider_instance_id: str,
        provider_file_id: int | None,
        provider_generation: str | None = None,
        provider_folder_id: int | None = None,
        last_seen_kind: str | None = None,
        last_seen_name: str | None = None,
        last_seen_mtime: str | None = None,
        last_seen_size: int | None = None,
        status: str,
    ) -> None:
        with self.lock:
            if not ASSET_ID_RE.match(asset_id):
                raise StorageError("invalid asset_id")
            if status not in LOCATOR_STATUSES:
                raise StorageError("invalid locator status")
            if provider_file_id is None and status != "pending":
                raise StorageError("locator without provider_file_id must be pending")
            now = utc_now()
            # Integrity conflicts (a provider file already bound to another
            # asset) become StorageError so callers never see raw SQL text; the
            # `with self._conn` transaction rolls back entirely, leaving no
            # orphan asset row.
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO assets (asset_id, origin, media_kind,"
                        " media_type, size_bytes, relative_managed_path,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT (asset_id) DO UPDATE SET"
                        " updated_at=excluded.updated_at,"
                        " size_bytes=COALESCE(excluded.size_bytes,"
                        " assets.size_bytes),"
                        " media_type=COALESCE(excluded.media_type,"
                        " assets.media_type)",
                        (asset_id, origin, media_kind, media_type, size_bytes,
                         relative_managed_path, now, now),
                    )
                    if provider_file_id is not None:
                        # provider_file_id NEVER changes on conflict (D-08):
                        # the UPDATE set deliberately omits it.
                        self._conn.execute(
                            "INSERT INTO provider_locators (asset_id,"
                            " provider_instance_id, provider_file_id,"
                            " provider_generation, provider_folder_id,"
                            " last_seen_kind, last_seen_name, last_seen_mtime,"
                            " last_seen_size, status, verified_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                            " ON CONFLICT (asset_id, provider_instance_id)"
                            " DO UPDATE SET"
                            " provider_generation=excluded.provider_generation,"
                            " provider_folder_id=COALESCE("
                            " excluded.provider_folder_id,"
                            " provider_locators.provider_folder_id),"
                            " last_seen_kind=COALESCE(excluded.last_seen_kind,"
                            " provider_locators.last_seen_kind),"
                            " last_seen_name=COALESCE(excluded.last_seen_name,"
                            " provider_locators.last_seen_name),"
                            " last_seen_mtime=COALESCE(excluded.last_seen_mtime,"
                            " provider_locators.last_seen_mtime),"
                            " last_seen_size=COALESCE(excluded.last_seen_size,"
                            " provider_locators.last_seen_size),"
                            " status=excluded.status,"
                            " verified_at=excluded.verified_at",
                            (asset_id, provider_instance_id, provider_file_id,
                             provider_generation, provider_folder_id,
                             last_seen_kind, last_seen_name, last_seen_mtime,
                             last_seen_size, status, now),
                        )
                    else:
                        # B-10: a managed import creates a pending locator with
                        # provider_file_id NULL but folder evidence set (the
                        # folder the copy landed in). Evidence columns update on
                        # conflict; provider_file_id still does not exist here
                        # at all (docs/11 §3: NULL only while pending).
                        self._conn.execute(
                            "INSERT INTO provider_locators (asset_id,"
                            " provider_instance_id, provider_generation,"
                            " provider_folder_id, last_seen_kind,"
                            " last_seen_name, last_seen_size, status,"
                            " verified_at)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                            " ON CONFLICT (asset_id, provider_instance_id)"
                            " DO UPDATE SET"
                            " provider_generation=excluded.provider_generation,"
                            " provider_folder_id=COALESCE("
                            " excluded.provider_folder_id,"
                            " provider_locators.provider_folder_id),"
                            " last_seen_kind=COALESCE(excluded.last_seen_kind,"
                            " provider_locators.last_seen_kind),"
                            " last_seen_name=excluded.last_seen_name,"
                            " last_seen_size=COALESCE(excluded.last_seen_size,"
                            " provider_locators.last_seen_size),"
                            " status=excluded.status,"
                            " verified_at=excluded.verified_at",
                            (asset_id, provider_instance_id, provider_generation,
                             provider_folder_id, last_seen_kind, last_seen_name,
                             last_seen_size, status, now),
                        )
            except sqlite3.Error:
                raise StorageError(
                    "asset/locator upsert violated a constraint"
                ) from None

    def set_asset_digest(self, *, asset_id: str, content_sha256: str) -> None:
        """Records the digest of bytes the Bridge itself verified via an
        explicit fetch (docs/11 §5: a hash exists ONLY after explicit fetch,
        never acquired implicitly). B-10 import uses it; nothing else may."""
        with self.lock:
            if not re.match(r"^[0-9a-f]{64}$", content_sha256):
                raise StorageError("invalid digest")
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE assets SET content_sha256=?, updated_at=?"
                    " WHERE asset_id=?",
                    (content_sha256, utc_now(), asset_id),
                )
                if cur.rowcount == 0:
                    raise StorageError("no such asset")

    def folder_pending_assets(self, provider_instance_id: str,
                              folder_id: int, limit: int = 100) -> list[dict]:
        """Pending locators awaiting file_id resolution inside a provider
        folder (B-11 resolution loop over B-10 imports)."""
        with self.lock:
            rows = self._conn.execute(
                "SELECT l.asset_id, a.relative_managed_path, a.media_type,"
                " a.size_bytes, a.content_sha256"
                " FROM provider_locators l JOIN assets a ON a.asset_id=l.asset_id"
                " WHERE l.provider_instance_id=? AND l.status='pending'"
                " AND l.provider_folder_id=? AND l.provider_file_id IS NULL"
                " ORDER BY l.rowid LIMIT ?",
                (provider_instance_id, int(folder_id), max(1, int(limit))),
            ).fetchall()
        return [
            {"asset_id": r[0], "relative_managed_path": r[1],
             "media_type": r[2], "size_bytes": r[3], "content_sha256": r[4]}
            for r in rows
        ]

    def sweep_locators(self, provider_instance_id: str,
                       statuses: tuple[str, ...] = ("ready", "stale",
                                                    "missing"),
                       limit: int = 200) -> list[dict]:
        """Locators due for revalidation (B-12 sweep input): every resolved
        locator in the given statuses with its stored evidence row."""
        with self.lock:
            marks = ",".join("?" for _ in statuses)
            rows = self._conn.execute(
                "SELECT l.asset_id, l.provider_file_id, l.status,"
                " l.last_seen_kind, l.last_seen_name, l.last_seen_mtime,"
                " l.last_seen_size, l.provider_generation"
                " FROM provider_locators l"
                " WHERE l.provider_instance_id=? AND l.status IN (" + marks + ")"
                " AND l.provider_file_id IS NOT NULL"
                " ORDER BY l.verified_at IS NULL, l.verified_at, l.rowid"
                " LIMIT ?",
                (provider_instance_id, *statuses, max(1, int(limit))),
            ).fetchall()
        return [
            {"asset_id": r[0],
             "evidence": {"provider_file_id": r[1], "status": r[2],
                          "last_seen_kind": r[3], "last_seen_name": r[4],
                          "last_seen_mtime": r[5], "last_seen_size": r[6],
                          "provider_generation": r[7]}}
            for r in rows
        ]

    def all_pending_locators(self, provider_instance_id: str,
                             limit: int = 200) -> list[dict]:
        """Every unresolved locator for this provider instance, linked or not
        (external links from B-07 can be pending; managed imports are pending
        until the watcher indexes the copy)."""
        with self.lock:
            rows = self._conn.execute(
                "SELECT l.asset_id, l.provider_file_id, l.provider_folder_id,"
                " l.last_seen_kind, l.last_seen_name, l.last_seen_mtime,"
                " l.last_seen_size, a.relative_managed_path"
                " FROM provider_locators l JOIN assets a ON a.asset_id=l.asset_id"
                " WHERE l.provider_instance_id=? AND l.status='pending'"
                " ORDER BY l.rowid LIMIT ?",
                (provider_instance_id, max(1, int(limit))),
            ).fetchall()
        return [
            {"asset_id": r[0], "provider_file_id": r[1],
             "provider_folder_id": r[2], "last_seen_kind": r[3],
             "last_seen_name": r[4], "last_seen_mtime": r[5],
             "last_seen_size": r[6], "relative_managed_path": r[7]}
            for r in rows
        ]

    def bind_pending_locator(
        self, *, asset_id: str, provider_instance_id: str,
        provider_file_id: int, provider_generation: str | None = None,
        last_seen_kind: str | None = None, last_seen_name: str | None = None,
        last_seen_mtime: str | None = None, last_seen_size: int | None = None,
    ) -> None:
        """Resolves a PENDING locator to a concrete provider file. This is the
        ONLY sanctioned write of provider_file_id; D-08 still forbids swapping
        the file of a RESOLVED locator (docs/11 §4). The partial unique index
        makes binding to an already-bound file an IntegrityError — never a
        silent double-bind."""
        with self.lock:
            if int(provider_file_id) <= 0:
                raise StorageError("invalid provider_file_id")
            now = utc_now()
            try:
                with self._conn:
                    cur = self._conn.execute(
                        "UPDATE provider_locators SET provider_file_id=?,"
                        " status='ready', provider_generation=COALESCE(?, provider_generation),"
                        " last_seen_kind=COALESCE(?, last_seen_kind),"
                        " last_seen_name=COALESCE(?, last_seen_name),"
                        " last_seen_mtime=COALESCE(?, last_seen_mtime),"
                        " last_seen_size=COALESCE(?, last_seen_size),"
                        " verified_at=?"
                        " WHERE asset_id=? AND provider_instance_id=?"
                        " AND status='pending' AND provider_file_id IS NULL",
                        (int(provider_file_id), provider_generation,
                         last_seen_kind, last_seen_name, last_seen_mtime,
                         last_seen_size, now, asset_id, provider_instance_id),
                    )
                    if cur.rowcount == 0:
                        raise StorageError(
                            "locator not pending (already resolved or gone)")
            except sqlite3.IntegrityError:
                raise StorageError("file already bound to another asset") from None

    def find_managed_asset_by_hash(self, content_sha256: str) -> dict | None:
        """Managed asset whose stored digest matches (content-addressed dedup,
        B-10). Exact 64-hex only — a malformed digest never matches."""
        with self.lock:
            if not re.match(r"^[0-9a-f]{64}$", content_sha256):
                return None
            cur = self._conn.execute(
                "SELECT asset_id, relative_managed_path, media_type, size_bytes"
                " FROM assets WHERE origin='managed' AND content_sha256 = ?",
                (content_sha256,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "asset_id": row[0], "relative_managed_path": row[1],
            "media_type": row[2], "size_bytes": row[3],
        }

    def locator_status(self, asset_id: str,
                       provider_instance_id: str) -> str | None:
        with self.lock:
            row = self._conn.execute(
                "SELECT status FROM provider_locators"
                " WHERE asset_id=? AND provider_instance_id=?",
                (asset_id, provider_instance_id),
            ).fetchone()
        return row[0] if row else None

    def set_locator_status(
        self,
        *,
        asset_id: str,
        provider_instance_id: str,
        status: str,
        provider_generation: str | None = None,
        last_seen_mtime: str | None = None,
        last_seen_size: int | None = None,
        last_seen_name: str | None = None,
    ) -> None:
        """Evidence update + status transition. NEVER touches provider_file_id
        (docs/11 §4: no transition may swap the file)."""
        with self.lock:
            if status not in LOCATOR_STATUSES:
                raise StorageError("invalid locator status")
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE provider_locators SET status=?,"
                    " provider_generation=COALESCE(?, provider_generation),"
                    " last_seen_mtime=COALESCE(?, last_seen_mtime),"
                    " last_seen_size=COALESCE(?, last_seen_size),"
                    " last_seen_name=COALESCE(?, last_seen_name),"
                    " verified_at=?"
                    " WHERE asset_id=? AND provider_instance_id=?",
                    (status, provider_generation, last_seen_mtime, last_seen_size,
                     last_seen_name, utc_now(), asset_id, provider_instance_id),
                )
                if cur.rowcount == 0:
                    raise StorageError("no such locator")
