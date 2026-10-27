"""Image-to-image search tests use only synthetic images and temporary DBs."""
import base64
import io
import uuid

from PIL import Image

from bridge.errors import BridgeError
from bridge.provider import FakeFile
from bridge.reconcile import resolve_pending
from bridge.tools_image_search import search_assets_by_image
from bridge.tools_link import link_asset
from tests.unit.test_tools_ingest import call, setup


def encoded(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def make_linked(cfg, provider, repo):
    result = call(cfg, provider, repo)
    asset = repo.asset(result.asset_id, cfg.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    data = (cfg.managed_root / asset["relative_managed_path"]).read_bytes()
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
                                size=len(data), content=data, status="indexed", width=3, height=2)
    provider.folder_files[1] = [9]
    assert resolve_pending(provider=provider, repo=repo, config=cfg).bound
    linked = link_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", provider_file_id=9)
    assert linked.ok
    return result.asset_id


def search(cfg, provider, repo, **kwargs):
    return search_assets_by_image(provider=provider, repo=repo, config=cfg, agent_id=kwargs.pop("agent_id", "a"), **kwargs)


def test_both_query_routes_return_only_linked_scope_and_no_bytes(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset_id = make_linked(cfg, provider, repo)
    for request in ({"image_base64": encoded()}, {"query_asset_id": asset_id}):
        result = search(cfg, provider, repo, **request)
        assert result.ok and [item.asset_id for item in result.results] == [asset_id]
        assert result.results[0].memory_refs == ["mem:1"]
        assert not hasattr(result.results[0], "data")
    other = search(cfg, provider, repo, agent_id="b", image_base64=encoded())
    assert other.ok and other.results == []
    repo.close()


def test_unlinked_owner_can_query_but_is_never_a_result(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    owned = call(cfg, provider, repo)
    result = search(cfg, provider, repo, query_asset_id=owned.asset_id)
    assert result.ok and result.results == []
    repo.close()


def test_cross_scope_query_and_invalid_mixed_inputs(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset_id = make_linked(cfg, provider, repo)
    provider.calls.clear()
    other = search(cfg, provider, repo, agent_id="b", query_asset_id=asset_id)
    unknown = search(cfg, provider, repo, agent_id="b", query_asset_id=str(uuid.uuid4()))
    assert other == unknown and other.error["code"] == "asset_not_found"
    assert provider.calls == []
    assert search(cfg, provider, repo, image_base64=encoded(), query_asset_id=asset_id).error["code"] == "invalid_request"
    assert search(cfg, provider, repo).error["code"] == "invalid_request"
    assert search(cfg, provider, repo, image_base64="!bad").error["code"] == "invalid_request"
    repo.close()


def test_wrong_content_and_reindex_never_return_a_link(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset_id = make_linked(cfg, provider, repo)
    original = provider.files[9].content
    provider.files[9].content = bytes([original[0] ^ 1]) + original[1:]
    result = search(cfg, provider, repo, image_base64=encoded())
    assert result.ok and result.results == [] and result.provider_warning == "asset_unavailable"
    provider.files[9].content = original
    provider.generation += 1
    result = search(cfg, provider, repo, query_asset_id=asset_id)
    assert not result.ok and result.error["code"] == "index_incompatible"
    repo.close()


def test_provider_search_fault_is_typed_not_empty_success(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    def fail(*args, **kwargs):
        raise BridgeError("index_incompatible")
    provider.search_media = fail
    result = search(cfg, provider, repo, image_base64=encoded())
    assert not result.ok and result.error["code"] == "index_incompatible"
    repo.close()


def test_provider_pagination_reaches_later_linked_result(tmp_path):
    from tests.unit.test_tools_discover import PagingProvider
    cfg, fake, repo = setup(tmp_path)
    for idx in range(23):
        data = base64.b64decode(encoded("red"))
        fake.files[1000 + idx] = FakeFile(file_id=1000 + idx, folder_id=1, name=f"item-{idx}.png",
            rel_dir="synthetic", size=len(data), content=data, status="indexed", width=3, height=2)
    class MediaPages(PagingProvider):
        def search_media(self, data, *, filename, folder_id=None, page_size=20):
            self.calls.append(("search_media", (), {"folder_id": folder_id}))
            return self._page(folder_id, 0)
    provider = MediaPages(files=fake.files, folders_data={1: {"watch": True}})
    linked = link_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:2", provider_file_id=1022)
    assert linked.ok
    result = search(cfg, provider, repo, image_base64=encoded(), folder_id=1)
    assert result.ok and [item.asset_id for item in result.results] == [linked.asset_id]
    assert result.search_coverage == "exact"
    assert len([call for call in provider.calls if call[0] == "search_page"]) == 1
    repo.close()


def test_page_cap_reports_partial_without_foreign_counts(tmp_path):
    from tests.unit.test_tools_discover import PagingProvider
    cfg, fake, repo = setup(tmp_path)
    for idx in range(101):
        data = base64.b64decode(encoded("red"))
        fake.files[1000 + idx] = FakeFile(file_id=1000 + idx, folder_id=1, name=f"item-{idx}.png",
            rel_dir="synthetic", size=len(data), content=data, status="indexed", width=3, height=2)
    class MediaPages(PagingProvider):
        def search_media(self, data, *, filename, folder_id=None, page_size=20):
            return self._page(folder_id, 0)
    provider = MediaPages(files=fake.files, folders_data={1: {"watch": True}})
    result = search(cfg, provider, repo, image_base64=encoded(), folder_id=1)
    assert result.ok and result.results == [] and result.search_coverage == "partial"
    assert len([call for call in provider.calls if call[0] == "search_page"]) == 4
    repo.close()


def test_external_query_and_downward_byte_limit(tmp_path):
    from dataclasses import replace
    cfg, provider, repo = setup(tmp_path)
    data = base64.b64decode(encoded())
    provider.files[7] = FakeFile(file_id=7, folder_id=1, name="external.png", rel_dir="synthetic",
                                  size=len(data), content=data, width=3, height=2)
    linked = link_asset(provider=provider, repo=repo, provider_instance_id=cfg.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1", provider_file_id=7)
    assert linked.ok
    result = search(cfg, provider, repo, query_asset_id=linked.asset_id)
    assert result.ok and result.results[0].asset_id == linked.asset_id
    too_small = replace(cfg, max_fetch_bytes=50)
    assert search(too_small, provider, repo, query_asset_id=linked.asset_id).error["code"] == "media_too_large"
    repo.close()


def test_timeout_and_reused_id_are_safe(tmp_path):
    cfg, provider, repo = setup(tmp_path)
    asset = make_linked(cfg, provider, repo)
    provider.files[9].name = "replacement.png"
    result = search(cfg, provider, repo, image_base64=encoded())
    assert result.ok and result.results == [] and result.search_coverage == "partial"
    def timeout(*args, **kwargs):
        raise BridgeError("dependency_unavailable")
    provider.search_media = timeout
    assert search(cfg, provider, repo, image_base64=encoded()).error["code"] == "dependency_unavailable"
    repo.close()


def test_input_image_data_is_not_logged(tmp_path, caplog):
    cfg, provider, repo = setup(tmp_path)
    image = encoded()
    assert search(cfg, provider, repo, image_base64=image).ok
    assert image not in caplog.text
    repo.close()
