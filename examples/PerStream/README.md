# PerStream: Personal Memory Assistant for Egocentric Video Streams

A multimodal AI system that processes egocentric video streams to build and query a personalized memory graph, supporting both **passive (query-driven)** and **proactive (autonomous)** response modes.

## Key Features

- **Dual-Mode Operation**: Query-driven (passive) and autonomous (proactive) response generation
- **Personalized Memory Graph (PMG)**: Two-layer architecture with 30 semantic memory categories
- **Real-Time Processing**: Continuous video stream understanding with memory augmentation
- **Remember Gate Mechanism**: Intelligent frame filtering using category similarity
- **Memory Efficiency**: Granular reduction strategies (NSBG/GSBN) for VRAM management
- **Multi-GPU Support**: Distributed model loading for large-scale inference
- **Spatial Preservation**: Pooled embeddings maintain patch grid structure for precise retrieval

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    VIDEO STREAM INPUT                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │  Frame Extraction      │
          │  (OpenCV + Resize)     │
          └────────┬───────────────┘
                   │
                   ▼
          ┌────────────────────────┐
          │  Optical Flow Sampling │
          │  (Keyframe Selection)  │
          └────────┬───────────────┘
                   │
        ┌──────────┴───────────────┐
        │                          │
        ▼                          ▼
    [Remember Gate]          [Caption Gen]
    [Category Match]         [Triplet Ext]
        │                          │
        └──────────┬───────────────┘
                   │
                   ▼
    ┌──────────────────────────────┐
    │ Personalized Memory Graph    │
    │  (30 categories, triplets)   │
    └──────────┬───────────────────┘
               │
        ┌──────┴─────┐
        │            │
        ▼            ▼
    [Passive]   [Proactive]
    [Query]     [Response]
        │            │
        └──────┬─────┘
               │
               ▼
        [User Response]
```

## Installation

### Prerequisites

- Python 3.10+
- CUDA 12.1+
- Conda

### Setup

```bash
# Create conda environment
conda create -n perstream python=3.10
conda activate perstream

# Install PyTorch with CUDA support
pip install torch==2.3.1+cu121 torchvision==0.20.1+cu121 torchaudio==2.3.1+cu121 --index-url https://download.pytorch.org/whl/cu121

# Install dependencies
pip install -r requirements.txt

# Install package in development mode
pip install -e .
```

### Environment Variables

Set up your OpenAI API key for triplet extraction in .env:
```bash
OPENAI_API_KEY=<your-openai-api-key>
```

## Project Structure

```
PerStream/
├── src/                                  # Core source code
│   ├── core/                             # Core algorithms and memory management
│   │   ├── perstream.py                  # Main orchestration system
│   │   ├── personalized_memory_graph.py  # Two-layer memory architecture
│   │   ├── memory_subcategories.py       # 30 semantic memory categories
│   │   ├── passive_user_query.py         # Query-driven response generation
│   │   ├── proactive_user_query.py       # Autonomous response generation
│   │   └── memory_dataset_class.py       # PyTorch Dataset for evaluation
│   │
│   ├── utils/                            # Utility functions
│   │   ├── perstream_utils.py            # Core utilities (embeddings, triplets, gates)
│   │   ├── video_utils.py                # Video processing and optical flow
│   │   └── model_utils.py                # Model loading and projection MLP
│   │
│   ├── train/                            # Training scripts
│   │   ├── train_perstream.py            # LoRA fine-tuning for Qwen2.5-Omni
│   │   └── train_projection.py           # Visual-semantic alignment training
│   │
│   └── eval/                             # Evaluation scripts
│       ├── eval_passive_dataset.py       # Passive mode evaluation
│       ├── eval_proactive_dataset.py     # Proactive mode evaluation
│       ├── eval_passive_reduction.py     # Memory reduction evaluation
│       ├── eval_proactive_reduction.py   # Memory reduction evaluation
│       ├── score_passive_judge.py        # LLM-based passive scoring
│       └── score_proactive_judge.py      # LLM-based proactive scoring
│
├── dataset/                              # Dataset files
│   ├── drivenact.json                    # DrivenAct dataset annotations
│   └── ego4d.json                        # Ego4D dataset annotations
│
├── finetuned_models/                     # Pre-trained model checkpoints
├── ckpts/                                # Projection model checkpoints
├── scripts/                              # Bash scripts for execution
├── evaluation/                           # Evaluation results and logs
├── sample_videos/                        # Test video samples
├── requirements.txt                      # Python dependencies
└── setup.py                              # Package setup configuration
```

## Usage

### Generate Dataset (DrivenAct and Ego4D)

```bash
bash scripts/generate_dataset.sh
```

### Running Inference

Run the main inference pipeline on a video:

```bash
bash scripts/perstream.sh
```

Or run directly with Python:

```python
from src.core.perstream import video_stream_with_memory

