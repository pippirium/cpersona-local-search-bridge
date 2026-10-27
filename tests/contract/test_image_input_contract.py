"""IMG-01 design fixtures: validate wire shapes without implementing tools."""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

CONTRACT = json.loads((Path(__file__).resolve().parents[2] /
                       "fixtures/contracts/image-input/requests.json").read_text())
TOOLS = CONTRACT["tools"]
ASSET = "b519a9d4-9f85-469c-b603-c8ce2c6a54c3"


def valid(tool, value):
    Draft202012Validator(TOOLS[tool], format_checker=FormatChecker()).validate(value)


def invalid(tool, value):
    assert list(Draft202012Validator(TOOLS[tool], format_checker=FormatChecker()).iter_errors(value))


def test_schemas_are_well_formed():
    for schema in TOOLS.values():
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(("tool", "args"), [
    ("ingest_image", {"image_base64": "eA=="}),
    ("import_staged_image", {"staging_id": "opaque"}),
    ("get_ingest_status", {"asset_id": ASSET}),
    ("discover_assets", {"query": "synthetic"}),
    ("search_assets_by_image", {"query_asset_id": ASSET}),
    ("link_registered_asset", {"asset_id": ASSET, "memory_ref": "mem:42"}),
    ("set_asset_metadata", {"asset_id": ASSET, "metadata_patch": {"source_kind": "local_selection"}}),
    ("get_asset_metadata", {"asset_id": ASSET}),
])
def test_each_request_requires_scope_and_rejects_paths(tool, args):
    invalid(tool, args)
    request = {"agent_id": "fixture", **args}
    valid(tool, request)
    for key in ("path", "url"):
        invalid(tool, {**request, key: "synthetic"})


def test_mutually_exclusive_image_and_candidate_inputs():
    for tool, first, second, common in [
        ("search_assets_by_image", {"query_asset_id": ASSET}, {"image_base64": "eA=="}, {}),
        ("link_registered_asset", {"asset_id": ASSET}, {"candidate_token": "opaque"}, {"memory_ref": "ep:7"}),
    ]:
        scope = {"agent_id": "fixture", **common}
        invalid(tool, scope)
        valid(tool, {**scope, **first})
        valid(tool, {**scope, **second})
        invalid(tool, {**scope, **first, **second})


def test_metadata_patch_unknown_fields_and_explicit_null():
    request = {"agent_id": "fixture", "asset_id": ASSET,
               "metadata_patch": {"user_caption": None}}
    valid("set_asset_metadata", request)
    bad = copy.deepcopy(request)
    bad["metadata_patch"]["unknown"] = "value"
    invalid("set_asset_metadata", bad)
    bad["metadata_patch"] = {"captured_at": "not-a-date"}
    invalid("set_asset_metadata", bad)


def test_wire_bounds_and_invalid_references():
    invalid("ingest_image", {"agent_id": "fixture", "image_base64": "!bad"})
    invalid("discover_assets", {"agent_id": "fixture", "query": "x", "limit": 21})
    invalid("link_registered_asset", {"agent_id": "fixture", "asset_id": ASSET,
                                      "memory_ref": "mem:0"})


def test_response_schemas_and_error_envelopes():
    responses = CONTRACT["responses"]
    assert set(responses) == set(TOOLS)
    for schema in responses.values():
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate({"ok": False, "error": {"code": "asset_not_found"}})
        assert list(validator.iter_errors({"ok": False, "error": {"code": "asset_not_found"},
                                          "asset_id": ASSET}))


def test_success_response_requires_id_and_state_without_extra_leak():
    validator = Draft202012Validator(CONTRACT["responses"]["ingest_image"])
    result = {"ok": True, "asset_id": ASSET, "ingest_status": "pending",
              "deduplicated": False, "media_type": "image/png", "size_bytes": 42}
    validator.validate(result)
    assert list(validator.iter_errors({**result, "path": "synthetic"}))
    assert list(validator.iter_errors({key: val for key, val in result.items() if key != "asset_id"}))
