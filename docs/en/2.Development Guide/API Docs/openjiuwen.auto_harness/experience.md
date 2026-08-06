# openjiuwen.auto_harness.experience

Auto Harness Experience Store module, providing persistent storage for experience records and active context synthesis capabilities. Experiences are archived in JSONL format with keyword search and time-decay sorting support. The synthesizer condenses recent experiences into markdown snippets injectable into prompts.

Submodules:
- `experience_store`: JSONL experience archive storage
- `synthesizer`: Active context synthesizer

---

## class openjiuwen.auto_harness.experience.experience_store.ExperienceStore

```python
class ExperienceStore:
    """JSONL-backed experience archive with keyword search."""

    def __init__(self, experience_dir: str) -> None
```

JSONL file-based experience archive storage with keyword search support. Experience records undergo deduplication checks before writing (same topic + type within 24 hours is considered a duplicate).

**Parameters**:
* **experience_dir**(`str`): Experience storage directory path; the directory is created automatically if it does not exist.

### record(experience: Experience) -> str

```python
async def record(self, experience: Experience) -> str
```

Persist an experience record after deduplication check. If a record with the same topic and type already exists within a 24-hour window, the write is rejected and an empty string is returned.

**Parameters**:
* **experience**(`Experience`): The experience object to persist.

**Returns**: The experience ID on success, or an empty string if rejected by deduplication.

---

### search(query: str, top_k: int = 5) -> List[Experience]

```python
async def search(self, query: str, top_k: int = 5) -> List[Experience]
```

Search experiences by keyword across the topic, summary, and details fields. Scoring is a weighted combination of keyword hit count and freshness decay, sorted by composite score in descending order.

**Parameters**:
* **query**(`str`): Search keyword or topic description.
* **top_k**(`int`): Maximum number of results, default 5.

**Returns**: Experience list sorted by relevance.

---

### list_recent(limit: int = 20) -> List[Experience]

```python
async def list_recent(self, limit: int = 20) -> List[Experience]
```

Return recent experience records sorted by timestamp in descending order.

**Parameters**:
* **limit**(`int`): Maximum number of results, default 20.

**Returns**: Experience list sorted by time in descending order.

---

### get(experience_id: str) -> Optional[Experience]

```python
async def get(self, experience_id: str) -> Optional[Experience]
```

Get a single experience record by ID.

**Parameters**:
* **experience_id**(`str`): Experience unique identifier.

**Returns**: The matching experience object, or `None` if not found.

---

## class openjiuwen.auto_harness.experience.synthesizer.ActiveContextSynthesizer

```python
class ActiveContextSynthesizer:
    """Synthesize recent experiences into an active-context string."""

    def __init__(self, experience_dir: str) -> None
```

Synthesize recent experiences into an active context string. Groups by experience type (optimization experiences / failure lessons / key insights), combining time-decay weights to generate markdown-formatted summaries for injection into agent prompts.

**Parameters**:
* **experience_dir**(`str`): Experience storage directory path.

### synthesize(experiences: List[Experience], max_tokens: int = 2000) -> str

```python
async def synthesize(self, experiences: List[Experience], max_tokens: int = 2000) -> str
```

Synthesize an experience list into a markdown summary. Groups by experience type, sorts within groups by time-decay weight, and fills each type's section within the token budget.

**Parameters**:
* **experiences**(`List[Experience]`): Experience list to synthesize.
* **max_tokens**(`int`): Maximum token budget, default 2000.

**Returns**: The synthesized markdown string, or an empty string if no experiences.

---

### load_and_synthesize(top_k: int = 30) -> str

```python
async def load_and_synthesize(self, top_k: int = 30) -> str
```

Convenience method: first loads recent experiences from the store, then synthesizes them.

**Parameters**:
* **top_k**(`int`): Number of recent experiences to load, default 30.

**Returns**: The synthesized markdown string.
