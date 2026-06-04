# openjiuwen.agent_evolving.sharing.experience_sharer

`openjiuwen.agent_evolving.sharing.experience_sharer` is the **upload/download facade** for the experience sharing module, responsible for:

- Maintaining a per-skill in-memory queue of experiences pending upload, with auto-deduplication;
- Ensuring skill_id and uploading the initial skill package on flush, then retrying bundle uploads with exponential backoff;
- Downloading relevant experience bundles and mirroring them to a local cache;
- Searching the hub for skills by keyword relevance.

---

## class openjiuwen.agent_evolving.sharing.experience_sharer.ExperienceSharer

Skill-scoped facade for the sharing path, encapsulating the full sharing lifecycle: staging queue, skill package sync, bundle upload retry, download mirroring.

```text
class ExperienceSharer(
    backend: SharingBackend,
    local_cache_dir: Optional[str | os.PathLike] = None,
    max_upload_retries: int = 3,
    backoff_base_secs: float = 0.5,
    skill_sharing_context_provider: Optional[SkillSharingContextProvider] = None,
)
```

**Parameters**:

* **backend**(SharingBackend): Sharing backend instance (e.g., `LocalFileBackend`).
* **local_cache_dir**(str | os.PathLike, optional): Local cache directory for mirroring uploaded/downloaded bundles. Default: `None`.
* **max_upload_retries**(int, optional): Maximum bundle upload retry attempts, at least 1. Default: `3`.
* **backoff_base_secs**(float, optional): Exponential backoff base seconds. Default: `0.5`.
* **skill_sharing_context_provider**(SkillSharingContextProvider, optional): Skill package context provider for obtaining skill_id, package bytes, etc. Default: `None`.

### backend -> SharingBackend

The currently bound sharing backend instance.

### local_cache_dir -> Path | None

Local cache directory path.

### set_skill_sharing_context_provider(provider) -> None

Late-bind the skill package context provider after construction.

**Parameters**:

* **provider**(SkillSharingContextProvider | None): New context provider; set to None to clear.

### async resolve_skill_id(skill_name) -> str

Returns the skill_id for a local skill via the context provider. Returns an empty string when the provider is unbound or the call fails.

**Parameters**:

* **skill_name**(str): Skill name.

**Returns**:

**str**, skill_id; returns `""` on failure.

### has_pending(skill_name) -> bool

Checks whether the specified skill has pending upload experiences.

**Parameters**:

* **skill_name**(str): Skill name.

**Returns**:

**bool**, True when the queue is non-empty.

### stage_for_upload(skill_name, exp) -> None

Queues a shared experience for later upload, auto-deduplicating on `(skill_name, record.id)`.

**Parameters**:

* **skill_name**(str): Skill name.
* **exp**(SharedExperience): Shared experience to upload.

### discard_pending_uploads(skill_name) -> int

Drops the in-memory upload queue for the specified skill (negative feedback scenario).

**Parameters**:

* **skill_name**(str): Skill name.

**Returns**:

**int**, number of discarded experiences.

### async flush_pending_uploads(skill_name) -> UploadResult

Bundles and uploads every pending experience for the specified skill. Flow: dequeue → build bundle → sync skill package → retry upload with exponential backoff → mirror to local cache.

**Parameters**:

* **skill_name**(str): Skill name.

**Returns**:

**UploadResult**, upload result; returns `UploadResult(ok=True)` when queue is empty; returns `UploadResult(ok=False)` when skill_id is unavailable.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.sharing import (
>>>     ExperienceSharer,
>>>     LocalFileBackend,
>>>     SharedExperience,
>>>     SharingMeta,
>>> )
>>> from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, EvolutionPatch
>>>
>>> async def demo():
>>>     backend = LocalFileBackend(hub_path="/tmp/experience_hub")
>>>     sharer = ExperienceSharer(backend=backend, local_cache_dir="/tmp/sharing_cache")
>>>
>>>     record = EvolutionRecord.make(
>>>         source="execution_failure",
>>>         context="bash command failed",
>>>         change=EvolutionPatch(script="echo hello", keywords=["bash", "error"], summary="bash error fix"),
>>>     )
>>>     exp = SharedExperience(
>>>         record=record,
>>>         keywords=["bash", "error"],
>>>         summary="bash error fix",
>>>         sharing_meta=SharingMeta(skill_name="bash_tool"),
>>>     )
>>>     sharer.stage_for_upload("bash_tool", exp)
>>>     result = await sharer.flush_pending_uploads("bash_tool")
>>>     print(result.ok, result.bundle_id)
>>>
>>> asyncio.run(demo())
True sb_xxxxxxxx
```

### async download_relevant(skill_id, query, top_k=3, *, skill_name="") -> List[SharedSkillBundle]

Downloads up to `top_k` relevant bundles from the hub and mirrors them to the local cache.

**Parameters**:

* **skill_id**(str): Skill stable ID.
* **query**(QueryKeywords): Query keyword set.
* **top_k**(int, optional): Maximum number of bundles to return. Default: `3`.
* **skill_name**(str, optional): Skill name (used only for logging). Default: `""`.

**Returns**:

**List[SharedSkillBundle]**, downloaded bundle list; returns empty list when skill_id is empty or backend fails.

### async search_skills(query, top_k=5) -> List[SkillSearchResult]

Searches the hub for skills relevant to the query keywords.

**Parameters**:

* **query**(QueryKeywords): Query keyword set.
* **top_k**(int, optional): Maximum number of results to return. Default: `5`.

**Returns**:

**List[SkillSearchResult]**, search result list; returns empty list on backend failure.

### async download_skill_package(skill_id) -> bytes | None

Downloads the immutable skill package bytes stored on the hub.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**bytes | None**, skill package bytes; returns None when skill_id is empty or package is missing.

### async get_skill_package_meta(skill_id) -> SkillPackageMeta | None

Retrieves hub metadata for the skill package.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**SkillPackageMeta | None**, metadata; returns None when skill_id is empty or backend fails.

### list_cached_bundles(skill_id) -> List[SharedSkillBundle]

Lists bundles previously mirrored into the local download cache.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**List[SharedSkillBundle]**, cached bundle list; returns empty list when no cache directory or skill_id is empty.

---

## class openjiuwen.agent_evolving.sharing.experience_sharer.SkillSharingContextProvider

Type alias for the skill package context provider, defined as `Callable[[str], Awaitable[Tuple[str, bytes, str, str]]]`.

Called with skill_name, returns `(skill_id, package_bytes, skill_name, description)` tuple. Provided by `EvolutionStore`, used for obtaining packaging data on first skill package upload.

**Parameters**:

* **skill_name**(str): Skill name.

**Returns**:

**Tuple[str, bytes, str, str]**, `(skill_id, package_bytes, skill_name, description)` tuple.