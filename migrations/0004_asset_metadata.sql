-- IMG-08: Scope-specific, user-supplied provenance. Never store image bytes or memory text.
-- Metadata follows access to owner-scoped or linked assets; it is not indexed.
BEGIN IMMEDIATE;
CREATE TABLE asset_metadata (
  source_instance_id TEXT NOT NULL DEFAULT '',
  agent_id TEXT NOT NULL CHECK (length(agent_id) > 0),
  project_id TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '',
  asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
  user_caption TEXT,
  caption_origin TEXT CHECK (caption_origin IN ('user', 'external_source')),
  source_kind TEXT CHECK (source_kind IN ('chat_attachment', 'local_selection',
                                         'managed_import', 'external_source')),
  captured_at TEXT,
  recorded_at TEXT,
  source_message_ref TEXT,
  consent_record TEXT CHECK (consent_record IN ('explicit_user', 'operator_confirmed')),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_instance_id, agent_id, project_id, channel, asset_id),
  CHECK ((user_caption IS NULL AND caption_origin IS NULL)
       OR (user_caption IS NOT NULL AND caption_origin IS NOT NULL))
);
CREATE INDEX idx_asset_metadata_asset ON asset_metadata(asset_id);
PRAGMA user_version = 4;
COMMIT;
