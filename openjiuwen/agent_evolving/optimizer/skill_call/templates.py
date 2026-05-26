# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt templates for skill experience optimization."""

from typing import Dict

SKILL_EXPERIENCE_GENERATE_PROMPT_CN = """\
你是一个 Skill 优化专家。根据对话中发现的问题信号和对话历史，为 Skill 生成演进经验。

## 输出格式（最重要）
你的回复必须是一个合法的 JSON 数组，不要任何其他内容。
- 不要使用 Markdown 代码块（```）包裹
- 不要添加解释性文字
- JSON 转义规则：字符串内换行用 \\n，引号用 \\"，制表符用 \\t
- 数组内每个对象必须包含 action 字段（"append" 或 "skip"）

## 角色约束
演进经验必须遵从 Agent 的角色能力和主要任务目标：
- 经验应当增强角色核心能力，而非引入角色职责之外的行为
- 从角色的主要任务出发，生成有价值的演进经验
- 避免生成与角色无关或超出角色能力范围的建议

## 输入信息

### 当前 Skill 内容
{skill_content}

### 预检测信号（规则引擎自动提取）
{signals_json}

### 对话历史
{conversation_snippet}

### 已有 description 经验
{existing_desc_summary}

### 已有 body 经验
{existing_body_summary}

### 用户主动描述的优化方向（可选）
{user_query}

## 经验来源

经验来自两个渠道，都要处理：

**渠道 A — 预检测信号**：上方「预检测信号」中列出的条目，由规则引擎自动从对话中提取，可能包含误报。

**渠道 B — 对话历史直接分析**：直接审视「对话历史」，发现规则引擎未捕获的有价值经验，包括但不限于：
- Agent 经过多次尝试/重试才成功的 workaround（说明 Skill 缺少相关指导）
- 用户含蓄的纠正或补充说明（未使用"错了""不对"等显式关键词）
- 低效的工具调用模式（如多余步骤、错误的调用顺序）
- Agent 遗漏的关键步骤（用户不得不手动补充）
- 需要特殊处理的边界情况（Skill 中未覆盖的场景）

**渠道 C — 脚本工件提取**：检查「预检测信号」中 type 为 "script_artifact" 的条目。这些是 Agent 在对话中生成并成功执行的脚本代码。评估其复用价值：
- 高复用价值：图表生成（matplotlib/plotly）、图标/配图生成（PIL）、数据处理（JSON/CSV/Excel 转换）、自动化脚本（批量操作、格式化等）
- 排除标准：仅包含硬编码特定数据的一次性脚本（纯硬编码特定内容才排除）
- 脚本类经验使用 target="script"，section="Scripts"

如果对话历史中没有额外发现，不需要强制生成；如果有发现，与预检测信号的经验一起输出。

## 数量限制

最终输出的有效经验（action 为 append 的条目）：**文本经验不超过 2 条，脚本经验不超过 1 条**，独立计数互不影响。
如果候选经验超过限制，按以下优先级保留最重要的，其余标记为 skip：
1. 导致任务失败或产出错误结果的问题 > 导致效率低下但最终成功的问题
2. 高频/可复现的模式 > 单次偶发现象
3. 渠道 A/B/C 的发现同等对待，仅按影响程度排序

## 决策流程（对每条潜在经验按顺序执行）

### 第一步：相关性判断
判断该经验是否与 Skill 本身相关：
- 相关：问题由 Skill 的指令、脚本、示例或排查逻辑导致 -> 继续第二步
- 不相关：问题由外部因素导致（网络、环境、权限、第三方服务等）-> 输出 {{"action": "skip", "skip_reason": "irrelevant"}}

### 第二步：去重判断
对比已有演进经验（description 和 body 两个列表）：
- 实质相同：与某条已有记录内容重复 -> 输出 {{"action": "skip", "skip_reason": "duplicate"}}
- 高度相似但有增量：与某条已有记录相关但有新信息 -> 输出合并后的完整内容，并设置 "merge_target" 为目标记录 id
- 全新：与已有记录无关 -> 继续第三步

### 第三步：优先级筛选与生成
将所有通过前两步的候选经验按优先级排序，仅为排名前 2 的候选生成内容，其余输出 {{"action": "skip", "skip_reason": "low_priority"}}。
确定经验归属层（target）和章节（section），然后生成内容。

**target 判断（三选一）：**
- **description**（描述/元数据层）：涉及 Skill 适用场景判断错误、描述不准确、缺少关键词导致未被选中或误选
- **body**（正文/指令层）：涉及执行步骤、工具调用错误、操作流程、排查逻辑
- **script**（脚本工件层）：Agent 生成并成功执行的可复用脚本代码（渠道 C）

**section 选择参考：**
- execution_failure / workaround 类：通常归入 Troubleshooting
- user_correction / 流程偏差类：通常归入 Instructions 或 Examples
- script_artifact 类：归入 Scripts
- collaboration_send / collaboration_claim / collaboration_view / collaboration_receive / collaboration_failure 类：归入 Collaboration（记录 AgentSkill 作为 TeamSkill 成员时的协作经验，如发送消息、认领任务、接收上下文等）

## 内容生成规范
1. 语言一致：输出语言必须与 Skill 完全一致（中文 Skill 输出中文，英文 Skill 输出英文）
2. 标题层级：使用与 Skill 相同的标题层级（##、### 等）
3. 每条记录：1 个标题 + 2-3 个无序列表分点（- 或 *），禁止子层级
4. 每条记录只涉及一个 section 类型，不混合
5. 提取可复用的通用规则，非临时补丁（好："遇到 X 错误时，先检查 Y 再执行 Z"；差："某用户某次提到某问题"）
6. 内容必须是 Skill 中未提及的新知识，精炼简洁
7. 多个发现指向同一问题时合并为一条；不同问题分别生成
8. 文本经验（action 为 append，target 为 description/body）最多 2 条；脚本经验（target 为 script）最多 1 条
9. 脚本经验的 content 字段直接放完整脚本源码，同时填写 script_filename、script_language、script_purpose
10. 每条 append 经验必须填写 summary：一句话说明“何时适用 + 应做什么/避免什么”，不要换行、表格或代码块
11. 每条 append 经验必须填写 keywords：10-20 个检索关键词，优先代码标识符/英文报错关键字，可附带中文术语以提升跨用户召回

## 输出格式
只输出以下 JSON 数组，不要其他内容（即使只有一条，也必须用数组包裹）：
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 action 为 skip 时填写，否则为 null）",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts | Collaboration",
    "summary": "一句话经验摘要（仅 action 为 append 时填写，否则为 null）",
    "keywords": ["10-20 个关键词（仅 action 为 append 时填写）"],
    "content": "Markdown 内容或脚本源码（仅 action 为 append 时填写）",
    "merge_target": "ev_xxxxxxxx 或 null",
    "script_filename": "文件名（仅 target 为 script 时填写，如 generate_chart.py）",
    "script_language": "语言标识（仅 target 为 script 时填写，如 python）",
    "script_purpose": "用途说明（仅 target 为 script 时填写）"
  }}
]"""

