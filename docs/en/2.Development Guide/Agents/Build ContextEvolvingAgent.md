# Context Evolver

Context Evolver is a memory management extension for openjiuwen agent-core, providing intelligent agents with the ability to learn from past interactions and retrieve relevant knowledge. It implements three advanced memory algorithms: **ACE** (Agentic Context Engineering), **RB** (Reasoning Bank), and **ReMe** (Remember Me, Refine Me).

## Overview

This extension enables agents to learn from past interactions and retrieve relevant knowledge to enhance future responses. It implements three memory algorithms:

- **ACE (Agentic Context Engineering)**: Uses `content` and `section` fields for structured memory storage, organized with playbooks
- **RB (Reasoning Bank)**: Uses `title`, `description`, and `content` fields for knowledge-oriented memory with rich descriptions
- **ReMe (Remember Me, Refine Me)**: Uses `when_to_use` and `content` fields, combining vector retrieval with LLM-based reranking and rewriting for intelligent memory management

## Quick Start

For a hands-on introduction, run the quickstart example:

```bash
cd __CLONE_DIR__\agent-core
python -m examples.context_evolver.quickstart
```

The `quickstart.py` script demonstrates:
1. **Verifying Configuration**: Check API keys and model settings
2. **Creating Memory Service**: Initialize TaskMemoryService with your chosen algorithm
3. **Adding Memories**: Store knowledge base items in algorithm-specific formats
4. **Creating Memory-Augmented Agent**: Set up ContextEvolvingReActAgent with automatic memory injection
5. **Querying with Memory Retrieval**: Invoke agent with automatic memory augmentation
6. **Learning from Interactions**: Summarize trajectories to extract and store new memories
7. **Advanced Features**: Demonstrate HotpotQA multi-hop reasoning with memory-aware parallel processing (MATTS)

### Prerequisites

