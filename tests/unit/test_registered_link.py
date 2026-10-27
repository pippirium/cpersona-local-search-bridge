"""Explicit link tests use synthetic media, temporary DBs and fake providers."""
import uuid

from bridge.provider import FakeFile
from bridge.repository import Repository
from bridge.tools_discover import DiscoveryState, discover_assets
from bridge.tools_get import GetAssetResult, get_asset
from bridge.tools_registered_link import link_registered_asset
from bridge.tools_search import list_memory_assets
from tests.unit.test_tools_discover import setup as discovery_setup
from tests.unit.test_tools_image_search import make_linked
from tests.unit.test_tools_ingest import call, setup


def link(cfg, provider, repo, state, **kwargs):
    return link_registered_asset(provider=provider, repo=repo, config=cfg, state=state,
        agent_id=kwargs.pop("agent_id", "a"), memory_ref=kwargs.pop("memory_ref", "mem:1"), **kwargs)


def listed(cfg, repo, memory="mem:1"):
    return list_memory_assets(repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", memory_ref=memory)


def test_pending_owner_link_is_idempotent_and_unlink_preserves_image(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset = call(cfg, provider, repo)
    state = DiscoveryState()
    first = link(cfg, provider, repo, state, asset_id=asset.asset_id, client_request_id="retry")
    assert first.ok and first.link_status == "pending" and not first.already_linked
    assert link(cfg, provider, repo, state, asset_id=asset.asset_id, client_request_id="retry") == first
    duplicate = link(cfg, provider, repo, state, asset_id=asset.asset_id)
    assert duplicate.ok and duplicate.already_linked
    assert [item["asset_id"] for item in listed(cfg, repo).assets] == [asset.asset_id]
    before = repo.asset(asset.asset_id, cfg.provider_instance_id)
    assert isinstance(get_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", asset_id=asset.asset_id), GetAssetResult)
    repo.unlink(source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", asset_id=asset.asset_id)
    assert listed(cfg, repo).assets == []
    assert (cfg.managed_root / before["relative_managed_path"]).exists()
    repo.close()


def test_scope_ref_and_mixed_inputs_rejected_without_link(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset = call(cfg, provider, repo)
    state = DiscoveryState()
    foreign = link(cfg, provider, repo, state, agent_id="b", asset_id=asset.asset_id)
    unknown = link(cfg, provider, repo, state, agent_id="b", asset_id=str(uuid.uuid4()))
    assert foreign == unknown and foreign.error["code"] == "asset_not_found"
    assert link(cfg, provider, repo, state, asset_id=asset.asset_id, memory_ref="mem:0").error["code"] == "invalid_request"
    assert link(cfg, provider, repo, state, asset_id=asset.asset_id, candidate_token="x").error["code"] == "invalid_request"
    assert link(cfg, provider, repo, state).error["code"] == "invalid_request"
    assert listed(cfg, repo).assets == []
    repo.close()


def test_ready_owner_rejects_changed_content_and_transaction_failure(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset_id = make_linked(cfg, provider, repo)
    repo.unlink(source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", asset_id=asset_id)
    state = DiscoveryState()
    original = provider.files[9].content
    provider.files[9].content = bytes([original[0] ^ 1]) + original[1:]
    assert link(cfg, provider, repo, state, asset_id=asset_id).error["code"] == "asset_unavailable"
    assert listed(cfg, repo).assets == []
    provider.files[9].content = original
    repo._conn.execute("CREATE TRIGGER reject_link BEFORE INSERT ON asset_links BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    repo._conn.commit()
    assert link(cfg, provider, repo, state, asset_id=asset_id).error["code"] == "storage_error"
    assert listed(cfg, repo).assets == []
    repo.close()


def test_request_key_conflict_and_locator_change_fail_closed(tmp_path, monkeypatch):
    cfg, provider, repo = setup(tmp_path)
    asset = call(cfg, provider, repo)
    state = DiscoveryState()
    assert link(cfg, provider, repo, state, asset_id=asset.asset_id, client_request_id="same").ok
    assert link(cfg, provider, repo, state, asset_id=asset.asset_id, memory_ref="mem:2",
                client_request_id="same").error["code"] == "invalid_request"
    original = repo.link_owned_image
    def changed(**kwargs):
        repo._conn.execute("UPDATE provider_locators SET status='stale' WHERE asset_id=?", (asset.asset_id,))
        repo._conn.commit()
        return original(**kwargs)
    monkeypatch.setattr(repo, "link_owned_image", changed)
    assert link(cfg, provider, repo, state, asset_id=asset.asset_id, memory_ref="mem:3").error["code"] == "asset_unavailable"
    assert listed(cfg, repo, "mem:3").assets == []
    repo.close()


def test_selected_unlinked_candidate_is_bound_with_hash_and_explicit_fetch(tmp_path):
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    token = found.candidates[0].candidate_token
    result = link(cfg, provider, repo, state, candidate_token=token, client_request_id="once")
    assert result.ok and result.link_status == "ready" and not result.already_linked
    assert link(cfg, provider, repo, state, candidate_token=token, client_request_id="once") == result
    assert link(cfg, provider, repo, state, candidate_token=token).already_linked
    asset = repo.asset(result.asset_id, cfg.provider_instance_id)
    assert asset["origin"] == "external" and asset["content_sha256"]
    image = get_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", asset_id=result.asset_id)
    from mcp.server.mcpserver.utilities.types import Image
    assert isinstance(image, Image)
    raw = provider.files[1000].content
    provider.files[1000].content = bytes([raw[0] ^ 1]) + raw[1:]
    refused = get_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", asset_id=result.asset_id)
    assert isinstance(refused, GetAssetResult) and refused.error["code"] == "asset_unavailable"
    repo.close()


def test_candidate_change_or_expiry_never_links(tmp_path):
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    token = found.candidates[0].candidate_token
    original = provider.files[1000].content
    provider.files[1000].content = bytes([original[0] ^ 1]) + original[1:]
    assert link(cfg, provider, repo, state, candidate_token=token).error["code"] == "asset_unavailable"
    assert listed(cfg, repo).assets == []
    state.candidates[token]["expires"] = 0
    assert link(cfg, provider, repo, state, candidate_token=token).error["code"] == "invalid_request"
    repo.close()


def test_changed_candidate_is_hidden_from_text_and_image_search(tmp_path):
    from bridge.tools_search import search_assets
    from bridge.tools_image_search import search_assets_by_image
    from tests.unit.test_tools_image_search import encoded
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    result = link(cfg, provider, repo, state, candidate_token=found.candidates[0].candidate_token)
    assert result.ok
    raw = provider.files[1000].content
    provider.files[1000].content = bytes([raw[0] ^ 1]) + raw[1:]
    text = search_assets(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", query="synthetic")
    assert text.ok and text.results == [] and text.search_coverage == "partial"
    image = search_assets_by_image(provider=provider, repo=repo, config=cfg, agent_id="a", image_base64=encoded())
    assert image.ok and image.results == [] and image.search_coverage == "partial"
    repo.close()


def test_candidate_transaction_failure_leaves_no_partial_asset(tmp_path):
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    repo._conn.execute("CREATE TRIGGER reject_link BEFORE INSERT ON asset_links BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    repo._conn.commit()
    result = link(cfg, provider, repo, state, candidate_token=found.candidates[0].candidate_token)
    assert result.error["code"] == "storage_error"
    assert repo._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    assert repo._conn.execute("SELECT COUNT(*) FROM provider_locators").fetchone()[0] == 0
    repo.close()


def test_candidate_for_owned_pending_asset_uses_the_owner_id(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    owned = call(cfg, provider, repo)
    asset = repo.asset(owned.asset_id, cfg.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    data = (cfg.managed_root / asset["relative_managed_path"]).read_bytes()
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
        size=len(data), content=data, status="indexed", width=3, height=2)
    state = DiscoveryState()
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state,
                            agent_id="a", query="synthetic", folder_id=1)
    assert len(found.candidates) == 1
    linked = link(cfg, provider, repo, state, candidate_token=found.candidates[0].candidate_token)
    assert linked.ok and linked.asset_id == owned.asset_id and linked.link_status == "pending"
    repo.close()


def test_link_request_key_cannot_cross_asset_and_candidate_inputs(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    owned = call(cfg, provider, repo)
    asset = repo.asset(owned.asset_id, cfg.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    data = (cfg.managed_root / asset["relative_managed_path"]).read_bytes()
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
        size=len(data), content=data, status="indexed", width=3, height=2)
    state = DiscoveryState()
    assert link(cfg, provider, repo, state, asset_id=owned.asset_id, client_request_id="same").ok
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state,
                            agent_id="a", query="synthetic", folder_id=1)
    result = link(cfg, provider, repo, state, candidate_token=found.candidates[0].candidate_token,
                  client_request_id="same")
    assert result.error["code"] == "invalid_request"
    repo.close()


def test_candidate_does_not_rebind_unverified_legacy_orphan(tmp_path):
    from bridge.tools_link import link_asset
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    legacy = link_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", provider_file_id=1000)
    repo.unlink(source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", asset_id=legacy.asset_id)
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    result = link(cfg, provider, repo, state, candidate_token=found.candidates[0].candidate_token)
    assert result.error["code"] == "asset_unavailable"
    assert repo.asset(legacy.asset_id, cfg.provider_instance_id)["content_sha256"] is None
    assert listed(cfg, repo).assets == []
    repo.close()


def test_ready_link_provider_outage_does_not_modify_links(tmp_path):
    from bridge.provider import FakeFailure
    cfg, provider, repo = setup(tmp_path)
    asset_id = make_linked(cfg, provider, repo)
    repo.unlink(source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", asset_id=asset_id)
    provider.failures.append(FakeFailure(route="status", code="connection"))
    result = link(cfg, provider, repo, DiscoveryState(), asset_id=asset_id)
    assert result.error["code"] == "dependency_unavailable"
    assert listed(cfg, repo).assets == []
    repo.close()


def test_stale_index_generation_refuses_ready_owner_link(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset_id = make_linked(cfg, provider, repo)
    repo.unlink(source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", asset_id=asset_id)
    provider.generation += 1
    result = link(cfg, provider, repo, DiscoveryState(), asset_id=asset_id)
    assert result.error["code"] == "index_incompatible"
    assert listed(cfg, repo).assets == []
    repo.close()


def test_candidate_rebuild_refuses_link_without_side_effect(tmp_path):
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    provider.generation += 1
    result = link(cfg, provider, repo, state, candidate_token=found.candidates[0].candidate_token)
    assert result.error["code"] == "index_incompatible"
    assert repo._conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    repo.close()


def test_candidate_scope_is_rechecked_in_link_transaction(tmp_path, monkeypatch):
    from bridge.tools_link import link_asset
    cfg, provider, repo, state = discovery_setup(tmp_path, folder_ids=(1,), count=1)
    found = discover_assets(provider=provider, repo=repo, config=cfg, state=state, agent_id="a", query="synthetic")
    original = repo.link_discovered_image
    def changed(**kwargs):
        other = link_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
            source_instance_id="", agent_id="other", project_id="", channel="", memory_ref="mem:2", provider_file_id=1000)
        assert other.ok
        return original(**kwargs)
    monkeypatch.setattr(repo, "link_discovered_image", changed)
    result = link(cfg, provider, repo, state, candidate_token=found.candidates[0].candidate_token)
    assert result.error["code"] == "asset_not_found" and listed(cfg, repo).assets == []
    repo.close()
