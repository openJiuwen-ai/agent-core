# openjiuwen.agent_evolving.sharing.backends.local_file

`openjiuwen.agent_evolving.sharing.backends.local_file` 是 `SharingBackend` 的**本地文件系统实现**，用目录结构模拟 Hub 存储，适用于开发调试与单机场景。

目录布局：

```
hub_path/
├── packages/<skill_id>/skill.tar.gz   # 不可变 Skill 包
├── packages/<skill_id>/meta.json      # Skill 元数据
├── bundles/<skill_id>/sb_xxx.json     # 经验 bundle
├── index/<skill_id>.jsonl             # per-skill 关键词索引
├── index/global.jsonl                 # 全局 Skill 搜索索引
└── .outbox/<skill_id>/sb_xxx.json     # 上传失败的 bundle
```

---

## class openjiuwen.agent_evolving.sharing.backends.local_file.LocalFileBackend

基于本地文件系统的 Hub 模拟实现，使用 Jaccard 相似度进行 bundle 去重和关键词检索。

```text
class LocalFileBackend(
    hub_path: Optional[str | os.PathLike] = None,
    dedup_jaccard_threshold: float = 0.85,
)
```

**参数**：

* **hub_path**(str | os.PathLike，可选)：Hub 根目录路径。默认值：`~/.openjiuwen/experience_hub`。
* **dedup_jaccard_threshold**(float，可选)：Jaccard 去重阈值，上传 bundle 时若与已有索引条目的关键词 Jaccard 相似度 ≥ 此值则拒绝。默认值：`0.85`。

### hub_path -> Path

Hub 根目录的绝对路径。

### outbox_dir -> Path

outbox 目录的绝对路径（上传失败时 bundle 被路由到此目录）。

### async upload_bundle(bundle) -> UploadResult

上传经验 bundle。流程：检查 skill_id → Jaccard 去重检查 → 写入 bundle JSON → 追加关键词索引 → 更新全局索引。OSError 时 bundle 被路由到 outbox 目录。

**参数**：

* **bundle**(SharedSkillBundle)：待上传的经验 bundle。

**返回**：

**UploadResult**，上传结果。去重拒绝时返回 `UploadResult(ok=False, reason="keywords overlap existing bundle ...")`；OSError 时返回 `UploadResult(ok=False, retryable=True)`。

### async download_bundles(skill_id, query, top_k=3) -> List[SharedSkillBundle]

按查询关键词下载相关 bundle。使用 Jaccard 相似度对索引条目排序，返回 top_k 个最相关的 bundle。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。
* **query**(QueryKeywords)：查询关键词集。
* **top_k**(int，可选)：返回的最大 bundle 数。默认值：`3`。

**返回**：

**List[SharedSkillBundle]**，按相关度排序的 bundle 列表；无索引或 skill_id 为空时返回空列表。

### async has_skill_package(skill_id) -> bool

检查 Skill 包是否已存在（检查 `skill.tar.gz` 文件）。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**bool**，包已存在时返回 True。

### async upload_skill_package(skill_id, package_bytes, meta) -> None

上传 Skill 包。首次上传写入 `skill.tar.gz` 和 `meta.json`，重复上传为 no-op（不覆盖）。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。
* **package_bytes**(bytes)：Skill 包字节内容，不可为空。
* **meta**(SkillPackageMeta)：Skill 包元数据。

**异常**：

* **ValueError**：skill_id 为空或 package_bytes 为空时抛出。
* **OSError**：文件写入失败时抛出。

### async download_skill_package(skill_id) -> bytes | None

下载 Skill 包字节。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**bytes | None**，Skill 包字节；文件不存在时返回 None。

### async get_skill_package_meta(skill_id) -> SkillPackageMeta | None

获取 Skill 包元数据。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**SkillPackageMeta | None**，元数据；文件不存在或解析失败时返回 None。

### async search_skills(query, top_k=5) -> List[SkillSearchResult]

在全局索引中按关键词搜索 Skill。使用 Jaccard 相似度匹配 keywords + skill_name + description。

**参数**：

* **query**(QueryKeywords)：查询关键词集。
* **top_k**(int，可选)：返回的最大结果数。默认值：`5`。

**返回**：

**List[SkillSearchResult]**，按相关度排序的搜索结果；全局索引为空时返回空列表。

**样例**：

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
>>>     # 上传 Skill 包
>>>     meta = SkillPackageMeta(skill_id="skill_abc", skill_name="bash_tool", description="bash command tool")
>>>     await backend.upload_skill_package("skill_abc", b"fake_package_bytes", meta)
>>>
>>>     # 上传经验 bundle
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
>>>     # 搜索 Skill
>>>     query = QueryKeywords(keywords=["bash", "error"], intent="bash error fix")
>>>     search_results = await backend.search_skills(query, top_k=3)
>>>     for r in search_results:
>>>         print(r.skill_id, r.skill_name, r.score)
>>>
>>> asyncio.run(demo())
True sb_xxxxxxxx
skill_abc bash_tool 0.85
```