SKILL_EXPERIENCE_GENERATE_PROMPT_EN = """\
You are a Skill optimization expert. Based on problem signals discovered in the conversation and the conversation history, generate evolution experiences for the Skill.

## Output Format (MOST IMPORTANT)
Your response must be a valid JSON array, nothing else.
- Do NOT wrap in Markdown code blocks (```)
- Do NOT add explanatory text
- JSON escaping: use \\n for newlines, \\" for quotes, \\t for tabs inside strings
- Every object in the array must include an "action" field ("append" or "skip")

## Role Constraints
Evolution experiences must respect the Agent's role capabilities and primary objectives:
- Experiences should enhance core role capabilities, not introduce behaviors outside the role's responsibilities
- Generate valuable experiences from the role's primary mission perspective
- Avoid generating suggestions unrelated to or beyond the role's capabilities

## Input Information

### Current Skill Content
{skill_content}

### Pre-detected Signals (automatically extracted by the rule engine)
{signals_json}

### Conversation History
{conversation_snippet}

### Existing description experiences
{existing_desc_summary}

### Existing body experiences
{existing_body_summary}

### User-specified optimization direction (optional)
{user_query}

## Experience Sources

Experiences come from two channels, both must be processed:

**Channel A — Pre-detected Signals**: The entries listed in the "Pre-detected Signals" section above, automatically extracted from the conversation by the rule engine. May contain false positives.

**Channel B — Direct Conversation History Analysis**: Directly examine the "Conversation History" to discover valuable experiences not captured by the rule engine, including but not limited to:
- Workarounds where the Agent succeeded only after multiple attempts/retries (indicating the Skill lacks relevant guidance)
- Implicit corrections or supplementary explanations from the user (without using explicit keywords like "wrong" or "incorrect")
- Inefficient tool invocation patterns (e.g., redundant steps, incorrect invocation order)
- Critical steps missed by the Agent (where the user had to manually fill in)
- Edge cases requiring special handling (scenarios not covered by the Skill)

**Channel C — Script Artifact Extraction**: Check the "Pre-detected Signals" for entries with type "script_artifact". These are scripts that the Agent generated and successfully executed during the conversation. Evaluate their reuse value:
- High reuse value: chart generation (matplotlib/plotly), icon/image generation (PIL), data processing (JSON/CSV/Excel conversion), automation scripts (batch operations, formatting, etc.)
- Exclusion criteria: one-off scripts that only contain hardcoded specific data
- Script experiences use target="script", section="Scripts"

If no additional findings exist in the conversation history, do not force generation; if findings exist, output them together with the pre-detected signal experiences.

## Quantity Limit

The final output of valid experiences (entries with action "append"): **text experiences must not exceed 2, script experiences must not exceed 1**, counted independently.
If candidate experiences exceed the limit, retain the most important ones by the following priority and mark the rest as skip:
1. Issues causing task failure or incorrect results > Issues causing inefficiency but eventual success
2. High-frequency / reproducible patterns > One-off occurrences
3. Findings from Channel A/B/C are treated equally, sorted only by impact level

## Decision Flow (execute sequentially for each potential experience)

### Step 1: Relevance Check
Determine whether the experience is related to the Skill itself:
- Relevant: The issue is caused by the Skill's instructions, scripts, examples, or troubleshooting logic -> proceed to Step 2
- Irrelevant: The issue is caused by external factors (network, environment, permissions, third-party services, etc.) -> output {{"action": "skip", "skip_reason": "irrelevant"}}

### Step 2: Deduplication Check
Compare against existing evolution experiences (both description and body lists):
- Essentially identical: Duplicates an existing record -> output {{"action": "skip", "skip_reason": "duplicate"}}
- Highly similar but with incremental value: Related to an existing record but contains new information -> output the merged complete content and set "merge_target" to the target record id
- Entirely new: Unrelated to existing records -> proceed to Step 3

### Step 3: Priority Filtering and Generation
Sort all candidates that passed the first two steps by priority, generate content only for the top 2, and output {{"action": "skip", "skip_reason": "low_priority"}} for the rest.
Determine the experience's target layer (target) and section (section), then generate the content.

**target selection (choose one):**
- **description** (metadata layer): Involves incorrect Skill applicability judgment, inaccurate description, missing keywords causing the Skill to be unselected or incorrectly selected
- **body** (instruction layer): Involves execution steps, tool invocation errors, operational procedures, troubleshooting logic
- **script** (script artifact layer): Reusable scripts that the Agent generated and successfully executed (Channel C)

**section selection reference:**
- execution_failure / workaround types: Usually belong to Troubleshooting
- user_correction / process deviation types: Usually belong to Instructions or Examples
- script_artifact types: Belong to Scripts
- collaboration_send / collaboration_claim / collaboration_view / collaboration_receive / collaboration_failure types:
  Belong to Collaboration (records AgentSkill collaboration experiences when acting as TeamSkill member,
  e.g., sending messages, claiming tasks, receiving context, etc.)

## Content Generation Guidelines
1. Language consistency: Output language must match the Skill exactly (Chinese Skill outputs Chinese, English Skill outputs English)
2. Heading levels: Use the same heading levels as the Skill (##, ###, etc.)
3. Each record: 1 heading + 2-3 unordered list items (- or *), no sub-levels allowed
4. Each record covers only one section type, no mixing
5. Extract reusable general rules, not temporary patches (good: "When encountering error X, first check Y then execute Z"; bad: "A certain user once mentioned a certain issue")
6. Content must be new knowledge not already mentioned in the Skill, concise and refined
7. When multiple findings point to the same issue, merge into one entry; generate separately for different issues
8. Text experiences (action "append", target description/body): at most 2; script experiences (target script): at most 1
9. For script experiences, put the full script source code in the content field, and fill in script_filename, script_language, script_purpose
10. Every append experience must include summary: one sentence describing when it applies and what to do or avoid; no newlines, tables, or code blocks
11. Every append experience must include keywords: 10-20 retrieval keywords; prefer code identifiers / English error keywords; you may add matching Chinese terms for cross-user recall

## Output Format
Output only the following JSON array, nothing else (even if there is only one entry, it must be wrapped in an array):
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority (fill only when action is skip, otherwise null)",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts | Collaboration",
    "summary": "one-sentence experience summary (only when action is append, otherwise null)",
    "keywords": ["10-20 keywords (only when action is append)"],
    "content": "Markdown content or script source code (fill only when action is append)",
    "merge_target": "ev_xxxxxxxx or null",
    "script_filename": "filename (only when target is script, e.g. generate_chart.py)",
    "script_language": "language identifier (only when target is script, e.g. python)",
    "script_purpose": "purpose description (only when target is script)"
  }}
]"""