1. Install Docker (see [Infrastructure Setup](#infrastructure-setup) below)
2. Start a Milvus instance (see [Infrastructure Setup](#infrastructure-setup) below)
3. Create and configure API credentials in the `.env` file
4. Configure algorithm settings in the `config.yaml` file

## Infrastructure Setup

### 1. Install Docker

Docker is required to run Milvus as a containerised service.

#### Linux (Ubuntu / Debian)

```bash
# Remove any old Docker packages
sudo apt-get remove -y docker docker-engine docker.io containerd runc

# Install dependencies
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Add Docker's official GPG key and repository
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine and the Compose plugin
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Allow running Docker without sudo (log out and back in after this)
sudo usermod -aG docker $USER

# Verify installation
docker --version
docker compose version
```

#### Windows

1. Download **Docker Desktop for Windows** from [https://docs.docker.com/desktop/install/windows-install/](https://docs.docker.com/desktop/install/windows-install/).
2. Run the installer (`Docker Desktop Installer.exe`) and follow the wizard.
3. Ensure **WSL 2 backend** is selected (recommended) or enable Hyper-V when prompted.
4. After installation, start Docker Desktop and verify from PowerShell:

```powershell
docker --version
docker compose version 
```

#### macOS

1. Download **Docker Desktop for Mac** from [https://docs.docker.com/desktop/install/mac-install/](https://docs.docker.com/desktop/install/mac-install/).
2. Open the `.dmg`, drag Docker to `Applications`, and launch it.
3. Verify in a terminal:

```bash
docker --version
docker compose version
```

---

### 2. Install Milvus with Docker

Milvus Standalone is the recommended single-node deployment for development and small-scale production. It uses Docker Compose to manage three services: **Milvus**, **etcd**, and **MinIO**.

#### Step 1 — Download the official Compose file

**Linux / macOS**

```bash
wget https://github.com/milvus-io/milvus/releases/download/v2.6.2/milvus-standalone-docker-compose.yml \
    -O docker-compose.yml
```

**Windows (PowerShell)**

```powershell
Invoke-WebRequest `
    -URI "https://github.com/milvus-io/milvus/releases/download/v2.6.2/milvus-standalone-docker-compose.yml" `
    -OutFile "docker-compose.yml"
```

#### Step 2 — Start Milvus

```bash
docker compose up -d
```

Confirm all three containers are running:

```bash
docker compose ps
```

Expected output:

```
NAME                IMAGE                               STATUS
milvus-standalone   milvusdb/milvus:v2.6.2             Up (healthy)
milvus-etcd         quay.io/coreos/etcd:v3.5.5         Up (healthy)
milvus-minio        minio/minio:RELEASE.2023-03-13...  Up (healthy)
```

Milvus gRPC is now available at `localhost:19530`.

#### Step 3 — Verify connectivity (optional)

```bash
# Confirm port 19530 is listening
docker exec milvus-standalone \
    python3 -c "from pymilvus import connections; connections.connect(); print('OK')"
```

#### Stopping and restarting Milvus

```bash
# Stop (data is preserved in Docker volumes)
docker compose down

# Start again
docker compose up -d

# Remove containers AND all data
docker compose down --volumes --remove-orphans
```

#### Environment variables for the connector

Add Milvus connection settings to your `.env` file (created in the next section):

```env
# Milvus Vector Database
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_COLLECTION=vector_nodes
```

---

## Configuration

Configuration is loaded from two files:

### 1. `.env` File (Credentials)

Create a `.env` file in `openjiuwen\extensions\context_evolver\` with sensitive settings:

```env
# API Configuration
API_KEY=your-openai-api-key
API_BASE=https://api.openai.com/v1

# Model Configuration
MODEL_NAME=gpt-5.2
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536

# Optional: LLM Parameters
LLM_TEMPERATURE=0.7
LLM_SEED=42
LLM_SSL_VERIFY=false
```

### 2. `config.yaml` File (Algorithm Settings)

Create a `config.yaml` file in `openjiuwen\extensions\context_evolver\` with algorithm settings:

```yaml
# Algorithm Selection
# Options: ACE (Agentic Context Engineering) or RB/REASONINGBANK (ReasoningBank) or REME
RETRIEVAL_ALGO: "REME" # ACE/RB/REME
SUMMARY_ALGO: "REME" # ACE/RB/REME
MANAGEMENT_ALGO: "REME" # ACE/RB/REME

USE_GOLDLABEL: true 

# MaTTS Configuration (Memory-aware Test-Time Scaling)
# Only applicable when ALGO=RB
MATTS_DEFAULT_K: 3 # Default scaling factor
MATTS_DEFAULT_TEMPERATURE: 0.9 # Default temperature for parallel scaling
MATTS_DEFAULT_MODE: "parallel"  # Options: none/parallel/sequential/combined

# ACE
USE_GROUNDTRUTH: true
MAX_PLAYBOOK_SIZE: 50

# RB
TOPK_QUERY: 1

# REME
# Retrieval
TOPK_RETRIEVAL: 10
LLM_RERANK: true
TOPK_RERANK: 5
LLM_REWRITE: true

# Extraction
MEMORY_VALIDATION: true
EXTRACT_BEST_TRAJ: true
EXTRACT_WORST_TRAJ: true
EXTRACT_COMPARATIVE_TRAJ: true

# Management (Pending Implementation)
MEMORY_DEDUPLICATION: true
MEMORY_UPDATE: true
DELETE_USAGE_THRESHOLD: 5
DELETE_UTILITY_THRESHOLD: 0.5

# Ours (Pending Implementation)
COMBINED_MATTS_PROMPT: "diversity" # Options : diversity/refine

# Logging
LOG_LEVEL: "INFO"
```

## Key Features

- **Multi-Algorithm Support**: Choose between ACE, ReasoningBank, and ReMe algorithms based on your use case
- **Semantic Memory Retrieval**: Retrieve relevant memories based on semantic similarity and LLM-based reranking
- **Trajectory Summarization**: Extract learnings from agent interactions and automatically store as new memories
- **MATTS Scaling**: Memory-aware Test-Time Scaling for parallel/sequential processing of multi-hop queries
- **Per-User Memory Management**: Isolated memory collections per user with add/clear/retrieve operations
- **Automatic Memory Injection**: Augment agent prompts with retrieved memories without code changes
- **Vector Store Integration**: Built-in vector store for semantic similarity search
- **File Persistence**: Save/load memories to/from JSON files for persistent storage

## Architecture

```
openjiuwen/extensions/context_evolver/
├── context_evolving_react_agent.py     # Memory-augmented ReActAgent subclass
├── __init__.py                         # Public API exports
├── config.yaml                         # Default configuration file
├── service/
│   └── task_memory_service.py # Core memory service with retrieve/summarize
├── retrieve/task/             # Retrieval algorithms (ACE, RB, ReMe)
├── summary/task/              # Summarization algorithms (ACE, RB, ReMe)
├── schema/                    # Data models (memory, trajectory, io_schema)
├── tool/                      # Tools (e.g., wikipedia_tool)
└── core/                      # Core utilities (context, ops, vector store, file_connector)

# Quick start example
examples/context_evolver/
└── quickstart.py              # Quick start example for new users

# Tests are located at:
tests/unit_tests/extensions/context_evolver/
├── test_retrieve_flow.py      # Retrieval flow tests
├── test_summary_flow.py       # Summary flow tests
├── test_quickstart.py         # quickstart tests
└── test_file_connector.py     # File connector tests

```

## Components

### ContextEvolvingReActAgent

A `ReActAgent` subclass that automatically retrieves relevant memories before processing queries. (The sample codes can be found in examples/context_evolver/quickstart.py)

```python
from openjiuwen.extensions.context_evolver.service.task_memory_service import TaskMemoryService
from openjiuwen.extensions.context_evolver.context_evolving_react_agent import ContextEvolvingReActAgent, create_memory_agent_config
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.extensions.context_evolver.service.task_memory_service import AddMemoryRequest

# Step 1: Create memory service (shared instance)
memory_service = TaskMemoryService()

# Step 2: Add memories (use algorithm-specific parameters)
await memory_service.add_memory(
    user_id="test_user",
    request=AddMemoryRequest(
        when_to_use="When asked about database optimization",
        content="Always use indexes on frequently queried columns. Consider using connection pooling for high-traffic applications.",
    ),
)

# Step 3: Create agent card
agent_card = AgentCard(
    id="memory-react-agent",
    name="memory-react-agent",
    description="ReActAgent with automatic memory injection"
)

# Step 4: Create ContextEvolvingReActAgent instance
agent = ContextEvolvingReActAgent(
    card=agent_card,
    user_id="test_user",
    memory_service=memory_service,
    inject_memories_in_context=True,  # Automatically add memories to prompts
)

# Step 5: Configure using helper function
agent_config = create_memory_agent_config(
    model_provider="OpenAI",
    api_key="your-api-key",
    api_base="https://api.openai.com/v1",
    model_name="gpt-5.2",
    system_prompt="You are a helpful software engineering assistant. "
                 "Use any provided memory context to enhance your answers.",
)
agent.configure(agent_config)

# Step 6: Invoke with automatic memory retrieval
result = await agent.invoke({
    "query": "How should I optimize my database queries?",
})
output = result.get("output", "No output")
memories_used = result.get("memories_used", 0)

print("\n" + "=" * 60)
print(f"   Query: 'How should I optimize my database queries?'")
print(f"   Memories used: {memories_used}")
# Handle Unicode encoding for Windows console
safe_output = output.encode('ascii', 'replace').decode('ascii')
print(f"   Response received: {safe_output}")


# Validate result
test_passed = True
if not output or output == "No output":
    print("\n   [FAIL] No output received")
    test_passed = False
else:
    print("\n   [PASS] Response received")
print("=" * 60)

```

#### Key Methods

| Method | Description |
|--------|-------------|
| `invoke(inputs, session)` | Invoke agent with automatic memory retrieval and injection |


### TaskMemoryService

Core service handling memory operations. Uses openjiuwen core libraries:

- `openjiuwen.core.foundation.llm.model_clients.openai_model_client` for LLM calls
- `openjiuwen.core.retrieval.embedding.api_embedding` for embeddings

```python
from service.task_memory_service import TaskMemoryService, AddMemoryRequest

# Using default config.yaml
service = TaskMemoryService(
    llm_model="gpt-5.2",
    embedding_model="text-embedding-3-small",
    api_key="your-api-key",
    retrieval_algo="ReMe",
    summary_algo="ReMe",
)

# Using custom configuration file path
service = TaskMemoryService(
    llm_model="gpt-5.2",
    embedding_model="text-embedding-3-small",
    api_key="your-api-key",
    retrieval_algo="ReMe",
    summary_algo="ReMe",
    config_path="/path/to/custom/config.yaml",  # Optional: load from custom config file
)

# Retrieve
result = await service.retrieve(user_id, query)

# Summarize
result = await service.summarize(user_id, matts, query, trajectories, label)

# Add memory
result = await service.add_memory(
    user_id,
    request=AddMemoryRequest(
        content="Memory content",
        # algorithm-specific fields:
        # - ReMe: when_to_use
        # - ReasoningBank: title, description
        # - ACE: section
    )
)
```

## Memory Algorithms

### ACE (Agentic Context Engineering)
- Stores memories with `content` and `section` fields
- Best for: Action-oriented memories with clear usage conditions
- Playbook-based organization

### RB (Reasoning Bank)
- Stores memories with `title`, `description`, and `content` fields
- Best for: Knowledge-oriented memories with rich descriptions
- Supports source attribution

### ReMe (Remember Me, Refine Me)
- Combines vector retrieval with LLM-based reranking and rewriting
- Supports multi-stage retrieval pipeline
- Best for: Complex queries requiring semantic understanding

## Running Tests

Tests are located in `tests/unit_tests/extensions/context_evolver/`. Navigate to the project root directory:

```bash
cd __CLONE_DIR__\agent-core
```

Then run tests using pytest:

```bash

# Run specific test files
python -m pytest tests/unit_tests/extensions/context_evolver/test_retrieve_flow.py -v
python -m pytest tests/unit_tests/extensions/context_evolver/test_summary_flow.py -v
python -m pytest tests/unit_tests/extensions/context_evolver/test_file_connector.py -v
python -m pytest tests/unit_tests/extensions/context_evolver/test_quickstart.py -v
```


## Integration with openjiuwen

This extension integrates with the openjiuwen agent-core framework:

1. **As Agent Subclass**: Use `ContextEvolvingReActAgent` for automatic memory injection
2. **As Service**: Use `TaskMemoryService` directly in custom agents
3. **As CLI Tool**: Use `main.py` for standalone memory operations

## License

Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
