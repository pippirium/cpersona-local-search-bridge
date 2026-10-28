"""Explicit staged handoff tests; all files and DBs are temporary."""
import io
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from bridge.config import ConfigError, validate_config
from bridge.errors import BridgeError
from bridge.repository import Repository, open_repository
from bridge.staging import stage_image, stage_relpath, token_hash
from bridge.tools_staging import import_staged_image
from tests.unit.test_tools_ingest import Provider

SCOPE = ("", "agent", "", "")


def setup(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir(mode=0o700)
    managed = tmp_path / "managed"
    staging = tmp_path / "staging"
    cfg = validate_config(data_dir=tmp_path / "db", provider_base_url="http://127.0.0.1:18765",
        managed_root=managed, managed_folder_id=1, staging_root=staging, staging_input_root=selected)
    repo = Repository(open_repository(cfg.data_dir))
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(buf, "PNG")
    (selected / "source.png").write_bytes(buf.getvalue())
    return cfg, Provider(managed), repo, buf.getvalue()


def stage(cfg, repo, source="source.png", scope=SCOPE, confirmed=True):
    return stage_image(config=cfg, repo=repo, scope=scope, relative_path=source, confirmed=confirmed)


def consume(cfg, provider, repo, token, agent="agent", key=None):
    return import_staged_image(config=cfg, provider=provider, repo=repo, agent_id=agent,
                               staging_id=token, client_request_id=key)


def test_staged_import_retry_scope_and_original_unchanged(tmp_path):
    cfg, provider, repo, original = setup(tmp_path)
    token = stage(cfg, repo)
    assert len(token) == 43 and not repo.asset_in_scope(source_instance_id="", agent_id="agent",
        project_id="", channel="", asset_id="invalid")
    foreign = consume(cfg, provider, repo, token, agent="other")
    unknown = consume(cfg, provider, repo, "a" * 43, agent="other")
    assert foreign == unknown and foreign.error["code"] == "invalid_request"
    result = consume(cfg, provider, repo, token, key="retry")
    assert result.ok and result.ingest_status == "pending" and not result.deduplicated
    assert consume(cfg, provider, repo, token, key="retry") == result
    assert consume(cfg, provider, repo, token) == result
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    assert repo.owned_asset(scope=SCOPE, asset_id=result.asset_id)
    repo.close()


def test_expiry_replacement_and_unfinished_input_are_refused(tmp_path):
    cfg, provider, repo, original = setup(tmp_path)
    token = stage(cfg, repo)
    staged = cfg.staging_root / stage_relpath(token_hash(token))
    staged.unlink()
    staged.write_bytes(original)  # same bytes, different inode must not count as the staged object
    assert consume(cfg, provider, repo, token).error["code"] == "invalid_request"
    token2 = stage(cfg, repo)
    repo._conn.execute("UPDATE staged_images SET expires_at='2000-01-01T00:00:00Z' WHERE token_hash=?", (token_hash(token2),))
    repo._conn.commit()
    assert consume(cfg, provider, repo, token2).error["code"] == "invalid_request"
    (cfg.staging_input_root / "broken.png").write_bytes(original[:20])
    with pytest.raises(BridgeError):
        stage(cfg, repo, "broken.png")
    repo.close()


def test_source_paths_symlinks_hardlinks_and_confirmation(tmp_path):
    cfg, provider, repo, original = setup(tmp_path)
    (cfg.staging_input_root / "alias.png").symlink_to("source.png")
    os.link(cfg.staging_input_root / "source.png", cfg.staging_input_root / "linked.png")
    for path in ("../outside", "/outside", "alias.png", "linked.png", "source.png"):
        with pytest.raises(BridgeError):
            stage(cfg, repo, path)
    with pytest.raises(BridgeError):
        stage(cfg, repo, confirmed=False)
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    repo.close()


def test_concurrent_consume_is_atomic(tmp_path):
    cfg, provider, repo, original = setup(tmp_path)
    token = stage(cfg, repo)
    repo.close()
    def worker(_):
        local = Repository(open_repository(cfg.data_dir))
        try:
            return consume(cfg, provider, local, token, key="same")
        finally:
            local.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(8)))
    assert all(item.ok for item in results)
    assert len({item.asset_id for item in results}) == 1
    assert all(not item.deduplicated for item in results)


