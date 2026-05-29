#!/usr/bin/env python3
"""Cluster detections by likely writer using CLIP image embeddings.

Same tagger leaves a consistent visual style across walls — same letter
forms, same colour palette, same paint texture. We encode each crop with
CLIP-ViT-Base, then group crops by cosine similarity of those vectors.
Clusters with ≥3 detections look like a serial offender; the city can
prosecute one person across many cases (which carries heavier penalty
than each tag treated alone, per the user's prior experience).

Usage:
    uv run python scripts/cluster_writers.py
    uv run python scripts/cluster_writers.py --threshold 0.20  # 0.15 default
    uv run python scripts/cluster_writers.py --min-cluster 5    # filter noise
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import torch
from PIL import Image
from sklearn.cluster import AgglomerativeClustering
from transformers import CLIPModel, CLIPProcessor

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "store.sqlite"
CACHE = ROOT / "data" / "embeddings" / "crop_clip_b32.npz"


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(detections)")}
    for col, decl in [
        ("writer_id", "INTEGER"),
        ("writer_cluster_size", "INTEGER"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE detections ADD COLUMN {col} {decl}")
    conn.commit()


def _batched(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def encode_crops(rows: list[sqlite3.Row], device: str) -> np.ndarray:
    """Return (N, D) normalized embeddings, aligned with rows order."""
    if CACHE.exists():
        cached = np.load(CACHE)
        cached_ids = list(cached["detection_ids"])
        wanted_ids = [r["detection_id"] for r in rows]
        if cached_ids == wanted_ids:
            print(f"  using cached embeddings ({cached['vectors'].shape})")
            return cached["vectors"]

    print(f"  loading CLIP on {device}...")
    model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32").to(device).eval()
    processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

    vectors: list[np.ndarray] = []
    bs = 32
    total = len(rows)
    for i, batch in enumerate(_batched(list(rows), bs)):
        images = []
        for r in batch:
            p = Path(r["crop_path"])
            if not p.exists():
                # placeholder zero-vector; will be filtered out of clustering
                images.append(Image.new("RGB", (32, 32)))
                continue
            images.append(Image.open(p).convert("RGB"))
        inputs = processor(images=images, return_tensors="pt")
        pixel_values = inputs["pixel_values"].to(device)
        with torch.no_grad():
            out = model.get_image_features(pixel_values=pixel_values)
        # transformers 5.x returns BaseModelOutputWithPooling whose
        # pooler_output is the 512-d CLIP image embedding.
        feats = out.pooler_output if hasattr(out, "pooler_output") else out
        feats = feats / feats.norm(dim=-1, keepdim=True)
        vectors.append(feats.cpu().numpy())
        done = min(i * bs + bs, total)
        print(f"  encoded {done}/{total}", end="\r", flush=True)
    print()
    arr = np.concatenate(vectors, axis=0).astype(np.float32)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        CACHE,
        vectors=arr,
        detection_ids=np.array([r["detection_id"] for r in rows]),
    )
    print(f"  cached embeddings to {CACHE.relative_to(ROOT)}")
    return arr


def cluster(vectors: np.ndarray, threshold: float) -> np.ndarray:
    """Agglomerative single-linkage on cosine distance. Returns labels (N,)."""
    if len(vectors) < 2:
        return np.zeros(len(vectors), dtype=np.int64)
    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        metric="cosine",
        linkage="average",
    )
    return model.fit_predict(vectors)


def annotate(threshold: float, min_cluster: int) -> None:
    conn = sqlite3.connect(DB, timeout=15)
    _ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT detection_id, crop_path FROM detections "
        "WHERE submit_status='pending' ORDER BY detection_id"
    ).fetchall()
    print(f"encoding {len(rows)} crops...")
    vectors = encode_crops(rows, _device())

    print(f"clustering with threshold={threshold} (cosine distance)...")
    labels = cluster(vectors, threshold)

    sizes: dict[int, int] = {}
    for lbl in labels:
        sizes[int(lbl)] = sizes.get(int(lbl), 0) + 1

    # Map raw cluster labels → stable writer_id (largest cluster = #1).
    by_size = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)
    writer_id_for_label = {lbl: rank + 1 for rank, (lbl, _) in enumerate(by_size)}

    serial = sum(1 for s in sizes.values() if s >= min_cluster)
    print(f"  {len(sizes)} clusters · {serial} look serial (≥{min_cluster} hits)")

    for row, lbl in zip(rows, labels):
        wid = writer_id_for_label[int(lbl)]
        size = sizes[int(lbl)]
        conn.execute(
            "UPDATE detections SET writer_id=?, writer_cluster_size=? "
            "WHERE detection_id=?",
            (wid, size, row["detection_id"]),
        )
    conn.commit()

    print("\ntop 10 candidate writers (cluster_size · writer_id):")
    for lbl, size in by_size[:10]:
        wid = writer_id_for_label[lbl]
        sample_ids = [
            r["detection_id"][:8]
            for r, l in zip(rows, labels)
            if int(l) == lbl
        ][:3]
        print(f"  writer #{wid:3d}  ×{size:3d}  samples {', '.join(sample_ids)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.15,
        help="cosine distance threshold (0.10=strict, 0.20=loose)",
    )
    parser.add_argument(
        "--min-cluster",
        type=int,
        default=3,
        help="cluster size below this is treated as one-off (not serial)",
    )
    args = parser.parse_args()
    annotate(args.threshold, args.min_cluster)


if __name__ == "__main__":
    main()