SKILL_EXPERIENCE_GENERATE_PROMPT: Dict[str, str] = {
    "cn": SKILL_EXPERIENCE_GENERATE_PROMPT_CN,
    "en": SKILL_EXPERIENCE_GENERATE_PROMPT_EN,
}


JSON_FIX_PROMPT = """\
你上次的输出不是合法 JSON，请修复并重新输出。
只输出修复后的 JSON 数组，不要任何解释文字。

## 解析错误
{parse_error}

## 目标格式
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 skip 时填写，否则为 null）",
    "target": "description | body | script",
    "section": "Instructions | Examples | Troubleshooting | Scripts",
    "summary": "一句话经验摘要或 null",
    "content": "Markdown 内容（注意 JSON 转义：换行用 \\\\n，引号用 \\\\"）",
    "merge_target": "ev_xxxxxxxx 或 null",
    "script_filename": "文件名或 null",
    "script_language": "语言标识或 null",
    "script_purpose": "用途说明或 null"
  }}
]

## 原始输出（请从中提取并修复）
{broken_output}"""

JSON_FIX_PROMPT_STRICT = """\
你的 JSON 输出多次解析失败。请完全重新生成。

## 解析错误
{parse_error}

## 上次输出预览
{broken_preview}

## 严格要求
1. 只输出一个 JSON 数组，以 [ 开头，以 ] 结尾
2. 不要任何解释文字、不要 Markdown 代码块
3. 所有字符串内的换行必须写成 \\n
4. 所有字符串内的引号必须写成 \\"
5. 不要用单引号，只用双引号

## 正确格式示例
[
  {{"action":"append","target":"body","section":"Troubleshooting","summary":"遇到 X 错误时先检查 Y 再执行 Z","content":"## 标题\\n- 要点1\\n- 要点2","merge_target":null}},
  {{"action":"skip","skip_reason":"irrelevant","target":null,"section":null,"summary":null,"content":null,"merge_target":null}}
]
"""

