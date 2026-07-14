# Architecture

Concept Loom is a pipeline over one SQLite database. Each stage is a CLI
subcommand; each is idempotent or explicitly destructive as noted.

## Data model

All state lives in `data/loom.db` (path configurable). The core tables:

| table | role |
|---|---|
| `evidence` | Append-only normalized text rows. Triggers reject UPDATE/DELETE — evidence is immutable once written. Deduplicated by content hash. |
| `ingest_run` | One row per pipeline run (ingest, cluster, name) with stats JSON. Cursor columns carry only a summary placeholder. |
| `adapter_cursor` | Latest scan cursor per source (one row each). Kept out of run rows because a cursor tracks every source file and would bloat frequent scheduled runs. Legacy fallback reads old run rows. |
| `embedding` | One vector per (evidence, model). Float32 blobs, normalized. |
| `cluster_member` / `cluster_state` | Current clustering assignment and per-cluster naming status (`pending`, `named`, `incoherent`, `naming_failed`, `naming_deferred`). Rebuilt wholesale by `loom cluster`. |
| `concept_event` | Append-only event log for concepts (`created`, `pinned`, ...). |
| `concept_card` | Materialized current view of each concept: title, summary, type, score, confidence grade, evidence stats. |
| `concept_evidence` | Concept → evidence links with transform chains. |

### Provenance

Every evidence row stores a JSON `ProvenancePointer`: source system, file
path, span (kind + offsets), message id, timestamp, content sha256, adapter
version, and transform chain. Verification (`loom verify`, or the UI's
per-evidence "verify" action) re-reads the span from the live source through
the adapter and checks (a) sha256 equality and (b) an independent
single-line ripgrep probe against the raw file. Provenance is the design
center: a concept you can't trace back to sources is treated as worthless.

## Pipeline stages

```
ingest → embed → cluster → name → rank → serve
```

- **ingest** — for each configured source: load cursor, scan, insert new
  evidence, persist next cursor. Incremental by file mtime/size.
- **embed** — embeds evidence rows missing a vector for the configured
  model. Local sentence-transformers; fp16 on CUDA. Incremental.
- **cluster** — PCA to `reduced_dimensions`, then HDBSCAN. **Destructive:**
  wipes `cluster_member`/`cluster_state` and thereby orphans the link
  between existing named concepts and clusters. Run it deliberately.
- **name** — samples up to 12 closest-to-centroid evidence rows per pending
  cluster, asks the configured LLM for `{title, concept_type, summary,
  aliases, coherent}` as strict JSON. Transport errors defer the cluster
  (retried next run, with a stop-early breaker when the transport looks
  down); incoherent clusters are marked and skipped. Creates concept events,
  cards, and evidence links.
- **rank** — recomputes scores: `0.35·frequency + 0.25·source_diversity +
  0.25·actionability + 0.15·pin`, plus nascent flags and confidence grades.
- **serve** — FastAPI + static UI. The graph endpoint derives interim edges
  from session/document co-occurrence of evidence (weight = shared session
  count, hub sessions skipped).

## Scheduled operation

`scripts/scheduled_ingest.py` runs guarded `ingest` + `embed --limit N`
under a lock file, logs one JSON line per tick, and trips a circuit breaker
(exit 2, embed skipped) when a tick inserts anomalously many rows —
catching glob/cursor misconfigurations before burning embed compute.
Cluster/name/rank are deliberately excluded from scheduling.

## Known limitations

- **Re-clustering wipes naming state.** Cluster identity is positional, so a
  re-cluster orphans named concepts. Incremental clustering with stable
  concept identity (assign new evidence to existing concepts; only
  genuinely-new material forms new clusters) is the designed successor, not
  yet built. Until then: ingest/embed continuously, re-cluster rarely and
  deliberately.
- **Graph edges are interim.** Co-occurrence by session/document is a proxy
  until a real merge/edge pipeline stage emits typed concept-edge events.
- **The UI loads data once at page load** — no polling or SSE; reload to see
  newly ingested data.
- **Single-user trust model.** No auth on the API; the pin action writes to
  the DB. Keep it on localhost or a private network.
