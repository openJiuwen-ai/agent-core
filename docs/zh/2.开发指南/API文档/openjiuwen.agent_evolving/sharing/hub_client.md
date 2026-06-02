# openjiuwen.agent_evolving.sharing.hub_client

`openjiuwen.agent_evolving.sharing.hub_client` 是经验共享模块的**高层客户端**，负责：

- 搜索 Hub 上与查询关键词相关的 Skill；
- 下载 Skill 包并通过 `EvolutionStore` 解压安装到本地 skills 目录。

---

## class openjiuwen.agent_evolving.sharing.hub_client.ExperienceHubClient

搜索 Hub Skill 并安装到本地的高层客户端，封装了搜索 + 安装的完整流程。

```text
class ExperienceHubClient(
    backend: SharingBackend,
    evolution_store: EvolutionStore,
)
```

**参数**：

* **backend**(SharingBackend)：共享后端实例。
* **evolution_store**(EvolutionStore)：演进存储实例，用于安装 Skill 包。

### sharer -> ExperienceSharer

内部使用的 ExperienceSharer 实例。

### async search_skills(query, *, top_k=5) -> List[SkillSearchResult]

搜索 Hub 上与查询关键词相关的 Skill。

**参数**：

* **query**(QueryKeywords)：查询关键词集。
* **top_k**(int，可选)：返回的最大结果数。默认值：`5`。

**返回**：

**List[SkillSearchResult]**，搜索结果列表。

### async install_skill(skill_id, *, skill_name=None) -> Path | None

下载 Skill 包并安装到本地 skills 目录。流程：下载包字节 → 获取元数据 → 调用 `EvolutionStore.install_skill_package` 解压安装。

**参数**：

* **skill_id**(str)：Skill 稳定 ID。
* **skill_name**(str，可选)：目标 Skill 名称；为空时从元数据获取。默认值：`None`。

**返回**：

**Path | None**，安装路径；skill_id 为空或包不存在时返回 None。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.sharing import ExperienceHubClient, LocalFileBackend, QueryKeywords
>>> from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
>>>
>>> async def demo():
>>>     backend = LocalFileBackend(hub_path="/tmp/experience_hub")
>>>     store = EvolutionStore(base_dir="/tmp/skills")
>>>     client = ExperienceHubClient(backend=backend, evolution_store=store)
>>>
>>>     query = QueryKeywords(keywords=["bash", "error"], intent="bash error fix")
>>>     results = await client.search_skills(query, top_k=3)
>>>     for r in results:
>>>         print(r.skill_id, r.skill_name, r.score)
>>>
>>> asyncio.run(demo())
skill_abc123 bash_tool 0.85
```