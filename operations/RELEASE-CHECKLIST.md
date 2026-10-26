# Release checklist (B-15)

Cut a release only when every line is either DONE with its command +
result recorded in the release notes, or explicitly marked UNVERIFIED in
the release notes themselves — "誤った完成宣言なし" is the acceptance
criterion for this checklist itself.

## Gate A — code and contracts

- [ ] `git status` clean; release commit tagged `vX.Y.Z` from `main`.
- [ ] `uv run pytest` — full offline suite green, exact count recorded.
- [ ] live contract tripwire against the pinned local-search (the checkout
      HEAD equals the D-14 pin `31f3104…` — verify, don't remember):
      `BRIDGE_LIVE_BASE_URL=… uv run pytest tests/integration -q` green.
- [ ] If any pin moved (local-search commit, CPersona fork commit, mcp
      range): docs/10 + contract fixtures re-audited, docs/15 audit log
      re-run; pins and audited results recorded together.

## Gate B — operational rehearsal (throwaway dirs only)

- [ ] `operations/RUNBOOK.md` §1–§3 followed on the release machine; any
      step that deviated is fixed in the RUNBOOK in the same release.
- [ ] `operations/smoke.py` exits 0 (9/9).
- [ ] backup→restore round-trip (RUNBOOK §4) on a scratch dir: restored
      `bridge_health` green, links intact.
- [ ] rebuild drill (RUNBOOK §5) once: audit classifies drift, no link
      lost, no wrong image served (`get_asset` on a stale asset refuses).
- [ ] `uv run python -m eval.run …` executes; report's limitations section
      reviewed for honesty (unmeasured stays unmeasured).

## Gate C — security review (docs/08, docs/12, AGENTS.md)

- [ ] `uv run pytest tests/security -q` green (text sentinel + policy).
- [ ] manual sweep of NEW code since last release for the invariants:
      no new filesystem write outside managed root; no new log sinks;
      provider messages still discarded; stdio transport still the only
      default; scope-args still never treated as authentication;
      `provider_file_id` still only written by bind_pending_locator.
- [ ] no personal paths/tokens in the diff: `git show` the release range and
      search for the sentinel families the CI test enumerates (home-dir
      prefixes, GitHub OAuth token prefixes, secret-key words). Written
      descriptively ON PURPOSE: this file is itself scanned by that test, so
      the marker strings must not be spelled out here either.
- [ ] docs/09-decisions.md has an entry for every behavior change shipped.

## Gate D — documentation truth

- [ ] README current-status section reflects the release (no planning-era
      "nothing exists" leftovers, no overclaims).
- [ ] RUNBOOK §8 known limits still accurate.
- [ ] Release notes state: what was measured (commands + results), what
      was NOT (fresh-Mac install, real-embedder Track C numbers, etc.).

## Rollback of a release

`git revert` the tag range or repoint the tag; Bridge holds no migrations
beyond its own `bdata/` schema, which is append-only — an older binary
reads it unchanged (verify before shipping any schema addition:
release the schema change first, binary second).
