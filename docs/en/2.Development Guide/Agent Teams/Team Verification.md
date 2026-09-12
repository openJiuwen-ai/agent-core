# Team Verification Layer

> **Goal:** Automatically review teammate task outputs for quality, correctness, and consistency before the Leader consolidates results. Inspired by Claude Code's verification subagents.
> 
> **Scope:** Agent Team mode (Cluster mode) only.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Quality Dimensions](#quality-dimensions)
- [Verification Results](#verification-results)
- [Memory & Trends](#memory--trends)
- [Events & Frontend Integration](#events--frontend-integration)
- [Architecture](#architecture)
- [FAQ](#faq)

---

## Overview

The **Team Verification Layer** is a quality assurance system that runs automatically when teammates complete tasks in Agent Team mode. It acts as an independent reviewer — assessing each task output across six quality dimensions and storing the results for accountability and trend analysis.

**Why it matters:**

- **Catches errors early** — before the Leader merges potentially flawed outputs
- **Enforces consistency** — ensures teammate work aligns with team context and requirements
- **Builds accountability** — verification history persists in `TEAM_MEMORY.md`
- **Enables data-driven improvement** — trend analysis reveals weak dimensions over time

**Key characteristics:**

| Property | Value |
|----------|-------|
| **Trigger** | Automatic on `TASK_COMPLETED` events |
| **Blocking** | No — runs asynchronously (fire-and-forget) |
| **Scope** | Per-task, per-teammate |
| **Storage** | `TEAM_MEMORY.md` under team workspace |
| **Events** | `team.verification.completed`, `team.verification.error` |

---

## How It Works

### High-Level Flow

```text
User states goal → Leader forms team → Teammates claim & execute tasks
→ Teammate marks task complete → Verification Layer triggers (async)
  → VerificationReviewer assesses output quality
  → Result stored in TEAM_MEMORY.md
  → Event emitted to frontend
→ Leader consolidates (with verification data available)
```

### Step-by-Step

1. **Task Completion** — A teammate finishes a task and reports results to the Leader.
2. **Verification Trigger** — The `TeamMonitorHandler` detects the `TASK_COMPLETED` event and spawns verification asynchronously via `asyncio.create_task()`.
3. **Quality Assessment** — The `VerificationReviewer` sends the task output to a configured model with a structured prompt assessing six quality dimensions.
4. **Result Storage** — The `VerificationMemory` appends the result to `TEAM_MEMORY.md` under a "Verification History" section.
5. **Event Emission** — A `team.verification.completed` event is emitted, visible in the frontend and logs.
6. **Leader Consolidation** — The Leader can query verification trends when making consolidation decisions.

### Skip Patterns

Certain low-value tasks are automatically excluded from verification to reduce noise:

| Pattern | Example Tasks |
|---------|---------------|
| `heartbeat` | System health checks |
| `ping` | Connectivity probes |
| `status` | Status polling tasks |

Configure additional skip patterns in `team.verification.skip_patterns`.

---

## Configuration

Add the following to your `config.yaml` under the `team` section:

```yaml
team:
  verification:
    enabled: true              # Master toggle (default: true)
    block_on_fail: false       # Block leader consolidation on FAIL (default: false)
    auto_rework: false         # Auto-create rework tasks (default: false)
    pass_threshold: 70         # Minimum score for PASS status (default: 70)
    rework_threshold: 40       # Below this = FAIL; between = NEEDS_REWORK (default: 40)
    skip_patterns:             # Task title patterns to skip (case-insensitive)
      - "heartbeat"
      - "ping"
      - "status"
```

### Configuration Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `true` | Master toggle. When `false`, the verification rail is not mounted. |
| `block_on_fail` | boolean | `false` | If `true`, prevents Leader consolidation when verification returns FAIL. |
| `auto_rework` | boolean | `false` | If `true`, automatically creates rework tasks for NEEDS_REWORK results. |
| `pass_threshold` | integer | `70` | Score ≥ this value → PASS status. |
| `rework_threshold` | integer | `40` | Score < this value → FAIL. Between rework and pass → NEEDS_REWORK. |
| `skip_patterns` | list[string] | `["heartbeat", "ping"]` | Task title substrings that skip verification. |

### Model Client

The verification layer uses the team's default model client. If no model client is configured, it falls back to **mock mode** — returning a default PASS result (score 75) so the system works out-of-the-box.

To use a dedicated (cheaper/faster) model for verification, configure a model alias and pass it to the `VerificationReviewer`.

---

## Quality Dimensions

Each task output is assessed across six dimensions:

| Dimension | Weight | What It Checks |
|-----------|--------|----------------|
| **Correctness** | 25% | Factual accuracy, technical validity, no hallucinations |
| **Completeness** | 20% | Requirements coverage, nothing missing |
| **Consistency** | 20% | Alignment with team context, prior decisions, and constraints |
| **Clarity** | 15% | Structure, readability, logical flow |
| **Security** | 10% | Risk avoidance, no unsafe patterns |
| **Performance** | 10% | Efficiency, no unnecessary overhead |

### Scoring

Each dimension receives a score from 0–100. The overall score is a weighted average:

```
overall_score = correctness*0.25 + completeness*0.20 + consistency*0.20
              + clarity*0.15 + security*0.10 + performance*0.10
```

### Status Determination

The model returns a suggested status, but the rail enforces threshold rules:

| Overall Score | Final Status | Meaning |
|---------------|--------------|---------|
| ≥ 70 | **PASS** | Output meets quality standards |
| 40 – 69 | **NEEDS_REWORK** | Output has issues; should be revised |
| < 40 | **FAIL** | Output is unacceptable |

---

## Verification Results

### Result Structure

```python
class VerificationResult:
    task_id: str           # Unique task identifier
    task_title: str        # Human-readable task name
    agent_name: str        # Teammate who produced the output
    status: str            # PASS | NEEDS_REWORK | FAIL
    overall_score: float   # 0–100 weighted average
    dimensions: list       # Per-dimension scores and feedback
    summary: str           # Human-readable assessment summary
    suggestions: list      # Actionable improvement suggestions
    timestamp: str         # ISO 8601 timestamp
```

### Example Result

```json
{
  "task_id": "task_001",
  "task_title": "Research competitor pricing models",
  "agent_name": "research_agent",
  "status": "PASS",
  "overall_score": 82,
  "dimensions": [
    {"name": "correctness", "score": 90, "feedback": "Data sources are credible and cited."},
    {"name": "completeness", "score": 75, "feedback": "Missing enterprise-tier pricing for Competitor C."},
    {"name": "consistency", "score": 85, "feedback": "Aligns with team's research framework."},
    {"name": "clarity", "score": 80, "feedback": "Well-structured; tables help readability."},
    {"name": "security", "score": 95, "feedback": "No sensitive data exposed."},
    {"name": "performance", "score": 70, "feedback": "Could be more concise."}
  ],
  "summary": "Solid research output. Minor gaps in enterprise pricing coverage.",
  "suggestions": [
    "Add enterprise-tier pricing for Competitor C.",
    "Condense the executive summary to 3 bullet points."
  ],
  "timestamp": "2026-07-11T04:15:00+08:00"
}
```

---

## Memory & Trends

### Storage Location

Verification results are persisted to:

```
team-workspace/TEAM_MEMORY.md
```

Under a dedicated "Verification History" section:

```markdown
## Verification History

### 2026-07-11

#### research_agent — task_001 (Research competitor pricing models)
- **Status:** PASS
- **Score:** 82/100
- **Summary:** Solid research output. Minor gaps in enterprise pricing coverage.
- **Weak Dimensions:** completeness (75), performance (70)
- **Suggestions:**
  1. Add enterprise-tier pricing for Competitor C.
  2. Condense the executive summary to 3 bullet points.
```

### Trend Analysis

The Leader (or any agent with access) can query quality trends:

| Metric | Description |
|--------|-------------|
| **Pass Rate** | Percentage of tasks that passed verification |
| **Average Score** | Mean overall score across all verifications |
| **Weak Dimensions** | Dimensions that most frequently score below threshold |
| **Agent Breakdown** | Per-agent pass rate and average score |

Example trend query result:

```text
Verification Trends (last 20 tasks):
- Pass rate: 75% (15/20)
- Average score: 78.5
- Most common weak dimension: completeness
- Agent with highest pass rate: writing_agent (92%)
- Agent with lowest pass rate: research_agent (60%)
```

---

## Events & Frontend Integration

### Event Types

| Event | When | Payload |
|-------|------|---------|
| `team.verification.completed` | Verification finishes successfully | Full `VerificationResult` JSON |
| `team.verification.error` | Verification fails (model error, parse error) | Error message and task ID |

### Frontend Display

In the web UI, verification results appear in:

- **Task detail panel** — Score badge and dimension breakdown
- **Team activity feed** — Verification events alongside task events
- **Quality dashboard** (future) — Aggregate trends and agent leaderboards

### Event Category

Verification events map to the `verification` category in the frontend event display system.

---

## Architecture

### Component Diagram

```text
┌─────────────────────────────────────────────────────────────┐
│                    Agent Team Runtime                        │
│                                                              │
│  ┌──────────────┐    ┌──────────────────┐                   │
│  │   Teammate   │───▶│ TeamMonitorHandler │                  │
│  │  completes   │    │  (TASK_COMPLETED)  │                  │
│  │    task      │    └────────┬───────────┘                  │
│  └──────────────┘             │                              │
│                               ▼                              │
│                    ┌─────────────────────┐                   │
│                    │ TeamVerificationRail │                  │
│                    │   (DeepAgentRail)    │                  │
│                    └──────────┬───────────┘                   │
│                               │                              │
│              ┌────────────────┼────────────────┐             │
│              ▼                ▼                ▼             │
│    ┌─────────────────┐ ┌──────────────┐ ┌──────────────┐    │
│    │VerificationReviewer│ │VerificationMemory│ │   Event Bus   │    │
│    │  (model-based QA)  │ │ (TEAM_MEMORY.md) │ │ (frontend UI) │    │
│    └─────────────────┘ └──────────────┘ └──────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | File | Responsibility |
|-----------|------|----------------|
| `TeamVerificationRail` | `rail.py` | Rail mounted on Leader; intercepts task completions; triggers async verification |
| `VerificationReviewer` | `reviewer.py` | Lightweight subagent; calls model with structured prompt; parses JSON result |
| `VerificationMemory` | `memory.py` | Persists results to `TEAM_MEMORY.md`; provides trend queries |
| `VerificationResult` | `result.py` | Data models: result, status, dimensions, scores |
| `VerificationConfig` | `config.py` | Typed configuration schema |

### Integration Points

1. **Rail System** — `TeamVerificationRail` extends `DeepAgentRail` and mounts via `build_member_rails()` on the Leader only.
2. **Event System** — Emits `VERIFICATION_COMPLETED` / `VERIFICATION_ERROR` through the existing `TeamMonitorHandler` event pipeline.
3. **Memory System** — Writes to `TEAM_MEMORY.md` using the same format as other team memory entries.
4. **Config System** — Reads from `team.verification.*` namespace in `config.yaml`.

---

## FAQ

**Q: Does verification block the team workflow?**

A: No. Verification runs asynchronously (fire-and-forget) via `asyncio.create_task()`. The Leader can consolidate results while verification is still running. Future versions may support `block_on_fail` for stricter quality gates.

**Q: What happens if the model call fails?**

A: The error is caught gracefully, a `team.verification.error` event is emitted, and the task proceeds normally. No blocking occurs.

**Q: Can I use a different model for verification than the main agent?**

A: Yes. Pass a dedicated `model_client` to `TeamVerificationRail`. This is useful for using a cheaper/faster model (e.g., GPT-4o-mini) for reviews.

**Q: How do I disable verification for specific tasks?**

A: Add task title patterns to `team.verification.skip_patterns` in `config.yaml`. By default, tasks matching "heartbeat", "ping", or "status" are skipped.

**Q: Where can I see verification history?**

A: Check `team-workspace/TEAM_MEMORY.md` under the "Verification History" section. The frontend also shows verification events in the task detail panel.

**Q: Does verification work in non-team modes?**

A: No. The Verification Layer is designed specifically for Agent Team mode (Cluster mode). It requires the team event infrastructure and `TEAM_MEMORY.md`.

**Q: Can teammates see their own verification scores?**

A: Yes — `TEAM_MEMORY.md` is read-only to all team members, so any agent can query verification history and trends.

---

> **Next steps:** See [Agent Teams](AgentTeams.md) for general team mode usage.