# Process video with memory augmentation
video_stream_with_memory(
    video_path="path/to/video.mp4",
    buffer_size=4,
    gamma_threshold=0.3,
    enable_proactive=True
)
```

### Training

#### LoRA Fine-tuning

Fine-tune Qwen2.5-Omni on memory-augmented video QA:

```bash
bash scripts/train.sh
```

Or run directly:

```bash
python -m src.train.train_perstream \
    --model_name "Qwen/Qwen2.5-Omni-7B" \
    --dataset_path "dataset/drivenact.json" \
    --output_dir "finetuned_models/custom_model" \
    --num_epochs 3 \
    --learning_rate 2e-4 \
    --batch_size 1 \
    --gradient_accumulation_steps 8
```

**Training Configuration:**
- LoRA: r=8, alpha=16, dropout=0.05
- Target modules: q_proj, k_proj, v_proj, o_proj
- Mixed precision: bf16/fp16

#### Projection Model Training

Train visual-semantic alignment:

```bash
bash scripts/proj_align.sh
```

### Evaluation

Run evaluation on datasets:

```bash
bash scripts/eval.sh
```

Run scoring:

```bash
bash scripts/score.sh
```

Run reduction evaluation:

```bash
bash scripts/reduction_eval.sh
```

---

## Using Your Own Dataset

### Dataset Format

Create a JSON file with the following structure:

```json
[
  {
    "participant_id": 1,
    "video_id": "path/to/video_file",
    "timestamp": "120.5",
    "caption": "Description of the scene",
    "split": "train",
    "type_1_memories": [
      "Short factual memory 1 (habits, preferences)",
      "Short factual memory 2"
    ],
    "type_2_memories": [
      "Longer narrative memory describing an episodic event...",
      "Another episodic memory..."
    ],
    "type": "passive",
    "question": "What did I do yesterday at the store?",
    "answer": "You bought groceries and talked to the cashier about..."
  }
]
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `participant_id` | int | Unique identifier for the participant |
| `video_id` | string | Path to video file (relative or absolute) |
| `timestamp` | string | Timestamp in seconds where the event occurs |
| `caption` | string | Brief description of the current scene |
| `split` | string | Dataset split: "train", "val", or "test" |
| `type_1_memories` | list[str] | Short factual memories (habits, preferences, facts) — randomly generated from `caption`, not from raw video; represent synthetic prior personal context |
| `type_2_memories` | list[str] | Long narrative memories (episodic events) — randomly generated from `caption`, not from raw video; represent synthetic prior personal context |
| `type` | string | Query type: "passive" (user-initiated) or "proactive" (system-initiated) |
| `question` | string | User query (for passive type) or context (for proactive) |
| `answer` | string | Ground truth answer |

### Memory Types

> **Important**: Type 1 and Type 2 memories are **not derived from the raw video**. They are **randomly generated by an LLM based solely on the scene caption**, serving as synthetic prior personal context for the user. This is an intentional design choice — the memories simulate what a user might already know or have experienced before the video was recorded, and are a **distinct feature** from the video-based memory graph built during inference.

- **Type 1 (Factual)**: Short, declarative statements about habits/preferences — randomly generated from the caption as plausible background facts about the user
  - Example: "I keep snacks in my car.", "I prefer coffee over tea."

