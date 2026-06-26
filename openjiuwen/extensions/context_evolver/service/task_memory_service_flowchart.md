# TaskMemoryService — Algorithm Flowchart

Illustrates the **retrieve** and **summary** operation pipelines for all algorithms
supported by `TaskMemoryService` in `task_memory_service.py`.

**Supported algorithms:** ACE · ReasoningBank · ReMe · Cognition · RefCon · DivCon

**Conventions used in the diagrams:**

| Convention | Meaning |
|---|---|
| Solid arrow `→` | Sequential, always-executed step |
| Dashed arrow `⤏` | Conditional step (appended only when `persist_type` is configured) |
| `matts` notation | Execution mode : `none` = serial op, `parallel` = parallel op |
| `(fixed)` notation | Hard-coded parameter, not exposed through config |

---

## 1. Retrieve Flow

Triggered by `TaskMemoryService.retrieve(user_id, query)`.

```mermaid
flowchart TD
    classDef io      fill:#2C3E50,color:#fff,stroke:#2C3E50
    classDef ace     fill:#3498DB,color:#fff,stroke:#2980B9
    classDef rb      fill:#9B59B6,color:#fff,stroke:#8E44AD
    classDef reme    fill:#27AE60,color:#fff,stroke:#1E8449
    classDef cog     fill:#E67E22,color:#fff,stroke:#D35400
    classDef refcon  fill:#E74C3C,color:#fff,stroke:#C0392B
    classDef decision fill:#F39C12,color:#fff,stroke:#E67E22

    R_IN(["retrieve(user_id, query)"]):::io
    R_IN --> R_ALG{"retrieval_algorithm"}:::decision
    R_OUT(["RetrieveResponse"]):::io

    %% ── ACE ──────────────────────────────────────────────────────────────────
    R_ALG -->|ACE| ACE_R1["RecallMemoryOp<br/>retrieve all memories"]:::ace
    ACE_R1 --> R_OUT

    %% ── ReasoningBank ────────────────────────────────────────────────────────
    R_ALG -->|ReasoningBank| RB_R1["RecallMemoryOp<br/>top_k = 1"]:::rb
    RB_R1 --> R_OUT

    %% ── ReMe ─────────────────────────────────────────────────────────────────
    R_ALG -->|ReMe| REME_R1["RecallMemoryOp<br/>topk = 10 (config)"]:::reme
    REME_R1 --> REME_R2["RerankMemoryOp<br/>llm_rerank · topk = 5 (config)"]:::reme
    REME_R2 --> REME_R3["RewriteMemoryOp<br/>llm_rewrite"]:::reme
    REME_R3 --> R_OUT

    %% ── Cognition ────────────────────────────────────────────────────────────
    R_ALG -->|Cognition| COG_R1["LoadSchemaOp<br/>rebuild dynamic attribute schema<br/>from all stored CognitionMemory nodes"]:::cog
    COG_R1 --> COG_R2["ClassifyQueryOp<br/>LLM classifies query<br/>into structured attributes"]:::cog
    COG_R2 --> COG_R3["RecallCognitionOp<br/>strict → relaxed structured match<br/>+ semantic 'other' match<br/>top_k = 10 (config)"]:::cog
    COG_R3 --> COG_R4["RerankCognitionOp<br/>LLM reranks by relevance<br/>top_k = 5 (config)"]:::cog
    COG_R4 --> COG_R5["RewriteMemoryOp<br/>format CognitionRetrievedMemory<br/>build catalog memory_string"]:::cog
    COG_R5 --> R_OUT

    %% ── RefCon / DivCon ──────────────────────────────────────────────────────
    R_ALG -->|"RefCon / DivCon"| RD_R1["RecallMemoryOp<br/>topk = 10 (fixed)"]:::refcon
    RD_R1 --> RD_R2["RerankMemoryOp<br/>llm_rerank=True · topk=5 (fixed)"]:::refcon
    RD_R2 --> RD_R3["RewriteMemoryOp<br/>llm_rewrite=True (fixed)"]:::refcon
    RD_R3 --> R_OUT
```

---