USER_PATCH_PROMPT_CN = """\
根据用户的改进意见，为团队技能生成演进 patch。

当前团队技能：
- 名称：{skill_name}
- 描述：{description}
- 角色：{roles_summary}
- 工作流：{workflow_summary}

## 当前 Team Skill 正文
{skill_content}

## 已有演进经验摘要
{existing_evolutions}

用户意见：{user_intent}

## 分析目标
你要判断：用户意见是否真的暴露了当前 team skill 在协作设计上的缺口。
只有当意见指向 team skill 本身可沉淀、可复用的协作规则时，才应该生成 patch。

## 先做判断，再输出
在心里按以下顺序判断，但最终只输出 JSON，不要输出分析过程：
1. 相关性判断：该意见是否与 team skill 本身有关，而不是外部环境、权限、网络、模型偶发波动？
2. 重复判断：该意见是否只是重复当前技能名称、描述、角色、工作流或“已有演进经验摘要”中已经表达的内容？
3. 价值判断：该意见是否能沉淀成未来可复用的协作规则，而不是一次性偏好或临时口径？
4. 归类判断：如果值得沉淀，应该落到哪个 section，且只能落一个。

如果不满足以上条件，请不要生成 patch，输出 need_patch=false；不要把用户原话简单改写后原样塞回。

## Team Skill 专属分析维度
优先从以下维度理解用户意见：
- Roles：角色是否缺失、过多、职责重叠、职责边界不清
- Collaboration：角色间是否缺少交接、消息同步、上下文透传、完成信号
- Workflow：任务依赖是否不清晰，是否把串行任务误并行，或把可并行任务误串行
- Constraints：是否缺少时限、轮次上限、质量门、失败回退条件
- Troubleshooting：成员失败、卡住、产出不合格时是否缺少恢复路径
- Instructions / Examples：是否缺少关键指引或典型协作示例

请分析用户意见属于以下哪类演进：
- Roles：角色增删或数量调整
- Constraints：新增或修改约束
- Collaboration：角色间协作经验
- Instructions：角色职责或任务指引
- Examples：协作流程示例
- Troubleshooting：问题排查

## section 选择参考
- Roles：角色数量、职责边界、所有权归属变化
- Collaboration：角色交接、消息同步、上下文透传、完成信号
- Workflow：任务顺序、依赖关系、并行/串行拆分
- Constraints：时限、轮次上限、质量门、回退条件
- Instructions：某个角色的操作指引或执行规则
- Examples：典型协作案例或推荐协作范式
- Troubleshooting：成员失败、卡住、产出异常时的恢复路径

## 内容要求
生成的 patch 必须满足：
- 只生成一条 patch，只落一个 section
- 内容必须是新规则、新边界或新协作约定，不能重复现有技能显式已有内容
- 内容必须具体、可执行、可复用，避免“加强协作”“优化流程”这类空话
- 优先写成 leader 或成员可直接遵守的规则、顺序、交接条件、失败恢复条件
- 使用 Markdown，推荐为“一个小标题 + 2-3 个 bullet”，不要写多层嵌套

生成 patch，包含：
- section: 上述之一
- action: append
- summary: 一句话摘要，说明协作场景和推荐做法
- content: 具体的演进内容

输出格式：
```json
{{
  "need_patch": true/false,
  "section": "章节名；need_patch=false 时可为 null 或空字符串",
  "action": "append | skip",
  "summary": "一句话经验摘要；need_patch=false 时为空字符串",
  "content": "Markdown 格式的经验内容；need_patch=false 时为空字符串",
  "reason": "new_learning | duplicate | irrelevant | low_value"
}}
```

只输出 JSON。"""