- **Type 2 (Episodic)**: Longer narratives describing past events — randomly generated from the caption as plausible prior personal experiences of the user
  - Example: "Last Tuesday, I accidentally left my umbrella at the coffee shop and had to go back the next day to retrieve it."

### Video Requirements

- **Format**: MP4, AVI, or other OpenCV-compatible formats
- **Resolution**: 224p+ recommended (auto-resized during processing)
- **Frame Rate**: Any (keyframes selected via optical flow)

### Example: Creating a Custom Dataset

```python
import json

dataset = [
    {
        "participant_id": 1,
        "video_id": "videos/cooking_session_01.mp4",
        "timestamp": "45.0",
        "caption": "chopping vegetables on cutting board",
        "split": "train",
        "type_1_memories": [
            "I am vegetarian.",
            "I prefer organic vegetables.",
            "I usually cook dinner at 7 PM."
        ],
        "type_2_memories": [
            "Last week I tried a new recipe for vegetable stir-fry and it turned out great. I used extra garlic.",
            "Two months ago I cut my finger while chopping onions and had to bandage it."
        ],
        "type": "passive",
        "question": "What vegetables do I usually cook with?",
        "answer": "Based on your memories, you typically cook with organic vegetables. You've recently made vegetable stir-fry with extra garlic."
    }
]

with open("dataset/my_custom_dataset.json", "w") as f:
    json.dump(dataset, f, indent=2)
```

### Running with Custom Dataset

```bash
# Training
python -m src.train.train_perstream \
    --dataset_path "dataset/my_custom_dataset.json" \

# Evaluation
python -m src.eval.eval_passive_dataset \
    --dataset_path "dataset/my_custom_dataset.json"
```

## Configuration Parameters

### Core PerStream PMG Parameters

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `gamma_threshold` | 0.3 | 0.0-1.0 | Remember gate similarity threshold |
| `delta_dfs_threshold` | 0.3 | 0.0-1.0 | PMG retrieval activation threshold |
| `buffer_size` | 4 | 1-32 | Frames per processing batch |
| `pool_size` | (8, 8) | (2,2)-(16,16) | Adaptive pooling patches |
| `top_k` | 5 | 1-20 | Number of top memories to retrieve |
| `queue_size` | 4 | 1-64 | Short-term memory FIFO size |

### Generation Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_new_tokens` | 200 | Maximum generation length |
| `temperature` | 1.0 (passive), 0.0 (proactive) | Sampling temperature |
| `do_sample` | True/False | Enable/disable sampling |

### Memory Reduction

| Strategy | Description |
|----------|-------------|
| NSBG | Node Scan by Granularity: Process cold→hot nodes, remove fine→coarse storage |
| GSBN | Granularity Scan by Node: Process fine→coarse granularities, remove cold→hot nodes |

**Granularity Hierarchy** (finest to coarsest):
1. `v_nearest`: Nearest visual vector (~1-10 MB/node)
2. `v_mean`: Mean visual vector (~1-10 MB/node)
3. `v_M`: Caption embedding (384-dim, ~1.5 KB/node)
4. `M`: Raw text caption (~100-500 bytes/node)

## Memory Categories

The system organizes memories into 30 semantic categories:

| Category Group | Categories |
|----------------|------------|
| Personal Identity | name, identification, vehicle, contact, education |
| Basic Profile | nickname, age, physical, birth, employment |
| Health/Medical | medical, health |
| Transactions | transactions, financial, cards |
| Daily Behavior | apps, schedule, browsing, mentioned, phrases |
| Location | travel, location, places |
| Social | relationships, birthdays, interests, addresses |
| Personal Data | personal_interests, notes, media, app_data, contacts_list, chat, messages |

## Model Information

### Base Model

- **Model**: Qwen2.5-Omni-7B
- **Vision Encoder**: Spatial visual features (3584-dim)
- **Language Decoder**: Text generation with memory context
- **Projection**: 3584 → 384 dimensional alignment

## Script Descriptions

This section provides detailed descriptions of each script in the `scripts/` directory and what they accomplish.

### `generate_dataset.sh`