## 2. Summary Flow

Triggered by `TaskMemoryService.summarize(user_id, matts, query, trajectories)`.

Dashed arrows `⤏` indicate the optional `PersistMemoryOp` appended to the pipeline
only when `persist_type` is set (`"json"`, `"milvus"`, or `"auto"`).

```mermaid
flowchart TD
    classDef io      fill:#2C3E50,color:#fff,stroke:#2C3E50
    classDef ace     fill:#3498DB,color:#fff,stroke:#2980B9
    classDef rb      fill:#9B59B6,color:#fff,stroke:#8E44AD
    classDef reme    fill:#27AE60,color:#fff,stroke:#1E8449
    classDef cog     fill:#E67E22,color:#fff,stroke:#D35400
    classDef refcon  fill:#E74C3C,color:#fff,stroke:#C0392B
    classDef decision fill:#F39C12,color:#fff,stroke:#E67E22
    classDef persist fill:#95A5A6,color:#fff,stroke:#7F8C8D

    S_IN(["summarize(user_id, matts, query, trajectories)"]):::io
    S_IN --> S_ALG{"summary_algorithm"}:::decision
    S_OUT(["SummarizeResponse"]):::io

    %% ── ACE ──────────────────────────────────────────────────────────────────
    S_ALG -->|ACE| ACE_S1["LoadPlaybookOp<br/>load existing bullet-point playbook"]:::ace
    ACE_S1 --> ACE_S2["ReflectOp / ParallelReflectOp<br/>matts controls serial vs parallel<br/>use_ground_truth = True/False"]:::ace
    ACE_S2 --> ACE_S3["CurateOp / ParallelCurateOp<br/>matts controls serial vs parallel"]:::ace
    ACE_S3 --> ACE_S4["ApplyDeltaOp<br/>max_bullets = 50"]:::ace
    ACE_S4 -.->|"persist_type set"| ACE_P["ACE PersistMemoryOp<br/>save playbook to JSON / Milvus"]:::persist
    ACE_S4 --> S_OUT
    ACE_P --> S_OUT

    %% ── ReasoningBank ────────────────────────────────────────────────────────
    S_ALG -->|ReasoningBank| RB_S1["SummarizeMemoryOp / ParallelOp<br/>matts controls serial vs parallel<br/>LLM generates memory from trajectory"]:::rb
    RB_S1 --> RB_S2["UpdateVectorStoreOp<br/>embed query · upsert VectorNode"]:::rb
    RB_S2 -.->|"persist_type set"| RB_P["RB PersistMemoryOp<br/>save to JSON / Milvus"]:::persist
    RB_S2 --> S_OUT
    RB_P --> S_OUT

    %% ── ReMe ─────────────────────────────────────────────────────────────────
    S_ALG -->|ReMe| REME_S1["TrajectoryPreprocessOp<br/>rank and score all trajectories"]:::reme
    REME_S1 --> REME_S2["SuccessExtractionOp (best traj)<br/>FailureExtractionOp (worst traj)<br/>ComparativeExtractionOp (contrastive)<br/>use_extraction flags from config"]:::reme
    REME_S2 --> REME_S3["MemoryValidationOp<br/>use_validation = True/False"]:::reme
    REME_S3 --> REME_S4["MemoryDeduplicationOp<br/>use_dedup = True/False"]:::reme
    REME_S4 --> REME_S5["UpdateVectorStoreOp<br/>embed when_to_use · upsert VectorNode"]:::reme
    REME_S5 -.->|"persist_type set"| REME_P["ReMe PersistMemoryOp<br/>save to JSON / Milvus"]:::persist
    REME_S5 --> S_OUT
    REME_P --> S_OUT

    %% ── Cognition ────────────────────────────────────────────────────────────
    S_ALG -->|Cognition| COG_S1["SolutionClassifyOp<br/>select best trajectory by score<br/>LLM reclassifies solution attributes"]:::cog
    COG_S1 --> COG_S2["GenerateExperienceOp<br/>LLM extracts description<br/>and actionable experience insights"]:::cog
    COG_S2 --> COG_S3["UpdateVectorStoreOp<br/>create CognitionMemory node<br/>embed query+description · upsert VectorNode"]:::cog
    COG_S3 -.->|"persist_type set"| COG_P["Cognition PersistMemoryOp<br/>save to JSON / Milvus"]:::persist
    COG_S3 --> S_OUT
    COG_P --> S_OUT

    %% ── RefCon / DivCon ──────────────────────────────────────────────────────
    S_ALG -->|"RefCon / DivCon"| RD_S1["TrajectoryPreprocessOp<br/>rank and score all trajectories"]:::refcon
    RD_S1 --> RD_S2["ComparativeAllExtractionOp<br/>comparative extraction across<br/>ALL trajectories at once"]:::refcon
    RD_S2 --> RD_S3["MemoryValidationOp<br/>use_validation=False (fixed)"]:::refcon
    RD_S3 --> RD_S4["MemoryDeduplicationOp<br/>use_deduplication=True (fixed)"]:::refcon
    RD_S4 --> RD_S5["UpdateVectorStoreOp<br/>embed when_to_use · upsert VectorNode"]:::refcon
    RD_S5 -.->|"persist_type set"| RD_P["ReMe PersistMemoryOp<br/>save to JSON / Milvus"]:::persist
    RD_S5 --> S_OUT
    RD_P --> S_OUT
```

