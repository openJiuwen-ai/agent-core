# openjiuwen.agent_evolving.sharing.share_stager

`openjiuwen.agent_evolving.sharing.share_stager` is the **QC screening and staging pipeline** for the experience sharing module, responsible for:

- Running quality gates on each evolution record (execution_failure gate + score gate);
- Wrapping passing records as `SharedExperience` and pushing them into the sharer's upload queue;
- Returning the screening result; rejected records do not affect local persistence.

**Boundary constraint**: This component never writes `evolutions.json` and never uploads; it only decides whether each record enters the share pool.

---

## class openjiuwen.agent_evolving.sharing.share_stager.ShareStager

QC screening + staging pipeline that screens, wraps, and stages experiences for sharing.

```text
class ShareStager(
    keyword_extractor: KeywordExtractor,
    sharer: ExperienceSharer,
    qc_score_threshold: float = 0.6,
    source_user_id: Optional[str] = None,
)
```

**Parameters**:

* **keyword_extractor**(KeywordExtractor): Keyword extractor for parsing keywords/summary from optimizer output.
* **sharer**(ExperienceSharer): Experience sharing facade that receives QC-passing experiences into its upload queue.
* **qc_score_threshold**(float, optional): Score gate threshold; records below this value are rejected. Default: `0.6`.
* **source_user_id**(str, optional): Uploader user identifier. Default: `None`.

### qc_score_threshold -> float

Current QC score gate threshold.

### async screen_and_stage(skill_name, records, messages=None) -> StagingResult

Runs QC → wrap → stage pipeline for sharing. This method does **not** write `evolutions.json` and does **not** upload; it only screens records through quality gates, wraps passing ones as `SharedExperience`, and pushes them into the sharer's pending-upload queue.

**Parameters**:

* **skill_name**(str): Skill name.
* **records**(List[EvolutionRecord]): Evolution records to screen.
* **messages**(List[dict], optional): Conversation messages for execution_failure gate judgment. Default: `None`.

**Returns**:

**StagingResult**, containing `staged_for_share` (QC-passing) and `dropped_for_share` (QC-rejected with reasons).

**QC gate rules**:

1. **execution_failure gate**: If the record source is `execution_failure` and the conversation has no successful tool execution results, the record is dropped (reason: "execution failure without successful follow-up tool call").
2. **score gate**: When record score < `qc_score_threshold`, the record is dropped (reason includes the specific score and threshold).

**Example**:

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