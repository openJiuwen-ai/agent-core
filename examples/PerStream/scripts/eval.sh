#!/bin/bash

# Evaluation script for passive and proactive datasets

# Initialize conda
eval "$(conda shell.bash hook)"
conda activate perstream

# Get OpenAI key from .env
source .env

# Configuration - can be overridden via environment variables
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-Omni-7B}"
VIDEO_DIR="${VIDEO_DIR:-dataset/videos}"
GT_FILE="${GT_FILE:-dataset/drivenact.json}"
OUTPUT_BASE_DIR="${OUTPUT_BASE_DIR:-evaluation}"
MAX_FRAMES="${MAX_FRAMES:-8}"
GPU_ID="${GPU_ID:-0}"

# Set GPU
export CUDA_VISIBLE_DEVICES=${GPU_ID}

# Passive evaluation
python src/eval/eval_passive_dataset.py \
    --model-path "${MODEL_PATH}" \
    --video_dir "${VIDEO_DIR}" \
    --gt_file "${GT_FILE}" \
    --output_dir "${OUTPUT_BASE_DIR}/drivenact_passive/perstream" \
    --output_name "pred" \
    --max-frames "${MAX_FRAMES}" \
    --api_key "${OPENAI_API_KEY:-${OPENAIKEY}}"

# Proactive evaluation
python src/eval/eval_proactive_dataset.py \
    --model-path "${MODEL_PATH}" \
    --video_dir "${VIDEO_DIR}" \
    --gt_file "${GT_FILE}" \
    --output_dir "${OUTPUT_BASE_DIR}/drivenact_proactive/perstream" \
    --output_name "pred" \
    --api_key "${OPENAI_API_KEY}"
