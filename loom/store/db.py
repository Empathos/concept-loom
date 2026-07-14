from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
import sqlite3
from typing import Any

from loom.model import NormalizedRecord
from loom.model.records import text_sha256


class LoomStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        with self.connect() as conn:
            conn.executescript(schema_path.read_text(encoding="utf-8"))

    def start_run(self, adapter: str, cursor_before: dict[str, Any] | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO ingest_run(adapter, cursor_before) VALUES (?, ?)",
                (adapter, json.dumps(cursor_before or {}, sort_keys=True)),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        cursor_after: dict[str, Any],
        stats: dict[str, Any],
        status: str = "completed",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ingest_run
                   SET finished_at = datetime('now'),
                       cursor_after = ?,
                       stats_json = ?,
                       status = ?
                 WHERE run_id = ?
                """,
                (
                    json.dumps(cursor_after, sort_keys=True),
                    json.dumps(stats, sort_keys=True),
                    status,
                    run_id,
                ),
            )

    def insert_evidence(self, run_id: int, records: Iterable[NormalizedRecord]) -> dict[str, int]:
        inserted = 0
        duplicates = 0
        with self.connect() as conn:
            for record in records:
                digest = text_sha256(record.text)
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO evidence(
                      run_id, source_system, source_class, ts, text, text_sha256,
                      provenance_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        record.provenance_pointer.source_system,
                        record.source_class,
                        record.timestamp,
                        record.text,
                        digest,
                        record.provenance_pointer.to_json(),
                        json.dumps(record.metadata, sort_keys=True),
                    ),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    duplicates += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def latest_cursor(self, adapter: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cursor_json FROM adapter_cursor WHERE adapter = ?",
                (adapter,),
            ).fetchone()
            if row:
                return json.loads(row["cursor_json"])
            # Legacy fallback: cursors used to live on ingest_run rows. Skip
            # post-migration rows, which only carry a "_summary" placeholder.
            row = conn.execute(
                """
                SELECT cursor_after
                  FROM ingest_run
                 WHERE adapter = ? AND status = 'completed'
                   AND cursor_after NOT LIKE '%"_summary"%'
              ORDER BY run_id DESC
                 LIMIT 1
                """,
                (adapter,),
            ).fetchone()
        if not row or not row["cursor_after"]:
            return {}
        return json.loads(row["cursor_after"])

    def set_adapter_cursor(self, adapter: str, cursor: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO adapter_cursor(adapter, cursor_json, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(adapter) DO UPDATE
                   SET cursor_json = excluded.cursor_json,
                       updated_at = excluded.updated_at
                """,
                (adapter, json.dumps(cursor, sort_keys=True)),
            )

    def counts(self) -> dict[str, int]:
        tables = (
            "ingest_run",
            "evidence",
            "embedding",
            "cluster_state",
            "concept_event",
            "concept_card",
            "concept_edge_event",
        )
        with self.connect() as conn:
            return {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            }

    def sample_evidence(self, limit: int, *, random: bool = True) -> list[sqlite3.Row]:
        order = "RANDOM()" if random else "evidence_id"
        with self.connect() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT evidence_id, text, text_sha256, provenance_json
                      FROM evidence
                  ORDER BY {order}
                     LIMIT ?
                    """,
                    (limit,),
                )
            )

    def evidence_without_embedding(self, model: str, limit: int | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT e.evidence_id, e.text
              FROM evidence e
         LEFT JOIN embedding emb
                ON emb.evidence_id = e.evidence_id AND emb.model = ?
             WHERE emb.evidence_id IS NULL
          ORDER BY e.evidence_id
        """
        params: list[Any] = [model]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            return list(conn.execute(sql, params))

    def insert_embeddings(self, model: str, dim: int, items: Iterable[tuple[int, bytes]]) -> int:
        inserted = 0
        with self.connect() as conn:
            for evidence_id, vector in items:
                cur = conn.execute(
                    """
                    INSERT OR REPLACE INTO embedding(evidence_id, model, dim, vector)
                    VALUES (?, ?, ?, ?)
                    """,
                    (evidence_id, model, dim, vector),
                )
                inserted += int(cur.rowcount > 0)
        return inserted

    def load_embeddings(self, model: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT e.evidence_id, e.text, e.source_system, e.source_class, e.ts,
                           emb.dim, emb.vector
                      FROM evidence e
                      JOIN embedding emb ON emb.evidence_id = e.evidence_id
                     WHERE emb.model = ?
                  ORDER BY e.evidence_id
                    """,
                    (model,),
                )
            )

    def replace_clusters(self, run_id: int, members: Iterable[tuple[str, int, float]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.connect() as conn:
            conn.execute("DELETE FROM cluster_member")
            conn.execute("DELETE FROM cluster_state")
            for cluster_id, evidence_id, distance in members:
                conn.execute(
                    """
                    INSERT INTO cluster_member(cluster_id, evidence_id, run_id, distance)
                    VALUES (?, ?, ?, ?)
                    """,
                    (cluster_id, evidence_id, run_id, distance),
                )
                counts[cluster_id] = counts.get(cluster_id, 0) + 1
            for cluster_id, size in counts.items():
                if cluster_id == "noise":
                    continue
                conn.execute(
                    """
                    INSERT INTO cluster_state(cluster_id, run_id, size, named, status)
                    VALUES (?, ?, ?, 0, 'pending')
                    """,
                    (cluster_id, run_id, size),
                )
        return counts

    def pending_clusters(self, limit: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT cluster_id, size
                      FROM cluster_state
                     WHERE named = 0 AND status = 'pending'
                  ORDER BY size DESC, cluster_id
                     LIMIT ?
                    """,
                    (limit,),
                )
            )

    def cluster_sample(self, cluster_id: str, limit: int = 12) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT e.evidence_id, e.text, e.source_system, e.source_class, e.ts,
                           cm.distance
                      FROM cluster_member cm
                      JOIN evidence e ON e.evidence_id = cm.evidence_id
                     WHERE cm.cluster_id = ?
                  ORDER BY cm.distance ASC, e.evidence_id
                     LIMIT ?
                    """,
                    (cluster_id, limit),
                )
            )

    def create_concept_from_cluster(
        self,
        *,
        run_id: int,
        cluster_id: str,
        concept_id: str,
        title: str,
        concept_type: str,
        summary: str,
        aliases: list[str],
        evidence_ids: list[int],
        payload: dict[str, Any],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO concept_event(run_id, concept_id, event_type, payload_json, actor)
                VALUES (?, ?, 'created', ?, 'pipeline/namer')
                """,
                (run_id, concept_id, json.dumps(payload, sort_keys=True)),
            )
            for evidence_id in evidence_ids:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO concept_evidence(
                      concept_id, evidence_id, link_type, transform_chain_json, created_at
                    ) VALUES (?, ?, 'core', ?, datetime('now'))
                    """,
                    (concept_id, evidence_id, json.dumps(["cluster", "namer"], sort_keys=True)),
                )
            first_last = conn.execute(
                """
                SELECT MIN(ts), MAX(ts), COUNT(*), json_group_array(DISTINCT source_system)
                  FROM evidence
                 WHERE evidence_id IN (%s)
                """ % ",".join("?" for _ in evidence_ids),
                evidence_ids,
            ).fetchone()
            conn.execute(
                """
                INSERT OR REPLACE INTO concept_card(
                  concept_id, title, summary, concept_type, aliases_json, status,
                  first_seen, last_seen, evidence_count, source_systems_json,
                  score, score_components_json, nascent, pinned, confidence_grade
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 0, '{}', 0, 0, 'D')
                """,
                (
                    concept_id,
                    title,
                    summary,
                    concept_type,
                    json.dumps(aliases, sort_keys=True),
                    first_last[0],
                    first_last[1],
                    first_last[2],
                    first_last[3],
                ),
            )
            conn.execute(
                "UPDATE cluster_state SET named = 1, status = 'named' WHERE cluster_id = ?",
                (cluster_id,),
            )

    def mark_cluster(self, cluster_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE cluster_state SET status = ? WHERE cluster_id = ?", (status, cluster_id))

    def rank_concepts(self) -> int:
        with self.connect() as conn:
            rows = list(
                conn.execute(
                    """
                    SELECT cc.concept_id, cc.concept_type, cc.pinned,
                           COUNT(ce.evidence_id) AS evidence_count,
                           COUNT(DISTINCT e.source_system) AS source_diversity,
                           MIN(e.ts) AS first_seen,
                           MAX(e.ts) AS last_seen
                      FROM concept_card cc
                 LEFT JOIN concept_evidence ce ON ce.concept_id = cc.concept_id
                 LEFT JOIN evidence e ON e.evidence_id = ce.evidence_id
                     WHERE cc.status = 'active'
                  GROUP BY cc.concept_id
                    """
                )
            )
            if not rows:
                return 0
            max_count = max(row["evidence_count"] or 1 for row in rows) or 1
            for row in rows:
                frequency = (row["evidence_count"] or 0) / max_count
                diversity = min((row["source_diversity"] or 0) / 3, 1.0)
                actionability = {
                    "build_proposal": 1.0,
                    "product_idea": 1.0,
                    "risk": 0.8,
                    "unresolved_question": 0.8,
                    "operating_principle": 0.6,
                    "system_behavior": 0.6,
                    "theme": 0.5,
                    "relationship_covenant": 0.5,
                    "recurring_phrase": 0.3,
                }.get(row["concept_type"], 0.5)
                pin = 1.0 if row["pinned"] else 0.0
                score = 0.35 * frequency + 0.25 * diversity + 0.25 * actionability + 0.15 * pin
                nascent = 1 if (row["evidence_count"] or 0) <= 15 else 0
                confidence = "A" if row["evidence_count"] >= 3 and row["source_diversity"] >= 2 else "B"
                if row["evidence_count"] < 3:
                    confidence = "D"
                components = {
                    "frequency": frequency,
                    "source_diversity": diversity,
                    "actionability": actionability,
                    "pin": pin,
                }
                conn.execute(
                    """
                    UPDATE concept_card
                       SET score = ?, score_components_json = ?, nascent = ?,
                           confidence_grade = ?, evidence_count = ?,
                           first_seen = ?, last_seen = ?
                     WHERE concept_id = ?
                    """,
                    (
                        score,
                        json.dumps(components, sort_keys=True),
                        nascent,
                        confidence,
                        row["evidence_count"],
                        row["first_seen"],
                        row["last_seen"],
                        row["concept_id"],
                    ),
                )
        return len(rows)

    def concepts(self, view: str = "nascent", limit: int = 50) -> list[sqlite3.Row]:
        where = "WHERE status = 'active'"
        if view == "nascent":
            where += " AND nascent = 1"
        with self.connect() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT concept_id, title, summary, concept_type, evidence_count,
                           score, score_components_json, nascent, pinned,
                           confidence_grade, first_seen, last_seen, source_systems_json
                      FROM concept_card
                      {where}
                  ORDER BY pinned DESC, score DESC, evidence_count DESC
                     LIMIT ?
                    """,
                    (limit,),
                )
            )

    def concept(self, concept_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM concept_card WHERE concept_id = ?", (concept_id,)).fetchone()

    def concept_evidence(self, concept_id: str, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT e.evidence_id, e.text, e.source_system, e.source_class, e.ts,
                           e.provenance_json
                      FROM concept_evidence ce
                      JOIN evidence e ON e.evidence_id = ce.evidence_id
                     WHERE ce.concept_id = ?
                  ORDER BY e.ts DESC, e.evidence_id
                     LIMIT ?
                    """,
                    (concept_id, limit),
                )
            )

    def set_pin(self, concept_id: str, pinned: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE concept_card SET pinned = ? WHERE concept_id = ?", (int(pinned), concept_id))
            conn.execute(
                """
                INSERT INTO concept_event(concept_id, event_type, payload_json, actor)
                VALUES (?, ?, ?, 'user')
                """,
                (
                    concept_id,
                    "pinned" if pinned else "unpinned",
                    json.dumps({"pinned": pinned}, sort_keys=True),
                ),
            )
