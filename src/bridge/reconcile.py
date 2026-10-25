"""Pending resolution & revalidation loop (B-11, docs/06).

Premise (verified here, not assumed): managed imports only become resolvable
because the operator registered the managed root as a local-search folder
WITH watching on. resolve_pending() verifies that premise every run; an
unregistered/unwatched root leaves pendings pending and says so — it never
marks anything ready (存在しないファイルready禁止) and never calls folder
mutation endpoints (local-search's watcher owns discovery; docs/13).

One provider file can legitimately answer multiple pendings ONLY if their
sizes differ — never. Size is the join evidence: a copy's provider-visible
facts at scan time are (kind=image, size=copy bytes, mtime=copy time). The
resolver binds exactly when:
  - folder ledger says registered + watch on
  - exactly one folder-inventory entry matches the pending's last_seen_size
    AND kind, and is inside the configured managed folder
  - its FileDetail confirms kind/available and the same size (the provider
    itself re-asserts the match; the listing alone is never enough)
Any ambiguity (0 or >=2 matches, detail disagreement, transient fault)
leaves the pending untouched with a truthful reason. No timeouts force a
bind: an unindexed file is NOT ready, ever.

revalidate() applies the docs/11 §5 verdict through the state machine, and
transient faults never change status (切断は欠落ではない).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bridge.config import BridgeConfig
from bridge.errors import BridgeError
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError
from bridge.statemachine import apply_transition, verdict_from_detail, verdict_from_fault

MAX_BINDS_PER_RUN = 50  # one call is a bounded page, not an unbounded scan


@dataclass
class ResolveReport:
    """Structured outcome of one resolution pass — the unified log of B-11,
    codes only (no paths, no provider messages; docs/12 §6)."""

    ok: bool
    premise_ready: bool                     # folder registered AND watched
    checked: int = 0                        # pendings examined
    bound: list[dict] = field(default_factory=list)      # [{asset_id,file_id}]
    waiting: list[dict] = field(default_factory=list)    # [{asset_id, reason}]
    error: dict | None = None


def _premise(provider: ProviderPort, config: BridgeConfig) -> tuple[bool, str]:
    if config.managed_folder_id is None or config.managed_root is None:
        return False, "disabled"
    try:
        folders = provider.folders()
    except BridgeError as exc:
        return False, "provider_" + exc.code
    match = next((f for f in folders
                  if f.get("id") == config.managed_folder_id), None)
    if match is None:
        return False, "folder_unregistered"
    if not match.get("watch"):
        return False, "folder_unwatched"
    return True, "ok"


def resolve_pending(
    *,
    provider: ProviderPort,
    repo: Repository,
    config: BridgeConfig,
) -> ResolveReport:
    if config.managed_folder_id is None:
        return ResolveReport(ok=True, premise_ready=False)
    ready_premise, why = _premise(provider, config)
    report = ResolveReport(ok=True, premise_ready=ready_premise)
    if not ready_premise:
        report.waiting.append({"asset_id": None, "reason": "premise_" + why})
        pendings = repo.folder_pending_assets(
            config.provider_instance_id, config.managed_folder_id, limit=1)
        report.checked = 0 if not pendings else 0
        return report

    pendings = repo.folder_pending_assets(
        config.provider_instance_id, config.managed_folder_id,
        limit=MAX_BINDS_PER_RUN * 2,
    )
    report.checked = len(pendings)
    if not pendings:
        return report

    try:
        inventory = provider.list_folder(
            config.managed_folder_id, limit=1000)
    except BridgeError as exc:
        report.waiting = [
            {"asset_id": p["asset_id"], "reason": "provider_" + exc.code}
            for p in pendings
        ]
        return report

    for pending in pendings:
        if len(report.bound) >= MAX_BINDS_PER_RUN:
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "page_full"})
            continue
        size = pending["size_bytes"]
        matches = [
            item for item in inventory
            if item.get("kind") == "image"
            and size is not None
            and item.get("size") is not None
            and int(item["size"]) == int(size)
        ]
        if len(matches) == 0:
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "not_indexed_yet"})
            continue
        if len(matches) > 1:
            # ambiguous: two same-size copies (identical bytes dedup upstream,
            # so this means foreign files) — never guess (docs/13 fail-closed)
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "ambiguous_size"})
            continue
        candidate = matches[0]
        file_id = int(candidate["file_id"])
        # the listing alone is never enough: the detail probe must confirm
        try:
            detail = provider.file_detail(file_id)
        except BridgeError as exc:
            report.waiting.append(
                {"asset_id": pending["asset_id"],
                 "reason": "provider_" + exc.code})
            continue
        if (detail.get("kind") != "image"
                or not detail.get("available", True)
                or detail.get("size") is None
                or int(detail["size"]) != int(size)
                or int(detail.get("folder_id", -1)) != int(config.managed_folder_id)):
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "detail_mismatch"})
            continue
        try:
            repo.bind_pending_locator(
                asset_id=pending["asset_id"],
                provider_instance_id=config.provider_instance_id,
                provider_file_id=file_id,
                last_seen_kind="image",
                last_seen_name=detail.get("name"),
                last_seen_mtime=detail.get("mtime"),
                last_seen_size=int(detail["size"]),
            )
        except StorageError:
            # concurrent bind/unique-index bite: leave pending, log code only
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "bind_conflict"})
            continue
        report.bound.append({"asset_id": pending["asset_id"],
                             "file_id": file_id})
    return report


@dataclass
class RevalidateReport:
    ok: bool
    checked: int = 0
    transitions: list[dict] = field(default_factory=list)  # asset_id/src/dst
    faults: list[dict] = field(default_factory=list)       # asset_id/code


def revalidate(
    *,
    provider: ProviderPort,
    repo: Repository,
    config: BridgeConfig,
    assets: list[dict],
) -> RevalidateReport:
    """`assets`: [{asset_id, evidence}] pairs (evidence = stored locator row).
    B-12 owns producing the list; this applies the frozen verdict."""
    report = RevalidateReport(ok=True)
    pid = config.provider_instance_id
    for entry in assets:
        report.checked += 1
        asset_id = entry["asset_id"]
        evidence = entry["evidence"]
        file_id = evidence.get("provider_file_id")
        src = evidence.get("status") or repo.locator_status(asset_id, pid)
        if file_id is None or src in (None, "incompatible"):
            continue
        try:
            detail = provider.file_detail(int(file_id))
        except BridgeError as exc:
            dst = verdict_from_fault(exc.code)
            if not dst:  # transient: status untouched (切断を欠落にしない)
                report.faults.append({"asset_id": asset_id, "code": exc.code})
                continue
            try:
                apply_transition(repo, asset_id=asset_id,
                                 provider_instance_id=pid, src=src, dst=dst)
                report.transitions.append(
                    {"asset_id": asset_id, "src": src, "dst": dst})
            except StorageError as sexc:
                report.faults.append({"asset_id": asset_id,
                                      "code": "storage_" + str(sexc)})
            continue
        dst = verdict_from_detail(detail, evidence)
        if dst == src:
            continue  # no-change verdicts never write (no forged freshness)
        try:
            apply_transition(
                repo, asset_id=asset_id, provider_instance_id=pid,
                src=src, dst=dst,
                last_seen_mtime=detail.get("mtime"),
                last_seen_size=(None if detail.get("size") is None
                                else int(detail["size"])),
                last_seen_name=detail.get("name"),
            )
            report.transitions.append({"asset_id": asset_id,
                                       "src": src, "dst": dst})
        except StorageError as sexc:
            report.faults.append({"asset_id": asset_id,
                                  "code": "storage_" + str(sexc)})
    return report
