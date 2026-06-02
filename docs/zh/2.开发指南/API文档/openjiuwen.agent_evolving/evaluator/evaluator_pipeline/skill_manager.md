# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager` 模块提供技能的加载、保存、版本管理和进化追踪功能。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager.SkillManager

```
class SkillManager(config: PipelineConfig)
```

技能管理器类，负责技能的加载、保存、版本管理和进化追踪。

**参数：**

* **config**(PipelineConfig)：流水线配置对象。

### init_for_task(task_id: str) -> None

为任务初始化技能管理器。

**参数：**

* **task_id**(str)：任务 ID。

### get_skill_dir_path(skill_name: str, iteration: int | None) -> Path

获取技能目录路径。

**参数：**

* **skill_name**(str)：技能名称。
* **iteration**(int，可选)：迭代次数，为 `None` 时返回 latest 目录。

**返回：**

**Path**，技能目录路径。

**异常：**

* **RuntimeError**：skill_dir 未初始化。

### load_all_skills(verbose: bool) -> dict[str, str]

加载所有技能。

**参数：**

* **verbose**(bool，可选)：是否输出日志。默认值：`True`。

**返回：**

**dict[str, str]**，技能名称到内容的映射。

**异常：**

* **RuntimeError**：skill_dir 未初始化。

### save_all_skills(skills: dict[str, str], iteration: int, evolutions: dict[str, str] | None, evolution_files: dict[str, dict[str, str]] | None) -> list[Path]

保存所有技能。

**参数：**

* **skills**(dict[str, str])：技能名称到内容的映射。
* **iteration**(int)：当前迭代次数。
* **evolutions**(dict[str, str]，可选)：进化信息映射。默认值：`None`。
* **evolution_files**(dict[str, dict[str, str]]，可选)：进化文件映射。默认值：`None`。

**返回：**

**list[Path]**，保存的技能文件路径列表。

**异常：**

* **RuntimeError**：skill_dir 未初始化。

### async render_evolution_to_skill_md_for(skill_name: str) -> None

将进化指导注入到技能文件中。

**参数：**

* **skill_name**(str)：技能名称。

**异常：**

* **RuntimeError**：skill_dir 未初始化。

### staticmethod compute_skill_hash(content: str | None) -> str

计算技能内容的哈希值。

**参数：**

* **content**(str，可选)：技能内容。

**返回：**

**str**，16位哈希值。

### has_skill_changed(new_content: str) -> bool

判断技能是否发生变化。

**参数：**

* **new_content**(str)：新技能内容。

**返回：**

**bool**，技能是否变化。

### get_all_skill_names() -> list[str]

获取所有技能名称。

**返回：**

**list[str]**，技能名称列表。

---

## func openjiuwen.agent_evolving.evaluator.evaluator_pipeline.skill_manager.extract_specific_errors(test_output: str) -> dict[str, str]

从测试输出中提取具体错误信息。

**参数：**

* **test_output**(str)：测试输出内容。

**返回：**

**dict[str, str]**，测试名称到错误详情的映射。