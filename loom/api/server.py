from __future__ import annotations

from pathlib import Path
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from loom.adapters import build_adapters
from loom.config import load_config
from loom.model import ProvenancePointer
from loom.pipeline.verifier import EvidenceVerifier
from loom.store import LoomStore


def rowdict(row) -> dict:
    data = dict(row)
    for key in ("score_components_json", "source_systems_json", "aliases_json"):
        if key in data and data[key]:
            try:
                data[key.replace("_json", "")] = json.loads(data[key])
            except Exception:
                pass
    return data


def create_app(config_path: str = "loom.toml") -> FastAPI:
    cfg = load_config(config_path)
    store = LoomStore(cfg.paths.db_path)
    store.init()
    app = FastAPI(title="Concept Loom")
    static_dir = Path(__file__).resolve().parents[2] / "ui" / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/concepts")
    def concepts(view: str = "nascent", limit: int = 50):
        return [rowdict(row) for row in store.concepts(view=view, limit=limit)]

    @app.get("/api/concepts/{concept_id}")
    def concept(concept_id: str):
        row = store.concept(concept_id)
        if not row:
            raise HTTPException(404, "concept not found")
        return rowdict(row)

    @app.get("/api/concepts/{concept_id}/evidence")
    def concept_evidence(concept_id: str, limit: int = 50):
        return [rowdict(row) for row in store.concept_evidence(concept_id, limit=limit)]

    @app.get("/api/evidence/{concept_id}/verify")
    def verify_concept_evidence(concept_id: str, limit: int = 50):
        verifier = EvidenceVerifier(build_adapters(cfg))
        out = []
        for row in store.concept_evidence(concept_id, limit=limit):
            result = verifier.verify_pointer(row["evidence_id"], ProvenancePointer.from_json(row["provenance_json"]))
            item = rowdict(row)
            item["verification"] = result.__dict__
            out.append(item)
        return out

    @app.get("/api/graph")
    def graph(limit: int = 400, min_shared: int = 2, max_edges: int = 1500):
        """Read-only concept co-occurrence edges via shared evidence sessions.

        Interim relational signal until the merge/edge pipeline stage emits
        real concept_edge events; sessions touching >30 concepts are treated
        as generic context and skipped so they don't create hairballs.
        """
        with store.connect() as conn:
            rows = conn.execute(
                """
                WITH top_concepts AS (
                  SELECT concept_id FROM concept_card
                   WHERE status = 'active'
                ORDER BY pinned DESC, score DESC LIMIT ?
                ),
                cs AS (
                  SELECT DISTINCT ce.concept_id,
                         json_extract(e.provenance_json, '$.session_id') AS sid
                    FROM concept_evidence ce
                    JOIN evidence e ON e.evidence_id = ce.evidence_id
                   WHERE ce.concept_id IN (SELECT concept_id FROM top_concepts)
                     AND sid IS NOT NULL
                ),
                busy AS (
                  SELECT sid FROM cs GROUP BY sid HAVING COUNT(*) > 30
                )
                SELECT a.concept_id AS source, b.concept_id AS target,
                       COUNT(*) AS weight
                  FROM cs a
                  JOIN cs b ON a.sid = b.sid AND a.concept_id < b.concept_id
                 WHERE a.sid NOT IN (SELECT sid FROM busy)
              GROUP BY source, target
                HAVING weight >= ?
              ORDER BY weight DESC
                 LIMIT ?
                """,
                (limit, min_shared, max_edges),
            ).fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/actions/pin/{concept_id}")
    def pin(concept_id: str, pinned: bool = True):
        if not store.concept(concept_id):
            raise HTTPException(404, "concept not found")
        store.set_pin(concept_id, pinned)
        store.rank_concepts()
        return {"concept_id": concept_id, "pinned": pinned}

    return app
