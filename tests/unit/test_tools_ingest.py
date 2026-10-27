import base64
import io

from PIL import Image

from bridge.config import validate_config
from bridge.errors import BridgeError
from bridge.provider import FakeProvider
from bridge.repository import Repository, open_repository
from bridge.tools_ingest import ingest_image


def encoded(color):
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Provider(FakeProvider):
    def __init__(self, root):
        super().__init__()
        self.root = root
        self.watched = True

    def folders(self):
        return [{"id": 1, "watch": self.watched, "path": str(self.root)}]


def setup(tmp_path):
    root = tmp_path / "managed"
    config = validate_config(data_dir=tmp_path / "db", provider_base_url="http://127.0.0.1:18765",
                             managed_root=root, managed_folder_id=1)
    return config, Provider(root), Repository(open_repository(tmp_path / "db"))


def call(config, provider, repo, data=None, **kwargs):
    return ingest_image(config=config, provider=provider, repo=repo, agent_id=kwargs.pop("agent_id", "a"),
                        image_base64=data or encoded("red"), **kwargs)


def test_input_retries_and_cross_scope_do_not_reveal_duplicate(tmp_path):
    config, provider, repo = setup(tmp_path)
    result = call(config, provider, repo, client_request_id="one")
    assert result.ok and result.ingest_status == "pending" and not result.deduplicated
    assert call(config, provider, repo, client_request_id="one") == result
    same = call(config, provider, repo)
    assert same.asset_id == result.asset_id and same.deduplicated
    other = call(config, provider, repo, agent_id="b")
    assert other.ok and other.asset_id != result.asset_id and not other.deduplicated
    assert not repo.asset_in_scope(source_instance_id="", agent_id="a", project_id="", channel="", asset_id=result.asset_id)
    assert len(list(config.managed_root.glob("*/*.png"))) == 1
    repo.close()


def test_bad_input_request_conflict_and_folder_failures(tmp_path):
    config, provider, repo = setup(tmp_path)
    assert call(config, provider, repo, data="invalid!").error["code"] == "invalid_request"
    assert call(config, provider, repo, client_request_id="one").ok
    assert call(config, provider, repo, data=encoded("blue"), client_request_id="one").error["code"] == "invalid_request"
    assert len(list(config.managed_root.glob("*/*.png"))) == 1
    provider.watched = False
    assert call(config, provider, repo).error["code"] == "invalid_request"
    provider.watched = True
    provider.root = tmp_path
    assert call(config, provider, repo).error["code"] == "invalid_request"
    repo.close()


def test_legacy_link_cannot_claim_another_scopes_owned_asset(tmp_path):
    from bridge.provider import FakeFile
    from bridge.tools_link import link_asset

    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo).asset_id
    repo.bind_pending_locator(asset_id=asset, provider_instance_id=config.provider_instance_id,
        provider_file_id=7, last_seen_kind="image", last_seen_name="synthetic.png",
        last_seen_mtime="2026-01-01T00:00:00Z", last_seen_size=42)
    owned = repo.asset(asset, config.provider_instance_id)
    content = (config.managed_root / owned["relative_managed_path"]).read_bytes()
    provider.files[7] = FakeFile(file_id=7, size=len(content), content=content)
    def link(agent):
        return link_asset(provider=provider, repo=repo, provider_instance_id=config.provider_instance_id,
            source_instance_id="", agent_id=agent, project_id="", channel="", memory_ref="mem:1", provider_file_id=7)
    assert link("b").error["code"] == "asset_not_found"
    assert link("a").asset_id == asset
    repo.close()


def test_index_resolution_of_ingested_image(tmp_path):
    from bridge.provider import FakeFile
    from bridge.reconcile import resolve_pending

    config, provider, repo = setup(tmp_path)
    result = call(config, provider, repo)
    asset = repo.asset(result.asset_id, config.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
                                size=asset["size_bytes"], status="indexed")
    provider.folder_files[1] = [9]
    report = resolve_pending(provider=provider, repo=repo, config=config)
    assert report.premise_ready and report.bound[0]["asset_id"] == result.asset_id
    assert repo.locator_status(result.asset_id, config.provider_instance_id) == "ready"
    repo.close()


def test_parallel_same_request_is_idempotent_across_connections(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    config, provider, initial = setup(tmp_path)
    initial.close()
    def worker(_):
        repo = Repository(open_repository(tmp_path / "db"))
        try:
            return call(config, provider, repo, client_request_id="parallel")
        finally:
            repo.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(8)))
    assert all(result.ok for result in results)
    assert len({result.asset_id for result in results}) == 1
    assert not any(result.deduplicated for result in results)


def test_invalid_input_does_not_log_payload(tmp_path, caplog):
    config, provider, repo = setup(tmp_path)
    sentinel = "PRIVATE-SYNTHETIC-PAYLOAD!"
    result = call(config, provider, repo, data=sentinel)
    assert result.error["code"] == "invalid_request"
    assert sentinel not in caplog.text
    repo.close()