USER_PATCH_PROMPT_EN = """\
Based on the user's improvement suggestions, generate an evolution patch for the team skill.

Current team skill:
- Name: {skill_name}
- Description: {description}
- Roles: {roles_summary}
- Workflow: {workflow_summary}

## Current Team Skill Body
{skill_content}

## Existing evolution summary
{existing_evolutions}

User suggestion: {user_intent}

## Goal
Determine whether the user's suggestion reveals a real, reusable gap in the current team skill design.
Only generate a patch when the suggestion can be distilled into a reusable collaboration rule for future executions.

## Decision process
Reason internally in this order, but output JSON only:
1. Relevance: Is this about the team skill itself rather than environment, permissions, network, or random model instability?
2. Duplication: Is this already clearly covered by the current name, description, roles, workflow, or the existing evolution summary?
3. Reuse value: Can this be turned into a reusable collaboration rule rather than a one-off preference?
4. Section fit: If worth capturing, which single section should own it?

If the suggestion is not relevant, is already covered, or is too low-value, output need_patch=false instead of forcing a patch.
Do not simply restate the user's words. Convert the suggestion into a compact rule only when it adds new operational value.

## Team-skill-specific dimensions
Prioritize these dimensions when interpreting the suggestion:
- Roles: missing roles, too many roles, overlapping responsibilities, unclear ownership boundaries
- Collaboration: missing handoffs, message synchronization, context transfer, completion signals
- Workflow: unclear task dependencies, work that should be sequential but is parallelized, or parallel work forced into sequence
- Constraints: missing time limits, round limits, quality gates, fallback conditions
- Troubleshooting: missing recovery paths when a member fails, stalls, or produces low-quality output
- Instructions / Examples: missing critical guidance or collaboration examples

Please classify the user feedback into one of these evolution categories:
- Roles: role addition/removal or count adjustment
- Constraints: new or modified constraints
- Collaboration: inter-role collaboration experience
- Instructions: role responsibilities or task guidance
- Examples: collaboration workflow examples
- Troubleshooting: problem resolution

## Section mapping guide
- Roles: role count, ownership boundaries, or responsibility changes
- Collaboration: handoffs, message synchronization, context transfer, completion signals
- Workflow: task ordering, dependency structure, parallel vs sequential execution
- Constraints: time limits, round limits, quality gates, fallback conditions
- Instructions: operating rules for a specific role
- Examples: representative collaboration patterns or worked examples
- Troubleshooting: recovery paths when a member fails, stalls, or produces invalid output

## Content requirements
The patch must satisfy all of the following:
- Generate exactly one patch and place it in exactly one section
- Add a new rule, boundary, or collaboration convention rather than repeating existing skill content
- Be specific, actionable, and reusable; avoid vague statements like "improve collaboration" or "optimize workflow"
- Prefer rules that a leader or member can directly follow: ordering, handoff criteria, escalation, fallback, or completion conditions
- Use Markdown, ideally one short heading plus 2-3 flat bullet points

Generate a patch with:
- section: one of the above
- action: append
- summary: one sentence describing the collaboration scenario and recommended practice
- content: specific evolution content in Markdown

Output format:
```json
{{
  "need_patch": true/false,
  "section": "section name; null or empty string when need_patch=false",
  "action": "append | skip",
  "summary": "one-sentence experience summary; empty string when need_patch=false",
  "content": "Markdown formatted experience content; empty string when need_patch=false",
  "reason": "new_learning | duplicate | irrelevant | low_value"
}}
```

Output JSON only."""

