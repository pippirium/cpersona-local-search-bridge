"""IMG-09 isolated fault scenarios; temp DBs, synthetic images and fake provider."""
import base64
import io

from PIL import Image

from bridge.errors import BridgeError
from bridge.repository import Repository, StorageError, open_repository
from bridge.reconcile import resolve_pending
from bridge.tools_discover import DiscoveryState, discover_assets
from bridge.tools_get import GetAssetResult, get_asset
from bridge.tools_image_search import search_assets_by_image
from bridge.tools_metadata import get_asset_metadata, set_asset_metadata
from bridge.tools_search import search_assets
from bridge.tools_status import get_ingest_status
from tests.unit.test_staging import setup as staging_setup, stage, consume
from tests.unit.test_tools_discover import setup as discovery_setup
from tests.unit.test_tools_image_search import encoded, make_linked
from tests.unit.test_tools_ingest import call, setup


def test_old_pending_remains_unavailable_across_restart(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset = call(cfg, provider, repo)
    repo._conn.execute("UPDATE assets SET created_at='2000-01-01T00:00:00Z' WHERE asset_id=?", (asset.asset_id,))
    repo._conn.commit()
    repo.close()
    repo = Repository(open_repository(cfg.data_dir))
    for _ in range(2):
        status = get_ingest_status(provider=provider, repo=repo, config=cfg, agent_id="a", asset_id=asset.asset_id)
        assert status.ok and status.status == "pending" and status.retryable and not status.indexed
        report = resolve_pending(provider=provider, repo=repo, config=cfg)
        assert not report.bound and repo.locator_status(asset.asset_id, cfg.provider_instance_id) == "pending"
    repo.close()


def test_interrupted_staging_is_retryable_after_restart(tmp_path, monkeypatch):
    import bridge.tools_ingest as ingest
    cfg, provider, repo, original = staging_setup(tmp_path)
    token = stage(cfg, repo)
    saved_publish = ingest.publish
    def disk_failure(*args, **kwargs):
        raise BridgeError("storage_full")
    monkeypatch.setattr(ingest, "publish", disk_failure)
    assert consume(cfg, provider, repo, token, key="retry").error["code"] == "storage_full"
    monkeypatch.setattr(ingest, "publish", saved_publish)
    def database_failure(**kwargs):
        raise StorageError("synthetic interruption")
    monkeypatch.setattr(repo, "create_owned_image", database_failure)
    assert consume(cfg, provider, repo, token, key="retry").error["code"] == "storage_error"
    assert repo._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    copies = list(cfg.managed_root.glob("*/*.png"))
    assert len(copies) == 1  # publication succeeded, DB commit did not
    copy_inode = copies[0].stat().st_ino
    repo.close()
    repo = Repository(open_repository(cfg.data_dir))
    result = consume(cfg, provider, repo, token, key="retry")
    assert result.ok and result.ingest_status == "pending"
    assert consume(cfg, provider, repo, token, key="retry") == result
    assert copies[0].stat().st_ino == copy_inode
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    repo.close()


def test_cancelled_candidate_selection_never_creates_memory_link(tmp_path):
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    before = repo._conn.total_changes
    result = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    assert result.ok and result.candidates
    # Simulate cancellation by ending the trusted host flow before any link call.
    assert repo._conn.total_changes == before
    text = search_assets(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", query="synthetic")
    image = search_assets_by_image(provider=provider, repo=repo, config=cfg, agent_id="a", image_base64=encoded())
    assert text.ok and text.results == [] and image.ok and image.results == []
    repo.close()


def test_rebuild_and_rowid_reuse_do_not_return_same_size_replacement(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset_id = make_linked(cfg, provider, repo)
    original = provider.files[9].content
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), (254, 0, 0)).save(buf, "PNG")
    replacement = buf.getvalue()
    assert len(replacement) == len(original) and replacement != original
    provider.files[9].content = replacement  # same ID, name, size and mtime
    provider.generation += 1  # synthetic rebuilt index with a reused row ID
    fetch = get_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", asset_id=asset_id)
    assert isinstance(fetch, GetAssetResult) and fetch.error["code"] == "asset_unavailable"
    text = search_assets(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", query="synthetic")
    image = search_assets_by_image(provider=provider, repo=repo, config=cfg, agent_id="a", image_base64=encoded())
    assert text.ok and asset_id not in [item.asset_id for item in text.results]
    assert image.ok and asset_id not in [item.asset_id for item in image.results]
    assert repo.links_for_asset(source_instance_id="", agent_id="a", project_id="", channel="", asset_id=asset_id) == ["mem:1"]
    repo.close()


def test_deleted_managed_file_retains_metadata_without_claiming_availability(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset = call(cfg, provider, repo)
    metadata = set_asset_metadata(repo=repo, agent_id="a", asset_id=asset.asset_id,
        metadata_patch={"source_kind": "chat_attachment"})
    assert metadata.ok
    info = repo.asset(asset.asset_id, cfg.provider_instance_id)
    (cfg.managed_root / info["relative_managed_path"]).unlink()
    status = get_ingest_status(provider=provider, repo=repo, config=cfg, agent_id="a", asset_id=asset.asset_id)
    assert status.status == "missing" and not status.indexed
    retained = get_asset_metadata(repo=repo, agent_id="a", asset_id=asset.asset_id)
    assert retained.ok and retained.metadata == {"source_kind": "chat_attachment"}
    repo.close()


def test_runbook_online_backup_restores_matching_v4_state(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    from bridge.staging import token_hash

    cfg, provider, repo, original = staging_setup(tmp_path)
    token = stage(cfg, repo)
    asset = consume(cfg, provider, repo, token, key="backup")
    assert asset.ok
    assert set_asset_metadata(repo=repo, agent_id="agent", asset_id=asset.asset_id,
        metadata_patch={"source_kind": "chat_attachment"}).ok
    repo.close()  # the runbook requires stopped writers
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    runbook = (Path(__file__).resolve().parents[2] / "operations/RUNBOOK.md").read_text()
    snippet = runbook.split("python - <<'PYCODE'\n", 1)[1].split("\nPYCODE", 1)[0]
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ}
    env["BRIDGE_DATA_DIR"] = str(cfg.data_dir)
    command = [sys.executable, "-c", snippet]
    first = subprocess.run(command, cwd=backup_dir, env=env, capture_output=True, timeout=10)
    assert first.returncode == 0
    database_backup = backup_dir / "bridge-backup.sqlite3"
    saved = database_backup.read_bytes()
    second = subprocess.run(command, cwd=backup_dir, env=env, capture_output=True, timeout=10)
    assert second.returncode != 0 and database_backup.read_bytes() == saved  # no overwrite
    shutil.copytree(cfg.managed_root, backup_dir / "managed")
    shutil.copytree(cfg.staging_root, backup_dir / "staging")
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    shutil.copyfile(database_backup, restored_dir / "bridge.db")
    restored = Repository(open_repository(restored_dir))
    assert restored.schema_version() == 4
    assert restored.owned_asset(scope=("", "agent", "", ""), asset_id=asset.asset_id)
    assert get_asset_metadata(repo=restored, agent_id="agent", asset_id=asset.asset_id).metadata == {
        "source_kind": "chat_attachment"}
    assert restored.staged_image(scope=("", "agent", "", ""), token_hash=token_hash(token))["asset_id"] == asset.asset_id
    assert list((backup_dir / "managed").glob("*/*.png"))
    assert list((backup_dir / "staging").glob("*/*.stage"))
    assert (cfg.staging_input_root / "source.png").read_bytes() == original
    restored.close()
