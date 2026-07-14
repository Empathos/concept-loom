# Writing an ingest adapter

An adapter turns one kind of source (a folder of files, an export format, an
API) into `NormalizedRecord`s with verifiable provenance. The two built-in
adapters (`loom/adapters/markdown_folder.py`, ~150 lines, and
`loom/adapters/jsonl_transcripts.py`) are the reference implementations.

## The contract

Subclass `loom.adapters.base.Adapter` and implement:

| method | responsibility |
|---|---|
| `scan(cursor)` | Yield `NormalizedRecord`s for content that changed since `cursor` (a dict you define — the built-ins use `{path: {mtime, size}}`). Build the *next* cursor as you go. |
| `next_cursor()` | Return the cursor captured by the last `scan()`. Persisted per adapter in the `adapter_cursor` table. |
| `read_span(pointer)` | Re-read the exact text a `ProvenancePointer` refers to from the **live** source. This is what makes evidence verifiable. |
| `source_paths()` | All files the adapter would consider (used by `loom ingest --limit-files` smoke tests). |

Optional overrides:

- `stats()` — counters merged into the ingest run's stats JSON.
- `lexical_probe(pointer, live_text)` — a single-line substring expected
  verbatim in the raw source file, used by the verifier as an independent
  check alongside the sha256 comparison. Return `None` to skip it (the
  default implementation returns the first non-empty line of the live text,
  which is correct whenever your records are exact slices of the file).

## Provenance rules

Every record's `ProvenancePointer` must satisfy:

1. `content_sha256 == text_sha256(record.text)` — enforced at construction.
2. `read_span(pointer)` returns exactly `record.text` while the source is
   unchanged — this is the round-trip the verifier checks. The easiest way
   to guarantee it is to make `text` an exact slice of the decoded source
   and store the slice offsets in `span`.
3. `source_system` is the configured source *name* (not the adapter type):
   the verifier routes pointers back to adapter instances by name.

Set `session_id` to whatever unit of co-occurrence makes sense for your
source (file path, chat session id, meeting id): concepts whose evidence
shares a `session_id` get linked in the graph view.

## Registering

Two ways to make your adapter available:

**In-tree** — for adapters worth upstreaming:

1. Add a config dataclass with a `from_source(source: SourceConfig,
   config_root: Path)` classmethod that reads `source.options` (the raw TOML
   keys minus `name`/`type`).
2. Register the pair in `ADAPTER_TYPES` in `loom/adapters/__init__.py`.
3. Users enable it with a `[[sources]]` entry whose `type` is your key.

**As a plugin — no fork needed.** Keep the adapter in your own module
(anywhere on disk; it never has to enter this repository — useful when the
adapter itself references private systems):

```toml
[plugins]
paths = ["~/my-loom-plugins"]        # added to sys.path at config load

[[sources]]
name = "worklog"
type = "plugin:my_adapters:WorklogAdapter"   # module:attribute
# ...your adapter's own options...
```

The referenced attribute must expose `from_source(source, config_root)`
returning an `Adapter` instance (a classmethod on your adapter class works).
The same mechanism covers custom LLM transports for `loom name`:
`provider = "plugin:my_llm:call_json"` in `[llm]`, where the target is a
callable with the signature `call_json(cfg, *, session_key, prompt) -> dict`.

## Testing

Copy the shape of `tests/test_adapters.py`: scan a fixture, assert the
records and stats, assert `read_span` round-trips every record, and assert a
second scan with the captured cursor skips unchanged files. If you claim
exact spans, also assert `record.text == raw[span.start:span.end]`.
