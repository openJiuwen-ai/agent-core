# openjiuwen.agent_evolving.sharing.experience_sharer

`openjiuwen.agent_evolving.sharing.experience_sharer` 是经验共享模块的**上传/下载门面类**，负责：

- 维护每个 Skill 的内存暂存上传队列，自动去重；
- 上传时确保 skill_id、首次上传 Skill 包，然后带指数退避重试上传经验 bundle；
- 下载相关经验 bundle 并镜像到本地缓存；
- 搜索 Hub 上的 Skill。

---

## class openjiuwen.agent_evolving.sharing.experience_sharer.ExperienceSharer

Skill 维度的上传/下载门面类，封装了暂存队列、skill 包同步、bundle 上传重试、下载镜像等完整共享生命周期。

```text
class ExperienceSharer(
    backend: SharingBackend,
    local_cache_dir: Optional[str | os.PathLike] = None,
    max_upload_retries: int = 3,
    backoff_base_secs: float = 0.5,
    skill_sharing_context_provider: Optional[SkillSharingContextProvider] = None,
)
```

**参数**：

* **backend**(SharingBackend)：共享后端实例（如 `LocalFileBackend`）。
* **local_cache_dir**(str | os.PathLike，可选)：本地缓存目录，用于镜像上传/下载的 bundle。默认值：`None`。
* **max_upload_retries**(int，可选)：bundle 上传最大重试次数，至少为 1。默认值：`3`。
* **backoff_base_secs**(float，可选)：指数退避基准秒数。默认值：`0.5`。
* **skill_sharing_context_provider**(SkillSharingContextProvider，可选)：Skill 包上下文提供者，用于获取 skill_id、打包字节等。默认值：`None`。

### backend -> SharingBackend

当前绑定的共享后端实例。

### local_cache_dir -> Path | None

本地缓存目录路径。

### set_skill_sharing_context_provider(provider) -> None

延迟绑定 Skill 包上下文提供者（构造后设置）。

**参数**：

* **provider**(SkillSharingContextProvider | None)：新的上下文提供者，设为 None 可清除。

### async resolve_skill_id(skill_name) -> str

返回本地 Skill 的 skill_id，通过上下文提供者获取。提供者未绑定或调用失败时返回空字符串。

**参数**：

* **skill_name**(str)：Skill 名称。

**返回**：

**str**，skill_id；失败时返回 `""`。

### has_pending(skill_name) -> bool

检查指定 Skill 是否有待上传的经验。

**参数**：

* **skill_name**(str)：Skill 名称。

**返回**：

**bool**，队列非空时返回 True。

### stage_for_upload(skill_name, exp) -> None

将共享经验入队等待上传，自动按 `(skill_name, record.id)` 去重。

**参数**：

* **skill_name**(str)：Skill 名称。
* **exp**(SharedExperience)：待上传的共享经验。

### discard_pending_uploads(skill_name) -> int

丢弃指定 Skill 的暂存上传队列（负反馈场景）。

**参数**：

* **skill_name**(str)：Skill 名称。

**返回**：

**int**，被丢弃的经验数量。

### async flush_pending_uploads(skill_name) -> UploadResult

打包并上传指定 Skill 的所有暂存经验。流程：取出队列 → 构建 bundle → 同步 skill 包 → 带指数退避重试上传 bundle → 镜像到本地缓存。

**参数**：

* **skill_name**(str)：Skill 名称。

**返回**：

**UploadResult**，上传结果；队列为空时返回 `UploadResult(ok=True)`；skill_id 不可用时返回 `UploadResult(ok=False)`。

**样例**：

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

从 Hub 下载与查询关键词相关的 top_k 个 bundle，并镜像到本地缓存。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。
* **query**(QueryKeywords)：查询关键词集。
* **top_k**(int，可选)：返回的最大 bundle 数。默认值：`3`。
* **skill_name**(str，可选)：Skill 名称（仅用于日志）。默认值：`""`。

**返回**：

**List[SharedSkillBundle]**，下载的 bundle 列表；skill_id 为空或后端失败时返回空列表。

### async search_skills(query, top_k=5) -> List[SkillSearchResult]

搜索 Hub 上与查询关键词相关的 Skill。

**参数**：

* **query**(QueryKeywords)：查询关键词集。
* **top_k**(int，可选)：返回的最大结果数。默认值：`5`。

**返回**：

**List[SkillSearchResult]**，搜索结果列表；后端失败时返回空列表。

### async download_skill_package(skill_id) -> bytes | None

下载 Hub 上存储的不可变 Skill 包字节。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**bytes | None**，Skill 包字节；skill_id 为空或包不存在时返回 None。

### async get_skill_package_meta(skill_id) -> SkillPackageMeta | None

获取 Hub 上 Skill 包的元数据。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**SkillPackageMeta | None**，元数据；skill_id 为空或后端失败时返回 None。

### list_cached_bundles(skill_id) -> List[SharedSkillBundle]

列出本地缓存中已镜像的 bundle。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。

**返回**：

**List[SharedSkillBundle]**，缓存的 bundle 列表；无缓存目录或 skill_id 为空时返回空列表。

---

## class openjiuwen.agent_evolving.sharing.experience_sharer.SkillSharingContextProvider

Skill 包上下文提供者的类型别名，定义为 `Callable[[str], Awaitable[Tuple[str, bytes, str, str]]]`。

调用时传入 skill_name，返回 `(skill_id, package_bytes, skill_name, description)` 四元组。由 `EvolutionStore` 提供，用于首次上传 Skill 包时获取打包数据。

**参数**：

* **skill_name**(str)：Skill 名称。

**返回**：

**Tuple[str, bytes, str, str]**，`(skill_id, package_bytes, skill_name, description)` 四元组。