"""Locator reconcile / repair / audit (B-12, docs/06).

Three verbs, one shared verdict engine (statemachine, docs/11 §5):

audit()      READ-ONLY. Classifies every locator and every managed file on
             disk against the provider ledger and the Bridge DB, WITHOUT
             writing anything. This is the default reconcile.
reconcile()  dry-run by default (apply=False). When applying, it performs
             ONLY state transitions from the frozen graph — it never binds,
             never swaps provider_file_id (D-08: a stale locator is NOT
             silently re-associated with a different file; re-binding is an
             explicit unlink + relink by a caller with the real file id),
             and never deletes a link (docs/11 §4: link = user intent).
report codes are machine tokens only — no paths, no provider messages
(docs/12 §6; paths are hashed-free by simply not being carried).

Managed-copy orphan detection is also report-only: deleting managed copies
is a separate authorized operation (docs/08) and does NOT live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bridge.config import BridgeConfig
from bridge.errors import BridgeError
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError
from bridge.statemachine import apply_transition, verdict_from_detail, verdict_from_fault

SWEEP_LIMIT = 200      # locators audited per call (bounded page)
INVENTORY_LIMIT = 1000  # provider folder rows fetched per managed folder


@dataclass
class AuditReport:
    ok: bool
    applied: bool = False
    locators_checked: int = 0
    findings: list[dict] = field(default_factory=list)
    # each finding: {"ref": asset_id|"file:<id>", "code": <machine token>}
    transient_faults: int = 0
    provider_healthy: bool = True
    error: dict | None = None


def _file_evidence(detail: dict) -> dict:
    return {
        "last_seen_kind": detail.get("kind"),
        "last_seen_name": detail.get("name"),
        "last_seen_mtime": detail.get("mtime"),
        "last_seen_size": detail.get("size"),
    }


def audit(*, provider: ProviderPort, repo: Repository,
          config: BridgeConfig) -> AuditReport:
    """Read-only classification. apply is hard-wired False by simply never
    touching the DB except through SELECTs."""
    report = AuditReport(ok=True, applied=False)
    pid = config.provider_instance_id
    try:
        # every status except pending (pending belongs to resolve_pending —
        # the file id there is NULL by contract and there is nothing to
        # revalidate) and incompatible (terminal: provider probes on it only
        # re-pay a request and never move state back, docs/11 §4)
        rows = repo.sweep_locators(
            pid, statuses=("ready", "stale", "missing"), limit=SWEEP_LIMIT)
    except StorageError:
        report.ok = False
        report.error = {"code": "storage_error"}
        return report
    report.locators_checked = len(rows)
    for row in rows:
        aid, ev = row["asset_id"], row["evidence"]
        src = ev["status"]
        try:
            detail = provider.file_detail(int(ev["provider_file_id"]))
        except BridgeError as exc:
            dst = verdict_from_fault(exc.code)
            if not dst:
                report.transient_faults += 1
                continue
            report.findings.append({"ref": aid, "code": src + "_to_" + dst})
            continue
        dst = verdict_from_detail(detail, ev)
        if dst != src:
            report.findings.append({"ref": aid, "code": src + "_to_" + dst})
    report.provider_healthy = not any(
        f["code"].endswith("_to_incompatible") for f in report.findings)
    _managed_copy_audit(repo=repo, config=config, report=report)
    return report


def _managed_copy_audit(*, repo: Repository, config: BridgeConfig,
                        report: AuditReport) -> None:
    """Compare the managed directory contents against the DB (docs/08 orphan
    rules). READ-ONLY, and report-only: removal is a separate authorized
    operation. Path-shaped refs never appear — only asset ids and the
    file NAME of foreign entries (a copy filename is a SHA-256 digest, not
    a user path; foreign names are reported by basename so operators can
    recognise their own stray files)."""
    if config.managed_root is None or config.managed_folder_id is None:
        return
    import re

    digest_re = re.compile(r"^[0-9a-f]{64}\.(jpg|jpeg|png|webp|gif)$")
    known = repo.managed_asset_paths(config.provider_instance_id)
    known_names = {p.rsplit("/", 1)[-1] for p in known.values()}
    try:
        listing = {
            str(rel): stat
            for rel, stat in _walk(config.managed_root).items()
        }
    except OSError:
        report.findings.append({"ref": None, "code": "managed_root_unreadable"})
        return
    for rel, is_file in listing.items():
        base = rel.rsplit("/", 1)[-1]
        if not is_file:
            continue
        if base in known_names:
            continue
        if rel.startswith(".tmp/") or base.endswith(".part"):
            report.findings.append({"ref": None, "code": "leftover_tmp"})
            continue
        if digest_re.match(base):
            # digest-shaped but no DB row: an orphan managed copy
            report.findings.append({"ref": None, "code": "orphan_managed_copy"})
        elif base == ".tmp" or base.endswith(".part"):
            report.findings.append({"ref": None, "code": "leftover_tmp"})
        else:
            # foreign file inside the managed root — operator's own stray;
            # never touched, just named (basename only)
            report.findings.append({"ref": None, "code": "foreign_in_managed_root",
                                    "name": base})
    # DB rows whose copy is gone from disk
    disk = set(listing)
    for aid, rel in known.items():
        if rel not in disk:
            report.findings.append({"ref": aid, "code": "managed_copy_missing"})


def _walk(root) -> dict:
    """Relative POSIX paths -> is_file, excluding the scratch dir itself."""
    out: dict[str, bool] = {}
    stack = [""]
    import os

    while stack:
        rel = stack.pop()
        full = os.path.join(root, rel) if rel else str(root)
        try:
            entries = os.scandir(full)
        except OSError:
            return out
        with entries:
            for e in entries:
                r = f"{rel}/{e.name}" if rel else e.name
                if r == ".tmp":
                    # scan the scratch dir's CONTENTS (leftover .part is a
                    # finding), but the directory itself is not an entry
                    stack.append(r)
                    continue
                out[r] = e.is_file(follow_symlinks=False)
                if e.is_dir(follow_symlinks=False):
                    stack.append(r)
    return out


def _managed_copy_present(repo: Repository, config: BridgeConfig,
                          aid: str) -> bool:
    """True when the managed copy of this asset exists on disk under our root
    (drive ejected but the copy survived — honest provider-wise still
    `missing`; serving from the copy is a separate product decision,
    docs/08 — never a state trick here)."""
    if config.managed_root is None:
        return False
    rel = repo.managed_asset_paths(config.provider_instance_id).get(aid)
    if rel is None:
        return False
    try:
        return (config.managed_root / rel).is_file()
    except OSError:
        return False


def reconcile(*, provider: ProviderPort, repo: Repository,
              config: BridgeConfig, apply: bool = False) -> AuditReport:
    """Locate drift; optionally apply ONLY legal status transitions. The
    verdict is computed once and applied through the state machine, which
    re-checks the observed src under the lock — a drift that healed between
    classify and apply simply raises and lands as `concurrent_change`."""
    report = audit(provider=provider, repo=repo, config=config)
    report.applied = apply
    if not apply or not report.ok:
        return report
    pid = config.provider_instance_id
    remaining = []
    for f in report.findings:
        code = f["code"]
        src, _, dst = code.partition("_to_")
        # repair = honest transitions from the classify pass. ready is a
        # LEGAL target (stale->ready / missing->ready, docs/11 §4) only via
        # this apply path — audit itself never writes. Anything else (e.g.
        # a hypothetical *_to_pending) is refused and left reported.
        if dst not in ("stale", "missing", "incompatible", "ready"):
            remaining.append(f)
            continue
        aid = f["ref"]
        if repo.locator_status(aid, pid) != src:
            remaining.append({"ref": aid, "code": "concurrent_change"})
            continue
        # A drift that healed since classify is NOT vetoed by re-probing the
        # provider here: a second read is a NEW observation, and re-verdicting
        # from a partially-consumed fault state could confirm a stale verdict
        # from a stale snapshot (the exact wrong-image class of bug). The
        # safety net is apply_transition: check-and-set inside the repository
        # lock, where the observed src is re-verified before the write.
        if dst == "missing" and _managed_copy_present(repo, config, aid):
            remaining.append({"ref": aid, "code": "missing_copy_on_disk"})
            continue
        try:
            apply_transition(repo, asset_id=aid, provider_instance_id=pid,
                             src=src, dst=dst)
        except StorageError:
            remaining.append({"ref": aid, "code": "concurrent_change"})
    report.findings = remaining
    return report
