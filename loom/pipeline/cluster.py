from __future__ import annotations

import math

import hdbscan
import numpy as np
from sklearn.decomposition import PCA

from loom.config import ClusteringConfig
from loom.store import LoomStore


def cluster_embeddings(store: LoomStore, cfg: ClusteringConfig, *, model: str) -> dict[str, int]:
    rows = store.load_embeddings(model)
    if not rows:
        return {"embeddings": 0, "clusters": 0, "noise": 0}

    dim = int(rows[0]["dim"])
    matrix = np.vstack([np.frombuffer(row["vector"], dtype="<f4", count=dim) for row in rows])
    if 0 < cfg.reduced_dimensions < matrix.shape[1]:
        reducer = PCA(n_components=cfg.reduced_dimensions, svd_solver="randomized", random_state=42)
        cluster_matrix = reducer.fit_transform(matrix).astype("float32")
    else:
        cluster_matrix = matrix

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=cfg.min_cluster_size,
        min_samples=cfg.min_samples,
        metric="euclidean",
        core_dist_n_jobs=-1,
    )
    labels = clusterer.fit_predict(cluster_matrix)

    centroids: dict[int, np.ndarray] = {}
    for label in sorted(set(int(label) for label in labels if label >= 0)):
        centroids[label] = cluster_matrix[labels == label].mean(axis=0)

    run_id = store.start_run("cluster", {"model": model})
    members: list[tuple[str, int, float]] = []
    for row, label, vector in zip(rows, labels, cluster_matrix, strict=True):
        label = int(label)
        if label < 0:
            members.append(("noise", int(row["evidence_id"]), math.inf))
            continue
        centroid = centroids[label]
        distance = float(np.linalg.norm(vector - centroid))
        members.append((f"cluster-{label}", int(row["evidence_id"]), distance))
    counts = store.replace_clusters(run_id, members)
    store.finish_run(
        run_id,
        cursor_after={"model": model},
        stats={
            "embeddings": len(rows),
            "clusters": len([k for k in counts if k != "noise"]),
            "noise": counts.get("noise", 0),
            "reduced_dimensions": cfg.reduced_dimensions,
        },
    )
    return {
        "embeddings": len(rows),
        "clusters": len([k for k in counts if k != "noise"]),
        "noise": counts.get("noise", 0),
        "reduced_dimensions": cfg.reduced_dimensions,
    }
