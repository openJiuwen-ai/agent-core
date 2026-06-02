# openjiuwen.agent_evolving.sharing.backends.base

`openjiuwen.agent_evolving.sharing.backends.base` defines the **abstract contract** for experience sharing backends. All hub backend implementations (e.g., `LocalFileBackend`) must inherit `SharingBackend` and implement its 7 abstract methods. Hub partitions are keyed by `skill_id`; each skill keeps a single immutable package, with experience bundles appended over time.

---

## class openjiuwen.agent_evolving.sharing.backends.base.SharingBackend

Sharing backend abstract base class. Developers implementing custom hub backends must inherit this base class and implement all abstract methods.

```text
class SharingBackend(ABC)
```

Sharing backend contract defining 7 core operations: bundle upload/download, skill package management, keyword search, etc.

### abstractmethod async upload_bundle(bundle) -> UploadResult

Persists an experience bundle when accepted and returns the upload outcome.

**Parameters**:

* **bundle**(SharedSkillBundle): Experience bundle to upload.

**Returns**:

**UploadResult**, upload outcome (success/failure/reason/retryable).

### abstractmethod async download_bundles(skill_id, query, top_k=3) -> List[SharedSkillBundle]

Returns up to `top_k` bundles ranked by relevance to `query` for the given skill_id.

**Parameters**:

* **skill_id**(str): Skill stable ID.
* **query**(QueryKeywords): Query keyword set.
* **top_k**(int, optional): Maximum number of bundles to return. Default: `3`.

**Returns**:

**List[SharedSkillBundle]**, bundles sorted by relevance.

### abstractmethod async has_skill_package(skill_id) -> bool

Returns True iff the hub already stores the initial skill package.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**bool**, True when the skill package exists.

### abstractmethod async upload_skill_package(skill_id, package_bytes, meta) -> None

Persists the initial skill package under `skill_id`. Implementations must treat re-upload as a no-op (the hub keeps only the first version).

**Parameters**:

* **skill_id**(str): Skill stable ID.
* **package_bytes**(bytes): Skill package byte content.
* **meta**(SkillPackageMeta): Skill package metadata.

### abstractmethod async download_skill_package(skill_id) -> bytes | None

Returns the stored skill package bytes, or `None` if missing.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**bytes | None**, skill package bytes; returns None when missing.

### abstractmethod async get_skill_package_meta(skill_id) -> SkillPackageMeta | None

Returns hub metadata for `skill_id`.

**Parameters**:

* **skill_id**(str): Skill stable ID.

**Returns**:

**SkillPackageMeta | None**, metadata; returns None when missing.

### abstractmethod async search_skills(query, top_k=5) -> List[SkillSearchResult]

Searches skills on the hub by keyword relevance.

**Parameters**:

* **query**(QueryKeywords): Query keyword set.
* **top_k**(int, optional): Maximum number of results to return. Default: `5`.

**Returns**:

**List[SkillSearchResult]**, search result list.

**Example**:

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.sharing.backends.base import SharingBackend
>>> from openjiuwen.agent_evolving.sharing import (
>>>     SharedSkillBundle,
>>>     SharedExperience,
>>>     SkillPackageMeta,
>>>     QueryKeywords,
>>>     SkillSearchResult,
>>>     UploadResult,
>>> )
>>>
>>> class MyBackend(SharingBackend):
>>>     def __init__(self, store_dir):
>>>         self._store_dir = store_dir
>>>         self._bundles = {}
>>>         self._packages = {}
>>>
>>>     async def upload_bundle(self, bundle):
>>>         self._bundles[bundle.bundle_id] = bundle
>>>         return UploadResult(ok=True, bundle_id=bundle.bundle_id)
>>>
>>>     async def download_bundles(self, skill_id, query, top_k=3):
>>>         return [b for b in self._bundles.values() if b.skill_id == skill_id][:top_k]
>>>
>>>     async def has_skill_package(self, skill_id):
>>>         return skill_id in self._packages
>>>
>>>     async def upload_skill_package(self, skill_id, package_bytes, meta):
>>>         if skill_id not in self._packages:
>>>             self._packages[skill_id] = (package_bytes, meta)
>>>
>>>     async def download_skill_package(self, skill_id):
>>>         return self._packages.get(skill_id, (None, None))[0]
>>>
>>>     async def get_skill_package_meta(self, skill_id):
>>>         entry = self._packages.get(skill_id)
>>>         return entry[1] if entry else None
>>>
>>>     async def search_skills(self, query, top_k=5):
>>>         return []
>>>
>>> backend = MyBackend(store_dir="/tmp/my_hub")
```