**Purpose**: Generates passive and proactive QA pairs from video datasets (DrivenAct and Ego4D).

**What it does**:
- Processes video datasets to extract meaningful timestamps and generate QA pairs
- For DrivenAct: Reads annotations CSV, extracts video frames at specified timestamps, and uses LLMs to generate:
  - Type 1 memories (short factual statements about habits/preferences) — generated **from the caption only**, not from raw video frames; these are randomized synthetic prior personal context
  - Type 2 memories (longer episodic narratives) — generated **from the caption only**, not from raw video frames; these are randomized synthetic prior personal context
  - Passive QA pairs (user-initiated questions)
  - Proactive QA pairs (system-initiated responses)
- For Ego4D: Processes narration annotations, selects relevant timestamps, and generates similar memory and QA pairs
- Uses parallel processing (8 workers by default) to speed up dataset generation
- Caches intermediate results to resume interrupted runs

**Key Parameters**:
- `DRIVENACT_PATH`: Path to DrivenAct dataset directory
- `EGO4D_PATH`: Path to Ego4D dataset directory
- `MODEL_ID`: LLM model for generation (default: `openai/gpt-4o-mini`)
- `VIDEO_START_BEFORE` / `VIDEO_END_AFTER`: Time window around timestamps to extract frames
- `NUM_WORKERS`: Number of parallel processes

**Output**: Generates passive and proactive datasets in separate directories for each dataset type.

---

### `perstream.sh`

**Purpose**: Runs the main PerStream inference pipeline on a video file with memory-enabled AI assistance.

**What it does**:
- Processes a video file frame-by-frame using optical flow for keyframe selection
- Extracts visual features using Qwen2.5-Omni vision encoder
- Applies the Remember Gate mechanism to filter frames based on memory category similarity
- Builds and queries the Personalized Memory Graph (PMG) for relevant memories
- Generates responses in two modes:
  - **Passive mode**: Answers user queries using retrieved memories
  - **Proactive mode**: Automatically generates responses when relevant memories are detected
- Uses a projection model to align visual embeddings with semantic memory space
- Maintains a short-term memory buffer and long-term PMG storage

**Key Parameters**:
- `VIDEO_PATH`: Path to input video file
- `MODEL_PATH`: Base model path (default: `Qwen/Qwen2.5-Omni-7B`)
- `PROJECTION_MODEL_PATH`: Path to trained projection MLP
- `GAMMA_THRESHOLD`: Remember gate similarity threshold (0.0-1.0)
- `BUFFER_SIZE`: Number of frames processed per batch
- `ENABLE_PROACTIVE`: Enable/disable proactive response generation

**Output**: Prints responses to console and can save to file. Demonstrates real-time memory-augmented video understanding.

---

### `train.sh`

**Purpose**: Fine-tunes Qwen2.5-Omni using LoRA on memory-augmented video QA pairs.

**What it does**:
- Loads pre-generated QA pairs from dataset (requires running `generate_dataset.sh` first)
- Pre-computes and caches memory embeddings using the PMG (requires `cache_memories.sh` first)
- Fine-tunes the model using LoRA (Low-Rank Adaptation) to adapt to memory-augmented responses
- Uses gradient checkpointing and mixed precision (bf16) for memory efficiency
- Trains on both passive and proactive QA pairs with memory context

**Key Parameters**:
- `MODEL_PATH`: Base model to fine-tune
- `DATA_FILE`: Path to training dataset JSON
- `VIDEO_DIR`: Directory containing video files referenced in dataset
- `CACHE_FILE`: Pre-computed memory cache (must exist)
- `NUM_EPOCHS`: Training epochs (default: 3)
- `LEARNING_RATE`: Learning rate (default: 2e-4)
- `LORA_R`, `LORA_ALPHA`, `LORA_DROPOUT`: LoRA hyperparameters

**Output**: Saves fine-tuned model checkpoints to `OUTPUT_DIR` with LoRA adapters.

---

### `proj_align.sh`

**Purpose**: Trains a projection MLP to align visual embeddings with semantic memory space.

