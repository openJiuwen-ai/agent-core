#!/usr/bin/env python3
"""
Test the remember gate using a labeled CSV of key/not-key frames.

labels.csv format: video_id, timestamp, frame_path, label
  label: 1 or "key" = should remember, 0 or "not_key" = should not remember

Samples 100 key frames and 100 not-key frames, collects raw similarity scores,
plots a histogram of the score distributions, finds the best threshold by F1,
and reports metrics at that threshold.
"""

import argparse
import csv
import json
import os
import random
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, confusion_matrix, classification_report
)
from tqdm import tqdm

from src.core.memory_subcategories import get_memory_subclass_embeddings
from src.utils.model_utils import load_model, load_projection_model
from src.utils.perstream_utils import remember_gate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_key(label: str) -> bool:
    return str(label).strip().lower() in ("1", "key", "true", "yes")


def load_and_sample(csv_path: str, n_key: int, n_not_key: int, seed: int = 42):
    key_rows, not_key_rows = [], []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            (key_rows if _is_key(row["label"]) else not_key_rows).append(row)

    print(f"Total key frames    : {len(key_rows)}")
    print(f"Total not-key frames: {len(not_key_rows)}")

    rng = random.Random(seed)
    sampled_key = rng.sample(key_rows, min(n_key, len(key_rows)))
    sampled_not = rng.sample(not_key_rows, min(n_not_key, len(not_key_rows)))

    print(f"Sampled key frames    : {len(sampled_key)}")
    print(f"Sampled not-key frames: {len(sampled_not)}")
    return sampled_key, sampled_not


# ---------------------------------------------------------------------------
# Best-threshold search
# ---------------------------------------------------------------------------

def find_best_threshold(y_true: np.ndarray, y_score: np.ndarray, n_steps: int = 200):
    """Sweep candidate thresholds and return the one with the highest F1."""
    lo, hi = y_score.min(), y_score.max()
    candidates = np.linspace(lo, hi, n_steps)
    best_t, best_f1 = lo, -1.0
    for t in candidates:
        y_pred = (y_score > t).astype(int)
        f = f1_score(y_true, y_pred, zero_division=0)
        if f > best_f1:
            best_f1, best_t = f, t
    return best_t, best_f1


# ---------------------------------------------------------------------------
# Histogram plot
# ---------------------------------------------------------------------------

