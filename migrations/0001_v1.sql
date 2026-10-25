-- Bridge schema v1 (B-02 frozen contract; docs/11-id-schema-scope.md).
-- Apply inside one transaction, then set user_version = 1 (B-05 migrator).
-- PRAGMA foreign_keys must be enabled per connection by the application:
-- it is OFF by default in SQLite and cannot be set inside a transaction.

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
  last_seen_mtime TEXT,
  last_seen_size INTEGER CHECK (last_seen_size IS NULL OR last_seen_size >= 0),
  status TEXT NOT NULL CHECK (status IN ('pending','ready','stale','missing','incompatible')),
  verified_at TEXT,
  PRIMARY KEY (asset_id, provider_instance_id),
  UNIQUE (provider_instance_id, provider_file_id)
);

-- Nullable-UNIQUE freeze (docs/11 §3): two pending locators (file_id NULL)
-- may coexist; two resolved locators for the same provider file may not.
CREATE UNIQUE INDEX uq_locator_provider_file
  ON provider_locators (provider_instance_id, provider_file_id)
  WHERE provider_file_id IS NOT NULL;

CREATE INDEX idx_provider_locators_status
  ON provider_locators (provider_instance_id, status);