def test_failed_copy_leaves_stage_retryable(tmp_path, monkeypatch):
    import bridge.tools_ingest as ingest
    cfg, provider, repo, _ = setup(tmp_path)
    token = stage(cfg, repo)
    original_publish = ingest.publish
    def fail(*args, **kwargs):
        raise BridgeError("storage_full")
    monkeypatch.setattr(ingest, "publish", fail)
    assert consume(cfg, provider, repo, token).error["code"] == "storage_full"
    assert repo.staged_image(scope=SCOPE, token_hash=token_hash(token))["asset_id"] is None
    monkeypatch.setattr(ingest, "publish", original_publish)
    assert consume(cfg, provider, repo, token).ok
    repo.close()


def test_staging_root_must_be_separate(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    with pytest.raises(ConfigError):
        validate_config(data_dir=tmp_path / "db", provider_base_url="http://127.0.0.1:18765",
                        staging_root=source / "nested", staging_input_root=source)
    assert not (source / "nested").exists()


def test_explicit_gc_removes_only_tracked_expired_staging(tmp_path):
    from bridge.staging import gc_expired_staging
    cfg, provider, repo, original = setup(tmp_path)
    expired = stage(cfg, repo)
    live = stage(cfg, repo)
    repo._conn.execute("UPDATE staged_images SET expires_at='2000-01-01T00:00:00Z' WHERE token_hash=?", (token_hash(expired),))
    repo._conn.commit()
    foreign = cfg.staging_root / "foreign"
    foreign.write_bytes(b"synthetic")
    with pytest.raises(BridgeError):
        gc_expired_staging(config=cfg, repo=repo, confirmed=False)
    report = gc_expired_staging(config=cfg, repo=repo, confirmed=True)
    assert report == {"removed": 1, "blocked": 0}
    assert not (cfg.staging_root / stage_relpath(token_hash(expired))).exists()
    assert (cfg.staging_root / stage_relpath(token_hash(live))).exists()
    assert foreign.read_bytes() == b"synthetic"
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    repo.close()


def test_gc_refuses_replaced_staging_object(tmp_path):
    from bridge.staging import gc_expired_staging
    cfg, provider, repo, original = setup(tmp_path)
    token = stage(cfg, repo)
    path = cfg.staging_root / stage_relpath(token_hash(token))
    path.unlink()
    path.write_bytes(original)
    repo._conn.execute("UPDATE staged_images SET expires_at='2000-01-01T00:00:00Z' WHERE token_hash=?", (token_hash(token),))
    repo._conn.commit()
    assert gc_expired_staging(config=cfg, repo=repo, confirmed=True) == {"removed": 0, "blocked": 1}
    assert path.read_bytes() == original
    repo.close()


def test_locked_source_cannot_be_staged(tmp_path):
    import fcntl
    cfg, provider, repo, original = setup(tmp_path)
    with (cfg.staging_input_root / "source.png").open("rb") as source:
        fcntl.flock(source.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BridgeError):
            stage(cfg, repo)
    assert repo._conn.execute("SELECT count(*) FROM staged_images").fetchone()[0] == 0
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    repo.close()


def test_staged_image_resolves_to_ready(tmp_path):
    from tests.unit.test_tools_status import indexed, status
    cfg, provider, repo, original = setup(tmp_path)
    token = stage(cfg, repo)
    result = consume(cfg, provider, repo, token)
    indexed(cfg, provider, repo, result)
    assert status(cfg, provider, repo, result.asset_id, agent="agent").indexed
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    repo.close()


def test_cli_returns_token_without_source_name_or_path(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    cfg, provider, repo, original = setup(tmp_path)
    repo.close()
    env = dict(os.environ)
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
        BRIDGE_DATA_DIR=str(cfg.data_dir), BRIDGE_PROVIDER_URL=cfg.provider_base_url,
        BRIDGE_MANAGED_ROOT=str(cfg.managed_root), BRIDGE_MANAGED_FOLDER_ID="1",
        BRIDGE_STAGING_ROOT=str(cfg.staging_root), BRIDGE_STAGING_INPUT_ROOT=str(cfg.staging_input_root))
    proc = subprocess.run([sys.executable, "-m", "bridge.stage_image", "--relative-path", "source.png",
                           "--agent-id", "agent", "--confirm"], env=env, capture_output=True, text=True, check=True)
    assert json.loads(proc.stdout)["ok"]
    assert "source.png" not in proc.stdout + proc.stderr
    assert str(cfg.staging_input_root) not in proc.stdout + proc.stderr
    assert (cfg.staging_input_root / "source.png").read_bytes() == original


def test_upgrade_v2_preserves_existing_owner_and_retry(tmp_path):
    import shutil
    from pathlib import Path
    from tests.unit.test_image_ownership import SCOPE_A, create
    old_migrations = tmp_path / "old_migrations"
    old_migrations.mkdir()
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    for name in ("0001_v1.sql", "0002_image_ownership.sql"):
        shutil.copyfile(migrations / name, old_migrations / name)
    old = Repository(open_repository(tmp_path, migrations_dir=old_migrations))
    before = create(old, request_id="key")
    old.close()
    upgraded = Repository(open_repository(tmp_path))
    assert upgraded.schema_version() == 4
    assert upgraded.owned_asset(scope=SCOPE_A, asset_id=before["asset_id"])
    assert create(upgraded, request_id="key") == before
    upgraded.close()


def test_database_failure_does_not_consume_stage_or_create_asset(tmp_path):
    cfg, provider, repo, original = setup(tmp_path)
    token = stage(cfg, repo)
    repo._conn.execute("CREATE TRIGGER reject_image BEFORE INSERT ON assets BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    repo._conn.commit()
    failed = consume(cfg, provider, repo, token)
    assert failed.error["code"] == "storage_error"
    assert repo.staged_image(scope=SCOPE, token_hash=token_hash(token))["asset_id"] is None
    assert repo._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    repo._conn.execute("DROP TRIGGER reject_image")
    repo._conn.commit()
    assert consume(cfg, provider, repo, token).ok
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    repo.close()


def test_expired_gc_does_not_discard_ledger_when_root_missing(tmp_path):
    from bridge.staging import gc_expired_staging
    cfg, provider, repo, _ = setup(tmp_path)
    token = stage(cfg, repo)
    repo._conn.execute("UPDATE staged_images SET expires_at='2000-01-01T00:00:00Z' WHERE token_hash=?", (token_hash(token),))
    repo._conn.commit()
    cfg.staging_root.rename(tmp_path / "removed-staging")
    assert gc_expired_staging(config=cfg, repo=repo, confirmed=True) == {"removed": 0, "blocked": 1}
    assert repo._conn.execute("SELECT COUNT(*) FROM staged_images").fetchone()[0] == 1
    repo.close()


def test_retry_key_cannot_be_reused_for_a_different_stage_token(tmp_path):
    cfg, provider, repo, _ = setup(tmp_path)
    first = stage(cfg, repo)
    second = stage(cfg, repo)
    assert consume(cfg, provider, repo, first, key="same").ok
    assert consume(cfg, provider, repo, second, key="same").error["code"] == "invalid_request"
    assert repo.staged_image(scope=SCOPE, token_hash=token_hash(second))["asset_id"] is None
    repo.close()


def test_closed_database_returns_sanitized_error(tmp_path):
    cfg, provider, repo, _ = setup(tmp_path)
    token = stage(cfg, repo)
    repo.close()
    result = consume(cfg, provider, repo, token, key="retry")
    assert result.error["code"] == "storage_error"
