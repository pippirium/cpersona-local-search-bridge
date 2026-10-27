-- IMG-02: unlinked managed-image ownership and durable retry records.
-- Apply inside one transaction; PRAGMA user_version = 2.
-- The same physical managed file can represent independent scoped assets.
BEGIN IMMEDIATE;
CREATE TABLE provider_locators_v2 (
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
  PRIMARY KEY (asset_id, provider_instance_id)
);
INSERT INTO provider_locators_v2 SELECT * FROM provider_locators;
DROP TABLE provider_locators;
ALTER TABLE provider_locators_v2 RENAME TO provider_locators;
CREATE INDEX idx_locator_provider_file ON provider_locators (provider_instance_id, provider_file_id);
CREATE INDEX idx_provider_locators_status ON provider_locators (provider_instance_id, status);
-- Shared provider IDs are legal only for the same verified managed copy.
CREATE TRIGGER locator_insert_identity BEFORE INSERT ON provider_locators
WHEN NEW.provider_file_id IS NOT NULL AND EXISTS (
  SELECT 1 FROM provider_locators old
  JOIN assets previous ON previous.asset_id = old.asset_id
  JOIN assets incoming ON incoming.asset_id = NEW.asset_id
  WHERE old.provider_instance_id = NEW.provider_instance_id
    AND old.provider_file_id = NEW.provider_file_id
    AND (previous.origin != 'managed' OR incoming.origin != 'managed'
      OR previous.content_sha256 IS NULL OR incoming.content_sha256 IS NULL
      OR previous.content_sha256 != incoming.content_sha256
      OR previous.relative_managed_path != incoming.relative_managed_path
      OR old.provider_folder_id IS NOT NEW.provider_folder_id)
)
BEGIN SELECT RAISE(ABORT, 'locator identity conflict'); END;
CREATE TRIGGER locator_update_identity BEFORE UPDATE OF provider_file_id ON provider_locators
WHEN NEW.provider_file_id IS NOT NULL AND EXISTS (
  SELECT 1 FROM provider_locators old
  JOIN assets previous ON previous.asset_id = old.asset_id
  JOIN assets incoming ON incoming.asset_id = NEW.asset_id
  WHERE old.provider_instance_id = NEW.provider_instance_id
    AND old.provider_file_id = NEW.provider_file_id AND old.asset_id != NEW.asset_id
    AND (previous.origin != 'managed' OR incoming.origin != 'managed'
      OR previous.content_sha256 IS NULL OR incoming.content_sha256 IS NULL
      OR previous.content_sha256 != incoming.content_sha256
      OR previous.relative_managed_path != incoming.relative_managed_path
      OR old.provider_folder_id IS NOT NEW.provider_folder_id)
)
BEGIN SELECT RAISE(ABORT, 'locator identity conflict'); END;
CREATE TABLE asset_owners (
  asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id) ON DELETE RESTRICT,
  source_instance_id TEXT NOT NULL DEFAULT '',
  agent_id TEXT NOT NULL CHECK (length(agent_id) > 0),
  project_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  registered_at TEXT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64)
);
CREATE INDEX idx_asset_owners_scope ON asset_owners
  (source_instance_id, agent_id, project_id, channel, content_sha256);
CREATE TABLE image_requests (
  source_instance_id TEXT NOT NULL DEFAULT '',
  agent_id TEXT NOT NULL CHECK (length(agent_id) > 0),
  project_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  operation TEXT NOT NULL,
  request_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  deduplicated INTEGER NOT NULL CHECK (deduplicated IN (0, 1)),
  initial_status TEXT NOT NULL,
  PRIMARY KEY (source_instance_id, agent_id, project_id, channel, operation, request_id)
);
PRAGMA user_version = 2;
COMMIT;
