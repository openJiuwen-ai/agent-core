#!/bin/bash

# Scoring script for evaluation results

# Initialize conda
eval "$(conda shell.bash hook)"
conda activate perstream

# Get OpenAI key from .env
source .env

# Configuration - can be overridden via environment variables
OUTPUT_BASE_DIR="${OUTPUT_BASE_DIR:-evaluation}"
NUM_TASKS="${NUM_TASKS:-16}"

# Score passive results
python src/eval/score_passive_judge.py \
    --pred_path "${OUTPUT_BASE_DIR}/drivenact_passive/perstream/pred.json" \
    --output_dir "${OUTPUT_BASE_DIR}/drivenact_passive/perstream/results" \
    --output_json "${OUTPUT_BASE_DIR}/drivenact_passive/perstream/results.json" \
    --num_tasks "${NUM_TASKS}" \
    --api_key "${OPENAI_API_KEY}"

# Score proactive results
python src/eval/score_proactive_judge.py \
    --pred_path "${OUTPUT_BASE_DIR}/drivenact_proactive/perstream/pred.json" \
    --output_dir "${OUTPUT_BASE_DIR}/drivenact_proactive/perstream/results" \
    --output_json "${OUTPUT_BASE_DIR}/drivenact_proactive/perstream/results.json" \
    --num_tasks "${NUM_TASKS}" \
    --api_key "${OPENAI_API_KEY}"
