# AGENTS.md — CPersona × local-search Bridge

## Mission
Implement a thin, opt-in MCP adapter that associates CPersona's textual memories with assets indexed by local-search. Keep both applications independent. Follow the planning documents under docs/ before writing runtime code.

## Mandatory reading order
1. README.md — current status and scope.
2. docs/01-requirements.md — requirements and out-of-scope.
3. docs/02-architecture.md — service and trust boundaries.
4. docs/03-data-model.md — persistent identifiers and invariants.
5. docs/04-interfaces.md — MCP and provider contracts.
6. docs/05-delivery-plan.md and docs/06-tickets.md — execution order and acceptance.
7. docs/07-evaluation.md, docs/08-security-operations.md and docs/09-decisions.md — evidence and policy.

## Source repositories and boundaries
- CPersona fork: https://github.com/pippirium/CPersona. This is the user's experimental fork; never push or open PRs to https://github.com/Cloto-dev/CPersona. Never update the user's live memory database or MCP client configuration during development.
- local-search: https://github.com/pippirium/local-search. Currently a personal/private project. Do not vendor or copy its proprietary implementation; rely on documented HTTP APIs. Never change its local-only listener, security guard, or read-only behavior as a shortcut.
- All changes outside this bridge repository require a separate explicit user instruction. Planning tasks labelled LS/CP are proposals, not permission to modify the other repositories.

## Runtime invariants
- The default MCP interface is local stdio. Do not expose a network listener or disable either system's authentication/Host/Origin controls by default.
- CPersona's ordinary recall/reconstruct must remain text plus asset references. Never automatically inline image bytes or send images without an explicit get_asset request.
- Do not run a generation LLM for storage, search or evaluation; do not send sensitive material to external APIs.
- Do not modify or delete the user's source files. Asset storage is opt-in; managed copies, if introduced, live under a dedicated configured root.
- Bridge SQLite is metadata only. Local-search continues to own its SQLite ledger and LanceDB. No direct mutation of other databases.
- Do not treat caller-provided agent_id/project_id/channel as security authentication. Document and test the actual trust boundary.
- An unknown or stale file mapping is unavailable, not a license to return another file. Never resolve arbitrary user-provided filesystem paths through get_asset.
- No private paths, search strings, image bytes, original text, tokens or credentials in logs, commits, issues or fixture files.

## Implementation workflow
- Work issue by issue, with tests for normal, invalid, and failure paths. No unrequested scope expansions.
- Every change has a descriptive conventional-commit subject and a body explaining why, the boundaries preserved, and verification; include:
  Co-authored-by: Codex <codex@openai.com>
- Prefer one small logical commit per ticket, or multiple commits if they form independently reviewable steps.
- Reference the corresponding B/LS/CP task ID and link the PR to an Issue when one exists; only use Closes #N for an issue actually completed by that PR.
- Open PRs against this repository's main branch; merge only when the user explicitly requests it. Squash merge for this planning PR is explicitly authorized.
- For scope-changing decisions, update docs/09-decisions.md and the relevant specification before implementation.
- Keep tests isolated: use temp paths, synthetic media, fake HTTP providers and temporary SQLite. Real model tests are opt-in and never run against personal source directories by default.

## Definition of done for an implementation ticket
Contract and tests updated; error handling and data isolation verified; security invariants preserved; documentation and migration/rollback updated where relevant; PR review performed; status reported with actual commands and results. Do not claim checks ran unless they did.

## Current status
Development version 0.0.0: stdio MCP with nine tools, opt-in managed import, reconciliation, evaluation CLI and runbook are implemented. No released version exists. Real-model quality, fresh-Mac install and full three-process CPersona integration remain unverified; README and merged PR evidence track current status.
