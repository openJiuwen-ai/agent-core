# openjiuwen.agent_evolving.sharing

`openjiuwen.agent_evolving.sharing` is the **experience sharing module** in the self-evolving framework, responsible for sharing local agent skill evolution experiences across users — uploading to an experience hub, and downloading relevant experiences from other users to apply locally. This module is optional and enabled via configuration.

Core responsibilities:

- Maintaining a per-skill in-memory upload queue with auto-deduplication;
- QC screening (execution_failure gate + score gate) before wrapping and staging experiences;
- Uploading skill packages and experience bundles to the hub backend with exponential backoff retry;
- Downloading relevant experience bundles and mirroring them to a local cache;
- Searching the hub for skills by keyword relevance and installing them locally.

---

## Module Export Overview

The sharing module exports the following public APIs from `__init__.py`, distributed across submodules:

| Export Name | Submodule | Doc Link |
|-------------|-----------|----------|
| SharingBackend | sharing.backends.base | [backends/base.md](backends/base.md) |
| LocalFileBackend | sharing.backends.local_file | [backends/local_file.md](backends/local_file.md) |
| KeywordExtractor | sharing.keyword_extractor | [keyword_extractor.md](keyword_extractor.md) |
| QUERY_KEYWORDS_LLM_POLICY | sharing.keyword_extractor | [keyword_extractor.md](keyword_extractor.md) |
| ShareStager | sharing.share_stager | [share_stager.md](share_stager.md) |
| ExperienceSharer | sharing.experience_sharer | [experience_sharer.md](experience_sharer.md) |
| SkillSharingContextProvider | sharing.experience_sharer | [experience_sharer.md](experience_sharer.md) |
| SharingMeta | sharing.types | [types.md](types.md) |
| SharedExperience | sharing.types | [types.md](types.md) |
| SharedSkillBundle | sharing.types | [types.md](types.md) |
| SkillPackageMeta | sharing.types | [types.md](types.md) |
| SkillSearchResult | sharing.types | [types.md](types.md) |
| QueryKeywords | sharing.types | [types.md](types.md) |
| UploadResult | sharing.types | [types.md](types.md) |
| StagingResult | sharing.types | [types.md](types.md) |
| ensure_skill_id_in_content | checkpointing.skill_package | [skill_package.md](skill_package.md) |
| pack_skill_directory | checkpointing.skill_package | [skill_package.md](skill_package.md) |
| read_skill_id_from_content | checkpointing.skill_package | [skill_package.md](skill_package.md) |
| unpack_skill_package | checkpointing.skill_package | [skill_package.md](skill_package.md) |

---

## Data Flow Overview

```
Upload path:
  EvolutionRecord → ShareStager.screen_and_stage (QC screening)
    → SharedExperience → ExperienceSharer.stage_for_upload (queue + dedup)
    → ExperienceSharer.flush_pending_uploads (bundle build + skill package sync + bundle upload)
    → SharingBackend.upload_bundle → Hub storage

Download path:
  Conversation excerpt → KeywordExtractor.extract_query_keywords → QueryKeywords
    → ExperienceSharer.download_relevant → SharingBackend.download_bundles
    → SharedSkillBundle.experiences → EvolutionRecord (tagged [shared origin=...])
    → Dedup → Evolution fusion (persist or approval)
```

---

## Configuration & Switches

- **Enable condition**: `sharing_config.enabled=True` or environment variable `EVOLUTION_SHARING_ENABLED=1/true/yes/on`
- **Hub path**: `sharing_config.hub_path` or environment variable `EVOLUTION_SHARING_HUB_PATH`, default `~/.openjiuwen/experience_hub`
- **Backend type**: Currently only `local_file` is supported; other values fall back to `local_file`
- **Download top_k**: `sharing_config.download_top_k`, default 3
- **Upload retries**: `sharing_config.max_upload_retries`, default 3
- **QC score threshold**: `ShareStager.qc_score_threshold`, default 0.6
- **Jaccard dedup threshold**: `LocalFileBackend.dedup_jaccard_threshold`, default 0.85