USER_PATCH_PROMPT = {"cn": USER_PATCH_PROMPT_CN, "en": USER_PATCH_PROMPT_EN}


TRAJECTORY_PATCH_PROMPT_CN = """\
分析以下执行轨迹，判断团队技能是否需要演进。

当前团队技能：{skill_content}
已有演进经验摘要：{existing_evolutions}
执行轨迹：{trajectory_summary}
轨迹分析发现的不足：{trajectory_issues}

## 决策原则
- 多数情况下不需要 patch（need_patch=false）
- 只有当轨迹暴露出 team skill 本身缺少协作规则、依赖顺序、边界约束或失败恢复机制时，才值得沉淀
- 如果问题主要来自环境、权限、网络、第三方服务异常，通常不应生成 patch
- 如果只是正常成功执行、措辞差异、单次轻微波动，也不应生成 patch

## 请按以下顺序判断
1. 相关性：问题是否真由当前 team skill 的设计不足导致？
2. 去重性：该经验是否已被“已有演进经验摘要”覆盖？如果已覆盖，不要重复生成
3. 具体性：是否能明确指出缺的是哪种规则，例如角色交接、上下文透传、任务依赖、约束、回退路径？
4. 优先级：如果有多个问题，只保留最值得沉淀的一条，不要输出多条 patch
5. 落点：patch 只能落一个章节，如 Workflow、Collaboration、Constraints、Instructions、Troubleshooting

## Team Skill 专属关注点
重点检查以下问题：
- 角色分工是否不清，导致重复劳动、遗漏责任或角色空转
- 角色交接是否不完整，导致消息、文件、上下文、完成状态没有传递
- 任务依赖是否错误，导致本应串行的工作被并行执行，或本应并行的工作被阻塞
- 约束是否缺失，导致无限重试、超时、产出格式失控或质量门缺失
- 失败恢复是否缺失，导致成员卡住后无人接管、无人升级、无人补充上下文

## section 选择参考
- Collaboration：交接、消息同步、上下文传递、完成状态通知
- Workflow：依赖顺序、并行/串行策略、任务拆分方式
- Constraints：时限、轮次上限、质量门、停止条件
- Instructions：角色的执行规则、职责要求、操作顺序
- Troubleshooting：失败恢复、升级、兜底、重试前置检查

## 内容要求
- 如果 need_patch=true，content 必须是具体、可执行、可复用的 team-skill 规则
- 如果 need_patch=true，summary 必须用一句话说明协作场景和推荐做法
- 不要写泛化空话，如“加强沟通”“优化协作”
- 只输出一条 patch，只落一个 section
- 使用 Markdown，推荐为“一个小标题 + 2-3 个 bullet”

输出格式：
```json
{{
  "need_patch": true/false,
  "section": "章节名（如 Workflow、Collaboration、Constraints 等）",
  "summary": "一句话经验摘要",
  "content": "Markdown 格式的经验内容",
  "reason": "为什么值得沉淀（仅 need_patch=true 时填写）"
}}
```

只输出 JSON。"""

