"""Ownership and v1 upgrade use isolated temporary SQLite databases."""
import sqlite3
import uuid
from pathlib import Path

import pytest

from bridge.repository import Repository, StorageError, open_repository

SCOPE_A = ("", "a", "", "")
SCOPE_B = ("", "b", "", "")
DIGEST = "a" * 64


def create(repo, scope=SCOPE_A, request_id=None, digest=DIGEST):
    return repo.create_owned_image(scope=scope, asset_id=str(uuid.uuid4()), digest=digest,
        relative_path=f"{digest[:2]}/{digest}.png", media_type="image/png", size_bytes=42,
        provider_instance_id="test-provider", folder_id=1, request_id=request_id)


def test_ownership_and_idempotency_are_atomic_and_scope_bound(tmp_path):
    repo = Repository(open_repository(tmp_path))
    first = create(repo, request_id="one")
    assert first["status"] == "pending" and not first["deduplicated"]
    assert repo.owned_asset(scope=SCOPE_A, asset_id=first["asset_id"])
    assert not repo.owned_asset(scope=SCOPE_B, asset_id=first["asset_id"])
    assert create(repo, request_id="one") == first
    same = create(repo)
    assert same["asset_id"] == first["asset_id"] and same["deduplicated"]
    other = create(repo, scope=SCOPE_B, request_id="one")
    assert other["asset_id"] != first["asset_id"] and not other["deduplicated"]
    with pytest.raises(StorageError):
        create(repo, request_id="one", digest="b" * 64)
    assert create(repo, request_id="one") == first
    repo.close()


def test_v1_upgrade_preserves_existing_links_and_locators(tmp_path):
    db = sqlite3.connect(tmp_path / "bridge.db")
    db.executescript((Path(__file__).resolve().parents[2] / "migrations/0001_v1.sql").read_text())
    asset = str(uuid.uuid4())
    db.execute("INSERT INTO assets(asset_id,origin,media_kind,created_at,updated_at) VALUES(?, 'external', 'image', 'x', 'x')", (asset,))
    db.execute("INSERT INTO provider_locators(asset_id,provider_instance_id,provider_file_id,status) VALUES(?, 'p', 8, 'ready')", (asset,))
    db.execute("INSERT INTO asset_links(source_instance_id,agent_id,project_id,channel,memory_ref,asset_id,created_at) VALUES('', 'a', '', '', 'mem:1', ?, 'x')", (asset,))
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    repo = Repository(open_repository(tmp_path))
    assert repo.schema_version() == 2
    assert repo.asset(asset, "p")["provider_file_id"] == 8
    assert repo.asset_in_scope(source_instance_id="", agent_id="a", project_id="", channel="", asset_id=asset)
    assert not repo.owned_asset(scope=SCOPE_A, asset_id=asset)
    repo.close()


def test_shared_locator_only_accepts_matching_managed_content(tmp_path):
    repo = Repository(open_repository(tmp_path))
    a = create(repo, scope=SCOPE_A)["asset_id"]
    b = create(repo, scope=SCOPE_B)["asset_id"]
    c = create(repo, scope=("", "c", "", ""), digest="b" * 64)["asset_id"]
    common = dict(provider_instance_id="test-provider", provider_file_id=12,
                  last_seen_kind="image", last_seen_name="fixture.png",
                  last_seen_mtime="2026-01-01T00:00:00Z", last_seen_size=42)
    repo.bind_pending_locator(asset_id=a, **common)
    repo.bind_pending_locator(asset_id=b, **common)
    with pytest.raises(StorageError):
        repo.bind_pending_locator(asset_id=c, **common)
    assert repo.locator_status(c, "test-provider") == "pending"
    repo.close()


def test_restore_v1_backup_and_old_binary_rejects_v2(tmp_path):
    v1_dir = tmp_path / "v1_migrations"
    v1_dir.mkdir()
    source = Path(__file__).resolve().parents[2] / "migrations/0001_v1.sql"
    (v1_dir / source.name).write_text(source.read_text())
    conn = open_repository(tmp_path, migrations_dir=v1_dir)
    backup = sqlite3.connect(tmp_path / "backup.db")
    conn.backup(backup)
    backup.close()
    conn.close()
    upgraded = open_repository(tmp_path)
    upgraded.close()
    from bridge.repository import SchemaTooNew
    with pytest.raises(SchemaTooNew):
        open_repository(tmp_path, migrations_dir=v1_dir)
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    restored = sqlite3.connect(restored_dir / "bridge.db")
    backup = sqlite3.connect(tmp_path / "backup.db")
    backup.backup(restored)
    restored.close()
    backup.close()
    conn = open_repository(restored_dir, migrations_dir=v1_dir)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()
