# 08 — Security, privacy and operations runbook

## Operating modes
M1: trusted, local macOS process. Bridge MCP speaks stdio; local-search listens on 127.0.0.1:18765. There is no public HTTP API, user directory, cloud sync or supported multi-user tenancy. A local process running under the same OS account may reach the loopback API; the Bridge must not claim otherwise.

**Security boundary:** agent_id/project_id/channel are record selectors, **not** authenticated user identities. True isolation from untrusted agents requires authenticated MCP transport and a bound identity policy, a separate future design. For MVP, install the bridge only for a trusted local agent/operator.

## Data exposure policy
- Ordinary CPersona recall/reconstruct stays text-only plus references; images are not preloaded.
- get_asset is an explicit tool invocation with an existing linked asset ID, scoped association, fresh provider validation and enforced byte/pixel caps.
- Never serve an arbitrary filesystem path or URL from a caller. Source reads go through the local-search guarded file_id API only.
- local-search's Host/Origin and localhost restrictions remain intact; configure a local HTTP client with strict URL validation and disable redirects.
- No model/embedding duplication; no external text/image API calls or telemetry added by Bridge.
- Do not log query strings, original text, image bytes, image captions from personal data, paths, credential values, MCP payload bodies, memory content, or full exception text.
- Never commit real user media, provider indexes, backups, .env files or local keys. Use test fixtures with invented data.
- Treat a public GitHub repo as public even when integrated with a currently private dependency. Never copy local-search source code or confidential designs.

## Threats and checks
The frozen M1 policy (B-03) is [docs/12-threat-model-media-policy.md](12-threat-model-media-policy.md): concrete caps (8×10⁶ bytes, 40 MP, JPEG/PNG/WebP/GIF; HEIC typed-rejected), HTTP client rules (literal `127.0.0.1` base URL, redirects off, 2s/10s/30s timeouts, connect-phase-only single retry), the identity-evidence restatement and the forbidden-strings log audit (scope tuples appear in logs only as a 12-hex truncated hash). The table below remains the summary; docs/12 §2 is the acceptance-level list and `tests/security/` executes its pure rules.

| Threat | Mitigation | Verification |
| --- | --- | --- |
| Scope spoofing | Local trusted-agent boundary; prevent accidental scope mismatch; no remote serving | Cross-agent/project/channel negative tests; remote disabled |
| Arbitrary file read | No path in get_asset; provider API checks file_id and contained path | Traversal, symlink and tampered locator tests |
| Index ID reuse | Stable bridge asset_id; revalidate source identity; stale fail-closed | Provider reindex and file swap tests |
| Huge/decompression-bomb media | MIME/size/dimension constraints; bounded streaming; safe decoding | Extreme size and crafted payload tests |
| SSRF/redirect | Loopback-only configured provider URL; redirects off | Redirect/foreign host/IPv6/alternate scheme tests |
| Leaked secrets/logs | Sanitized structured errors, 0700 dir, no raw payload logs | Sentinel search audit in logs and Git |
| Partial write/crash | SQLite transaction and atomic copy; restart reconciliation | Kill/restart at each simulated stage |
| Provider outage | Typed dependency error, preserve original metadata | Stubbed timeout/connectivity tests |

## First-run (implemented development runtime)
1. Install a supported Python version and dependencies from a pinned lockfile.
2. Start local-search separately using its documented startup procedure; confirm its model/index health.
3. Configure an isolated Bridge data directory **outside** either provider's internal data directory.
4. Register Bridge as an MCP stdio service with your trusted local agent. Do not share it publicly or forward the port.
5. Run bridge_health and a dry-run with synthetic memory/file IDs from a disposable corpus.
6. Explicitly link an indexed image, list/search its reference and explicitly fetch that image.
7. Verify no source file modification and no sensitive strings logged.
The runnable stdio service and nine tools are implemented. Follow [RUNBOOK](../operations/RUNBOOK.md) for exact commands/configuration and recorded rehearsal limits. Fresh-Mac install and full three-process integration remain unverified. `bridge_health` is readiness information, not a security certification.

## Backup/restore and deletion
- Metadata: use SQLite online backup in a quiescent or consistent snapshot; account for WAL and migration version.
- Managed files (M2): include managed asset root in the same logical backup. Original user files remain external and are never modified.
- Provider index: local-search manages its own SQLite/LanceDB backup and possible reindex; Bridge must detect generation mismatch.
- Restore: verify stable asset IDs and link counts, then revalidate provider locators; do not remap blindly.
- Unlink must not delete source or managed file. Explicit garbage collection (M2+) requires operator consent, zero remaining links, retention window and an audit report. No destructive cleanup in M1.

## Incident handling
- Provider 409 index_mismatch: stop asset fetch, label locators incompatible, guide provider's documented rebuild.
- Missing disk/file: retain association but mark unavailable; retries after availability returns, validating identity.
- Bridge DB corruption: stop writes, preserve original, restore known good backup; never attempt silent destructive rebuild.
- Unsafe provider URL/config: abort at startup with sanitized message.
- Version/API incompatibility: reject requests with actionable status, do not reinterpret provider payloads optimistically.
- Lost trusted local machine session: rotate any separately introduced credentials, re-evaluate local OS access; M1 has no remote credentials.

## Pre-release review
Confirm threat cases, separate data dirs, no hidden image auto-fetch, explicit opt-in import, clean git diff, lockfile, reproducible tests, docs and external-repo changes individually authorized.
