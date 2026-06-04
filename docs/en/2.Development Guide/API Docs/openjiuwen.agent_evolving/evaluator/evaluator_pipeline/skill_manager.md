# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager

The `openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager` module provides skill loading, saving, version management, and evolution tracking functionality.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager.SkillManager

```
class SkillManager(config: PipelineConfig)
```

Skill manager class responsible for loading, saving, version management, and evolution tracking of skills.

**Parameters:**

* **config**(PipelineConfig): Pipeline configuration object.

### init_for_task(task_id: str) -> None

Initialize skill manager for task.

**Parameters:**

* **task_id**(str): Task ID.

### get_skill_dir_path(skill_name: str, iteration: int | None) -> Path

Get skill directory path.

**Parameters:**

* **skill_name**(str): Skill name.
* **iteration**(int, optional): Iteration number, returns latest directory when `None`.

**Returns:**

**Path** - skill directory path.

**Exceptions:**

* **RuntimeError**: skill_dir not initialized.

### load_all_skills(verbose: bool) -> dict[str, str]

Load all skills.

**Parameters:**

* **verbose**(bool, optional): Whether to output logs. Default: `True`.

**Returns:**

**dict[str, str]** - mapping of skill names to content.

**Exceptions:**

* **RuntimeError**: skill_dir not initialized.

### save_all_skills(skills: dict[str, str], iteration: int, evolutions: dict[str, str] | None, evolution_files: dict[str, dict[str, str]] | None) -> list[Path]

Save all skills.

**Parameters:**

* **skills**(dict[str, str]): Mapping of skill names to content.
* **iteration**(int): Current iteration number.
* **evolutions**(dict[str, str], optional): Evolution information mapping. Default: `None`.
* **evolution_files**(dict[str, dict[str, str]], optional): Evolution file mapping. Default: `None`.

**Returns:**

**list[Path]** - list of saved skill file paths.

**Exceptions:**

* **RuntimeError**: skill_dir not initialized.

### async render_evolution_to_skill_md_for(skill_name: str) -> None

Inject evolution guidance into skill file.

**Parameters:**

* **skill_name**(str): Skill name.

**Exceptions:**

* **RuntimeError**: skill_dir not initialized.

### staticmethod compute_skill_hash(content: str | None) -> str

Compute hash of skill content.

**Parameters:**

* **content**(str, optional): Skill content.

**Returns:**

**str** - 16-character hash value.

### has_skill_changed(new_content: str) -> bool

Check if skill has changed.

**Parameters:**

* **new_content**(str): New skill content.

**Returns:**

**bool** - whether skill changed.

### get_all_skill_names() -> list[str]

Get all skill names.

**Returns:**

**list[str]** - list of skill names.

---

## func openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager.extract_specific_errors(test_output: str) -> dict[str, str]

Extract specific error information from test output.

**Parameters:**

* **test_output**(str): Test output content.

**Returns:**

**dict[str, str]** - mapping of test names to error details.