"""Allowlisted discovery, paging and ephemeral token tests with synthetic media."""
import io
from pathlib import Path

import pytest
from PIL import Image

from bridge.config import validate_config
from bridge.errors import BridgeError
from bridge.provider import FakeFile, FakeProvider
from bridge.repository import Repository, open_repository
from bridge.tools_discover import DiscoveryState, discover_assets, validate_candidate


def setup(tmp_path, folder_ids=(1, 2), count=4):
    cfg = validate_config(data_dir=tmp_path / "db", provider_base_url="http://127.0.0.1:18765",
                          discovery_folder_ids=folder_ids)
    repo = Repository(open_repository(cfg.data_dir))
    provider = FakeProvider(folders_data={fid: {"watch": True} for fid in folder_ids})
    for folder in folder_ids:
        for index in range(count):
            file_id = folder * 1000 + index
            buf = io.BytesIO()
            Image.new("RGB", (2, 2), (folder, index % 256, 0)).save(buf, "PNG")
            data = buf.getvalue()
            provider.files[file_id] = FakeFile(file_id=file_id, folder_id=folder, name=f"item-{file_id}.png",
                rel_dir="synthetic", content=data, size=len(data), width=2, height=2)
    return cfg, provider, repo, DiscoveryState()


def discover(cfg, provider, repo, state, **kwargs):
    return discover_assets(config=cfg, provider=provider, repo=repo, state=state,
        agent_id=kwargs.pop("agent_id", "a"), query=kwargs.pop("query", "synthetic"), **kwargs)


def test_only_allowed_folder_candidates_and_no_bytes(tmp_path):
    cfg, provider, repo, state = setup(tmp_path)
    result = discover(cfg, provider, repo, state, folder_id=1)
    assert result.ok and result.search_coverage == "exact" and len(result.candidates) == 4
    assert all(item.label.startswith("item-1") for item in result.candidates)
    assert all(call[2].get("folder_id") == 1 for call in provider.calls if call[0] == "search")
    before = len(provider.calls)
    denied = discover(cfg, provider, repo, state, folder_id=999)
    assert denied.error["code"] == "asset_not_found" and len(provider.calls) == before
    assert all(not hasattr(item, "data") for item in result.candidates)
    repo.close()


def test_other_scopes_known_file_is_hidden_and_candidate_revalidated(tmp_path):
    from bridge.tools_link import link_asset
    cfg, provider, repo, state = setup(tmp_path)
    linked = link_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="other", project_id="", channel="", memory_ref="mem:1", provider_file_id=1000)
    assert linked.ok
    result = discover(cfg, provider, repo, state, folder_id=1)
    assert "item-1000.png" not in [item.label for item in result.candidates]
    token = result.candidates[0].candidate_token
    evidence = validate_candidate(provider=provider, repo=repo, config=cfg, state=state,
                                  token=token, scope=("", "a", "", ""))
    assert evidence["file_id"] != 1000
    f = provider.files[evidence["file_id"]]
    f.content = bytes([f.content[0] ^ 1]) + f.content[1:]
    with pytest.raises(BridgeError, match="asset_unavailable"):
        validate_candidate(provider=provider, repo=repo, config=cfg, state=state, token=token,
                           scope=("", "a", "", ""))
    with pytest.raises(BridgeError, match="invalid_request"):
        validate_candidate(provider=provider, repo=repo, config=cfg, state=state, token=token,
                           scope=("", "other", "", ""))
    repo.close()


