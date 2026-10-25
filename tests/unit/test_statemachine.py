"""B-11: frozen transition graph, check-and-set concurrency, evidence verdict
(docs/11 §4/§5) — the state machine exists before the resolver runs."""

from __future__ import annotations

import threading

import pytest

from bridge.provider import FakeFile, FakeProvider
from bridge.repository import Repository, StorageError, open_repository
from bridge.statemachine import (
    ALLOWED,
    apply_transition,
    can_transition,
    verdict_from_detail,
    verdict_from_fault,
)

P = "http-127.0.0.1:18765"


@pytest.fixture
def repo(tmp_path):
    conn = open_repository(tmp_path)
    r = Repository(conn)
    yield r
    r.close()


def seed(r, status="pending", file_id=None):
    import uuid

    aid = str(uuid.uuid4())
    r.upsert_asset_with_locator(
        asset_id=aid, origin="external", media_kind="image",
        provider_instance_id=P,
        provider_file_id=file_id if status != "pending" else None,
        last_seen_kind="image", last_seen_name="x.png",
        last_seen_size=100, status=status,
    )
    return aid


# ---- graph ---------------------------------------------------------------------

def test_graph_matches_docs11():
    assert can_transition("pending", "ready")
    assert can_transition("pending", "stale")
    assert can_transition("pending", "missing")
    assert can_transition("pending", "incompatible")
    assert can_transition("ready", "stale")
    assert can_transition("ready", "missing")
    assert can_transition("stale", "ready")
    assert can_transition("missing", "ready")
    # forbidden: no ready->pending, nothing leaves incompatible, no self-loop
    assert not can_transition("ready", "pending")
    assert not can_transition("incompatible", "ready")
    assert not can_transition("ready", "ready")
    assert ALLOWED["incompatible"] == frozenset()


def test_illegal_transition_rejected_by_apply(repo):
    aid = seed(repo, status="ready", file_id=1)
    with pytest.raises(StorageError):
        apply_transition(repo, asset_id=aid, provider_instance_id=P,
                         src="ready", dst="pending")
    assert repo.locator_status(aid, P) == "ready"


def test_legal_transition_applies(repo):
    aid = seed(repo, status="ready", file_id=1)
    apply_transition(repo, asset_id=aid, provider_instance_id=P,
                     src="ready", dst="stale", last_seen_size=55)
    assert repo.locator_status(aid, P) == "stale"


def test_check_and_set_against_concurrent_change(repo):
    aid = seed(repo, status="ready", file_id=1)
    # observed src is stale: row already moved -> refuse, keep the NEW verdict
    repo.set_locator_status(asset_id=aid, provider_instance_id=P,
                            status="missing")
    with pytest.raises(StorageError):
        apply_transition(repo, asset_id=aid, provider_instance_id=P,
                         src="ready", dst="stale")
    assert repo.locator_status(aid, P) == "missing"


def test_concurrent_races_commit_exactly_one_legal_chain(repo):
    aid = seed(repo, status="ready", file_id=1)
    results = []

    def racer(dst):
        try:
            apply_transition(repo, asset_id=aid, provider_instance_id=P,
                             src="ready", dst=dst)
            results.append(dst)
        except StorageError:
            results.append(None)

    ts = [threading.Thread(target=racer, args=(d,)) for d in
          ("stale", "stale", "missing", "missing", "stale")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("stale") + results.count("missing") == 1  # exactly one won
    assert repo.locator_status(aid, P) in ("stale", "missing")


# ---- evidence verdict ------------------------------------------------------------

EV = {"last_seen_kind": "image", "last_seen_size": 100, "last_seen_name": "x.png"}


def test_verdict_ready():
    assert verdict_from_detail(
        {"kind": "image", "size": 100, "available": True, "status": "indexed"},
        EV) == "ready"


def test_verdict_mtime_drift_stays_ready():
    # docs/11 §5.3: an indexed edit legitimately changes mtime; evidence set
    # (kind/size/available) still matches -> ready
    assert verdict_from_detail(
        {"kind": "image", "size": 100, "available": True, "mtime": "new"},
        EV) == "ready"


def test_verdict_size_conflict_stale():
    assert verdict_from_detail(
        {"kind": "image", "size": 999, "available": True}, EV) == "stale"


def test_verdict_kind_conflict_stale():
    assert verdict_from_detail(
        {"kind": "video", "size": 100, "available": True}, EV) == "stale"


def test_verdict_unavailable_missing():
    assert verdict_from_detail(
        {"kind": "image", "size": 100, "available": False}, EV) == "missing"


def test_verdict_from_fault():
    # probe faults arrive in BRIDGE taxonomy (errors.py maps provider codes)
    assert verdict_from_fault("dependency_unavailable") == ""  # transient
    assert verdict_from_fault("storage_error") == ""           # transient
    assert verdict_from_fault("asset_unavailable") == "missing"  # 404/409 mapped
    assert verdict_from_fault("index_incompatible") == "incompatible"
