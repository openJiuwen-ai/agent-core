#!/bin/bash

# Reduction evaluation script for passive and proactive datasets

# Initialize conda
CONDA_BASE="${CONDA_BASE:-${HOME}/miniconda3}"
if [ -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]; then
    source "${CONDA_BASE}/etc/profile.d/conda.sh"
else
    eval "$(conda shell.bash hook)"
fi

conda activate perstream

# Get OpenAI key from .env
source .env

# Configuration - can be overridden via environment variables
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-Omni-7B}"
VIDEO_DIR="${VIDEO_DIR:-dataset/videos}"
GT_FILE="${GT_FILE:-tmp/drivenact_reduction.json}"
OUTPUT_BASE_DIR="${OUTPUT_BASE_DIR:-evaluation}"
PROJECTION_MODEL_PATH="${PROJECTION_MODEL_PATH:-ckpts/projection_mlp.pt}"

# Period reduction parameters
NUM_PERIODS="${NUM_PERIODS:-4}"
DEVICE_RAM_GB="${DEVICE_RAM_GB:-7}"
R_PRIME_GB="${R_PRIME_GB:-0.5}"
VIDEO_START_BEFORE="${VIDEO_START_BEFORE:-6.0}"
VIDEO_END_AFTER="${VIDEO_END_AFTER:-2.0}"

# Passive reduction evaluation
python src/eval/eval_passive_reduction.py \
    --model-path "${MODEL_PATH}" \
    --video_dir "${VIDEO_DIR}" \
    --gt_file "${GT_FILE}" \
    --output_dir "${OUTPUT_BASE_DIR}/drivenact_passive_reduction/perstream" \
    --output_name "period_reduction_results" \
    --num_periods "${NUM_PERIODS}" \
    --device_ram_gb "${DEVICE_RAM_GB}" \
    --r_prime_gb "${R_PRIME_GB}" \
    --api_key "${OPENAI_API_KEY}"

# Proactive reduction evaluation
python src/eval/eval_proactive_reduction.py \
    --model-path "${MODEL_PATH}" \
    --video_dir "${VIDEO_DIR}" \
    --gt_file "${GT_FILE}" \
    --output_dir "${OUTPUT_BASE_DIR}/drivenact_proactive_reduction/perstream" \
    --output_name "period_reduction_results_proactive" \
    --projection_model_path "${PROJECTION_MODEL_PATH}" \
    --num_periods "${NUM_PERIODS}" \
    --device_ram_gb "${DEVICE_RAM_GB}" \
    --r_prime_gb "${R_PRIME_GB}" \
    --video-start-before "${VIDEO_START_BEFORE}" \
    --video-end-after "${VIDEO_END_AFTER}" \
    --api_key "${OPENAI_API_KEY}"