class PagingProvider(FakeProvider):
    def _page(self, folder, offset):
        files = sorted((f for f in self.files.values() if f.folder_id == folder), key=lambda f: f.file_id)
        items = []
        for rank, f in enumerate(files[offset:offset + 20], start=offset):
            items.append({"file_id": f.file_id, "kind": "image", "folder_id": folder,
                "name": f.name, "rel_dir": f.rel_dir, "size": f.size, "mtime": f.mtime,
                "score": 1 - rank / 1000 - folder / 10000, "available": True})
        next_offset = offset + len(items)
        return {"search_id": f"search-{folder}", "generation": self.generation, "items": items,
                "next_cursor": str(next_offset) if next_offset < len(files) else None, "counts_capped": []}

    def search(self, q, *, page_size, folder_id=None, cursor=None):
        self.calls.append(("search", (), {"folder_id": folder_id}))
        assert folder_id is not None and cursor is None
        return self._page(folder_id, 0)

    def search_page(self, search_id, cursor, *, folder_id, page_size):
        self.calls.append(("search_page", (), {"folder_id": folder_id}))
        assert search_id == f"search-{folder_id}"
        return self._page(folder_id, int(cursor))


def test_multi_folder_pagination_preserves_results_and_binds_cursor(tmp_path):
    cfg, fake, repo, state = setup(tmp_path, count=23)
    provider = PagingProvider(files=fake.files, folders_data=fake.folders_data)
    result = discover(cfg, provider, repo, state, limit=7)
    assert result.ok and result.search_coverage == "partial" and result.next_cursor
    cursor = result.next_cursor
    assert discover(cfg, provider, repo, state, limit=7, cursor=cursor, query="changed").error["code"] == "invalid_request"
    assert discover(cfg, provider, repo, state, limit=7, cursor=cursor, agent_id="other").error["code"] == "invalid_request"
    labels = [item.label for item in result.candidates]
    while result.next_cursor:
        result = discover(cfg, provider, repo, state, limit=7, cursor=result.next_cursor)
        assert result.ok
        labels.extend(item.label for item in result.candidates)
    assert result.search_coverage == "exact" and len(labels) == len(set(labels)) == 46
    assert any(call[0] == "search_page" for call in provider.calls)
    assert all(call[2]["folder_id"] in (1, 2) for call in provider.calls if call[0] in ("search", "search_page"))
    repo.close()


def test_index_rebuild_and_expired_tokens_fail_closed(tmp_path):
    cfg, provider, repo, state = setup(tmp_path, count=2)
    result = discover(cfg, provider, repo, state, folder_id=1, limit=1)
    token, cursor = result.candidates[0].candidate_token, result.next_cursor
    provider.generation += 1
    assert discover(cfg, provider, repo, state, folder_id=1, limit=1, cursor=cursor).error["code"] == "index_incompatible"
    with pytest.raises(BridgeError, match="index_incompatible"):
        validate_candidate(provider=provider, repo=repo, config=cfg, state=state, token=token, scope=("", "a", "", ""))
    state.candidates[token]["expires"] = 0
    with pytest.raises(BridgeError, match="invalid_request"):
        validate_candidate(provider=provider, repo=repo, config=cfg, state=state, token=token, scope=("", "a", "", ""))
    repo.close()


def test_more_than_five_folders_initializes_with_bounded_continuation(tmp_path):
    cfg, provider, repo, state = setup(tmp_path, folder_ids=(1, 2, 3, 4, 5, 6), count=1)
    first = discover(cfg, provider, repo, state)
    assert first.ok and first.candidates == [] and first.next_cursor and first.search_coverage == "partial"
    assert len([call for call in provider.calls if call[0] == "search"]) == 5
    second = discover(cfg, provider, repo, state, cursor=first.next_cursor)
    assert second.ok and len(second.candidates) == 6 and second.search_coverage == "exact"
    assert len([call for call in provider.calls if call[0] == "search"]) == 6
    repo.close()


def test_provider_cap_makes_coverage_partial_without_next_page(tmp_path):
    cfg, fake, repo, state = setup(tmp_path, folder_ids=(1,), count=2)
    class Capped(PagingProvider):
        def _page(self, folder, offset):
            result = super()._page(folder, offset)
            result["counts_capped"] = ["image"]
            return result
    provider = Capped(files=fake.files, folders_data=fake.folders_data)
    result = discover(cfg, provider, repo, state)
    assert result.ok and len(result.candidates) == 2 and result.search_coverage == "partial"
    assert result.next_cursor is None
    repo.close()


