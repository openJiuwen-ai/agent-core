# openjiuwen.agent_evolving.sharing.backends.local_file

`openjiuwen.agent_evolving.sharing.backends.local_file` is the **local filesystem implementation** of `SharingBackend`, using a directory layout to simulate hub storage. Suitable for development debugging and single-machine scenarios.

Directory layout:

```
hub_path/
├── packages/<skill_id>/skill.tar.gz   # Immutable skill package
├── packages/<skill_id>/meta.json      # Skill metadata
├── bundles/<skill_id>/sb_xxx.json     # Experience bundles
├── index/<skill_id>.jsonl             # Per-skill keyword index
├── index/global.jsonl                 # Global skill search index
└── .outbox/<skill_id>/sb_xxx.json     # Failed bundle uploads
```

---

## class openjiuwen.agent_evolving.sharing.backends.local_file.LocalFileBackend

Local-filesystem hub simulation using Jaccard similarity for bundle deduplication and keyword retrieval.

```text
class LocalFileBackend(
    hub_path: Optional[str | os.PathLike] = None,
    dedup_jaccard_threshold: float = 0.85,
)
```

**Parameters**:

* **hub_path**(str | os.PathLike, optional): Hub root directory path. Default: `~/.openjiuwen/experience_hub`.
* **dedup_jaccard_threshold**(float, optional): Jaccard dedup threshold; bundles are rejected when their keywords_aggregate Jaccard similarity with an existing index entry ≥ this value. Default: `0.85`.

### hub_path -> Path

Absolute path of the hub root directory.

### outbox_dir -> Path

Absolute path of the outbox directory (failed uploads are routed here).

### async upload_bundle(bundle) -> UploadResult

Uploads an experience bundle. Flow: check skill_id → Jaccard dedup check → write bundle JSON → append keyword index → update global index. On OSError, the bundle is routed to the outbox directory.

**Parameters**:

* **bundle**(SharedSkillBundle): Experience bundle to upload.

**Returns**:

**UploadResult**, upload result. Dedup rejection returns `UploadResult(ok=False, reason="keywords overlap existing bundle ...")`; OSError returns `UploadResult(ok=False, retryable=True)`.

### async download_bundles(skill_id, query, top_k=3) -> List[SharedSkillBundle]

Downloads relevant bundles by query keywords. Uses Jaccard similarity to rank index entries, returning top_k most relevant bundles.

**Parameters**:

* **skill_id**(str): Skill stable ID.
* **query**(QueryKeywords): Query keyword set.
* **top_k**(int, optional): Maximum number of bundles to return. Default: `3`.

**Returns**:

**List[SharedSkillBundle]**, bundles sorted by relevance; returns empty list when no index or skill_id is empty.

### async has_skill_package(skill_id) -> bool

Checks whether the skill package already exists (checks `skill.tar.gz` file).

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**bool**, True when the package exists.

### async upload_skill_package(skill_id, package_bytes, meta) -> None

Uploads the skill package. First upload writes `skill.tar.gz` and `meta.json`; subsequent uploads are no-op (does not overwrite).

**Parameters**:

* **skill_id**(str): Skill stable ID.
* **package_bytes**(bytes): Skill package byte content; must not be empty.
* **meta**(SkillPackageMeta): Skill package metadata.

**Exceptions**:

* **ValueError**: Raised when skill_id is empty or package_bytes is empty.
* **OSError**: Raised when file write fails.

### async download_skill_package(skill_id) -> bytes | None

Downloads skill package bytes.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**bytes | None**, skill package bytes; returns None when file is missing.

### async get_skill_package_meta(skill_id) -> SkillPackageMeta | None

Retrieves skill package metadata.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**SkillPackageMeta | None**, metadata; returns None when file is missing or parse fails.

### async search_skills(query, top_k=5) -> List[SkillSearchResult]

Searches skills in the global index by keyword relevance. Uses Jaccard similarity matching against keywords + skill_name + description.

**Parameters**:

* **query**(QueryKeywords): Query keyword set.
* **top_k**(int, optional): Maximum number of results to return. Default: `5`.

**Returns**:

**List[SkillSearchResult]**, search results sorted by relevance; returns empty list when global index is empty.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.sharing import (
>>>     LocalFileBackend,
>>>     SharedSkillBundle,
>>>     SharedExperience,
>>>     SharingMeta,
>>>     SkillPackageMeta,
>>>     QueryKeywords,
>>>     UploadResult,
>>> )
>>> from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, EvolutionPatch
>>>
>>> async def demo():
>>>     backend = LocalFileBackend(hub_path="/tmp/experience_hub")
>>>
>>>     # Upload skill package
>>>     meta = SkillPackageMeta(skill_id="skill_abc", skill_name="bash_tool", description="bash command tool")
>>>     await backend.upload_skill_package("skill_abc", b"fake_package_bytes", meta)
>>>
>>>     # Upload experience bundle
>>>     record = EvolutionRecord.make(
>>>         source="execution_failure",
>>>         context="bash error",
>>>         change=EvolutionPatch(script="echo hello", keywords=["bash", "error"], summary="fix"),
>>>     )
>>>     exp = SharedExperience(record=record, keywords=["bash", "error"], summary="fix", sharing_meta=SharingMeta(skill_name="bash_tool"))
>>>     bundle = SharedSkillBundle.make(skill_name="bash_tool", experiences=[exp])
>>>     bundle.skill_id = "skill_abc"
>>>     result = await backend.upload_bundle(bundle)
>>>     print(result.ok, result.bundle_id)
>>>
>>>     # Search skills
>>>     query = QueryKeywords(keywords=["bash", "error"], intent="bash error fix")
>>>     search_results = await backend.search_skills(query, top_k=3)
>>>     for r in search_results:
>>>         print(r.skill_id, r.skill_name, r.score)
>>>
>>> asyncio.run(demo())
True sb_xxxxxxxx
skill_abc bash_tool 0.85
```