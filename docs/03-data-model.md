# 03 — Data model and storage invariants

## Why a bridge-owned SQLite database?
Do not add binary assets to CPersona's single-file memory store. Do not insert into local-search's own file ledger or LanceDB. A **small, separate SQLite database** records relationships and last-seen provider locators only.

Images stay at original local-search indexed file paths for M1 (read-only). M2 can place explicitly imported **copies** in an opt-in managed folder. Both assets can share the logical asset-reference protocol.

## Key concepts
- memory_ref: opaque string supplied from a verified CPersona memory listing; not assumed to be SQLite INTEGER. B-01 verified the actual shapes: `mem:<int>` (memories) and `ep:<int>` (episodes) as spelled by CPersona's recall/reconstruct refs (docs/10-contract-audit.md). They are stored verbatim, validated by pattern only, never parsed as numbers. Multiple CPersona instances require source_instance_id — operator-configured, because CPersona responses carry no instance identifier.
- scope: (source_instance_id, agent_id, project_id, channel), with normalization of optional components to empty strings; explicit values must round-trip without conflation.
- asset_id: bridge-generated UUID; stable across provider index rebuilds and multiple memory links.
- provider_file_id: local-search's numeric file_id, **only a cache/locator**. Never use as permanent identity outside its index/DB instance.
- content_sha256: file-byte checksum if available after a controlled read; optional for M1 references. This is not an authorization token.
- managed: bridge owns a safely copied file; external: original is retained by a user-selected indexed folder. Deleting a link never deletes either.
- locator_state: pending, ready, stale, missing, incompatible. A stale reference is reported rather than silently remapped.

## Original planning DDL (historical; use migration 1)

The block below is the original design, not the current complete schema. Runtime uses [migration 1](../migrations/0001_v1.sql), [docs/11](11-id-schema-scope.md) and `src/bridge/repository.py`.

~~~sql
PRAGMA foreign_keys = ON;

CREATE TABLE assets (
  asset_id TEXT PRIMARY KEY,
  origin TEXT NOT NULL CHECK (origin IN ('external','managed')),
  media_kind TEXT NOT NULL CHECK (media_kind IN ('image','video','text')),
  content_sha256 TEXT,
  relative_managed_path TEXT,
  media_type TEXT,
  size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    (origin = 'external' AND relative_managed_path IS NULL)
    OR (origin = 'managed' AND relative_managed_path IS NOT NULL)
  )
);

CREATE TABLE asset_links (
  source_instance_id TEXT NOT NULL DEFAULT '',
  agent_id TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  memory_ref TEXT NOT NULL,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (
    source_instance_id, agent_id, project_id,
    channel, memory_ref, asset_id
  )
);
CREATE INDEX idx_asset_links_scope_asset
  ON asset_links (source_instance_id, agent_id, project_id, channel, asset_id);

CREATE TABLE provider_locators (
  asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
  provider_instance_id TEXT NOT NULL,
  provider_file_id INTEGER,
  provider_generation TEXT,
  provider_folder_id INTEGER,
  last_seen_kind TEXT,
  last_seen_name TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending','ready','stale','missing','incompatible')),
  verified_at TEXT,
  PRIMARY KEY (asset_id, provider_instance_id),
  UNIQUE (provider_instance_id, provider_file_id)
);
CREATE INDEX idx_provider_locators_status
  ON provider_locators (provider_instance_id, status);
~~~

This DDL was frozen by B-02 into `migrations/0001_v1.sql` with the decisions recorded in [docs/11-id-schema-scope.md](11-id-schema-scope.md): `PRAGMA user_version` versioning, per-connection `foreign_keys=ON`, a partial unique index so pending locators (NULL `provider_file_id`) coexist while resolved ones collide per provider instance, evidence columns `last_seen_mtime`/`last_seen_size`, `provider_generation` as TEXT, and the frozen locator state graph. Scope is exact-equality over the four strings normalized `None→""` only — CPersona's γ/channel widening is deliberately **not** mirrored (D-16).

## Consistency and identity
1. Verify file ID existence/kind with the live local-search API before binding a locator.
2. When a provider file ID is reused or its identity cannot be re-established, mark the old link stale. Never attach a different file by numeric ID alone.
3. On restart, reload links without invoking the embedding model. If provider is unavailable, return dependency status and preserve metadata.
4. On index generation change, revalidate each locator using supported authoritative provider information. If identity is uncertain, mark stale and request operator reconciliation.
5. Distinguish **link deletion** from **managed-copy deletion**. Only an explicitly separate, authorized garbage-collection operation may remove unreferenced managed copies.
6. Use transactions for link changes; explicit WAL/backup procedure must account for accompanying files.
7. A link may refer to one asset shared by many memories. There is no implicit cascade from deleting a CPersona record because the bridge does not observe CPersona's database; orphan cleanup is a separate auditable task.
8. Treat collisions in file hashes and path changes as testable identity cases. Content hash alone cannot prove which *source* a user meant after mutations.

## Backup and restore
Include the entire Bridge SQLite database via sqlite3 backup API; if managed copies are enabled, snapshot those files consistently too. local-search's own index and registered folders are independent backups and may need rebuilding. Never ship personal DBs or media as sample fixtures.

## Migration strategy
- Schema version stored in PRAGMA user_version.
- Every migration has forward-only tests, fail-closed behavior on newer schema, and documented backup before upgrade.
- Do not change CPersona's production schema, or import a CPersona DB directly.

## Planned unlinked-asset ownership (IMG-01)

[docs/19](19-image-input-contract.md) specifies a versioned owner relation for new unlinked managed assets, scope-preserving deduplication, and backup/restore and locator migration requirements. The existing v1 schema stays unchanged until the runtime implementation migrates it. Existing linked-only visibility is not widened.
