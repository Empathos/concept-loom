PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ingest_run (
  run_id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  adapter TEXT NOT NULL,
  cursor_before TEXT,
  cursor_after TEXT,
  stats_json TEXT,
  status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS evidence (
  evidence_id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES ingest_run(run_id),
  source_system TEXT NOT NULL,
  source_class TEXT NOT NULL,
  ts TEXT,
  text TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  metadata_json TEXT,
  UNIQUE(text_sha256, source_system, provenance_json)
);

CREATE TABLE IF NOT EXISTS embedding (
  evidence_id INTEGER PRIMARY KEY REFERENCES evidence(evidence_id),
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS concept_event (
  event_id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  run_id INTEGER REFERENCES ingest_run(run_id),
  concept_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  actor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concept_edge_event (
  event_id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  concept_a TEXT NOT NULL,
  concept_b TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  event_type TEXT NOT NULL,
  weight REAL,
  payload_json TEXT,
  actor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concept_card (
  concept_id TEXT PRIMARY KEY,
  title TEXT,
  summary TEXT,
  concept_type TEXT,
  aliases_json TEXT,
  status TEXT,
  first_seen TEXT,
  last_seen TEXT,
  evidence_count INTEGER,
  source_systems_json TEXT,
  score REAL,
  score_components_json TEXT,
  nascent INTEGER DEFAULT 0,
  pinned INTEGER DEFAULT 0,
  confidence_grade TEXT
);

CREATE TABLE IF NOT EXISTS concept_evidence (
  concept_id TEXT,
  evidence_id INTEGER,
  link_type TEXT,
  transform_chain_json TEXT,
  created_at TEXT,
  PRIMARY KEY (concept_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS cluster_member (
  cluster_id TEXT,
  evidence_id INTEGER,
  run_id INTEGER,
  distance REAL,
  PRIMARY KEY (cluster_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS cluster_state (
  cluster_id TEXT PRIMARY KEY,
  run_id INTEGER,
  size INTEGER NOT NULL,
  named INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_pin (
  concept_id TEXT PRIMARY KEY,
  pinned INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Latest scan cursor per adapter, kept out of ingest_run: a cursor tracks
-- every source file (which can run to hundreds of KB of JSON on a large
-- corpus) and storing it per run bloats the DB on frequent scheduled ingests.
CREATE TABLE IF NOT EXISTS adapter_cursor (
  adapter TEXT PRIMARY KEY,
  cursor_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts
USING fts5(text, content='evidence', content_rowid='evidence_id');

CREATE TRIGGER IF NOT EXISTS evidence_ai
AFTER INSERT ON evidence BEGIN
  INSERT INTO evidence_fts(rowid, text) VALUES (new.evidence_id, new.text);
END;

DROP TRIGGER IF EXISTS evidence_ad;
DROP TRIGGER IF EXISTS evidence_au;

CREATE TRIGGER IF NOT EXISTS evidence_no_update
BEFORE UPDATE ON evidence BEGIN
  SELECT RAISE(ABORT, 'append-only');
END;

CREATE TRIGGER IF NOT EXISTS evidence_no_delete
BEFORE DELETE ON evidence BEGIN
  SELECT RAISE(ABORT, 'append-only');
END;

CREATE TRIGGER IF NOT EXISTS concept_event_no_update
BEFORE UPDATE ON concept_event BEGIN
  SELECT RAISE(ABORT, 'append-only');
END;

CREATE TRIGGER IF NOT EXISTS concept_event_no_delete
BEFORE DELETE ON concept_event BEGIN
  SELECT RAISE(ABORT, 'append-only');
END;

CREATE TRIGGER IF NOT EXISTS concept_edge_event_no_update
BEFORE UPDATE ON concept_edge_event BEGIN
  SELECT RAISE(ABORT, 'append-only');
END;

CREATE TRIGGER IF NOT EXISTS concept_edge_event_no_delete
BEFORE DELETE ON concept_edge_event BEGIN
  SELECT RAISE(ABORT, 'append-only');
END;

CREATE INDEX IF NOT EXISTS idx_embedding_model ON embedding(model);
CREATE INDEX IF NOT EXISTS idx_cluster_member_cluster ON cluster_member(cluster_id);
CREATE INDEX IF NOT EXISTS idx_concept_evidence_concept ON concept_evidence(concept_id);
CREATE INDEX IF NOT EXISTS idx_evidence_ts ON evidence(ts);

INSERT OR IGNORE INTO schema_version(version) VALUES (1);
