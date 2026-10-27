"""Scope-only user-supplied asset provenance (IMG-08).

Never treats captions, consent flags, or input timestamps as identity evidence.
No remote provider call is needed and neither image bytes nor memory text live here.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from bridge.errors import BridgeError, error_result
from bridge.repository import ASSET_ID_RE, AssetNotFound, Repository, RequestConflict, StorageError
from bridge.tools_ingest import validate_scope_and_key

FIELDS = (
    "user_caption", "caption_origin", "source_kind", "captured_at", "recorded_at",
    "source_message_ref", "consent_record",
)
ENUMS = {
    "caption_origin": {"user", "external_source"},
    "source_kind": {"chat_attachment", "local_selection", "managed_import", "external_source"},
    "consent_record": {"explicit_user", "operator_confirmed"},
}
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_OPAQUE = re.compile(r"^[A-Za-z0-9._:-]+$")
_URL_OR_PATH = re.compile(r"(?i)(?:https?\s*:\s*|file\s*:|data\s*:|www\.|[/\\]|~[/\\])")


@dataclass
class MetadataResult:
    ok: bool
    asset_id: str | None = None
    metadata: dict | None = None
    error: dict | None = None


def _safe_string(value: str, max_bytes: int) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        if len(value.encode("utf-8")) > max_bytes:
            return False
    except UnicodeError:
        return False
    return (not any(unicodedata.category(c).startswith("C") for c in value)
            and not _URL_OR_PATH.search(value))


def _timestamp_valid(value: str) -> bool:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return False
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.tzinfo is not None and dt.utcoffset() is not None
    except ValueError:
        return False


def validate_patch(patch: object) -> dict[str, str | None]:
    """Only omitted keys mean unchanged; explicit null clears a key."""
    if not isinstance(patch, dict) or not patch or any(k not in FIELDS for k in patch):
        raise BridgeError("invalid_request")
    for key, value in patch.items():
        if value is None:
            continue
        if key in ENUMS:
            if not isinstance(value, str) or value not in ENUMS[key]:
                raise BridgeError("invalid_request")
        elif key in ("captured_at", "recorded_at"):
            if not _timestamp_valid(value):
                raise BridgeError("invalid_request")
        elif key == "user_caption":
            if not _safe_string(value, 1000):
                raise BridgeError("invalid_request")
        elif key == "source_message_ref":
            if not _safe_string(value, 128) or not _OPAQUE.fullmatch(value):
                raise BridgeError("invalid_request")
    return dict(patch)


def _validate_scope_id(agent_id, asset_id, source_instance_id, project_id, channel):
    scope = validate_scope_and_key((source_instance_id, agent_id, project_id, channel), None)
    if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
        raise BridgeError("asset_not_found")
    return scope


def _result_error(code: str) -> MetadataResult:
    return MetadataResult(False, error=error_result(code)["error"])


def get_asset_metadata(*, repo: Repository, agent_id: str, asset_id: str,
                       source_instance_id: str = "", project_id: str = "",
                       channel: str = "") -> MetadataResult:
    try:
        scope = _validate_scope_id(agent_id, asset_id, source_instance_id, project_id, channel)
        return MetadataResult(True, asset_id, repo.read_asset_metadata(scope=scope, asset_id=asset_id))
    except AssetNotFound:
        return _result_error("asset_not_found")
    except RequestConflict:
        return _result_error("invalid_request")
    except (StorageError, sqlite3.Error):
        return _result_error("storage_error")
    except BridgeError as exc:
        return _result_error(exc.code)


def set_asset_metadata(*, repo: Repository, agent_id: str, asset_id: str, metadata_patch: dict,
                       source_instance_id: str = "", project_id: str = "",
                       channel: str = "") -> MetadataResult:
    try:
        scope = _validate_scope_id(agent_id, asset_id, source_instance_id, project_id, channel)
        patch = validate_patch(metadata_patch)
        metadata = repo.patch_asset_metadata(scope=scope, asset_id=asset_id, patch=patch)
        return MetadataResult(True, asset_id, metadata)
    except AssetNotFound:
        return _result_error("asset_not_found")
    except RequestConflict:
        return _result_error("invalid_request")
    except (StorageError, sqlite3.Error):
        return _result_error("storage_error")
    except BridgeError as exc:
        return _result_error(exc.code)