---

## 3. Algorithm Comparison

| Algorithm | Retrieve Pipeline | Retrieve Description | Summary Pipeline | Summary Description | Key Characteristic |
|---|---|---|---|---|---|
| **ACE** | RecallMemoryOp | Return all existing memories in the playbook. | LoadPlaybook → Reflect → Curate → ApplyDelta | Loads the existing playbook, reflects on the trajectory to generate delta bullets, curates them for quality, and applies the delta to keep the playbook within the size limit. | Bullet-point playbook with memory section, content, and helpful/harmful counter |
| **ReasoningBank** | RecallMemoryOp | Embeds the query and retrieves the top-k most semantically similar memory entries from the vector store. | SummarizeMemory → UpdateVectorStore | LLM summarizes the trajectory into a structured memory (title, description, content), then embeds by query and upserts it into the vector store. | Title/description/content memory structure |
| **ReMe** | Recall → Rerank → Rewrite | Recalls candidates by semantic search on when to use index, reranks them by LLM relevance scoring, and rewrites the memory string to fit the current query context. | Preprocess → Extract(3 modes) → Validate → Dedup → Update | Ranks trajectories by score, extracts insights from the best, worst, and contrastive pairs (configurable), validates memory quality, deduplicates, and upserts into the vector store. | Multi-mode extraction from best/worst/contrast |
| **Cognition** | LoadSchema → Classify → Recall → Rerank → Rewrite | Rebuilds the dynamic attribute schema from stored memories, classifies the query via LLM, recalls via strict-then-relaxed structured matching plus semantic "other" matching, reranks with LLM, and formats the output. | SolutionClassify → GenerateExperience → Update | Selects the best trajectory by score, uses LLM to reclassify solution attributes and extract a structured description with actionable experience insights, then stores the resulting CognitionMemory node. | Attribute-based schema + LLM experience extraction |
| **RefCon** | Recall → Rerank → Rewrite (fixed params) | Same three-step ReMe pipeline with hyperparameters fixed at optimized values (topk=10 recall, topk=5 rerank, llm_rerank=True, llm_rewrite=True). | Preprocess → ComparativeAll → Validate(off) → Dedup → Update | Applies comparative extraction across all trajectories simultaneously; validation is disabled and deduplication is always enabled, all with fixed hyperparameters. | All-trajectory comparative, fixed hyperparams |
| **DivCon** | Recall → Rerank → Rewrite (fixed params) | Identical retrieve pipeline to RefCon; fixed hyperparameters are tuned for diverse and contrastive trajectory sets. | Preprocess → ComparativeAll → Validate(off) → Dedup → Update | Same pipeline as RefCon; intended for trajectory sets that are diverse or contrastive in nature rather than reference-aligned. | Same pipeline as RefCon, diverse trajectory intent |