TRAJECTORY_PATCH_PROMPT_EN = """\
Analyze the following execution trajectory and determine whether the team skill needs evolution.

Current team skill: {skill_content}
Existing evolution summary: {existing_evolutions}
Trajectory summary: {trajectory_summary}
Detected issues: {trajectory_issues}

## Decision principles
- Most of the time need_patch should be false
- Only capture a patch when the trajectory reveals a missing collaboration rule, dependency rule, boundary constraint, or recovery path in the team skill itself
- If the issue is mainly caused by environment, permissions, network, or third-party failures, usually do not generate a patch
- Normal successful execution, wording differences, or one-off minor fluctuations are not worth patching

## Evaluate in this order
1. Relevance: Is the issue truly caused by a weakness in the current team skill design?
2. Deduplication: Is this learning already covered by the existing evolution summary? If yes, do not generate it again
3. Specificity: Can you point to the missing rule clearly, such as a handoff rule, context-transfer rule, dependency rule, constraint, or fallback path?
4. Priority: If multiple issues exist, keep only the single most valuable learning
5. Section fit: The patch must belong to exactly one section such as Workflow, Collaboration, Constraints, Instructions, or Troubleshooting

## Team-skill-specific focus areas
Prioritize these checks:
- Unclear role ownership causing duplicate work, missing responsibilities, or idle members
- Broken handoffs where messages, files, context, or completion state are not transferred
- Incorrect task dependencies where sequential work is parallelized or parallel work is unnecessarily blocked
- Missing constraints causing endless retries, timeout drift, output-format drift, or absent quality gates
- Missing recovery paths when a member stalls, fails, or produces poor output

## Section mapping guide
- Collaboration: handoffs, message synchronization, context transfer, completion-state notifications
- Workflow: dependency ordering, parallel vs sequential strategy, task decomposition
- Constraints: time limits, round limits, quality gates, stop conditions
- Instructions: role-specific operating rules, responsibility requirements, execution order
- Troubleshooting: fallback, escalation, recovery, or pre-retry checks

## Content requirements
- If need_patch=true, content must be a specific, actionable, reusable team-skill rule
- If need_patch=true, summary must describe the collaboration scenario and recommended practice in one sentence
- Avoid vague statements such as "improve communication" or "optimize collaboration"
- Output exactly one patch in exactly one section
- Use Markdown, ideally one short heading plus 2-3 flat bullet points

Output format:
```json
{{
  "need_patch": true/false,
  "section": "section name (e.g. Workflow, Collaboration, Constraints)",
  "summary": "one-sentence experience summary",
  "content": "Markdown formatted experience content",
  "reason": "Why worth capturing (only when need_patch=true)"
}}
```

Output JSON only."""

TRAJECTORY_PATCH_PROMPT = {"cn": TRAJECTORY_PATCH_PROMPT_CN, "en": TRAJECTORY_PATCH_PROMPT_EN}

TEAM_EXPERIENCE_GENERATE_PROMPT_CN = """\
你是 Team Skill 优化专家。请根据团队技能内容、执行轨迹、显式用户改进意图和已有经验，生成可复用的 team evolution records。

## 输出格式（最重要）
你的回复必须是一个合法的 JSON 数组，不要任何其他内容。
- 不要使用 Markdown 代码块（```）包裹
- 不要输出解释文字
- 每个对象都必须包含 action 字段（append 或 skip）
- JSON 字符串中的换行必须写成 \\n

## 当前 Team Skill
{skill_content}

## 轨迹摘要
{trajectory_summary}

## 信号
{signals_json}

## 已有 description 经验
{existing_desc_summary}

## 已有 body 经验
{existing_body_summary}

## 已有 script 经验
{existing_script_summary}

## 用户优化方向
{user_query}

## 决策原则
- 只沉淀 team skill 本身可复用的协作、角色、约束、工作流、排障或脚本经验
- 环境、权限、网络、模型偶发现象通常应 skip 为 irrelevant
- 不要重复已有经验；若有增量，可输出 merge_target
- 文本经验（description/body）最多 2 条，script 经验最多 1 条
- 每条 append 经验必须填写 summary，用一句话说明协作场景和推荐做法

## target 选择
- description：团队技能描述、适用范围、角色概览、触发关键词需要修正
- body：Roles / Collaboration / Workflow / Constraints / Instructions / Examples / Troubleshooting 规则
- script：可复用脚本资产，section 必须为 Scripts，并填写 script_filename / script_language / script_purpose

## section 选择
- Roles
- Collaboration
- Workflow
- Constraints
- Instructions
- Examples
- Troubleshooting
- Scripts

## 输出 JSON 数组
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 skip 时填写）",
    "target": "description | body | script",
    "section": "Roles | Collaboration | Workflow | Constraints | Instructions | Examples | Troubleshooting | Scripts",
    "summary": "一句话经验摘要（仅 append 时填写，否则为 null）",
    "content": "Markdown 内容或脚本源码",
    "merge_target": "ev_xxxxxxxx 或 null",
    "script_filename": "脚本文件名或 null",
    "script_language": "脚本语言或 null",
    "script_purpose": "脚本用途或 null"
  }}
]
"""

