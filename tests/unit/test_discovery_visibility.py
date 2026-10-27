from bridge.provider import FakeFile
from bridge.reconcile import resolve_pending
from tests.unit.test_tools_ingest import call, setup


def test_unknown_is_visible_but_other_owner_is_not_even_before_binding(tmp_path):
    config, provider, repo = setup(tmp_path)
    result = call(config, provider, repo)
    asset = repo.asset(result.asset_id, config.provider_instance_id)
    rel = asset["relative_managed_path"]
    common = dict(provider_instance_id=config.provider_instance_id, file_id=9, folder_id=1, relative_path=rel)
    assert repo.discovery_visible(scope=("", "a", "", ""), **common)
    assert not repo.discovery_visible(scope=("", "other", "", ""), **common)
    assert repo.discovery_visible(scope=("", "other", "", ""), **{**common, "relative_path": "other/synthetic.png"})
    shard, name = rel.split("/")
    provider.files[9] = FakeFile(file_id=9, folder_id=1, name=name, rel_dir=shard, size=asset["size_bytes"], status="indexed")
    provider.folder_files[1] = [9]
    assert resolve_pending(provider=provider, repo=repo, config=config).bound
    assert not repo.discovery_visible(scope=("", "other", "", ""), **common)
    repo.close()
