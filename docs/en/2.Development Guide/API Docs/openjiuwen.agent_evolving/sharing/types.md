# openjiuwen.agent_evolving.sharing.types

Data structures for the experience sharing path. These wrapper objects are only used on the *sharing* side; the local `EvolutionRecord` schema is unchanged — cross-user metadata lives on the wrapper objects defined here, never inside `evolutions.json`.

---

## class openjiuwen.agent_evolving.sharing.types.SharingMeta

Per-experience sharing-side metadata.

* **skill_name**(str): Associated skill name.
* **skill_version**(str, optional): Skill version identifier. Default: `""`.
* **upload_trigger**(str, optional): Upload trigger method. Default: `"user_approval"`.
* **upload_at**(str, optional): Upload timestamp (ISO format). Default: current UTC time.
* **feedback_excerpt**(str, optional): Feedback excerpt snippet. Default: `None`.
* **source_user_id**(str, optional): Uploader user identifier. Default: `None`.
* **confidence**(float, optional): Experience confidence score. Default: `0.7`.
* **origin_bundle_id**(str, optional): Original bundle ID (for traceability). Default: `None`.

### to_dict() -> dict

Serializes sharing metadata to a dict; optional fields are emitted only when non-None.

### from_dict(data: dict) -> SharingMeta

Restores sharing metadata from a persisted dict; missing fields use compatible defaults.

---

## class openjiuwen.agent_evolving.sharing.types.SharedExperience

Sharing wrapper around a single `EvolutionRecord`. The underlying `EvolutionRecord` schema is untouched; all sharing-only metadata (keywords/summary/SharingMeta) live on this wrapper.

* **record**(EvolutionRecord): The wrapped local evolution record.
* **keywords**(List[str], optional): Keywords extracted from the optimizer output. Default: `[]`.
* **summary**(str, optional): One-sentence experience summary. Default: `""`.
* **sharing_meta**(SharingMeta, optional): Sharing-side metadata. Default: `None`.

### to_dict() -> dict

Serializes the shared experience to a dict, including record, keywords, summary, and sharing_meta.

### from_dict(data: dict) -> SharedExperience

Restores a shared experience from a persisted dict; sharing_meta is auto-deserialized when it is a dict.

---

## class openjiuwen.agent_evolving.sharing.types.SharedSkillBundle

The smallest unit a backend stores — a batch of experiences for one skill. Bundle is the basic granularity for backend storage and retrieval.

* **bundle_id**(str, optional): Bundle unique identifier, auto-generated with `sb_` prefix. Default: auto-generated.
* **skill_id**(str, optional): Associated skill stable ID (from SKILL.md frontmatter). Default: `""`.
* **skill_name**(str, optional): Skill name. Default: `""`.
* **skill_version**(str, optional): Skill version. Default: `""`.
* **keywords_aggregate**(List[str], optional): Aggregated keywords from all experiences. Default: `[]`.
* **summary_aggregate**(str, optional): Aggregated summary from all experiences. Default: `""`.
* **experiences**(List[SharedExperience], optional): List of shared experiences contained. Default: `[]`.
* **created_at**(str, optional): Creation timestamp (ISO format). Default: current UTC time.

### to_dict() -> dict

Serializes the bundle to a dict; each experience in the list is recursively serialized.

### from_dict(data: dict) -> SharedSkillBundle

Restores a bundle from a persisted dict; compatible with legacy `skill_content_hash` field as skill_id fallback.

### staticmethod make(skill_name, experiences, *, skill_version="", summary_aggregate="") -> SharedSkillBundle

Builds a bundle from an experience list, auto-aggregating keywords and summary.

**Parameters**:

* **skill_name**(str): Skill name.
* **experiences**(List[SharedExperience]): Shared experiences to pack.
* **skill_version**(str, optional): Skill version. Default: `""`.
* **summary_aggregate**(str, optional): Aggregated summary; auto-joined from each experience's summary when empty. Default: `""`.

**Returns**:

**SharedSkillBundle**, the constructed bundle object.

---

## class openjiuwen.agent_evolving.sharing.types.SkillPackageMeta

Hub metadata for the single immutable skill package under one `skill_id`.

* **skill_id**(str): Skill stable ID.
* **skill_name**(str, optional): Skill name. Default: `""`.
* **description**(str, optional): Skill description. Default: `""`.
* **uploaded_at**(str, optional): Upload timestamp (ISO format). Default: current UTC time.

### to_dict() -> dict

Serializes skill package metadata to a dict.

### from_dict(data: dict) -> SkillPackageMeta

Restores skill package metadata from a persisted dict.

---

## class openjiuwen.agent_evolving.sharing.types.SkillSearchResult

One row returned by hub keyword search.

* **skill_id**(str): Skill stable ID.
* **skill_name**(str, optional): Skill name. Default: `""`.
* **description**(str, optional): Skill description. Default: `""`.
* **experience_count**(int, optional): Number of experiences under this skill. Default: `0`.
* **keywords**(List[str], optional): Aggregated keywords. Default: `[]`.
* **score**(float, optional): Search relevance score. Default: `0.0`.

### to_dict() -> dict

Serializes the search result to a dict.

### from_dict(data: dict) -> SkillSearchResult

Restores a search result from a persisted dict.

---

## class openjiuwen.agent_evolving.sharing.types.QueryKeywords

Query-side keyword set used to retrieve relevant bundles from the hub.

* **keywords**(List[str], optional): Retrieval keyword list. Default: `[]`.
* **intent**(str, optional): Query intent description (≤40 chars). Default: `""`.
* **raw_excerpt**(str, optional): Raw conversation excerpt. Default: `""`.

### to_dict() -> dict

Serializes query keywords to a dict.

### from_dict(data: dict) -> QueryKeywords

Restores query keywords from a persisted dict.

---

## class openjiuwen.agent_evolving.sharing.types.UploadResult

Outcome of a backend bundle upload.

* **ok**(bool): Whether the upload succeeded.
* **bundle_id**(str, optional): Bundle ID on successful upload. Default: `""`.
* **reason**(str, optional): Failure reason description. Default: `""`.
* **retryable**(bool, optional): Whether the failure is retryable. Default: `False`.

---

## class openjiuwen.agent_evolving.sharing.types.StagingResult

Result of `ShareStager.screen_and_stage()`. Local persistence is the caller's responsibility (`SkillEvolutionRail`) — records passed into `screen_and_stage()` are never lost regardless of QC outcome.

* **staged_for_share**(List[SharedExperience], optional): Experiences that passed QC and were queued. Default: `[]`.
* **dropped_for_share**(List[Tuple[EvolutionRecord, str]], optional): Records rejected by QC with reasons. Default: `[]`.

### classmethod empty() -> StagingResult

Returns an empty staging result (no staged, no dropped).

### has_shareable -> bool

Whether there are shareable experiences available.