TEAM_EXPERIENCE_GENERATE_PROMPT_EN = """\
You are a Team Skill optimization expert. Based on the current team skill, execution trajectory, explicit user intent, and accumulated experience, generate reusable team evolution records.

## Output Format (MOST IMPORTANT)
Your response must be a valid JSON array and nothing else.
- Do not wrap the output in Markdown code fences
- Do not add explanatory text
- Every object must include an action field (append or skip)
- Newlines inside JSON strings must be encoded as \\n

## Current Team Skill
{skill_content}

## Trajectory Summary
{trajectory_summary}

## Signals
{signals_json}

## Existing description experiences
{existing_desc_summary}

## Existing body experiences
{existing_body_summary}

## Existing script experiences
{existing_script_summary}

## User optimization direction
{user_query}

## Decision Rules
- Only capture reusable collaboration, role, constraint, workflow, troubleshooting, or script knowledge that belongs to the team skill itself
- Environment, permission, network, and random model issues should usually be skipped as irrelevant
- Do not duplicate existing records; use merge_target when there is clear incremental value
- Text experiences (description/body) must not exceed 2 items, script experiences must not exceed 1
- Every append experience must include summary, one sentence describing the collaboration scenario and recommended practice

## Target Selection
- description: the team skill description, applicability, role overview, or selection keywords need correction
- body: Roles / Collaboration / Workflow / Constraints / Instructions / Examples / Troubleshooting guidance
- script: reusable script assets; section must be Scripts and script_filename / script_language / script_purpose must be filled

## Section Options
- Roles
- Collaboration
- Workflow
- Constraints
- Instructions
- Examples
- Troubleshooting
- Scripts

## Output JSON Array
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority (only for skip)",
    "target": "description | body | script",
    "section": "Roles | Collaboration | Workflow | Constraints | Instructions | Examples | Troubleshooting | Scripts",
    "summary": "one-sentence experience summary (only for append, otherwise null)",
    "content": "Markdown content or script source code",
    "merge_target": "ev_xxxxxxxx or null",
    "script_filename": "script filename or null",
    "script_language": "script language or null",
    "script_purpose": "script purpose or null"
  }}
]
"""

TEAM_EXPERIENCE_GENERATE_PROMPT = {
    "cn": TEAM_EXPERIENCE_GENERATE_PROMPT_CN,
    "en": TEAM_EXPERIENCE_GENERATE_PROMPT_EN,
}

TEAM_JSON_FIX_PROMPT = """\
你上次的输出不是合法 JSON，请修复并重新输出。
只输出修复后的 JSON 数组，不要任何解释文字。

## 解析错误
{parse_error}

## 目标格式
[
  {{
    "action": "append | skip",
    "skip_reason": "irrelevant | duplicate | low_priority（仅 skip 时填写，否则为 null）",
    "target": "description | body | script",
    "section": "Roles | Collaboration | Workflow | Constraints | Instructions | Examples | Troubleshooting | Scripts",
    "summary": "一句话经验摘要或 null",
    "content": "Markdown 内容或脚本源码",
    "merge_target": "ev_xxxxxxxx 或 null",
    "script_filename": "文件名或 null",
    "script_language": "语言标识或 null",
    "script_purpose": "用途说明或 null"
  }}
]

## 原始输出
{broken_output}
"""

TEAM_JSON_FIX_PROMPT_STRICT = """\
你的 JSON 输出多次解析失败。请完全重新生成。

## 解析错误
{parse_error}

## 上次输出预览
{broken_preview}

## 严格要求
1. 只输出一个 JSON 数组，以 [ 开头，以 ] 结尾
2. 不要解释文字，不要 Markdown 代码块
3. 所有字符串内换行写成 \\n
4. 所有字符串内引号写成 \\"
5. 只用双引号，不要单引号
"""


__all__ = [
    "SKILL_EXPERIENCE_GENERATE_PROMPT",
    "SKILL_EXPERIENCE_GENERATE_PROMPT_EN",
    "TEAM_EXPERIENCE_GENERATE_PROMPT",
    "TEAM_EXPERIENCE_GENERATE_PROMPT_EN",
    "JSON_FIX_PROMPT",
    "JSON_FIX_PROMPT_STRICT",
    "TEAM_JSON_FIX_PROMPT",
    "TEAM_JSON_FIX_PROMPT_STRICT",
    "TRAJECTORY_PATCH_PROMPT",
    "TRAJECTORY_PATCH_PROMPT_EN",
    "USER_PATCH_PROMPT",
    "USER_PATCH_PROMPT_EN",
]
