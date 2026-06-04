# openjiuwen.agent_evolving.sharing.backends.base

`openjiuwen.agent_evolving.sharing.backends.base` 定义了经验共享后端的**抽象契约**，所有 Hub 后端实现（如 `LocalFileBackend`）必须继承 `SharingBackend` 并实现其 7 个抽象方法。Hub 按 `skill_id` 分区，每个 Skill 保持一个不可变的 package，经验 bundle 按时间追加。

---

## class openjiuwen.agent_evolving.sharing.backends.base.SharingBackend

共享后端抽象基类。开发者实现自定义 Hub 后端时，需要继承此基类并实现全部抽象方法。

```text
class SharingBackend(ABC)
```

共享后端契约，定义了 bundle 上传/下载、Skill 包管理、关键词搜索等 7 个核心操作。

### abstractmethod async upload_bundle(bundle) -> UploadResult

持久化经验 bundle，返回上传结果。

**参数**：

* **bundle**(SharedSkillBundle)：待上传的经验 bundle。

**返回**：

**UploadResult**，上传结果（成功/失败/原因/是否可重试）。

### abstractmethod async download_bundles(skill_id, query, top_k=3) -> List[SharedSkillBundle]

按查询关键词下载与 skill_id 相关的 top_k 个 bundle。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。
* **query**(QueryKeywords)：查询关键词集。
* **top_k**(int，可选)：返回的最大 bundle 数。默认值：`3`。

**返回**：

**List[SharedSkillBundle]**，按相关度排序的 bundle 列表。

### abstractmethod async has_skill_package(skill_id) -> bool

检查 Hub 是否已存储该 Skill 的初始包。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**bool**，Skill 包已存在时返回 True。

### abstractmethod async upload_skill_package(skill_id, package_bytes, meta) -> None

上传初始 Skill 包。实现必须将重复上传视为 no-op（Hub 只保留首次版本）。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。
* **package_bytes**(bytes)：Skill 包的字节内容。
* **meta**(SkillPackageMeta)：Skill 包元数据。

### abstractmethod async download_skill_package(skill_id) -> bytes | None

下载 Skill 包字节。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**bytes | None**，Skill 包字节；不存在时返回 None。

### abstractmethod async get_skill_package_meta(skill_id) -> SkillPackageMeta | None

获取 Skill 包的 Hub 元数据。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**SkillPackageMeta | None**，元数据；不存在时返回 None。

### abstractmethod async search_skills(query, top_k=5) -> List[SkillSearchResult]

按关键词搜索 Hub 上的 Skill。

**参数**：

* **query**(QueryKeywords)：查询关键词集。
* **top_k**(int，可选)：返回的最大结果数。默认值：`5`。

**返回**：

**List[SkillSearchResult]**，搜索结果列表。

**样例**：

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