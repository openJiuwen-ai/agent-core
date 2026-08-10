#!/bin/bash

# Projection alignment training script
# Trains vision-to-text embedding alignment from video timestamps

# Initialize conda
eval "$(conda shell.bash hook)"
conda activate perstream

# Configuration - can be overridden via environment variables
MODEL_PATH="${MODEL_PATH:-Qwen/Qwen2.5-Omni-7B}"
VIDEO_DIR="${VIDEO_DIR:-dataset/videos}"
QUESTIONS_FILE="${QUESTIONS_FILE:-dataset/drivenact.json}"
OUTPUT_MODEL="${OUTPUT_MODEL:-projection_mlp.pt}"
POOL_SIZE="${POOL_SIZE:-4}"

# Optional parameters
MODE="${MODE:-both}"
DATASET_FILE="${DATASET_FILE:-alignment_dataset.pt}"
SENTENCE_MODEL="${SENTENCE_MODEL:-all-MiniLM-L6-v2}"
MAX_SAMPLES="${MAX_SAMPLES:-}"
IMAGE_SIZE="${IMAGE_SIZE:-224}"
VIDEO_START_BEFORE="${VIDEO_START_BEFORE:-2.0}"
VIDEO_END_AFTER="${VIDEO_END_AFTER:-2.0}"
VIDEO_EXTENSIONS="${VIDEO_EXTENSIONS:-.mp4 .avi .mkv}"
BATCH_SIZE="${BATCH_SIZE:-32}"
NUM_EPOCHS="${NUM_EPOCHS:-50}"
LEARNING_RATE="${LEARNING_RATE:-1e-3}"

python src/train/train_projection.py \
    --mode "${MODE}" \
    --model_path "${MODEL_PATH}" \
    --video_dir "${VIDEO_DIR}" \
    --questions_file "${QUESTIONS_FILE}" \
    --output_model "${OUTPUT_MODEL}" \
    --dataset_file "${DATASET_FILE}" \
    --sentence_model "${SENTENCE_MODEL}" \
    --pool_size "${POOL_SIZE}" \
    $([ -n "${MAX_SAMPLES}" ] && echo "--max_samples ${MAX_SAMPLES}" || echo "") \
    --image_size "${IMAGE_SIZE}" \
    --video_start_before "${VIDEO_START_BEFORE}" \
    --video_end_after "${VIDEO_END_AFTER}" \
    --video_extensions ${VIDEO_EXTENSIONS} \
    --batch_size "${BATCH_SIZE}" \
    --num_epochs "${NUM_EPOCHS}" \
    --lr "${LEARNING_RATE}"
