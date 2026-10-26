"""Managed import: register_asset (B-10) — copy-in with byte-authority.

The managed root is a Bridge-owned folder that the operator has ALREADY
registered with local-search outside the Bridge (docs/10: folder endpoints
exist; the Bridge never calls them — local-search discovers new files via
its watcher, so the Bridge writes only inside its own root, never into a
user folder and never into the provider's databases).

Order of operations (each is a gate; the whole thing runs under a
process-local import lock so two Bridges on one root cannot interleave):

1. feature gate: managed_folder_id/managed_root configured (opt-in; the
   default None refuses — managed storage is never implicit);
2. provider revalidation of the SOURCE: kind==image, available, and the
   docs/11 §5 evidence set must not contradict what we stored (a stale
   source is copied never; the locator is transitioned truthfully);
   declared size/pixels within caps BEFORE any fetch;
3. bounded fetch through the provider HTTP client (Range + streaming
   re-measure inside, T-11);
4. byte-authority MIME gate: magic-sniffed type must be an allowed image;
   heic/heif typed-reject; a lying Content-Type cannot smuggle bytes
   (identical rule as get_asset — one policy, docs/12 §3);
5. SHA-256 over the exact bytes we verified;
6. dedup: an asset with the same (provider_instance, content_sha256) whose
   managed file still exists answers reuse (same asset_id, link added);
7. atomic copy: write under <root>/.tmp/, fsync file, rename to
   <root>/aa/<sha256>.<ext>; fsync directory. tmp files are crash garbage
   — GC is a separate authorized operation (docs/08); the rename target is
   NEVER overwritten (existing managed path for this digest = reuse).
8. SQLite truth: asset(origin=managed, content_sha256, relative_managed_path
   — relative only, docs/12 §6) + locator(provider_file_id NULL = pending per
   the docs/11 §3 freeze, provider_folder_id = managed folder evidence). The
   SOURCE file_id is deliberately NOT stored on the locator: it is not the
   copy's identity, and recording it would tempt an auto-rebind. The copy's
   own file_id is resolved later by reconciliation (B-11/B-12) via size/mtime
   evidence inside managed_folder_id — never by guessing.
9. link LAST (same atomicity principle as B-07: links are the last write).

Paths: only relative paths are stored/returned; the absolute root never
leaves this process (T-09). No provider call happens after the fetch, so a
provider that dies mid-import still leaves either no row (crash before 8)
or a truthful pending row (crash after 8).
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from bridge.config import BridgeConfig
from bridge.errors import BridgeError, error_result
from bridge.provider import ProviderPort
from bridge.repository import Repository, StorageError, utc_now

import bridge_policy
from bridge.tools_get import _dimensions, _mime_from_magic

# one import at a time per process (docs/06 B-10: 並列importでゴミを残さない)
_IMPORT_LOCK = threading.Lock()

_EXT_FOR_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

@dataclass
class RegisterAssetResult:
    ok: bool
    asset_id: str | None = None
    link_status: str | None = None   # "pending" | "ready" | "stale"
    deduplicated: bool = False       # true when an existing managed asset was reused
    media_type: str | None = None
    error: dict | None = None


def _managed_relpath(digest: str, mime: str) -> str:
    # deterministic, content-addressed; sharded by the first hex pair so a
    # large library does not sit in one directory. '/' only, forward-slash
    # canonical (docs/03 relative_managed_path).
    return f"{digest[:2]}/{digest}.{_EXT_FOR_MIME[mime]}"


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)




def _copy_atomically(root: Path, rel: str, data: bytes) -> bool:
    """Lands `data` at root/rel WITHOUT ever overwriting an existing file.
    Returns False if the target already exists (reuse), True if written.
    Crash anywhere leaves only a .tmp file, never a partial final file."""
    final = root / rel
    if final.exists():
        return False
    final.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_dir = root / ".tmp"
    tmp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # rename is atomic within the same filesystem; it can never clobber
        # `final` silently — if another writer beat us, rename would replace,
        # so re-check under the import lock is what guarantees no overwrite.
        if final.exists():  # pragma: no cover - lock makes this unreachable
            return False
        os.replace(tmp, final)
        _fsync_dir(final.parent)
        return True
    finally:
        # on any failure the tmp is gone; on success it has been renamed.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass


def register_asset(
    *,
    provider: ProviderPort,
    repo: Repository,
    config: BridgeConfig,
    source_instance_id: str,
    agent_id: str,
    project_id: str,
    channel: str,
    memory_ref: str,
    provider_file_id: int,
) -> RegisterAssetResult:
    def fail(code: str, *, aid: str | None = None) -> RegisterAssetResult:
        return RegisterAssetResult(ok=False, asset_id=aid,
                                   error=error_result(code)["error"])

    # ---- gate 1: opt-in feature ---------------------------------------------
    if config.managed_root is None or config.managed_folder_id is None:
        return fail("invalid_request")
    if not isinstance(provider_file_id, int) or provider_file_id <= 0:
        return fail("invalid_request")

    try:
        from bridge.repository import MEMORY_REF_RE

        if not MEMORY_REF_RE.match(str(memory_ref)):
            return fail("invalid_request")
    except Exception:
        return fail("invalid_request")

    pid = config.provider_instance_id

    with _IMPORT_LOCK:
        try:
            # ---- gate 2: source revalidation on live evidence ----------------
            # rowid reuse (docs/13: delete+re-add hands the SAME numeric id to
            # a different file) is caught by comparing the live FileDetail
            # against the stored locator evidence BEFORE any copy.
            try:
                detail = provider.file_detail(provider_file_id)
            except BridgeError as exc:
                return fail(exc.code)
            if detail.get("kind") != "image" or not detail.get("available", True):
                return fail("unsupported_media" if detail.get("kind") != "image"
                            else "asset_unavailable")
            # generation evidence comes from provider data only (docs/11 §5):
            # one status probe per import; a fault leaves it None (the
            # pending locator simply carries less evidence, and resolution
            # re-reads). No caller-provided generation is trusted.
            try:
                st = provider.status()
                gen = (st.get("index") or {}).get("generation")
                live_generation = None if gen is None else str(gen)
            except BridgeError:
                live_generation = None

            declared = detail.get("size")
            if declared is not None and not bridge_policy.content_size_acceptable(
                int(declared)
            ):
                return fail("media_too_large")

            # a prior external locator on the SOURCE file (B-07 link) supplies
            # staleness evidence; rowid reuse is caught by its last_seen_* set.
            existing = repo.locator_evidence_for_file(pid, provider_file_id)
            if existing:
                ev = existing[0]
                # docs/11 §5 evidence conflict => stale + refuse. NOTE: mtime
                # drift is NOT a conflict here (an indexed edit legitimately
                # changes it); kind/size/name are the copy-integrity set.
                # docs/11 §5 evidence set, copy-integrity subset. Name drift is
                # a conflict UNLESS the index generation is unchanged — within
                # one generation a scan move-detection keeps the row id across
                # renames (docs/13), so name alone cannot prove reuse there.
                gen_unchanged = (
                    ev.get("provider_generation") is not None
                    and live_generation is not None
                    and str(ev["provider_generation"]) == live_generation
                )
                conflict = (
                    (ev.get("last_seen_size") is not None
                     and declared is not None
                     and int(ev["last_seen_size"]) != int(declared))
                    or (ev.get("last_seen_kind") is not None
                        and detail.get("kind") != ev["last_seen_kind"])
                    or (not gen_unchanged
                        and ev.get("last_seen_name") is not None
                        and detail.get("name") is not None
                        and ev["last_seen_name"] != detail["name"])
                )
                if conflict:
                    # id handed to a different file: refuse, mark stale truth
                    try:
                        from bridge.statemachine import apply_transition

                        apply_transition(
                            repo, asset_id=ev["asset_id"],
                            provider_instance_id=pid,
                            src=ev["status"], dst="stale",
                        )
                    except Exception:
                        pass
                    return fail("asset_unavailable", aid=ev["asset_id"])

            # ---- gate 3: bounded fetch (Range inside the client) -------------
            try:
                data, served_mime = provider.file_content(
                    provider_file_id, config.max_fetch_bytes
                )
            except BridgeError as exc:
                return fail("source_unreadable" if exc.code in (
                    "asset_unavailable", "dependency_unavailable") else exc.code)

            # ---- gate 4: byte-authority MIME (same policy as get_asset) ------
            sniffed = _mime_from_magic(data)
            if sniffed is None or sniffed in bridge_policy.TYPED_REJECT_MIME \
                    or sniffed not in bridge_policy.ALLOWED_IMAGE_MIME:
                return fail("unsupported_media")
            if not bridge_policy.content_size_acceptable(len(data)):
                return fail("media_too_large")
            dims = _dimensions(data, sniffed)
            if dims is not None and not bridge_policy.pixels_within_cap(*dims):
                return fail("media_too_large")

            # ---- gate 5: digest of the exact verified bytes -------------------
            digest = hashlib.sha256(data).hexdigest()
            rel = _managed_relpath(digest, sniffed)

            # ---- gate 6/7: dedup, then atomic no-overwrite copy --------------
            prior = repo.find_managed_asset_by_hash(digest)
            dedup = False
            if prior is not None and prior["relative_managed_path"]:
                if (config.managed_root / prior["relative_managed_path"]).exists():
                    asset_id = prior["asset_id"]
                    dedup = True
                else:
                    # DB says managed, file is gone: never overwrite blindly
                    # with different provenance; audit (B-12) owns this case.
                    return fail("asset_unavailable", aid=prior["asset_id"])
            else:
                asset_id = str(uuid.uuid4())
                try:
                    _copy_atomically(config.managed_root, rel, data)
                except OSError:
                    return fail("storage_full")

            # ---- gate 8: SQLite truth (asset + pending locator) --------------
            # managed_folder_id is recorded as provider_folder_id evidence:
            # where local-search should be able to find the copy. The copy's
            # own file_id is resolved by B-11/B-12 reconciliation via
            # (folder_id, name/size/mtime) — never guessed here.
            try:
                if repo.asset(asset_id, pid) is None:
                    repo.upsert_asset_with_locator(
                        asset_id=asset_id, origin="managed", media_kind="image",
                        media_type=sniffed, size_bytes=len(data),
                        relative_managed_path=rel,
                        provider_instance_id=pid,
                        provider_file_id=None,
                        provider_generation=live_generation,
                        provider_folder_id=config.managed_folder_id,
                        last_seen_kind="image",
                        # Resolution evidence the provider can actually match for
                        # a content-addressed copy: size + kind + generation.
                        # mtime is deliberately NOT stored yet — the copy's mtime
                        # is its COPY time, which the watcher rewrites as it
                        # scans; last_seen_mtime is filled at bind time from the
                        # provider's own value (docs/11 §5: provider data only).
                        last_seen_size=len(data),
                        status="pending",
                    )
                    repo.set_asset_digest(asset_id=asset_id,
                                          content_sha256=digest)
            except StorageError:
                return fail("asset_unavailable", aid=asset_id)

            # ---- gate 9: link LAST --------------------------------------------
            try:
                repo.link(asset_id=asset_id, memory_ref=memory_ref,
                          source_instance_id=source_instance_id,
                          agent_id=agent_id, project_id=project_id,
                          channel=channel)
            except StorageError:
                return fail("invalid_request", aid=asset_id)

            return RegisterAssetResult(
                ok=True, asset_id=asset_id,
                link_status=repo.asset(asset_id, pid)["locator_status"],
                deduplicated=dedup, media_type=sniffed,
            )
        except BridgeError as exc:
            return fail(exc.code)
