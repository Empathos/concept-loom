# Examples

`sample-notes/` is a tiny synthetic corpus (four themed journals) for trying
the pipeline without pointing it at your own data. It's small, so the demo
config lowers the clustering thresholds.

```bash
cp examples/loom.demo.toml loom.toml
loom init && loom ingest && loom embed && loom cluster
loom name          # needs ANTHROPIC_API_KEY (or edit [llm] for a local model)
loom rank && loom serve
# open http://127.0.0.1:8901/static/orbit.html
```

With ~30 evidence chunks expect a handful of concepts (chunking by paragraph,
one theme per file). Real corpora — thousands of notes or transcript
messages — are where the layout starts earning its keep.
