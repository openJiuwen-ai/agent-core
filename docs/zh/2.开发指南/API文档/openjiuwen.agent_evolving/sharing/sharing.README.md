# openjiuwen.agent_evolving.sharing

`openjiuwen.agent_evolving.sharing` 是自演进框架的**经验共享模块**，负责将本地智能体技能演进经验跨用户共享——上传到经验中心（Hub），以及从 Hub 下载其他用户的相关经验并应用到本地技能。该模块是可选功能，通过配置开关启用。

核心职责：

- 维护每个 Skill 的内存暂存上传队列，自动去重；
- QC 筛选（execution_failure 门控 + score 门控）后包装并暂存经验；
- 上传 Skill 包与经验 bundle 到 Hub 后端，带指数退避重试；
- 下载相关经验 bundle 并镜像到本地缓存；
- 搜索 Hub 上与查询关键词相关的 Skill 并安装到本地。

---

## 模块导出总览

sharing 模块在 `__init__.py` 中导出以下公开 API，按子模块分布：

| 导出名称 | 所属子模块 | 文档链接 |
|----------|-----------|---------|
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

## 数据流概览

```
上传路径:
  EvolutionRecord → ShareStager.screen_and_stage (QC筛选)
    → SharedExperience → ExperienceSharer.stage_for_upload (入队去重)
    → ExperienceSharer.flush_pending_uploads (打包bundle + 上传skill包 + 上传bundle)
    → SharingBackend.upload_bundle → Hub存储

下载路径:
  对话摘要 → KeywordExtractor.extract_query_keywords → QueryKeywords
    → ExperienceSharer.download_relevant → SharingBackend.download_bundles
    → SharedSkillBundle.experiences → EvolutionRecord (标记[shared origin=...])
    → 去重 → 演进融合 (持久化或审批)
```

---

## 配置与开关

- **启用条件**：`sharing_config.enabled=True` 或环境变量 `EVOLUTION_SHARING_ENABLED=1/true/yes/on`
- **Hub 路径**：`sharing_config.hub_path` 或环境变量 `EVOLUTION_SHARING_HUB_PATH`，默认 `~/.openjiuwen/experience_hub`
- **后端类型**：目前仅支持 `local_file`，其他值回退到 `local_file`
- **下载 top_k**：`sharing_config.download_top_k`，默认 3
- **上传重试**：`sharing_config.max_upload_retries`，默认 3
- **QC score 阈值**：`ShareStager.qc_score_threshold`，默认 0.6
- **Jaccard 去重阈值**：`LocalFileBackend.dedup_jaccard_threshold`，默认 0.85