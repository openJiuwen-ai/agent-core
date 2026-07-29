#!/bin/bash

# Training script with PMG memory cache
# Run cache_memories.sh first to generate the cache files

set -e  # Exit on error

# Initialize conda
eval "$(conda shell.bash hook)"
conda activate perstream

# Configuration - can be overridden via environment variables
PROJECT_ROOT="${PROJECT_ROOT:-$(pwd)}"
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-Omni-7B}"
VIDEO_DIR="${VIDEO_DIR:-dataset/videos}"
DATA_FILE="${DATA_FILE:-${PROJECT_ROOT}/drivenact_proactive_results/drivenact_proactive_responses.json}"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_ROOT}/finetuned_models/qwen_lora_finetune_drivenact_proactive}"
CACHE_FILE="${CACHE_FILE:-${PROJECT_ROOT}/cache/train_pmg_memories.json}"

# Training hyperparameters
NUM_EPOCHS="${NUM_EPOCHS:-3}"
BATCH_SIZE="${BATCH_SIZE:-1}"
GRAD_ACCUM="${GRAD_ACCUM:-8}"
LEARNING_RATE="${LEARNING_RATE:-2e-4}"

# LoRA parameters
LORA_R="${LORA_R:-8}"
LORA_ALPHA="${LORA_ALPHA:-16}"
LORA_DROPOUT="${LORA_DROPOUT:-0.05}"

# Change to project root if specified
if [ -n "${PROJECT_ROOT}" ] && [ -d "${PROJECT_ROOT}" ]; then
    cd "${PROJECT_ROOT}"
    if [ -d "src" ]; then
        cd "src"
    fi
fi

echo "================================================"
echo "Training with PMG Memory Cache"
echo "================================================"
echo "Model: ${MODEL_PATH}"
echo "Output: ${OUTPUT_DIR}"
echo "Memory cache: ${CACHE_FILE}"
echo "================================================"

# Check if cache file exists
if [ ! -f "${CACHE_FILE}" ]; then
    echo "Error: Cache file not found: ${CACHE_FILE}"
    echo "Please run scripts/cache_memories.sh first"
    exit 1
fi

# Train
python finetune_qwen_lora.py \
  --model-path "${MODEL_PATH}" \
  --output-dir "${OUTPUT_DIR}" \
  --video-dir "${VIDEO_DIR}" \
  --data-file "${DATA_FILE}" \
  --memory-cache "${CACHE_FILE}" \
  --num-train-epochs "${NUM_EPOCHS}" \
  --per-device-train-batch-size "${BATCH_SIZE}" \
  --per-device-eval-batch-size "${BATCH_SIZE}" \
  --gradient-accumulation-steps "${GRAD_ACCUM}" \
  --learning-rate "${LEARNING_RATE}" \
  --lora-r "${LORA_R}" \
  --lora-alpha "${LORA_ALPHA}" \
  --lora-dropout "${LORA_DROPOUT}" \
  --bf16 \
  --gradient-checkpointing

echo ""
echo "================================================"
echo "Training complete!"
echo "Model saved to: ${OUTPUT_DIR}"
echo "================================================"
