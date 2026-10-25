"""Locator state machine (B-11): the frozen graph of docs/11 §4, enforced.

Every status change goes through apply_transition, so no runtime path can
invent an edge the contract does not admit:

    pending -> ready | stale | missing | incompatible
    ready   -> stale | missing | incompatible
    stale   -> ready | missing | incompatible
    missing -> ready | incompatible
    incompatible      (terminal: re-resolution means explicit re-link)

The `src` argument is the status the caller OBSERVED; apply_transition
re-checks it inside the repository lock, so a concurrent verdict that moved
the row makes this refuse rather than clobber. provider_file_id is never
written here at all (D-08; only bind_pending_locator may fill it, while
pending).

Verdicts (docs/11 §5 evidence set) come in two flavours: from a live
FileDetail (verdict_from_detail) or from a BridgeError code the probe
raised (verdict_from_fault) — transient faults return "" because 切断を
欠落にしない: an unreachable provider NEVER demotes stored truth.
"""

from __future__ import annotations

from bridge.repository import Repository, StorageError

# docs/11 §4 verbatim, plus * -> incompatible, minus self-loops (a "no change"
# verdict never calls this — writing verified_at on every glance would forge
# freshness).
ALLOWED: dict[str, frozenset[str]] = {
    "pending": frozenset({"ready", "stale", "missing", "incompatible"}),
    "ready": frozenset({"stale", "missing", "incompatible"}),
    "stale": frozenset({"ready", "missing", "incompatible"}),
    "missing": frozenset({"ready", "incompatible"}),
    "incompatible": frozenset(),
}


def can_transition(src: str, dst: str) -> bool:
    return dst in ALLOWED.get(src, frozenset())


def apply_transition(
    repo: Repository,
    *,
    asset_id: str,
    provider_instance_id: str,
    src: str,
    dst: str,
    provider_generation: str | None = None,
    last_seen_mtime: str | None = None,
    last_seen_size: int | None = None,
    last_seen_name: str | None = None,
) -> None:
    if not can_transition(src, dst):
        raise StorageError(f"illegal transition {src}->{dst}")
    with repo.lock:  # check-and-set atomically against concurrent verdicts
        current = repo.locator_status(asset_id, provider_instance_id)
        if current != src:
            raise StorageError("locator changed concurrently")
        repo.set_locator_status(
            asset_id=asset_id, provider_instance_id=provider_instance_id,
            status=dst, provider_generation=provider_generation,
            last_seen_mtime=last_seen_mtime, last_seen_size=last_seen_size,
            last_seen_name=last_seen_name,
        )


# ---------------------------------------------------------------------------
# Evidence verdicts (docs/11 §5 frozen set), shared by B-11 resolution and
# B-12 reconciliation — one implementation, one contract.
# ---------------------------------------------------------------------------

# provider/API states that make resolution meaningless right now
INDEX_FATAL_CODES = frozenset({"index_incompatible"})
# the provider itself says the file is gone or unreachable
GONE_CODES = frozenset({"asset_unavailable"})


def verdict_from_detail(detail: dict, evidence: dict) -> str:
    """Compare a live FileDetail against the stored locator row and return
    the admissible target status for a locator that HAS a provider_file_id.
    mtime drift is NOT a conflict (docs/11 §5.3: an indexed edit legitimately
    changes mtime while the file stays searchable and available)."""
    if detail.get("kind") != evidence.get("last_seen_kind"):
        return "stale"
    ev_size = evidence.get("last_seen_size")
    if ev_size is not None and detail.get("size") is not None \
            and int(ev_size) != int(detail["size"]):
        return "stale"
    if not detail.get("available", True):
        return "missing"
    status = detail.get("status")
    if status is not None and status not in ("indexed", "pending"):
        return "stale"
    return "ready"


def verdict_from_fault(code: str) -> str:
    """Maps a probe's BridgeError code to a status, or "" when NO transition
    is admissible. Transient faults (dependency_unavailable, storage_error,
    provider timeouts) must never degrade state (B-11: 切断を欠落にしない)."""
    if code in INDEX_FATAL_CODES:
        return "incompatible"
    if code in GONE_CODES:
        return "missing"
    return ""
