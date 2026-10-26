"""get_asset: the ONLY media path (B-09, docs/04/12 §4).

MCP clients can see image bytes only through an explicit get_asset call;
recall/reconstruct/search never inline media (docs/01). The order below is
the security contract, each step a hard gate:

    1. exact-scope link membership     (asset_in_scope; unknown id and a
                                        cross-scope id answer IDENTICALLY,
                                        anti-enumeration T-02)
    2. optional memory_ref narrowing   (docs/04)
    3. locator evidence revalidation   (fresh FileDetail: kind, availability,
                                        size/mtime/name evidence — a stale
                                        mapping is asset_unavailable, and we
                                        NEVER answer with a different file;
                                        docs/13 lifecycle model)
    4. declared bounds before fetch    (locator.last_seen_size caps checked
                                        on the *declared* evidence)
    5. fetch with byte cap enforced    (provider.file_content already
                                        Range-bounds and re-measures)
    6. MIME gate on served bytes       (allow-list from bridge_policy;
                                        heic/heif typed-reject; other
                                        unknown -> unsupported_media)
    7. dimension gate                  (provider meta width/height re-check
                                        against MAX_PIXELS; if absent, the
                                        decoded header of the actual bytes)
    8. locator evidence update         (status/verified_at only; NEVER
                                        provider_file_id)

Returns mcp Image(data=..., format=...) so bytes travel as one ImageContent
block and no structuredContent echoes them (SDK _returns_content rule).
Failures return a typed GetAssetResult with the error dict only — no bytes,
no paths, no provider messages (T-09).
"""

from __future__ import annotations

from dataclasses import dataclass

from bridge.errors import BridgeError, error_result
from bridge.provider import ProviderPort
from bridge.repository import Repository
from bridge.statemachine import apply_transition

import bridge_policy


@dataclass
class GetAssetResult:
    """Structured error result for get_asset failures. Success returns an
    Image content object instead, which carries NO structuredContent — so a
    failed fetch's error is the only case a result body exists."""

    ok: bool
    asset_id: str | None = None
    media_type: str | None = None
    error: dict | None = None


