#!/usr/bin/env bash
# Test the remember gate on labeled key/not-key frames.
# Produces a similarity histogram and reports the best threshold by F1.
#
# Usage:
#   bash scripts/test_remember_gate.sh
#   LABELS_CSV=path/to/labels.csv bash scripts/test_remember_gate.sh

set -euo pipefail

# ── Config (override via env vars or edit here) ────────────────────────────
LABELS_CSV="${LABELS_CSV:-data/labels.csv}"
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-Omni-7B}"
PROJECTION_MODEL_PATH="${PROJECTION_MODEL_PATH:-checkpoints/projection_mlp.pt}"
POOL_SIZE="${POOL_SIZE:-8}"
IMAGE_SIZE="${IMAGE_SIZE:-224}"
NUM_SAMPLES="${NUM_SAMPLES:-100}"
SEED="${SEED:-42}"
OUTPUT_DIR="${OUTPUT_DIR:-results/remember_gate_test}"
# ───────────────────────────────────────────────────────────────────────────

echo "=================================================="
echo " Remember Gate Test"
echo "=================================================="
echo "  labels_csv   : $LABELS_CSV"
echo "  model_path   : $MODEL_PATH"
echo "  projection   : $PROJECTION_MODEL_PATH"
echo "  pool_size    : ${POOL_SIZE}x${POOL_SIZE}"
echo "  image_size   : $IMAGE_SIZE"
echo "  num_samples  : $NUM_SAMPLES (per class)"
echo "  seed         : $SEED"
echo "  output_dir   : $OUTPUT_DIR"
echo "=================================================="

python -m src.eval.test_remember_gate \
    --labels_csv           "$LABELS_CSV" \
    --model_path           "$MODEL_PATH" \
    --projection_model_path "$PROJECTION_MODEL_PATH" \
    --pool_size            "$POOL_SIZE" \
    --image_size           "$IMAGE_SIZE" \
    --num_samples          "$NUM_SAMPLES" \
    --seed                 "$SEED" \
    --output_dir           "$OUTPUT_DIR"

echo ""
echo "Done. Outputs written to: $OUTPUT_DIR"
