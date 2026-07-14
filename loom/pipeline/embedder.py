from __future__ import annotations

from collections.abc import Iterable
import time

import numpy as np

from loom.config import EmbeddingConfig
from loom.store import LoomStore


def _device(configured: str) -> str:
    if configured != "auto":
        return configured
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def embed_missing(store: LoomStore, cfg: EmbeddingConfig, *, limit: int | None = None) -> dict[str, object]:
    from sentence_transformers import SentenceTransformer
    import torch

    rows = store.evidence_without_embedding(cfg.model, limit)
    if not rows:
        return {"model": cfg.model, "embedded": 0, "remaining": 0, "device": _device(cfg.device)}

    device = _device(cfg.device)
    model_kwargs = {"torch_dtype": torch.float16} if device == "cuda" else {}
    model = SentenceTransformer(cfg.model, device=device, model_kwargs=model_kwargs)
    model.max_seq_length = cfg.max_seq_length
    embedded = 0
    started = time.time()
    for index in range(0, len(rows), cfg.batch_size):
        batch = rows[index : index + cfg.batch_size]
        texts = [row["text"] for row in batch]
        vectors = model.encode(
            texts,
            batch_size=cfg.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")
        items: Iterable[tuple[int, bytes]] = (
            (int(row["evidence_id"]), np.asarray(vector, dtype="<f4").tobytes())
            for row, vector in zip(batch, vectors, strict=True)
        )
        embedded += store.insert_embeddings(cfg.model, int(vectors.shape[1]), items)

    remaining = len(store.evidence_without_embedding(cfg.model, 1))
    return {
        "model": cfg.model,
        "device": device,
        "max_seq_length": cfg.max_seq_length,
        "embedded": embedded,
        "remaining": remaining,
        "elapsed_seconds": round(time.time() - started, 2),
    }
