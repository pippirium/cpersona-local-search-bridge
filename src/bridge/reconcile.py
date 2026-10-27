"""Pending resolution & revalidation loop (B-11, docs/06).

Premise (verified here, not assumed): managed imports only become resolvable
because the operator registered the managed root as a local-search folder
WITH watching on. resolve_pending() verifies that premise every run; an
unregistered/unwatched root leaves pendings pending and says so — it never
marks anything ready (存在しないファイルready禁止) and never calls folder
mutation endpoints (local-search's watcher owns discovery; docs/13).

A pending copy binds only when listing and fresh detail both match its stored
managed relative directory, basename, size, image kind, availability/status
and configured folder. Only one matching candidate is admissible. A foreign
same-size image is not identity evidence. No image bytes are read to bind.

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

RECENT_PAGE_MAX = 100  # provider RECENT_MAX (docs/10): newest-only, no paging
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
        # RECENT_MAX=100: /api/files/recent answers at most the 100 newest
        # indexed files and cannot page further. Ambiguity checks are thus
        # sound WITHIN the recent page: if the page is full, the entry count
        # is at the provider's cap and we treat size matches as ambiguous —
        # never bind from a page we know we cannot fully survey.
        inventory = provider.folder_entries(
            config.managed_folder_id, limit=RECENT_PAGE_MAX)
    except BridgeError as exc:
        report.waiting = [
            {"asset_id": p["asset_id"], "reason": "provider_" + exc.code}
            for p in pendings
        ]
        return report

    for pending in pendings:
        if inventory.truncated or len(report.bound) >= MAX_BINDS_PER_RUN:
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "page_full"})
            continue
        size = pending["size_bytes"]
        rel = pending.get("relative_managed_path") or ""
        directory, _, name = rel.rpartition("/")
        if not directory or not name:
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "identity_missing"})
            continue
        matches = [
            item for item in inventory.items
            if item.get("kind") == "image"
            and item.get("name") == name
            and item.get("rel_dir") == directory
            and item.get("folder_id") == config.managed_folder_id
            and item.get("available", True)
            and item.get("status", "indexed") in ("indexed", "pending")
            and size is not None
            and item.get("size") is not None
            and int(item["size"]) == int(size)
        ]
        if len(matches) == 0:
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "not_indexed_yet"})
            continue
        if len(matches) > 1:
            # Multiple records claim the same copy identity: never guess.
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "ambiguous_identity"})
            continue
        candidate = matches[0]
        file_id = int(candidate["file_id"])
        owned = repo.asset_has_owner(asset_id=pending["asset_id"])
        generation_before = None
        if owned:
            try:
                index = provider.status().get("index") or {}
                generation_before = index.get("generation")
                if index.get("mismatch") or generation_before is None:
                    raise BridgeError("index_incompatible")
            except BridgeError:
                report.waiting.append({"asset_id": pending["asset_id"], "reason": "index_unavailable"})
                continue
        # the listing alone is never enough: the detail probe must confirm.
        # recent has no available flag guarantee beyond ResultItem; folder
        # filter happens client-side, and detail re-checks folder_id.
        try:
            detail = provider.file_detail(file_id)
        except BridgeError as exc:
            report.waiting.append(
                {"asset_id": pending["asset_id"],
                 "reason": "provider_" + exc.code})
            continue
        if (detail.get("name") != name
                or detail.get("rel_dir") != directory
                or detail.get("status") not in ("indexed", "pending")
                or detail.get("kind") != "image"
                or not detail.get("available", True)
                or detail.get("size") is None
                or int(detail["size"]) != int(size)
                or int(detail.get("folder_id", -1)) != int(config.managed_folder_id)):
            report.waiting.append(
                {"asset_id": pending["asset_id"], "reason": "detail_mismatch"})
            continue
        # New owned inputs have a byte digest; name/size evidence alone cannot
        # establish that the managed copy has not been replaced in place.
        owned_asset = repo.asset(pending["asset_id"], config.provider_instance_id)
        if owned:
            import os
            from bridge.managed_store import verify_digest
            try:
                folders = provider.folders()
                folder = next((f for f in folders if f.get("id") == config.managed_folder_id), None)
                folder_matches = (folder is not None and folder.get("watch") and folder.get("path")
                                  and os.path.samefile(config.managed_root, folder["path"]))
            except (BridgeError, OSError, TypeError, ValueError):
                folder_matches = False
            if (not folder_matches or not owned_asset or config.managed_root is None
                    or not verify_digest(config.managed_root, rel, owned_asset.get("content_sha256"), size)):
                report.waiting.append({"asset_id": pending["asset_id"], "reason": "identity_mismatch"})
                continue
        if owned:
            try:
                index_after = provider.status().get("index") or {}
                if index_after.get("mismatch") or str(index_after.get("generation")) != str(generation_before):
                    raise BridgeError("index_incompatible")
            except BridgeError:
                report.waiting.append({"asset_id": pending["asset_id"], "reason": "index_unavailable"})
                continue
        try:
            repo.bind_pending_locator(
                asset_id=pending["asset_id"],
                provider_instance_id=config.provider_instance_id,
                provider_file_id=file_id,
                provider_generation=str(generation_before) if owned else None,
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
            )
            report.transitions.append({"asset_id": asset_id,
                                       "src": src, "dst": dst})
        except StorageError as sexc:
            report.faults.append({"asset_id": asset_id,
                                  "code": "storage_" + str(sexc)})
    return report