**What it does**:
- Creates a training dataset by extracting visual features from video frames at specified timestamps
- Generates corresponding text embeddings from captions using a sentence transformer
- Trains a multi-layer perceptron (MLP) to project visual embeddings (3584-dim) to semantic space (384-dim)
- Enables efficient memory retrieval by aligning visual and textual representations
- Supports three modes: `create_dataset` (extract features only), `train` (train only), or `both` (default)

**Key Parameters**:
- `QUESTIONS_FILE`: JSON file with video_id and timestamp annotations
- `VIDEO_DIR`: Directory containing video files
- `OUTPUT_MODEL`: Path to save trained projection model
- `POOL_SIZE`: Spatial pooling size for visual features (default: 4x4)
- `SENTENCE_MODEL`: Sentence transformer model (default: `all-MiniLM-L6-v2`)
- `NUM_EPOCHS`: Training epochs (default: 50)
- `LEARNING_RATE`: Learning rate (default: 1e-3)

**Output**: Saves trained projection MLP model (typically `projection_mlp.pt`) used for memory retrieval.

---

### `eval.sh`

**Purpose**: Evaluates model performance on passive and proactive QA tasks.

**What it does**:
- Runs inference on test dataset for both passive and proactive modes
- For each test sample:
  - Loads video and extracts frames at specified timestamp
  - Retrieves relevant memories from PMG
  - Generates model predictions using memory-augmented context
- Saves predictions to JSON files for later scoring
- Uses the same model configuration as training (supports fine-tuned models)

**Key Parameters**:
- `MODEL_PATH`: Model to evaluate (can be fine-tuned model)
- `VIDEO_DIR`: Directory containing test videos
- `GT_FILE`: Ground truth dataset JSON file
- `OUTPUT_BASE_DIR`: Base directory for saving results
- `MAX_FRAMES`: Maximum frames to extract per video (default: 8)

**Output**: Saves predictions to `{OUTPUT_BASE_DIR}/drivenact_passive/perstream/pred.json` and `{OUTPUT_BASE_DIR}/drivenact_proactive/perstream/pred.json`.

---

### `score.sh`

**Purpose**: Scores evaluation results using LLM-based judges.

**What it does**:
- Loads predictions from `eval.sh` output
- Uses GPT-4 or similar LLM as a judge to compare predictions with ground truth
- For passive mode: Evaluates answer correctness, relevance, and completeness
- For proactive mode: Evaluates response appropriateness, urgency, and relevance
- Generates detailed scores and saves results to JSON
- Provides per-task and aggregate statistics

**Key Parameters**:
- `OUTPUT_BASE_DIR`: Base directory containing evaluation results
- `NUM_TASKS`: Number of parallel scoring tasks (default: 16)

**Output**: Saves scored results to `{OUTPUT_BASE_DIR}/drivenact_passive/perstream/results.json` and `{OUTPUT_BASE_DIR}/drivenact_proactive/perstream/results.json`.

---

### `reduction_eval.sh`

**Purpose**: Evaluates memory reduction strategies (NSBG/GSBN) under memory constraints.

**What it does**:
- Simulates memory-constrained scenarios with limited VRAM
- Tests two reduction strategies:
  - **NSBG (Node Scan by Granularity)**: Processes nodes from cold→hot, removes fine→coarse granularities
  - **GSBN (Granularity Scan by Node)**: Processes granularities from fine→coarse, removes cold→hot nodes
- Evaluates both passive and proactive modes under reduction
- Measures performance degradation vs. memory savings
- Uses period-based reduction where memory is reduced at regular intervals

**Key Parameters**:
- `MODEL_PATH`: Model to evaluate
- `GT_FILE`: Ground truth dataset (typically reduction subset)
- `NUM_PERIODS`: Number of reduction periods (default: 4)
- `DEVICE_RAM_GB`: Simulated device RAM in GB (default: 7)
- `R_PRIME_GB`: Target memory budget in GB (default: 0.5)
- `VIDEO_START_BEFORE` / `VIDEO_END_AFTER`: Video extraction window

**Output**: Saves reduction evaluation results showing memory usage and performance metrics for each strategy.