# openjiuwen.agent_evolving.sharing.types

经验共享路径的专用数据结构。这些包装对象仅用于共享侧，本地 `EvolutionRecord` schema 不变，跨用户元数据只存在于 wrapper 对象上，不侵入 `evolutions.json`。

---

## class openjiuwen.agent_evolving.sharing.types.SharingMeta

每条经验的共享侧元数据。

* **skill_name**(str)：关联的 Skill 名称。
* **skill_version**(str，可选)：Skill 版本标识。默认值：`""`。
* **upload_trigger**(str，可选)：上传触发方式。默认值：`"user_approval"`。
* **upload_at**(str，可选)：上传时间（ISO 格式）。默认值：当前 UTC 时间。
* **feedback_excerpt**(str，可选)：反馈摘要片段。默认值：`None`。
* **source_user_id**(str，可选)：上传用户标识。默认值：`None`。
* **confidence**(float，可选)：经验置信度分值。默认值：`0.7`。
* **origin_bundle_id**(str，可选)：原始 bundle ID（用于溯源）。默认值：`None`。

### to_dict() -> dict

将共享元数据序列化为字典，可选字段仅在非 None 时输出。

### from_dict(data: dict) -> SharingMeta

从持久化字典恢复共享元数据，缺失字段使用兼容默认值。

---

## class openjiuwen.agent_evolving.sharing.types.SharedExperience

对单条 `EvolutionRecord` 的共享包装。底层 `EvolutionRecord` schema 不变，所有共享专属元数据（keywords/summary/SharingMeta）均存于此 wrapper。

* **record**(EvolutionRecord)：被包装的本地演进记录。
* **keywords**(List[str]，可选)：从优化器输出中提取的关键词列表。默认值：`[]`。
* **summary**(str，可选)：经验的一句话摘要。默认值：`""`。
* **sharing_meta**(SharingMeta，可选)：共享侧元数据。默认值：`None`。

### to_dict() -> dict

将共享经验序列化为字典，包含 record、keywords、summary 及 sharing_meta。

### from_dict(data: dict) -> SharedExperience

从持久化字典恢复共享经验，sharing_meta 为 dict 时自动反序列化为 SharingMeta。

---

## class openjiuwen.agent_evolving.sharing.types.SharedSkillBundle

Hub 存储的最小单元——一个 Skill 的一批经验。bundle 是后端存储和检索的基本粒度。

* **bundle_id**(str，可选)：bundle 唯一标识，自动生成 `sb_` 前缀 ID。默认值：自动生成。
* **skill_id**(str，可选)：关联的 Skill 稳定 ID（来自 SKILL.md frontmatter）。默认值：`""`。
* **skill_name**(str，可选)：Skill 名称。默认值：`""`。
* **skill_version**(str，可选)：Skill 版本。默认值：`""`。
* **keywords_aggregate**(List[str]，可选)：聚合所有经验的关键词。默认值：`[]`。
* **summary_aggregate**(str，可选)：聚合所有经验的摘要。默认值：`""`。
* **experiences**(List[SharedExperience]，可选)：包含的共享经验列表。默认值：`[]`。
* **created_at**(str，可选)：创建时间（ISO 格式）。默认值：当前 UTC 时间。

### to_dict() -> dict

将 bundle 序列化为字典，experiences 列表中每条经验也会递归序列化。

### from_dict(data: dict) -> SharedSkillBundle

从持久化字典恢复 bundle，兼容旧版 `skill_content_hash` 字段作为 skill_id 回退。

### staticmethod make(skill_name, experiences, *, skill_version="", summary_aggregate="") -> SharedSkillBundle

从经验列表构建 bundle，自动聚合关键词与摘要。

**参数**：

* **skill_name**(str)：Skill 名称。
* **experiences**(List[SharedExperience])：待打包的共享经验列表。
* **skill_version**(str，可选)：Skill 版本。默认值：`""`。
* **summary_aggregate**(str，可选)：聚合摘要；为空时自动拼接各经验 summary。默认值：`""`。

**返回**：

**SharedSkillBundle**，构建完成的 bundle 对象。

---

## class openjiuwen.agent_evolving.sharing.types.SkillPackageMeta

Hub 上 Skill 包的元数据。每个 skill_id 下只有一个不可变的 skill package。

* **skill_id**(str)：Skill 稳定 ID。
* **skill_name**(str，可选)：Skill 名称。默认值：`""`。
* **description**(str，可选)：Skill 描述。默认值：`""`。
* **uploaded_at**(str，可选)：上传时间（ISO 格式）。默认值：当前 UTC 时间。

### to_dict() -> dict

将 Skill 包元数据序列化为字典。

### from_dict(data: dict) -> SkillPackageMeta

从持久化字典恢复 Skill 包元数据。

---

## class openjiuwen.agent_evolving.sharing.types.SkillSearchResult

Hub 关键词搜索返回的一行结果。

* **skill_id**(str)：Skill 稳定 ID。
* **skill_name**(str，可选)：Skill 名称。默认值：`""`。
* **description**(str，可选)：Skill 描述。默认值：`""`。
* **experience_count**(int，可选)：该 Skill 下的经验数量。默认值：`0`。
* **keywords**(List[str]，可选)：聚合关键词。默认值：`[]`。
* **score**(float，可选)：搜索相关度得分。默认值：`0.0`。

### to_dict() -> dict

将搜索结果序列化为字典。

### from_dict(data: dict) -> SkillSearchResult

从持久化字典恢复搜索结果。

---

## class openjiuwen.agent_evolving.sharing.types.QueryKeywords

下载侧查询关键词集，用于从 Hub 检索相关经验 bundle。

* **keywords**(List[str]，可选)：检索关键词列表。默认值：`[]`。
* **intent**(str，可选)：查询意图描述（≤40 字）。默认值：`""`。
* **raw_excerpt**(str，可选)：原始对话摘录。默认值：`""`。

### to_dict() -> dict

将查询关键词序列化为字典。

### from_dict(data: dict) -> QueryKeywords

从持久化字典恢复查询关键词。

---

## class openjiuwen.agent_evolving.sharing.types.UploadResult

后端 bundle 上传的结果。

* **ok**(bool)：上传是否成功。
* **bundle_id**(str，可选)：上传成功时的 bundle ID。默认值：`""`。
* **reason**(str，可选)：失败原因描述。默认值：`""`。
* **retryable**(bool，可选)：失败是否可重试。默认值：`False`。

---

## class openjiuwen.agent_evolving.sharing.types.StagingResult

`ShareStager.screen_and_stage()` 的筛选结果。本地持久化由调用方（`SkillEvolutionRail`）负责，传入的记录不会因 QC 拒绝而丢失。

* **staged_for_share**(List[SharedExperience]，可选)：通过 QC 筛选、已入队的共享经验。默认值：`[]`。
* **dropped_for_share**(List[Tuple[EvolutionRecord, str]]，可选)：被 QC 拒绝的记录及原因。默认值：`[]`。

### classmethod empty() -> StagingResult

返回空的筛选结果（无通过、无拒绝）。

### has_shareable -> bool

是否存在可共享的经验。