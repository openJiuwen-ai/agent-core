#!/bin/bash

# Dataset generation script for DrivenAct and Ego4D
# Generates passive and proactive QA pairs from video datasets

# Initialize conda
eval "$(conda shell.bash hook)"
conda activate perstream

# get openai key from .env
source .env

# Configuration - Update these paths as needed
DRIVENACT_PATH="${DRIVENACT_PATH:-dataset/drivenact}"
DRIVENACT_DATASET="${DRIVENACT_DATASET:-drivenact}"
ANNOTATIONS_CSV="${ANNOTATIONS_CSV:-${DRIVENACT_PATH}/iccv_activities_3s/activities_3s/kinect_color/midlevel.chunks_90.csv}"

EGO4D_PATH="${EGO4D_PATH:-dataset/ego4d}"
EGO4D_DATASET="${EGO4D_DATASET:-ego4d}"

# Optional arguments (using defaults if not specified)
MODEL_ID="${MODEL_ID:-openai/gpt-4o-mini}"
SCALE_DOWN="${SCALE_DOWN:-1}"
VIDEO_START_BEFORE="${VIDEO_START_BEFORE:-6.0}"
VIDEO_END_AFTER="${VIDEO_END_AFTER:-2.0}"
NUM_WORKERS="${NUM_WORKERS:-8}"

# Generate DrivenAct dataset
if [ -f "${ANNOTATIONS_CSV}" ]; then
    echo "Generating DrivenAct dataset..."
    python src/generate_dataset/generate_drivenact.py \
        --drivenact_path "${DRIVENACT_PATH}" \
        --drivenact_dataset "${DRIVENACT_DATASET}" \
        --annotations_csv "${ANNOTATIONS_CSV}" \
        --model_id "${MODEL_ID}" \
        --scale_down "${SCALE_DOWN}" \
        --video_start_before "${VIDEO_START_BEFORE}" \
        --video_end_after "${VIDEO_END_AFTER}" \
        --num_workers "${NUM_WORKERS}" \
        --api_key "${OPENAI_API_KEY}"
else
    echo "Warning: Annotations CSV not found at ${ANNOTATIONS_CSV}. Skipping DrivenAct generation."
fi

# Generate Ego4D dataset
if [ -d "${EGO4D_PATH}" ]; then
    echo "Generating Ego4D dataset..."
    python src/generate_dataset/generate_ego4d.py \
        --ego4d_path "${EGO4D_PATH}" \
        --ego4d_dataset "${EGO4D_DATASET}" \
        --model_id "${MODEL_ID}" \
        --scale_down "${SCALE_DOWN}" \
        --video_start_before "${VIDEO_START_BEFORE}" \
        --video_end_after "${VIDEO_END_AFTER}" \
        --num_workers "${NUM_WORKERS}" \
        --api_key "${OPENAI_API_KEY}"
else
    echo "Warning: Ego4D path not found at ${EGO4D_PATH}. Skipping Ego4D generation."
fi
