# openjiuwen.agent_evolving.sharing.share_stager

`openjiuwen.agent_evolving.sharing.share_stager` 是经验共享模块的**QC 筛选与暂存管道**，负责：

- 对每条演进记录执行质量门控（execution_failure 门控 + score 门控）；
- 将通过 QC 的记录包装为 `SharedExperience` 并推入 sharer 的上传队列；
- 返回筛选结果，被拒绝的记录不影响本地持久化。

**边界约束**：此组件不写 `evolutions.json`，不上传，只决定每条记录是否进入共享池。

---

## class openjiuwen.agent_evolving.sharing.share_stager.ShareStager

QC 筛选 + 暂存管道，将演进记录筛选、包装并推入 sharer 上传队列。

```text
class ShareStager(
    keyword_extractor: KeywordExtractor,
    sharer: ExperienceSharer,
    qc_score_threshold: float = 0.6,
    source_user_id: Optional[str] = None,
)
```

**参数**：

* **keyword_extractor**(KeywordExtractor)：关键词提取器，用于从优化器输出中解析 keywords/summary。
* **sharer**(ExperienceSharer)：经验共享门面，接收通过 QC 的经验入队。
* **qc_score_threshold**(float，可选)：score 门控阈值，低于此值的记录被拒绝。默认值：`0.6`。
* **source_user_id**(str，可选)：上传用户标识。默认值：`None`。

### qc_score_threshold -> float

当前 QC score 门控阈值。

### async screen_and_stage(skill_name, records, messages=None) -> StagingResult

对每条记录执行 QC → 包装 → 入队流程。此方法不写 `evolutions.json`，不上传，只筛选并推入 sharer 的暂存队列。

**参数**：

* **skill_name**(str)：Skill 名称。
* **records**(List[EvolutionRecord])：待筛选的演进记录列表。
* **messages**(List[dict]，可选)：对话消息列表，用于 execution_failure 门控判断。默认值：`None`。

**返回**：

**StagingResult**，包含通过 QC 的 `staged_for_share` 和被拒绝的 `dropped_for_share` 及原因。

**QC 门控规则**：

1. **execution_failure 门控**：如果记录 source 为 `execution_failure` 且对话中没有成功的工具执行结果，则丢弃（理由："execution failure without successful follow-up tool call"）。
2. **score 门控**：记录 score < `qc_score_threshold` 时丢弃（理由包含具体分值与阈值）。

**样例**：

```python
>>> import asyncio
>>> from openjiuwen.agent_evolving.sharing import ShareStager, ExperienceSharer, LocalFileBackend, KeywordExtractor
>>> from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord, EvolutionPatch
>>>
>>> async def demo():
>>>     backend = LocalFileBackend(hub_path="/tmp/experience_hub")
>>>     sharer = ExperienceSharer(backend=backend)
>>>     extractor = KeywordExtractor()
>>>     stager = ShareStager(keyword_extractor=extractor, sharer=sharer, qc_score_threshold=0.6)
>>>
>>>     record = EvolutionRecord.make(
>>>         source="execution_failure",
>>>         context="bash command failed",
>>>         change=EvolutionPatch(script="echo hello", keywords=["bash"], summary="fix"),
>>>         score=0.8,
>>>     )
>>>     result = await stager.screen_and_stage("bash_tool", [record])
>>>     print(result.has_shareable, len(result.dropped_for_share))
>>>
>>> asyncio.run(demo())
True 0
```