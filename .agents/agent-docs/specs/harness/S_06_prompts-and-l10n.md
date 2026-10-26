# S_06 Prompts 与 i18n

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/prompts/`（67 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 harness 的 prompt 子系统：语言解析、prompt 装配、sections、附件、清洗、
report。`prompts/` 67 文件是 DeepAgent 的"输入面"，只定义契约，具体文案在语言模板里。

具体覆盖：

- `prompts/__init__.py` 的导出面：`SUPPORTED_LANGUAGES` / `DEFAULT_LANGUAGE` /
  `resolve_language` / `resolve_mode` / `PromptMode` / `PromptSection` / `PromptReport` /
  `SystemPromptBuilder` / 附件族 / sanitize 族。
- `SystemPromptBuilder`（`prompts/builder.py`）：mode 过滤 + sections 装配。
- `prompts/sections/` 20 个 section 模块。
- 附件机制 `PromptAttachmentManager`（`prompts/prompt_attachment_manager.py`）。
- 清洗 `sanitize_path` / `sanitize_user_content`。

不在本规约范围内：
- 具体 section 的文案内容 —— 语言模板文件。
- `resolve_language` 的消费方（factory / rails）—— `S_01` / `S_04`。
- team 侧 prompt（`agent_teams/prompts/`）—— 另一子系统。

## 不变量

1. **语言解析唯一**：`resolve_language(config_language=None)` 优先级
   `config 参数 in SUPPORTED_LANGUAGES` > `AGENT_PROMPT_LANGUAGE 环境变量` > `DEFAULT_LANGUAGE`。
   `DEFAULT_LANGUAGE` / `SUPPORTED_LANGUAGES` 在 `prompts/builder.py` 定义。
2. **mode 解析唯一**：`resolve_mode(config_mode=None)` 从配置解析 `PromptMode`；
   非法值回退 `PromptMode.FULL`。`PromptMode` 值：`FULL` / `MINIMAL` / `NONE`。
3. **`SystemPromptBuilder` 是 prompt 装配唯一入口**：`build() -> str` 产出完整 system prompt；
   `build_report() -> PromptReport` 产出分节诊断（每节名称/字数/hash）；`_get_sections_for_build`
   按 mode 过滤。MINIMAL 模式只保留 `IDENTITY` / `SAFETY` 等白名单节（`_MINIMAL_SECTIONS`）。
4. **sections 是唯一内容单元**：`prompts/sections/` 每模块定义一个 section（identity /
   safety / skills / memory / goal / todo / task / workspace / agent_mode / subagent_tools /
   session_tools / task_completion / progressive_tool_rail / heartbeat / coding_memory /
   compression_recall / external_memory / context / offload / reload）。
   新增 section = 新模块 + 注册进 builder。
5. **附件机制契约**：`PromptAttachment`（pydantic）是附件单元；`PromptAttachmentKind` 区分类型；
   `PromptAttachmentManager.bind_context(ctx)` 返回 `PromptAttachmentContextWriter`，按 session
   + section 落库 `add_section` / `clear_section`；`hash_rendered` / `hash_prompt_attachment`
   是内容指纹（sha256）。
6. **清洗唯一**：`sanitize_path(path)` 清洗路径字符串；`sanitize_user_content(content, max_len=2000)`
   截断用户内容。任何透传给 LLM 的用户内容必须先过这两道。
7. **`prompts/__init__.py` 的 `__all__` 是契约**；`from openjiuwen.harness.prompts import sections`
   暴露子包供引用（`# noqa: F401` 刻意）。

## 接口契约

```python
def resolve_language(config_language: Optional[str] = None) -> str
def resolve_mode(config_mode: Optional[str] = None) -> PromptMode

class PromptMode(str, Enum):
    FULL = "full"
    MINIMAL = "minimal"
    NONE = "none"

class SystemPromptBuilder(BaseSystemPromptBuilder):
    def build(self) -> str
    def build_report(self) -> PromptReport

def sanitize_path(path: str) -> str
def sanitize_user_content(content: str, max_len: int = 2000) -> str

class PromptAttachmentKind(str, Enum): ...
class PromptAttachment(BaseModel): ...
class PromptAttachmentUpdate(BaseModel): ...
class PromptAttachmentManager:
    def bind_context(self, ctx: Any) -> PromptAttachmentContextWriter
    async def add_section(self, ...) -> None
    async def clear_section(self, *, session_id: str, section: str) -> int
    async def get_by_id(self, prompt_attachment_id: str, *, session_id: str | None = None) -> PromptAttachment | None
    async def update_by_id(self, prompt_attachment_id: str, update: PromptAttachmentUpdate) -> PromptAttachment

class PromptAttachmentContextWriter:
    async def add_section(self, ...) -> None
    async def add_from_prompt_section(self, ...) -> None
    async def clear_section(self, section: str) -> int
```

错误 / 返回语义：

- `resolve_language` 未知语言 → 回退 `DEFAULT_LANGUAGE`（不抛）。
- `resolve_mode` 非法 mode → `PromptMode.FULL`（不抛）。
- `PromptAttachmentManager.get_by_id` 未找到 → `None`；`update_by_id` 找不到 → 抛。
- `PromptAttachmentContextWriter.add_section` 无 session → 抛（`_require_session_id`）。

## 数据结构

### PromptSection（核心字段）

| 字段 | 语义 |
|---|---|
| `name` | section 名（`SectionName` 枚举，如 `IDENTITY` / `SAFETY`） |
| `content` | 渲染后文本 |
| `language` | 语言标签 |
| priority / order | 装配顺序（builder 侧） |

### prompt 附件生命周期

| 阶段 | 组件 | 说明 |
|---|---|---|
| 创建 | `PromptAttachment` + `PromptAttachmentUpdate` | 附件负载 + 更新描述 |
| 指纹 | `hash_prompt_attachment` / `hash_rendered` | sha256 内容指纹，供去重/变更检测 |
| 落库 | `PromptAttachmentManager.add_section` / `clear_section` | 按 session_id + section 组织 |
| 读取 | `get_by_id` / `update_by_id` | 单附件读/改 |
| 注入 | `PromptAttachmentContextWriter` | 绑定 ctx 后直接写 section |

## 与其它 spec 的关系

- 工具描述模板归属 `prompts/tools/` —— `S_05`；本节只锚定装配机制。
- rail 注入 / 移除 section（`heartbeat_rail` 等）消费 `SystemPromptBuilder` —— `S_04`。
- `resolve_language` 在 `factory.py` / `extension_resolver` 中消费 —— `S_01` / `S_13`。
- session 工具 section（`session_tools.py`）与 `SessionToolkit` 配套 —— `S_05`。
- 附件按 session 落库的 session 语义 —— `S_02`。
