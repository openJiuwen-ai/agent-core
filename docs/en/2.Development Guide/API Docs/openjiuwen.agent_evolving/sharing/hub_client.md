# openjiuwen.agent_evolving.sharing.hub_client

`openjiuwen.agent_evolving.sharing.hub_client` is the **high-level client** for the experience sharing module, responsible for:

- Searching the hub for skills relevant to query keywords;
- Downloading skill packages and installing them into the local skills directory via `EvolutionStore`.

---

## class openjiuwen.agent_evolving.sharing.hub_client.ExperienceHubClient

High-level client for searching hub skills and installing packages locally, encapsulating the search + install flow.

```text
class ExperienceHubClient(
    backend: SharingBackend,
    evolution_store: EvolutionStore,
)
```

**Parameters**:

* **backend**(SharingBackend): Sharing backend instance.
* **evolution_store**(EvolutionStore): Evolution store instance for installing skill packages.

### sharer -> ExperienceSharer

The internal ExperienceSharer instance.

### async search_skills(query, *, top_k=5) -> List[SkillSearchResult]

Searches the hub for skills relevant to the query keywords.

**Parameters**:

* **query**(QueryKeywords): Query keyword set.
* **top_k**(int, optional): Maximum number of results to return. Default: `5`.

**Returns**:

**List[SkillSearchResult]**, search result list.

### async install_skill(skill_id, *, skill_name=None) -> Path | None

Downloads and installs the hub skill package into the local skills directory. Flow: download package bytes → get metadata → call `EvolutionStore.install_skill_package` to unpack and install.

**Parameters**:

* **skill_id**(str): Skill stable ID.
* **skill_name**(str, optional): Target skill name; falls back to metadata when empty. Default: `None`.

**Returns**:

**Path | None**, installation path; returns None when skill_id is empty or package is missing.

**Example**:

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