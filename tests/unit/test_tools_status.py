"""Read-only status checks with synthetic scoped images and fake provider."""
import uuid

from bridge.provider import FakeFailure, FakeFile
from bridge.reconcile import resolve_pending
from bridge.tools_status import get_ingest_status
from tests.unit.test_tools_ingest import call, setup


def status(config, provider, repo, asset_id, agent="a"):
    return get_ingest_status(config=config, provider=provider, repo=repo, asset_id=asset_id, agent_id=agent)


def indexed(config, provider, repo, result):
    asset = repo.asset(result.asset_id, config.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    content = (config.managed_root / asset["relative_managed_path"]).read_bytes()
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
        size=len(content), content=content, status="indexed", width=3, height=2)
    provider.folder_files[1] = [9]
    assert resolve_pending(provider=provider, repo=repo, config=config).bound


def test_pending_then_resolved_without_status_side_effects(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    changes = repo._conn.total_changes
    pending = status(config, provider, repo, asset.asset_id)
    assert pending.ok and pending.status == "pending" and not pending.indexed
    assert pending.retryable and pending.reason_code == "waiting_for_index"
    assert repo._conn.total_changes == changes
    indexed(config, provider, repo, asset)
    changes = repo._conn.total_changes
    ready = status(config, provider, repo, asset.asset_id)
    assert ready.ok and ready.status == "ready" and ready.indexed and not ready.retryable
    assert ready.checked_at and repo._conn.total_changes == changes
    repo.close()


def test_unknown_and_other_scope_match_and_do_not_contact_provider(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    provider.calls.clear()
    other = status(config, provider, repo, asset.asset_id, agent="b")
    unknown = status(config, provider, repo, str(uuid.uuid4()), agent="b")
    assert other == unknown and other.error["code"] == "asset_not_found"
    assert provider.calls == []
    repo.close()


def test_folder_provider_fault_and_page_cap_never_claim_ready(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    provider.watched = False
    assert status(config, provider, repo, asset.asset_id).reason_code == "folder_not_watched"
    provider.watched = True
    provider.failures.append(FakeFailure(route="status", code="connection"))
    assert status(config, provider, repo, asset.asset_id).reason_code == "provider_unavailable"
    provider.failures.clear()
    provider.folder_files[99] = list(range(100, 200))
    capped = status(config, provider, repo, asset.asset_id)
    assert capped.reason_code == "page_full" and not capped.indexed
    repo.close()


def test_changed_file_generation_and_rowid_reuse_do_not_report_ready(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    indexed(config, provider, repo, asset)
    path = config.managed_root / repo.asset(asset.asset_id, config.provider_instance_id)["relative_managed_path"]
    original = path.read_bytes()
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    changed = status(config, provider, repo, asset.asset_id)
    assert changed.status == "stale" and not changed.indexed and changed.reason_code == "identity_mismatch"
    assert repo.locator_status(asset.asset_id, config.provider_instance_id) == "ready"
    path.write_bytes(original)
    provider.files[9].name = "different.png"
    reused = status(config, provider, repo, asset.asset_id)
    assert reused.status == "stale" and not reused.indexed
    provider.mismatch = "changed"
    mismatch = status(config, provider, repo, asset.asset_id)
    assert mismatch.status == "incompatible" and not mismatch.retryable
    repo.close()


def test_removed_copy_and_provider_pending_are_not_indexed(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    indexed(config, provider, repo, asset)
    provider.files[9].status = "pending"
    pending = status(config, provider, repo, asset.asset_id)
    assert pending.status == "pending" and not pending.indexed
    provider.files[9].status = "indexed"
    path = config.managed_root / repo.asset(asset.asset_id, config.provider_instance_id)["relative_managed_path"]
    path.unlink()
    missing = status(config, provider, repo, asset.asset_id)
    assert missing.status == "missing" and missing.reason_code == "file_missing"
    assert repo.locator_status(asset.asset_id, config.provider_instance_id) == "ready"
    repo.close()


def test_tampered_relative_path_never_read_outside_managed_root(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    repo._conn.execute("UPDATE assets SET relative_managed_path=? WHERE asset_id=?", ("../outside", asset.asset_id))
    repo._conn.commit()
    observed = status(config, provider, repo, asset.asset_id)
    assert observed.status == "stale" and observed.reason_code == "identity_mismatch"
    repo.close()


def test_existing_linked_scope_can_read_external_asset_status(tmp_path):
    from bridge.tools_link import link_asset
    config, provider, repo = setup(tmp_path)
    provider.files[10] = FakeFile(file_id=10, kind="image", name="fixture.png", status="indexed")
    linked = link_asset(provider=provider, repo=repo, provider_instance_id=config.provider_instance_id,
        source_instance_id="", agent_id="linked", project_id="", channel="", memory_ref="mem:1", provider_file_id=10)
    assert linked.ok
    observed = status(config, provider, repo, linked.asset_id, agent="linked")
    assert observed.ok and observed.status == "ready" and observed.indexed
    assert status(config, provider, repo, linked.asset_id, agent="other").error["code"] == "asset_not_found"
    repo.close()


def test_rebuild_generation_change_refuses_even_when_row_metadata_matches(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    indexed(config, provider, repo, asset)
    saved = repo.asset(asset.asset_id, config.provider_instance_id)
    assert saved["provider_generation"] == str(provider.generation)
    provider.generation += 1
    result = status(config, provider, repo, asset.asset_id)
    assert result.status == "incompatible" and not result.indexed
    assert result.reason_code == "index_incompatible" and not result.retryable
    assert repo.locator_status(asset.asset_id, config.provider_instance_id) == "ready"
    repo.close()


def test_rebuild_during_resolve_never_binds_pending(tmp_path):
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo)
    data = repo.asset(asset.asset_id, config.provider_instance_id)
    shard, name = data["relative_managed_path"].split("/")
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
                                 size=data["size_bytes"], status="indexed")
    provider.folder_files[1] = [9]
    original = provider.status
    calls = 0
    def rebuilding():
        nonlocal calls
        calls += 1
        if calls == 2:
            provider.generation += 1
        return original()
    provider.status = rebuilding
    report = resolve_pending(provider=provider, repo=repo, config=config)
    assert not report.bound and report.waiting[0]["reason"] == "index_unavailable"
    assert repo.locator_status(asset.asset_id, config.provider_instance_id) == "pending"
    repo.close()