def test_managed_fetch_rejects_same_size_changed_bytes(tmp_path):
    from bridge.provider import FakeFile
    from bridge.tools_get import GetAssetResult, get_asset
    from bridge.tools_link import link_asset
    from bridge.reconcile import resolve_pending

    config, provider, repo = setup(tmp_path)
    result = call(config, provider, repo)
    asset = repo.asset(result.asset_id, config.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    original = (config.managed_root / asset["relative_managed_path"]).read_bytes()
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
        size=len(original), content=original, status="indexed", width=3, height=2)
    provider.folder_files[1] = [9]
    assert resolve_pending(provider=provider, repo=repo, config=config).bound
    assert link_asset(provider=provider, repo=repo, provider_instance_id=config.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", memory_ref="mem:1",
        provider_file_id=9).ok
    from mcp.server.mcpserver.utilities.types import Image as MCPImage
    good = get_asset(provider=provider, repo=repo, provider_instance_id=config.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", asset_id=result.asset_id)
    assert isinstance(good, MCPImage) and good.data == original
    provider.files[9].content = bytes([original[0] ^ 1]) + original[1:]
    response = get_asset(provider=provider, repo=repo, provider_instance_id=config.provider_instance_id,
        source_instance_id="", agent_id="a", project_id="", channel="", asset_id=result.asset_id)
    assert isinstance(response, GetAssetResult) and response.error["code"] == "asset_unavailable"
    assert repo.locator_status(result.asset_id, config.provider_instance_id) == "stale"
    repo.close()


def test_pending_resolution_rejects_same_size_changed_copy(tmp_path):
    from bridge.provider import FakeFile
    from bridge.reconcile import resolve_pending
    config, provider, repo = setup(tmp_path)
    result = call(config, provider, repo)
    asset = repo.asset(result.asset_id, config.provider_instance_id)
    path = config.managed_root / asset["relative_managed_path"]
    original = path.read_bytes()
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    shard, name = asset["relative_managed_path"].split("/")
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
                               size=len(original), status="indexed")
    provider.folder_files[1] = [9]
    report = resolve_pending(provider=provider, repo=repo, config=config)
    assert not report.bound and report.waiting[0]["reason"] == "identity_mismatch"
    assert repo.locator_status(result.asset_id, config.provider_instance_id) == "pending"
    repo.close()


def test_pending_resolution_rejects_repointed_provider_folder(tmp_path):
    from bridge.provider import FakeFile
    from bridge.reconcile import resolve_pending
    config, provider, repo = setup(tmp_path)
    result = call(config, provider, repo)
    asset = repo.asset(result.asset_id, config.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
                                size=asset["size_bytes"], status="indexed")
    provider.folder_files[1] = [9]
    provider.root = tmp_path
    report = resolve_pending(provider=provider, repo=repo, config=config)
    assert not report.bound and report.waiting[0]["reason"] == "identity_mismatch"
    assert repo.locator_status(result.asset_id, config.provider_instance_id) == "pending"
    repo.close()


def test_legacy_import_cannot_bypass_ingested_owner(tmp_path):
    from bridge.provider import FakeFile
    from bridge.tools_import import register_asset
    config, provider, repo = setup(tmp_path)
    asset = call(config, provider, repo).asset_id
    repo.bind_pending_locator(asset_id=asset, provider_instance_id=config.provider_instance_id,
        provider_file_id=7, last_seen_kind="image", last_seen_name="synthetic.png",
        last_seen_mtime="2026-01-01T00:00:00Z", last_seen_size=42)
    provider.files[7] = FakeFile(file_id=7)
    def register(agent):
        return register_asset(provider=provider, repo=repo, config=config, source_instance_id="",
            agent_id=agent, project_id="", channel="", memory_ref="mem:1", provider_file_id=7)
    assert register("b").error["code"] == "asset_not_found"
    assert register("a").error["code"] == "invalid_request"
    assert not any(call[0] == "file_content" for call in provider.calls)
    repo.close()


def test_legacy_tools_cannot_claim_an_indexed_but_unresolved_owned_copy(tmp_path):
    from bridge.provider import FakeFile
    from bridge.tools_import import register_asset
    from bridge.tools_link import link_asset
    config, provider, repo = setup(tmp_path)
    result = call(config, provider, repo)
    asset = repo.asset(result.asset_id, config.provider_instance_id)
    shard, name = asset["relative_managed_path"].split("/")
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard,
                                size=asset["size_bytes"], status="indexed")
    common = dict(provider=provider, repo=repo, source_instance_id="", agent_id="b", project_id="",
                  channel="", memory_ref="mem:1", provider_file_id=9)
    assert link_asset(provider_instance_id=config.provider_instance_id, **common).error["code"] == "asset_not_found"
    assert register_asset(config=config, **common).error["code"] == "asset_not_found"
    assert not any(call[0] == "file_content" for call in provider.calls)
    repo.close()
