"""IMG-08 regression: provenance is scope-specific, nullable and non-authoritative."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from bridge.repository import Repository, SchemaTooNew, open_repository
from bridge.tools_metadata import get_asset_metadata, set_asset_metadata
from tests.unit.test_image_ownership import create

A = ("", "alpha", "project", "chat")
B = ("", "beta", "project", "chat")


def _asset(repo, scope=A):
    return create(repo, scope=scope)["asset_id"]


def _get(repo, asset, scope=A):
    return get_asset_metadata(repo=repo, asset_id=asset, agent_id=scope[1],
        source_instance_id=scope[0], project_id=scope[2], channel=scope[3])


def _set(repo, asset, patch, scope=A):
    return set_asset_metadata(repo=repo, asset_id=asset, metadata_patch=patch,
        agent_id=scope[1], source_instance_id=scope[0], project_id=scope[2],
        channel=scope[3])


def test_owner_update_null_erase_and_unlink_does_not_delete(tmp_path):
    repo = Repository(open_repository(tmp_path))
    asset = _asset(repo)
    assert _get(repo, asset).metadata == {}
    result = _set(repo, asset, {
        "user_caption": "synthetic photo", "caption_origin": "user",
        "source_kind": "chat_attachment", "captured_at": "2026-10-26T12:34:56+09:00",
        "recorded_at": "2026-10-26T03:34:56Z",
        "source_message_ref": "opaque:42", "consent_record": "explicit_user",
    })
    assert result.ok and result.metadata["caption_origin"] == "user"
    assert _get(repo, asset).metadata == result.metadata
    assert _set(repo, asset, {"source_kind": "managed_import"}).metadata["user_caption"] == "synthetic photo"
    cleared = _set(repo, asset, {"user_caption": None, "recorded_at": None})
    assert "user_caption" not in cleared.metadata and "caption_origin" not in cleared.metadata
    assert "recorded_at" not in cleared.metadata
    repo.link(source_instance_id=A[0], agent_id=A[1], project_id=A[2],
              channel=A[3], memory_ref="mem:42", asset_id=asset)
    repo.unlink(source_instance_id=A[0], agent_id=A[1], project_id=A[2],
                channel=A[3], memory_ref="mem:42", asset_id=asset)
    assert _get(repo, asset).metadata == cleared.metadata
    assert _set(repo, asset, {key: None for key in cleared.metadata}).metadata == {}
    assert repo._conn.execute("SELECT COUNT(*) FROM asset_metadata").fetchone()[0] == 0
    repo.close()


def test_link_only_scope_has_separate_provenance_and_no_other_scope_access(tmp_path):
    repo = Repository(open_repository(tmp_path))
    asset = _asset(repo)
    assert _get(repo, asset, B).error["code"] == "asset_not_found"
    assert _set(repo, asset, {"source_kind": "local_selection"}, B).error["code"] == "asset_not_found"
    assert _get(repo, str(uuid.uuid4()), B).error == _get(repo, asset, B).error
    # The lower-level legacy link table may expose an asset to another scope.
    # Metadata still belongs to the exact scope, not to a shared asset_id.
    repo.link(source_instance_id=B[0], agent_id=B[1], project_id=B[2], channel=B[3],
              memory_ref="mem:11", asset_id=asset)
    assert _set(repo, asset, {"source_kind": "external_source"}, B).ok
    assert _get(repo, asset, A).metadata == {}
    assert _get(repo, asset, B).metadata == {"source_kind": "external_source"}
    repo.unlink(source_instance_id=B[0], agent_id=B[1], project_id=B[2],
                channel=B[3], memory_ref="mem:11", asset_id=asset)
    assert _get(repo, asset, B).error["code"] == "asset_not_found"
    assert _get(repo, asset, A).ok
    repo.close()


@pytest.mark.parametrize("patch", [
    {}, {"unknown": "x"}, {"user_caption": "alone"}, {"caption_origin": "user"},
    {"source_kind": "unknown"}, {"consent_record": True},
    {"user_caption": "a" * 1001, "caption_origin": "user"},
    {"user_caption": "日本" * 334, "caption_origin": "user"},
    {"user_caption": "https://example.invalid", "caption_origin": "user"},
    {"user_caption": "../image", "caption_origin": "user"},
    {"user_caption": "C:\\private\\image", "caption_origin": "user"},
    {"user_caption": "a\nsecret", "caption_origin": "user"},
    {"source_message_ref": "https://elsewhere.invalid"},
    {"source_message_ref": "x" * 129},
    {"source_message_ref": "slash/escape"},
    {"captured_at": "2026-10-26"},
    {"captured_at": "2026-02-30T00:00:00Z"},
    {"recorded_at": "2026-10-26T12:00:00"},
    {"recorded_at": "2026-10-26T00:00:00+25:00"},
])
def test_invalid_patch_does_not_mutate(tmp_path, patch):
    repo = Repository(open_repository(tmp_path))
    asset = _asset(repo)
    before = _get(repo, asset).metadata
    assert _set(repo, asset, patch).error["code"] == "invalid_request"
    assert _get(repo, asset).metadata == before
    repo.close()


def test_caption_clear_and_conflicting_origin_are_transactional(tmp_path):
    repo = Repository(open_repository(tmp_path))
    asset = _asset(repo)
    assert _set(repo, asset, {"user_caption": "label", "caption_origin": "user"}).ok
    assert _set(repo, asset, {"caption_origin": None}).error["code"] == "invalid_request"
    assert _get(repo, asset).metadata["caption_origin"] == "user"
    assert _set(repo, asset, {"user_caption": None, "caption_origin": "user"}).error["code"] == "invalid_request"
    assert _get(repo, asset).metadata["user_caption"] == "label"
    repo.close()


def test_metadata_is_independent_from_locator_changes(tmp_path):
    repo = Repository(open_repository(tmp_path))
    asset = _asset(repo)
    assert _set(repo, asset, {"source_kind": "managed_import"}).ok
    repo.set_locator_status(asset_id=asset, provider_instance_id="test-provider", status="missing")
    assert _get(repo, asset).metadata == {"source_kind": "managed_import"}
    repo.close()


def test_migration_preserves_v3_backup_and_old_reader_fails_closed(tmp_path):
    mig = Path(__file__).resolve().parents[2] / "migrations"
    old = tmp_path / "v3_sql"
    old.mkdir()
    for file in sorted(mig.glob("*.sql")):
        if file.name.startswith(("0001_", "0002_", "0003_")):
            (old / file.name).write_text(file.read_text())
    db_dir = tmp_path / "source"
    db_dir.mkdir()
    prior = open_repository(db_dir, migrations_dir=old)
    assert prior.execute("PRAGMA user_version").fetchone()[0] == 3
    backup = sqlite3.connect(tmp_path / "backup.db")
    prior.backup(backup)
    backup.close()
    prior.close()
    current = Repository(open_repository(db_dir))
    assert current.schema_version() == 4
    asset = _asset(current)
    assert _set(current, asset, {"source_kind": "local_selection"}).ok
    current.close()
    with pytest.raises(SchemaTooNew):
        open_repository(db_dir, migrations_dir=old)
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    restored = sqlite3.connect(restored_dir / "bridge.db")
    saved = sqlite3.connect(tmp_path / "backup.db")
    saved.backup(restored)
    saved.close()
    restored.close()
    old_reader = open_repository(restored_dir, migrations_dir=old)
    assert old_reader.execute("PRAGMA user_version").fetchone()[0] == 3
    old_reader.close()


def test_caller_cannot_supply_path_or_unknown_field_via_mcp(tmp_path):
    from bridge.config import validate_config
    from bridge.provider import FakeProvider
    from bridge.server import BridgeContext, create_server
    from tests.unit.test_server_health import connected_client
    import anyio

    async def check():
        cfg = validate_config(data_dir=tmp_path, provider_base_url="http://127.0.0.1:18765")
        repo = Repository(open_repository(tmp_path))
        asset = _asset(repo)
        server = create_server(BridgeContext(config=cfg, provider=FakeProvider(), db=repo))
        async with connected_client(server) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert {"set_asset_metadata", "get_asset_metadata"} <= names
            good = (await client.call_tool("set_asset_metadata", {
                "agent_id": "alpha", "project_id": "project", "channel": "chat",
                "asset_id": asset, "metadata_patch": {"source_kind": "local_selection"},
            })).structured_content
            assert good["ok"]
            import json
            from jsonschema import Draft202012Validator
            spec = json.loads((Path(__file__).resolve().parents[2] /
                "fixtures/contracts/image-input/requests.json").read_text())
            Draft202012Validator(spec["responses"]["set_asset_metadata"]).validate(good)
            read = (await client.call_tool("get_asset_metadata", {
                "agent_id": "alpha", "project_id": "project", "channel": "chat",
                "asset_id": asset,
            })).structured_content
            assert read["metadata"] == {"source_kind": "local_selection"}
            Draft202012Validator(spec["responses"]["get_asset_metadata"]).validate(read)
            denied = (await client.call_tool("get_asset_metadata", {
                "agent_id": "other", "asset_id": asset,
            })).structured_content
            assert denied["error"]["code"] == "asset_not_found"
            Draft202012Validator(spec["responses"]["get_asset_metadata"]).validate(denied)
            invalid = (await client.call_tool("set_asset_metadata", {
                "agent_id": "alpha", "project_id": "project", "channel": "chat",
                "asset_id": asset, "metadata_patch": {"url": "https://example.invalid"},
            })).structured_content
            assert invalid["error"]["code"] == "invalid_request"
            Draft202012Validator(spec["responses"]["set_asset_metadata"]).validate(invalid)
        repo.close()

    anyio.run(check)
