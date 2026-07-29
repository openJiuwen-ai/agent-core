#!/bin/bash

# PerStream video processing script
# Processes video with memory-enabled AI assistant

# Initialize conda
eval "$(conda shell.bash hook)"
conda activate perstream

# Get OpenAI key from .env
source .env

# Configuration - can be overridden via environment variables
VIDEO_PATH="${VIDEO_PATH:-sample_videos/7246228-hd_1920_1080_24fps.mp4}"
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-Omni-7B}"
PROJECTION_MODEL_PATH="${PROJECTION_MODEL_PATH:-ckpts/projection_mlp.pt}"
GPU_ID="${GPU_ID:-0}"

# Optional parameters
GAMMA_THRESHOLD="${GAMMA_THRESHOLD:-0.2}"
BUFFER_SIZE="${BUFFER_SIZE:-4}"
QUEUE_SIZE="${QUEUE_SIZE:-1}"
POOL_LEN="${POOL_LEN:-4}"
ENABLE_PROACTIVE="${ENABLE_PROACTIVE:-true}"

# Set GPU
export CUDA_VISIBLE_DEVICES=${GPU_ID}
echo "CUDA_VISIBLE_DEVICES: $CUDA_VISIBLE_DEVICES"

# Run PerStream
python src/core/perstream.py \
    --api_key "${OPENAI_API_KEY}" \
    --video_path "${VIDEO_PATH}" \
    --model_path "${MODEL_PATH}" \
    --projection_model_path "${PROJECTION_MODEL_PATH}" \
    --gamma_threshold "${GAMMA_THRESHOLD}" \
    --buffer_size "${BUFFER_SIZE}" \
    --queue_size "${QUEUE_SIZE}" \
    --pool_len "${POOL_LEN}" \
    $([ "${ENABLE_PROACTIVE}" = "true" ] && echo "--enable_proactive" || echo "--disable_proactive")