def _mime_from_magic(data: bytes) -> str | None:
    """Sniff the actual bytes (never trust headers alone, T-11)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (
        b"heic", b"heix", b"mif1", b"hevc", b"hevm", b"hevs"
    ):
        return "image/heic"
    return None


def _dimensions(data: bytes, mime: str) -> tuple[int, int] | None:
    """Parse width/height from the encoded bytes' header (stdlib only —
    Pillow is intentionally not pulled into the runtime path)."""
    try:
        if (
            len(data) >= 24
            and data[:8] == b"\x89PNG\r\n\x1a\n"
            and data[12:16] == b"IHDR"
        ):
            w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
            if w > 0 and h > 0:
                return (w, h)
            return None
        if mime == "image/gif" and data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return (int.from_bytes(data[6:8], "little"),
                    int.from_bytes(data[8:10], "little"))
        if (mime == "image/webp" and data[:4] == b"RIFF"
                and data[8:12] == b"VP8 " and len(data) >= 30):
            w = int.from_bytes(data[26:28], "little") & 0x3FFF
            h = int.from_bytes(data[28:30], "little") & 0x3FFF
            return (w, h)
        if (mime == "image/webp" and data[:4] == b"RIFF"
                and data[8:12] in (b"VP8L", b"VP8X") and len(data) >= 30):
            if data[8:12] == b"VP8X" and len(data) >= 24:
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return (w, h)
            # VP8L packed dimensions: 14 bits after signature byte
            bits = int.from_bytes(data[21:25], "little")
            return (1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF))
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seglen = int.from_bytes(data[i + 2 : i + 4], "big")
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h = int.from_bytes(data[i + 5 : i + 7], "big")
                    w = int.from_bytes(data[i + 7 : i + 9], "big")
                    return (w, h)
                i += 2 + seglen
        return None
    except Exception:
        return None


_FORMAT_FOR_MIME = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def get_asset(
    *,
    provider: ProviderPort,
    repo: Repository,
    provider_instance_id: str,
    source_instance_id: str,
    agent_id: str,
    project_id: str,
    channel: str,
    asset_id: str,
    memory_ref: str | None = None,
) -> "object":
    """Returns mcp Image on success, GetAssetResult on failure."""
    from mcp.server.mcpserver.utilities.types import Image

    def fail(code: str, *, aid: str | None = None,
             media: str | None = None) -> GetAssetResult:
        return GetAssetResult(ok=False, asset_id=aid, media_type=media,
                              error=error_result(code)["error"])

    try:
        # ---- gate 1: exact-scope membership (T-01/T-02) ----------------------
        try:
            in_scope = repo.asset_in_scope(
                source_instance_id=source_instance_id, agent_id=agent_id,
                project_id=project_id, channel=channel, asset_id=asset_id,
            )
        except Exception:
            return fail("invalid_request")
        if not in_scope:
            # unknown id and foreign-scope id converge here: identical shape.
            return fail("asset_not_found")

        # ---- gate 2: optional memory_ref narrowing ----------------------------
        if memory_ref is not None:
            from bridge.repository import MEMORY_REF_RE

            if not MEMORY_REF_RE.match(str(memory_ref)):
                return fail("invalid_request")
            refs = repo.links_for_asset(
                source_instance_id=source_instance_id, agent_id=agent_id,
                project_id=project_id, channel=channel, asset_id=asset_id,
            )
            if memory_ref not in refs:
                return fail("asset_not_found")

        asset = repo.asset(asset_id, provider_instance_id)
        if asset is None or asset["provider_file_id"] is None:
            # linked but never resolved to a provider file (pending): this is
            # unavailable — NOT a license to guess a file (docs/03 invariant).
            return fail("asset_unavailable", aid=asset_id)
        if asset["media_kind"] != "image":
            return fail("unsupported_media", aid=asset_id)

        file_id = int(asset["provider_file_id"])
        # The fetch cap is the POLICY cap. A stale stored size_bytes must
        # never shrink it below what the file actually needs (that would
        # reject a legitimately grown file) — it can never raise it above the
        # cap either. Declared-evidence checks below use the fresh detail.
        max_bytes = bridge_policy.MAX_FETCH_BYTES

        # ---- gate 3: provider revalidation on fresh evidence ------------------
        try:
            detail = provider.file_detail(file_id)
        except BridgeError as exc:
            return fail(exc.code, aid=asset_id)
        if detail.get("kind") != "image" or not detail.get("available", True):
            # stale/missing: transition the locator truthfully and stop.
            try:
                repo.set_locator_status(
                    asset_id=asset_id, provider_instance_id=provider_instance_id,
                    status="missing",
                    last_seen_size=detail.get("size"),
                    last_seen_mtime=detail.get("mtime"),
                    last_seen_name=detail.get("name"),
                )
            except Exception:
                pass
            return fail("asset_unavailable", aid=asset_id)

        # ---- gate 3b: full evidence conflict (docs/11 §5 frozen set) ---------
        # docs/11 §5 item 3 is a CONJUNCTION: mtime must match, OR the
        # generation must be unchanged AND name/rel_dir must match. A
        # mismatch with no waiver available is an evidence conflict =>
        # stale + refuse. Refusing here is the only defense against provider
        # rowid reuse (docs/13: a stale id can be handed to a different
        # file; a different file under the same id passes kind/size gates,
        # so an mtime/name/rel_dir/generation conflict must never serve
        # bytes). Measured live (docs/15): rebuild + re-add reuses ids; the
        # old code served the NEW file's bytes under the OLD asset's id.
        #
        # "generation unchanged" means: no rebuild happened between the
        # stored evidence and this response — bridge_health.generation is
        # the live authority (audited docs/10 GET /api/status shape). A
        # locator that never recorded a generation cannot use the waiver
        # either. A status probe that fails CANNOT grant the waiver
        # (unknown never widens trust — the rule the B-13 fix established).
        # mtime/name are compared as stored strings (docs/11 §6: provider
        # data is evidence, never re-parsed). rel_dir: the locator table
        # stores no rel_dir (docs/11 §3); docs/11 §5 says name/rel_dir, and
        # the audited name is a path-free display label that the ledger
        # maintains across renames (docs/13: rename detected via name) — so
        # name equality stands in for name/rel_dir, a recorded narrowing
        # written into docs/13, never silently.
        ev_mtime, live_mtime = (asset.get("last_seen_mtime"),
                                detail.get("mtime"))
        if (ev_mtime is not None and live_mtime is not None
                and str(ev_mtime) != str(live_mtime)):
            gen_ev = asset.get("provider_generation")
            try:
                live_gen = str((provider.status().get("index")
                                or {}).get("generation"))
            except BridgeError:
                live_gen = None
            same_generation = (gen_ev is not None and live_gen is not None
                               and str(gen_ev) == live_gen)
            names_match = (detail.get("name") is not None
                           and str(detail["name"])
                           == str(asset.get("last_seen_name")))
            if not (same_generation and names_match):
                # truthful transition where the graph admits it; refuse
                # either way — never bytes for a conflicted mapping.
                src_status = asset.get("locator_status") or "ready"
                if src_status != "stale":
                    try:
                        apply_transition(
                            repo, asset_id=asset_id,
                            provider_instance_id=provider_instance_id,
                            src=src_status, dst="stale",
                            last_seen_mtime=live_mtime,
                            last_seen_size=(None if detail.get("size") is None
                                            else int(detail["size"])),
                            last_seen_name=detail.get("name"))
                    except Exception:
                        pass  # concurrent/healed verdicts must not mask the refusal
                return fail("asset_unavailable", aid=asset_id)
        # Size/mtime drift WITHIN the admitted waiver (same generation, same
        # name/rel_dir: an indexed edit) is not a refusal condition; gate 8
        # refreshes the stored evidence, so drift only means the write
        # changes values.

        # ---- gate 4: declared bounds before fetching --------------------------
        declared = detail.get("size")
        if declared is not None and not bridge_policy.content_size_acceptable(
            int(declared)
        ):
            return fail("media_too_large", aid=asset_id)
        # Declared pixels reject ONLY in the obvious over-cap direction: if
        # the provider says the image already exceeds MAX_PIXELS, there is no
        # reason to fetch it at all (a wrongly-huge hint costs a refusal for
        # a file that could not be served within caps anyway, because the
        # post-fetch gate below parses the real header and re-checks).
        meta = detail.get("meta") or {}
        w, h = meta.get("width"), meta.get("height")
        if w is not None and h is not None and not bridge_policy.pixels_within_cap(
            int(w), int(h)
        ):
            return fail("media_too_large", aid=asset_id)

        # ---- gate 5: capped fetch (Range enforced inside) ---------------------
        try:
            data, served_mime = provider.file_content(file_id, max_bytes)
        except BridgeError as exc:
            return fail(exc.code, aid=asset_id)

        # ---- gate 6: MIME gate on the actual BYTES ----------------------------
        # Bytes are authoritative (T-11): content whose magic no image
        # signature explains is rejected even when the provider's Content-Type
        # claims an allowed type — a lying header must never smuggle an
        # unknown blob into an MCP ImageContent block.
        sniffed = _mime_from_magic(data)
        served_base = (served_mime or "").split(";")[0].strip().lower()
        if sniffed is None:
            return fail("unsupported_media", aid=asset_id)
        if sniffed in bridge_policy.TYPED_REJECT_MIME:
            return fail("unsupported_media", aid=asset_id)
        if sniffed not in bridge_policy.ALLOWED_IMAGE_MIME:
            return fail("unsupported_media", aid=asset_id)
        # A lying Content-Type (header png, bytes gif) resolves in favour of
        # the bytes: only the sniffed MIME reaches Image below.
        effective = sniffed

        if not bridge_policy.content_size_acceptable(len(data)):
            return fail("media_too_large", aid=asset_id)

        # ---- gate 7: dimensions from the real bytes (hint as fallback) --------
        dims = _dimensions(data, effective) or (
            (int(w), int(h)) if w is not None and h is not None else None
        )
        if dims is not None and not bridge_policy.pixels_within_cap(*dims):
            return fail("media_too_large", aid=asset_id)

        # ---- gate 8: truthful evidence refresh ---------------------------------
        try:
            repo.set_locator_status(
                asset_id=asset_id, provider_instance_id=provider_instance_id,
                status="ready",
                last_seen_size=detail.get("size"),
                last_seen_mtime=detail.get("mtime"),
                last_seen_name=detail.get("name"),
            )
        except Exception:
            pass  # a successful fetch never fails on bookkeeping

        fmt = _FORMAT_FOR_MIME[effective]
        return Image(data=data, format=fmt)
    except BridgeError as exc:
        return fail(exc.code, aid=asset_id)