def plot_histogram(y_true: np.ndarray, y_score: np.ndarray,
                   best_threshold: float, output_path: str):
    key_scores = y_score[y_true == 1]
    not_key_scores = y_score[y_true == 0]

    fig, ax = plt.subplots(figsize=(9, 5))

    bins = np.linspace(y_score.min(), y_score.max(), 40)
    ax.hist(not_key_scores, bins=bins, alpha=0.6, color="steelblue",
            label=f"Not-key (n={len(not_key_scores)})", edgecolor="white")
    ax.hist(key_scores, bins=bins, alpha=0.6, color="tomato",
            label=f"Key (n={len(key_scores)})", edgecolor="white")

    ax.axvline(best_threshold, color="black", linestyle="--", linewidth=1.5,
               label=f"Best threshold = {best_threshold:.4f}")

    ax.set_xlabel("Max cosine similarity (remember gate score)")
    ax.set_ylabel("Frame count")
    ax.set_title("Remember Gate — Similarity Score Distribution")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"Saved histogram -> {output_path}")


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def evaluate(args):
    # ---- Load models --------------------------------------------------------
    print("Loading Qwen model...")
    model, processor = load_model(args.model_path)

    print("Loading projection MLP...")
    projection_mlp = load_projection_model(args.projection_model_path)
    projection_mlp = projection_mlp.to(model.device)

    print("Building memory subclass embeddings...")
    memory_subclass_embedding_matrix, category_names = get_memory_subclass_embeddings(model, processor)
    pool_size = (args.pool_size, args.pool_size)

    # ---- Sample frames ------------------------------------------------------
    sampled_key, sampled_not = load_and_sample(
        args.labels_csv, n_key=args.num_samples, n_not_key=args.num_samples, seed=args.seed
    )
    all_rows = [(row, 1) for row in sampled_key] + [(row, 0) for row in sampled_not]
    random.Random(args.seed).shuffle(all_rows)

    # ---- Collect raw similarity scores (no threshold applied) ---------------
    y_true, y_score = [], []
    per_frame_results = []

    for row, gt_label in tqdm(all_rows, desc="Collecting similarity scores"):
        frame_path = row["frame_path"]
        if not os.path.exists(frame_path):
            print(f"  [WARN] Not found, skipping: {frame_path}")
            continue
        try:
            img = Image.open(frame_path).convert("RGB")
            if args.image_size > 0:
                img = img.resize((args.image_size, args.image_size))

            _, max_similarity, best_category, _ = remember_gate(
                img,
                memory_subclass_embedding_matrix,
                category_names,
                model,
                processor,
                projection_mlp,
                gamma_threshold=-999.0,   # always collect score, never gate
                pool_size=pool_size,
            )
        except Exception as e:
            print(f"  [ERROR] {frame_path}: {e}")
            continue

        y_true.append(gt_label)
        y_score.append(float(max_similarity))
        per_frame_results.append({
            "video_id": row.get("video_id", ""),
            "timestamp": row.get("timestamp", ""),
            "frame_path": frame_path,
            "gt_label": gt_label,
            "max_similarity": float(max_similarity),
            "best_category": best_category,
        })

    y_true = np.array(y_true)
    y_score = np.array(y_score)

    # ---- Find best threshold ------------------------------------------------
    best_threshold, _ = find_best_threshold(y_true, y_score)
    y_pred = (y_score > best_threshold).astype(int)

    # ---- Compute metrics at best threshold ----------------------------------
    acc  = accuracy_score(y_true, y_pred)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec  = recall_score(y_true, y_pred, zero_division=0)
    f1   = f1_score(y_true, y_pred, zero_division=0)
    try:
        auc = roc_auc_score(y_true, y_score)
    except ValueError:
        auc = float("nan")
    cm = confusion_matrix(y_true, y_pred).tolist()

    # ---- Print summary ------------------------------------------------------
    print("\n" + "=" * 60)
    print("REMEMBER GATE EVALUATION RESULTS")
    print("=" * 60)
    print(f"  pool_size           : {args.pool_size}x{args.pool_size}")
    print(f"  key frames          : {int((y_true == 1).sum())}")
    print(f"  not-key frames      : {int((y_true == 0).sum())}")
    print(f"\n  Score range         : [{y_score.min():.4f}, {y_score.max():.4f}]")
    print(f"  Mean (key)          : {y_score[y_true == 1].mean():.4f}")
    print(f"  Mean (not-key)      : {y_score[y_true == 0].mean():.4f}")
    print(f"\n  Best threshold (F1) : {best_threshold:.4f}")
    print(f"  Accuracy            : {acc:.4f}")
    print(f"  Precision           : {prec:.4f}")
    print(f"  Recall              : {rec:.4f}")
    print(f"  F1                  : {f1:.4f}")
    print(f"  AUC-ROC             : {auc:.4f}")
    print(f"\nConfusion matrix (rows=true, cols=pred):")
    print(f"  TN={cm[0][0]}  FP={cm[0][1]}")
    print(f"  FN={cm[1][0]}  TP={cm[1][1]}")
    print(f"\n{classification_report(y_true, y_pred, target_names=['not_key', 'key'])}")

    # ---- Save outputs -------------------------------------------------------
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

        metrics = {
            "best_threshold": best_threshold,
            "pool_size": args.pool_size,
            "num_key_evaluated": int((y_true == 1).sum()),
            "num_not_key_evaluated": int((y_true == 0).sum()),
            "score_range": [float(y_score.min()), float(y_score.max())],
            "mean_score_key": float(y_score[y_true == 1].mean()),
            "mean_score_not_key": float(y_score[y_true == 0].mean()),
            "accuracy": acc, "precision": prec, "recall": rec, "f1": f1,
            "auc_roc": auc, "confusion_matrix": cm,
        }
        with open(os.path.join(args.output_dir, "remember_gate_metrics.json"), "w") as f:
            json.dump(metrics, f, indent=2)
        with open(os.path.join(args.output_dir, "remember_gate_per_frame.json"), "w") as f:
            json.dump(per_frame_results, f, indent=2)

        plot_histogram(y_true, y_score, best_threshold,
                       os.path.join(args.output_dir, "remember_gate_histogram.png"))

        print(f"Saved metrics   -> {os.path.join(args.output_dir, 'remember_gate_metrics.json')}")
        print(f"Saved per-frame -> {os.path.join(args.output_dir, 'remember_gate_per_frame.json')}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Test the remember gate — histogram + best threshold")
    parser.add_argument("--labels_csv", required=True,
                        help="Path to labels.csv (columns: video_id, timestamp, frame_path, label)")
    parser.add_argument("--model_path", required=True,
                        help="Path to Qwen model")
    parser.add_argument("--projection_model_path", required=True,
                        help="Path to trained projection MLP (.pt)")
    parser.add_argument("--pool_size", type=int, default=8,
                        help="Spatial pooling size (pool_size x pool_size, default: 8)")
    parser.add_argument("--image_size", type=int, default=224,
                        help="Resize frames to this size (0 = no resize, default: 224)")
    parser.add_argument("--num_samples", type=int, default=100,
                        help="Frames to sample per class (default: 100)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--output_dir", type=str, default=None,
                        help="Directory to save metrics, per-frame JSON, and histogram PNG")
    return parser.parse_args()


if __name__ == "__main__":
    evaluate(parse_args())
