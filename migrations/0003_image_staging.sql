-- IMG-03: opaque staging ledger; PRAGMA user_version = 3.
BEGIN IMMEDIATE;
CREATE TABLE staged_images (
  token_hash TEXT PRIMARY KEY CHECK (length(token_hash)=64),
  source_instance_id TEXT NOT NULL DEFAULT '',
  agent_id TEXT NOT NULL CHECK (length(agent_id)>0),
  project_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256)=64),
  media_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK (size_bytes>0),
  source_device INTEGER NOT NULL,
  source_inode INTEGER NOT NULL,
  source_mtime_ns INTEGER NOT NULL,
  stage_device INTEGER NOT NULL,
  stage_inode INTEGER NOT NULL,
  stage_mtime_ns INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_asset_id TEXT REFERENCES assets(asset_id),
  consumed_deduplicated INTEGER CHECK (consumed_deduplicated IN (0,1)),
  consumed_status TEXT,
  CHECK ((consumed_asset_id IS NULL AND consumed_deduplicated IS NULL AND consumed_status IS NULL)
      OR (consumed_asset_id IS NOT NULL AND consumed_deduplicated IS NOT NULL AND consumed_status IS NOT NULL))
);
CREATE INDEX idx_staged_images_expiry ON staged_images(expires_at);
PRAGMA user_version = 3;
COMMIT;