def test_discovery_has_no_implicit_links_and_hides_inputs_from_logs(tmp_path, caplog):
    cfg, provider, repo, state = setup(tmp_path)
    before = repo._conn.total_changes
    query = "SYNTHETIC-PRIVATE-QUERY"
    result = discover(cfg, provider, repo, state, query=query, folder_id=1)
    assert result.ok and repo._conn.total_changes == before
    assert query not in caplog.text
    assert not any(item.candidate_token in caplog.text for item in result.candidates)
    repo.close()


def test_missing_allowed_folder_and_changed_allowlist_fail_closed(tmp_path):
    from dataclasses import replace
    cfg, provider, repo, state = setup(tmp_path, count=3)
    result = discover(cfg, provider, repo, state, folder_id=1, limit=1)
    assert result.next_cursor
    changed = replace(cfg, discovery_folder_ids=(1,))
    assert discover(changed, provider, repo, state, folder_id=1, limit=1, cursor=result.next_cursor).error["code"] == "invalid_request"
    del provider.folders_data[1]
    assert discover(cfg, provider, repo, state, folder_id=1).error["code"] == "asset_unavailable"
    repo.close()


def test_verification_work_is_bounded_and_cursor_continues(tmp_path):
    cfg, fake, repo, state = setup(tmp_path, folder_ids=(1,), count=23)
    for item in fake.files.values():
        item.content = bytes([item.content[0] ^ 1]) + item.content[1:]
    provider = PagingProvider(files=fake.files, folders_data=fake.folders_data)
    first = discover(cfg, provider, repo, state, folder_id=1)
    assert first.ok and first.candidates == [] and first.next_cursor and first.search_coverage == "partial"
    assert len([call for call in provider.calls if call[0] == "file_content"]) == 20
    second = discover(cfg, provider, repo, state, folder_id=1, cursor=first.next_cursor)
    assert second.ok and second.candidates == [] and second.search_coverage == "exact"
    assert len([call for call in provider.calls if call[0] == "file_content"]) == 23
    repo.close()


def test_candidate_deletion_refuses_selection(tmp_path):
    cfg, provider, repo, state = setup(tmp_path, folder_ids=(1,), count=1)
    result = discover(cfg, provider, repo, state)
    token = result.candidates[0].candidate_token
    del provider.files[1000]
    with pytest.raises(BridgeError, match="asset_unavailable"):
        validate_candidate(provider=provider, repo=repo, config=cfg, state=state,
                           token=token, scope=("", "a", "", ""))
    repo.close()


def test_reused_file_id_does_not_reveal_new_label_to_old_scope(tmp_path):
    from bridge.tools_link import link_asset
    cfg, provider, repo, state = setup(tmp_path, folder_ids=(1,), count=1)
    linked = link_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", provider_file_id=1000)
    assert linked.ok
    provider.files[1000].name = "replacement.png"
    result = discover(cfg, provider, repo, state, folder_id=1)
    assert result.ok and result.candidates == []
    assert not any(call[0] == "file_content" for call in provider.calls)
    repo.close()


def test_owned_file_with_same_size_changed_bytes_is_not_discovered(tmp_path):
    from tests.unit.test_tools_ingest import setup as managed_setup, call
    cfg, provider, repo = managed_setup(tmp_path)
    result = call(cfg, provider, repo)
    asset = repo.asset(result.asset_id, cfg.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), (254, 0, 0)).save(buf, "PNG")
    replacement = buf.getvalue()
    assert len(replacement) == asset["size_bytes"]
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
                                 size=len(replacement), content=replacement, width=3, height=2)
    found = discover(cfg, provider, repo, DiscoveryState(), folder_id=1)
    assert found.ok and found.candidates == []
    repo.close()
