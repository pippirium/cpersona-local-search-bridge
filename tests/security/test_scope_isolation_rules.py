"""B-03 T-01/T-02: scope-isolation and anti-enumeration rules (pattern level).

The storage-level enforcement is B-02's (tests/unit/test_schema_contract.py);
tool-level identical-error-shape checks land with B-07/B-08. This module pins
the *rules* documents/08, /11, /12 froze:

- a scope tuple is exactly four strings; only None->"" normalization exists;
- two different tuples never match, including '' vs 'X' asymmetry;
- cross-scope misses answer the SAME asset_not_found shape as unknown ids;
- scope names may be sensitive: logs carry only the truncated hash.
"""

from __future__ import annotations

import hashlib

EMPTY_TUPLE = ("", "", "", "")


def normalize_scope(source_instance_id, agent_id, project_id, channel):
    """D-16: normalization is None -> "" and nothing else (no trim/lower/fold)."""
    out = []
    for value in (source_instance_id, agent_id, project_id, channel):
        out.append("" if value is None else value)
    source, agent, project, chan = out
    if not agent:
        raise ValueError("agent_id required")
    return (source, agent, project, chan)


def scope_equal(a, b):
    return tuple(a) == tuple(b)


def log_scope_hash(scope) -> str:
    return hashlib.sha256("\x1f".join(scope).encode("utf-8")).hexdigest()[:12]


# ---- rules --------------------------------------------------------------------

def test_normalization_only_maps_none():
    assert normalize_scope(None, "a", None, None) == ("", "a", "", "")
    assert normalize_scope("", "a", "P", "c") == ("", "a", "P", "c")
    # deliberately different, never folded:
    assert normalize_scope("", "a", "Proj", "") != normalize_scope("", "a", "proj", "")
    assert normalize_scope("", " a", "", "") != normalize_scope("", "a", "", "")


def test_agent_id_required():
    for bad in ("", None):
        try:
            normalize_scope("", bad, "", "")
        except ValueError:
            continue
        raise AssertionError(f"agent_id={bad!r} must be refused")


def test_cpersona_widening_is_not_mirrored():
    # CPersona reads would widen channel 'c' to include ''; Bridge must not.
    strict = normalize_scope("", "a", "p", "c")
    global_filed = normalize_scope("", "a", "", "")
    assert not scope_equal(strict, global_filed)
    # same for γ-style global project: exact equality only
    assert not scope_equal(normalize_scope("", "a", "X", ""), ("", "a", "", ""))


def test_asset_not_found_shape_is_identical():
    """T-02: 'unknown id' and 'id exists in another scope' produce byte-identical
    responses so existence cannot be probed across scopes."""

    def not_found(asset_id: str) -> dict:
        return {"error": {"code": "asset_not_found", "message": "No such asset in this scope."}}

    unknown = not_found("11111111-1111-4111-8111-111111111111")
    cross_scope = not_found("22222222-2222-4222-8222-222222222222")
    # same shape, no fields leaking which of the two it was:
    assert set(unknown) == set(cross_scope) == {"error"}
    assert unknown["error"]["code"] == cross_scope["error"]["code"]
    assert "exists" not in unknown["error"]["message"]


def test_scope_names_never_appear_in_log_line():
    scope = normalize_scope("inst-1", "work-agent", "medical-project", "private-notes")
    line = f"event=link_ok asset_id=x scope_hash={log_scope_hash(scope)}"
    for token in scope:
        assert token not in line
    assert len(log_scope_hash(scope)) == 12
