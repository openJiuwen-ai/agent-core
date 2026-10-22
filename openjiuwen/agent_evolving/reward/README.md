# Reward

`openjiuwen.agent_evolving.reward` 为 Agent 交付的作品打分。输出是一份标准 JSON，包含元信息、总分与总评、一级维度和全部二级子项；面向用户的 Markdown 回复由这份 JSON 渲染。

本包只负责评分：不读取轨迹，不给出修改建议。归因到步骤和修改建议由独立的 `fast_attribution` 包完成，它读取本包输出的 JSON 和轨迹。两个包互不 import。

## 概览

一次评分按以下五步进行：

1. **出题**：根据任务描述（和匹配到的评测知识）写出若干一级维度，每个维度包含基础项和主动挑战项；有用户意见或过程信号时，将其推广为子项并入对应维度。
2. **取证**：在交付物的临时副本中读取文件、运行 Python 脚本、用离线浏览器操作网页并截图。
3. **判定**：由独立的模型调用逐条判定子项（met / partial / unmet / unknown），给出理由和维度评价；画面维度只依据截图判定。
4. **计分**：由代码按固定规则计算子项、维度和总分，模型不直接给分。
5. **总评**：根据各维度分数和评价生成两三句总评。

只有取证由一个 DeepAgent 以 ReAct 方式完成，其余步骤都是代码按顺序发起的普通模型调用：

```
reward()
  ├─ 建临时目录；写评分标准，并入用户意见和过程信号        （模型调用，JSON 输出）
  ├─ 取证 DeepAgent（每次新建，用完销毁）
  │     工具：run_checks（一批检查并发执行）、read_evidence（读取已保存的结果）
  │     每轮根据上一轮的摘要决定补查什么；证据充分或达到轮数、时间上限即停止
  ├─ 各维度判定                                            （并行的模型调用）
  ├─ 计分                                                  （代码）
  └─ 总评 → 组装 JSON → 删除临时目录
```

## 快速开始

`openai`、`PyYAML` 是 agent-core 的基础依赖。评测网页类作品（游戏、绘本页面等）还需要浏览器，属于可选依赖：

```bash
pip install 'openjiuwen[reward-browser]'   # playwright + Pillow
playwright install chromium
```

作品包含网页而未安装 playwright 时直接报错，不会退回到只看源码。已安装 playwright 但未安装 Chromium 时，浏览器检查会逐个失败，取证拿不到运行证据。

命令行：

```bash
python -m openjiuwen.agent_evolving.reward --task task.md --artifact /abs/index.html --out reward_result.json

python -m openjiuwen.agent_evolving.reward --task task.md --artifact report.md table.xlsx \
    --txt-response reply.md --user-note "第 2 页多一只手" --process-signal errors.txt \
    --rubric rubric.json --config config.json --out reward_result.json
```

代码调用：

```python
import asyncio
from openjiuwen.agent_evolving.reward import load_config, reward, render

config = load_config()                                        # 也可以直接传入 dict
result = asyncio.run(reward(task_text, ["report.md", "table.xlsx"], config, txt_response=reply_text))
print(render(result))                                         # Markdown 回复
```

## 接口

```python
result = await reward(task, artifacts, config, txt_response="", user_note="", process_signal="",
                      rubric_path=None, previous_result=None)
```

| 参数 | 命令行 | 类型 | 必需 | 说明 |
|---|---|---|---|---|
| `task` | `--task` | str | 是 | 任务描述，即用户的原话。评分标准只依据它编写 |
| `artifacts` | `--artifact` | list[str] | 是 | 交付物路径，文件或目录均可。评测在临时副本上进行，结束后删除，不修改原文件。答案只在回复中时传 `[]`（命令行省略该参数） |
| `config` | `--config` | dict | 是 | 模型、时限等配置，见「配置」一节；`load_config()` 读取默认配置 |
| `txt_response` | `--txt-response` | str | 否 | Agent 交付时给用户的文字回复。答案在回复中时评测它 |
| `user_note` | `--user-note` | str | 否 | 用户对作品的意见，并入评分标准，见「用户意见与过程信号」一节 |
| `process_signal` | `--process-signal` | str | 否 | 过程信号：Agent 工作中遇到的环境报错和多轮对话中的用户反馈，并入评分标准 |
| `rubric_path` | `--rubric` | str / Path | 否 | 评分标准文件。不传时每次按任务重新出题；传入时首次写入，之后沿用，见「沿用与重新出题」一节 |
| `previous_result` | `--previous-result` | dict | 否 | 同一任务上一次的结果 JSON，仅在重新出题时使用 |

命令行的文本参数（`--task`、`--txt-response`、`--user-note`、`--process-signal`）可以是文本，也可以是文件路径。

本包不接收轨迹。从会话中取出任务、最终回复、用户意见和过程信号由调用方负责（JiuwenSwarm 使用 `fast_attribution.locate_task_output`）。

其他公开接口：`text_reward()`、`write_criteria()`、`archive_rubric()`、`render()`、`load_config()`、`SCHEMA_VERSION`。

## 输出格式

返回一个 dict，`schema_version` 为 `"reward/3"`：

```json
{
  "schema_version": "reward/3",
  "meta": {"evaluated_at": "2026-10-16T08:00:00+00:00", "task": "…", "deliverable": ["index.html"],
           "final_reply": true, "inputs": [], "user_note": "…", "process_signal": "…",
           "models": {"producer": "…", "judge": "…", "art_judge": "…"}, "tools": {"available": [], "used": []},
           "guides": ["game"], "scoring": "计分规则一句话", "warnings": []},
  "total": {"score": 72, "summary": "总评两三句"},
  "categories": [{"category_id": "visual", "category_name": "画面质量", "rubric": "这个维度怎样算好",
                  "comment": "该维度评价", "score": 60, "issues": ["头和身体分离"]}],
  "sub_category_items": [{"sub_category_id": "visual#2", "category_id": "visual", "category_name": "画面质量",
                          "rubric": "子项要求", "comment": "判定理由", "score": 50, "status": "partial",
                          "type": "proactive_challenge", "gain": 6.0, "from_user_note": false,
                          "from_process_signal": false}]
}
```

| 字段 | 说明 |
|---|---|
| `meta` | 评测时间；评测对象（交付物、是否有最终回复、用户意见、过程信号）；使用的模型、工具和评测知识（`guides`）；计分规则；`warnings` 记录未完成的非关键步骤 |
| `total.score` | 总分，0–100，各维度分数的平均 |
| `total.summary` | 总评，依据各维度分数和评价生成，不含建议 |
| `categories[]` | 一级维度。`category_name`：评什么（2–6 个词）；`rubric`：怎样算好；`comment`：维度评价（判定模型未写时用子项理由代替）；`score`；`issues`：仅画面维度有，为看图判定发现的严重画面问题 |
| `sub_category_items[]` | 全部二级子项。`sub_category_id` 形如 `维度id#序号`，供归因模块引用；`rubric`：子项要求；`comment`：判定理由；`status` 和 `score` 见下表；`type`：`floor`（基础项）或 `proactive_challenge`（主动挑战项）；`gain`：该项达成后总分可提高多少，供归因排序；`from_user_note` / `from_process_signal`：是否由用户意见 / 过程信号生成 |

子项状态：

| `status` | 含义 | `score` |
|---|---|---|
| `met` | 证据显示完全做到 | 100 |
| `partial` | 部分做到，或受严重画面问题影响 | 50 |
| `unmet` | 证据显示没有做到 | 0 |
| `unknown` | 没有证据，无法判断（不等于没做到），不计入分数 | `null` |

说明：

- `score` 是由 `status` 换算的百分制分数，供人阅读；程序需要区分「没做到」和「没查到」时应读取 `status`。
- 一个维度的所有子项都是 `unknown` 时，维度 `score` 为 `null`，不计入总分。
- 一份结果只描述本次评测，不包含历史分数；需要比较多轮时，由调用方保存各次结果。
- `type` 只出现在 JSON 中，Markdown 回复按维度合并显示。
- 配置 `"include_evidence": true`（命令行 `--evidence`）时，结果额外包含 `evidence` 字段，记录每个检查的工具、代码和输出。

`render(result)` 生成的 Markdown 依次为：总分、总评、提醒、输入、各维度（分数、规则、评价、未达成的子项及理由、画面问题）、计分规则。

## 评分标准

### 维度与子项

出题模型为每个维度写出两类子项：

- **基础项**（`level: "floor"`）：任务明确要求的内容，做不到即不合格。由用户意见、过程信号生成的子项也属于基础项。
- **主动挑战项**（`level: "challenge"`）：任务没有要求、但同类作品中做得最好的会主动做到的内容。必须带来超出基础项的独立提升，不能把同一要求换个说法重复计分，也不能规定任务未要求的实现方式。高分取决于这类子项。

交付物维度最多 `criteria_budget` 个（默认 3），另加一个「质量」维度，需要时再加「画面」维度。

### 用户意见与过程信号

有 `user_note` 或 `process_signal` 时，单独调用一次模型（`FEEDBACK_PROMPT`），对照现有维度将其推广为适用于整部作品的子项。例如「某一页文字难读」推广为「每页文字都清晰可读」。

- 子项放入意见涉及的维度，不新增维度；找不到合适维度时放入最接近的非画面维度（通常是「质量」维度）。
- 不凭空增加数量或门槛。
- 环境报错只在作品本身仍可能出现时才写成子项，例如「运行时不出现这类错误」。
- 模型跳过语义重复的意见，代码再排除逐字重复；每次最多 6 条。
- 生成的子项为基础项，标记 `from_user_note` 或 `from_process_signal`，并写入评分标准文件，之后每轮都会检查（`pipeline.feedback_claims` / `merge_feedback`）。

### 评测知识

评测知识是按任务领域编写的参考文档。开始评测前，代码挑出与本次任务相关的文档，交给出题模型和取证 Agent 参考，它不是固定模板。

**文档位置**

- 包内 `metrics/`：自带 `picture_book.md`（绘本）、`game.md`（游戏）、`personal_assistant.md`（个人助手）；
- `~/.reward/metrics/`（或 `$REWARD_HOME/metrics/`），放入即生效；
- 配置项 `metric_guides` 列出的文件或目录。

**选取方式**：文档首行为 `keywords: 绘本, picture book, …` 时，任务中出现任一关键词即选用，不额外调用模型（自带文档均有关键词）。没有关键词的文档，由一次轻量模型调用根据任务原文和各文档的标题、开头判断是否相关（`guides.for_task`）。

**使用方式**：选中的文档附在任务之后交给出题模型，由它按任务挑选适用的维度，可以写针对单页、相邻两页或整部作品的子项；取证 Agent 也能看到这些文档（例如逐页翻看、对比各页主角）。结果的「输入」一行和 JSON 的 `meta.guides` 列出使用了哪些文档。

**编写原则**：评测知识只写通用原则，即「好是什么」（如「操作即时、结果公平」「图文互补」），不写具体规定（尺寸、画面占比、数量门槛、特定画风或情节）。具体、可检查的子项由出题模型按任务编写；把品味写成固定数字，会让判定用同一把错误的尺子衡量所有作品。

新增一类任务时，编写一份 Markdown 文档，首行写关键词，放入上述任一位置即可。注意：

- 选中文档合计最多取前 8000 个字符，单份文档宜控制在 7000 字符以内；同一类任务只放一份，两份同时命中会重复并被截断。
- 证据只能来自读文件、运行脚本、浏览器操作后的截图和状态探针，每条子项应能在一次一分钟以内的浏览器会话中验证。依赖录屏、音频、多次会话、人工盲测等手段的要求只会成为「无证据」子项。
- 不写具体规定和示例数字；举例只举原则层面的，否则会被套用到其他任务上。

### 沿用与重新出题

- 不传 `rubric_path` 时，每次调用都按任务重新出题。
- 传入 `rubric_path` 时，首次运行写入该文件，之后沿用同一套标准，各轮分数才可比较。文件只保存标准（含由用户意见、过程信号生成的子项），不保存分数。已保存的标准损坏时报错，避免悄悄换成另一套标准。
- 需要换一套标准时，换一个路径，或先调用 `archive_rubric(path)` 存档旧文件（命令行 `--new-rubric`）。
- 重新出题时可传入 `previous_result`：新标准保留上次的基础项，已经达成的主动挑战项不再出现，新的主动挑战项定得更高，使标准随作品一起提高。沿用已保存的标准时该参数不起作用。

## 取证

### 取证 Agent

取证由一个 agent-core DeepAgent 完成，每次评测新建、用完销毁。它只有两个工具：

- `run_checks`：一次提交一批检查，批内并发执行；每个检查注明为哪些维度取证，一个检查可同时服务多个维度。
- `read_evidence`：按需读取已保存的完整检查结果。

完整结果保存在临时目录中。每批检查最后两张截图进入 Agent 上下文（模型支持图片时），每个维度最新的 16 张截图交给判定。

**Agent 能看到的内容**：任务、各维度子项、交付物的文件清单（每个文件几行），以及文档和数据文件的摘录。

- 作品是网页（游戏、页面、应用）时，其源码（.html、.js、.css）对取证和判定都不可见：不提供摘录，`read` 也读不到。评测像用户一样使用作品；探针需要的变量名由探针自行查找（如 `Object.keys(window)`、页面上的按钮）。
- 交付物本身是代码（脚本、库，或回复中的代码）时才细读代码。
- 读文件只返回「第 a–b 行 / 共 N 行」，不提示从哪一行接着读。
- 浏览器检查只向 Agent 返回每一步是否成功、探针读数和报错（最多 1500 字符），不回显 Agent 自己发出的动作和脚本。需要更多细节时用 `read_evidence`。

**防止空转**：取证 Agent 有时会反复提交同一个检查或发出空请求，拿不到新证据。代码按「本轮是否有新检查」计数：

- 已执行过的相同检查不再执行，直接回复「已作为 c3 执行，请使用其结果」；
- 一次 `run_checks` 中有任一新检查，计数清零；没有新检查（全部重复，或列表为空、格式错误），计数加 1；
- 计数达到 2 时提醒 Agent 停止；达到 4 时代码直接结束取证，用已有证据进入判定；
- `read_evidence` 属于正常工作，不计数。

### 检查工具

| 工具 | 用途 | 默认 | 说明 |
|---|---|---|---|
| `read` | 引用文件内容，或按正则搜索 | 开启 | 路径限于临时目录；读目录时返回文件列表；网页作品的源码不可读（见上文） |
| `python` | 编写小脚本测量：重算汇总、核对行数、解析表格、校验格式 | 开启 | 仅限标准库，超时 60 秒；安全限制见下文 |
| `browser` | 在离线浏览器中点击、按键、等待、读取运行时数值、截图 | 有网页时开启 | 见下文 |

**`browser` 的细节**

- 默认通过本地虚拟地址加载；检查写 `"via": "file"` 时用 `file://` 直接打开，与用户双击文件一致（子项涉及「直接打开」时使用）。
- 动作 `drag`（按住拖动，如旋转 3D 视角、拖动棋子）和 `wheel`（滚轮，如缩放 3D 视角）用于测试鼠标交互；`closeup` 截取元素或其局部的 2 倍特写，用于查看主角、图表等细节。
- 支持中文文件名和 `localStorage`；累计等待最多 60 秒。
- 页面本身加载失败时，该次检查记为失败，不作为作品证据。
- 外网请求一律拦截，唯一例外是 jsdelivr、unpkg、cdnjs 上写明确切版本号的库文件（如 `three@0.160.0`）：由评测代码下载一次并缓存在 `~/.reward/cdn/`，再提供给页面。未写版本号（如 `@latest`）的请求同样拦截。

**通用约定**

- 检查只测量、不下结论：输出事实和数值，判定由另一次模型调用完成。
- 检查本身出错（Python 报 Traceback、浏览器无法启动）时，报错原样返回取证 Agent，由它在下一轮改写该检查。
- 没有 shell 工具，检查不能直接运行命令。

### Python 检查的安全限制

脚本在作品的临时副本中运行（脚本文件本身放在副本之外，列目录时只看到作品），超时 60 秒。运行前安装 Python 审计钩子（`checks.GUARD`），安装后在该进程内无法移除。它拒绝以下操作：

- 启动其他程序（`subprocess`、`os.system`、`exec` / `spawn` / `fork`）；
- 网络连接（`socket` 的连接、绑定、发送）；
- 加载本地代码（`ctypes`）；
- 写入、删除、重命名作品临时副本和脚本自身临时目录以外的文件；
- 读取或列出这两处及 Python 安装目录以外的文件（如 `~/.ssh`、其他项目、其他临时文件）。

被拒绝的操作在检查结果中显示为 `PermissionError`。

> 审计钩子能挡住误操作和明显的越权，但不是操作系统级沙箱，无法防御刻意构造的绕过。在本机评测自己 Agent 的作品可以接受；评测不可信的作品或上线运行时，应将 reward 放入容器或沙箱（agent-core 提供 `core/sys_operation/sandbox`）。

## 判定规则

判定模型为每个子项给出 `status`。以下规则保证分数只反映作品本身。

**1. 什么算证据**

| 子项类型 | 例子 | 判为 `met` 需要 | 只有源码或自述时 |
|---|---|---|---|
| 行为 | 得分、碰撞、胜负、翻页、表单保存 | 检查实际运行到相应场景并观察到结果（探针读数、状态变化、截图）；调用作品自身的函数设置场景也算 | 最多 `partial`，理由写明「源码中有，运行中未观察到」；网页作品不看源码，运行中未观察到即为 `unknown` |
| 事实、数字 | 统计值、计数、引用 | 检查实际重算或核对过（如用交付物中的原始数据重算） | 交付物自称正确不算；没有可核对的数据时判 `unknown` |
| 画面 | 造型、配色、构图 | 截图中可见 | 「代码中画了渐变」只说明意图，不算 |
| 文件本身 | 结构、常量、文字 | 读取源码或文件即可 | — |

**2. 检查失败不等于作品失败**

以下情况判 `unknown`（没查到），而不是 `unmet`（没做到）：

- 页面没有加载出来（HTTP 错误、白屏）；
- 检查自身的操作有误（如盲目按键导致游戏开局即结束）；
- 截图没有拍到相应状态。没拍到不能推出作品没有这个状态。

评测环境的限制（只有一个浏览器、没有真机、运行时间短）也不算作品缺陷；评分标准不写依赖这些条件才能判定的子项。

**3. 同一个问题只扣一次**

- 每个维度只判定自己的子项，评价也只谈本维度。画面问题只在画面维度扣分，不影响玩法、内容、数据等维度。
- 一条子项只检查一件事。

**4. 画面维度单独判定**

- 游戏、网页、图表、海报等给人看的作品都有画面维度；出题模型遗漏时由代码补上（`pipeline.ensure_visual`）。
- 维度 id 或名称明确指外观（视觉、画面、美术、外观、造型、画风、观感、美观、visual、looks、art 等）时，按画面维度判定。只在目标描述中提到外观，或名称同时含交互、流程、手感、数据时，不自动视为画面维度；「界面」「图表」等词也不算。反之，任务不产出给人看的东西、维度名也不涉及外观时（如纯文字回答的「表达清晰」），代码去掉画面标记，按普通维度判定。
- 画面维度只包含「看起来怎样」的子项。为用户意见寻找维度时，代码只回退到非画面维度，没有合适维度时明确报错。
- 画面由一次只看截图、不看源码的调用判定（`ART_JUDGE_PROMPT`）：先列出用户一眼能看到的问题，再判定子项。存在严重问题时，相关主动挑战项最多为 `partial`，问题写入该维度的 `issues`。
- 评判尺度是「同题材、同画风的已发布作品」，不设固定尺寸或比例。
- 截图来源：取证 Agent 按作品实际导航逐页截图。未截图时，代码按默认流程补拍（`pipeline.default_screenshots`）：打开页面、尝试开始操作、截取特写，再尝试翻页和滚动；默认流程不保证覆盖所有页面，未看到的内容仍判为无证据。作品只有图片时，直接使用交付的图片，按页码排序取前 8 张（`checks.image_views`）。
- 可以为画面判定单独配置看图能力更强的模型（`art_judge`）。

## 计分

计分完全由代码完成，采用百分制。概括地说：基础项全部达成、主动挑战项达成一半约为 75 分；全部达成才是 100 分；任一基础项未达成，该维度最多 25 分。

| 规则 | 说明 |
|---|---|
| 子项取值 | met = 1，partial = 0.5，unmet = 0 |
| 维度分 | 基础项平均 × 50 + 主动挑战项平均 × 50。只有一类子项（或另一类全部为 unknown）的维度，按该类平均 × 100 |
| 基础项未达成 | 任一基础项为 unmet 时，该维度最高 25 分 |
| 无证据 | unknown 子项不计入，基础项和主动挑战项相同。维度所有子项都是 unknown 时，维度分为 null，不计入总分；看图判定无法看图时，画面维度同样为 null，回复开头会提示 |
| 总分 | 有分数的维度等权平均 |
| `gain` | 假设该子项达成，按上述规则重算后总分的增量 |

## 错误处理

- **决定分数的环节**（出题、取证、各维度判定）失败时抛出 `EvidenceError`，说明失败的步骤和原因，不给出没有依据的分数。取证达到轮数或时间上限不算失败，用已有证据继续判定。
- **不决定分数的环节**（挑选评测知识、并入用户意见和过程信号、生成总评、看图能力试探、默认截图）失败时跳过该步骤，在 `meta.warnings` 和回复开头说明；总评失败时由代码根据分数生成一句。
- **宽松解析模型输出**：没有文字的子项丢弃；无法识别的级别按基础项处理；重复的维度编号自动加后缀；检查缺少 `tool` 字段时按其他字段补全（有 `page` / `actions` 为浏览器，有 `code` 为 Python，有 `path` 为读文件）。

## 配置

`config` 是一个 dict。命令行按以下顺序查找配置文件（JSON）：`--config` 指定的文件 → `$REWARD_CONFIG` → `~/.reward/config.json`（目录可用 `$REWARD_HOME` 指定）→ 内置 `DEFAULT_CONFIG`（与 `config.example.json` 相同）。代码调用时直接传入 dict，可用 `load_config(path)` 读取文件并解析模型。

### 示例

出题、判定、取证三个角色均使用 deepseek-v4-flash，开启思考、低强度，与 JiuwenSwarm `/reward` 的默认设置一致。`art_judge`、`evidence_model` 未配置时分别沿用 `judge`、`producer`；需要单独的看图模型判定画面时再配置 `art_judge`。

```json
{
  "producer": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com/v1", "api_key": "env:DEEPSEEK_API_KEY",
               "timeout": 360, "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}},
  "judge": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com/v1", "api_key": "env:DEEPSEEK_API_KEY",
            "timeout": 360, "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}},
  "evidence_rounds": 12,
  "evidence_seconds": 300,
  "criteria_budget": 3,
  "metric_guides": ["/path/to/my_guides/"]
}
```

### 配置项

| 键 | 类型 | 默认 | 作用 | 注意 |
|---|---|---|---|---|
| `producer` | 模型配置 | `{"model": "session", "timeout": 360}` | 编写评分标准，并入用户意见和过程信号 | 必填；`"session"` 仅在 JiuwenSwarm 环境可用 |
| `judge` | 模型配置 | 同 `producer` | 逐条判定子项，编写维度评价和总评 | 与生成作品的模型相同时，判定可能偏宽松；需要稳定的奖励信号时应固定为独立模型 |
| `art_judge` | 模型配置 | 同 `judge` | 只看截图判定画面维度 | 必须支持图片输入，否则画面子项全部为无证据 |
| `evidence_model` | 模型配置或 agent-core `ModelPoolEntry` | 同 `producer` | 取证 DeepAgent 使用的模型 | 必须支持工具调用（function calling），否则报 `EvidenceError: ran no check`；支持图片时 Agent 能看到截图 |
| `evidence_rounds` | int | 12 | 取证最多几轮模型调用 | 防止死循环的保险；达到上限后继续判定，不报错 |
| `evidence_seconds` | int | 300 | 取证最长秒数 | 设置 `deadline_seconds` 时会被进一步缩短，为判定和总评留出时间 |
| `deadline_seconds` | int | 不限 | 整次运行的总时限，出题、意见整理、判定和总评共用 | 仅当调用方外层有超时时设置，且应小于外层超时。JiuwenSwarm 的 `/reward` 传 720；归因诊断默认 600（`REWARD_DEADLINE` 可改），余下时间留给归因 |
| `criteria_budget` | int | 3 | 交付物维度的上限（另有「质量」维度和必要时的「画面」维度） | 调大会增加判定耗时 |
| `include_evidence` | bool | false | 结果 JSON 是否包含每个检查的完整输出 | 仅用于排查判定；打开后 JSON 体积很大；命令行为 `--evidence` |
| `metric_guides` | 路径列表 | 无 | 额外的评测知识文件或目录 | 只写通用原则，见「评测知识」一节 |

### 模型配置

每个模型配置是一个 dict：

| 字段 | 必需 | 说明 |
|---|---|---|
| `model` | 是 | 模型名；`"session"` 表示使用 JiuwenSwarm 当前会话的模型 |
| `base_url` | 是（`"session"` 时不需要） | OpenAI 兼容接口地址 |
| `api_key` | 是（`"session"` 时不需要） | 不要写明文，写 `"env:变量名"`，运行时从环境变量读取 |
| `timeout` | 否 | 单次调用的总时限（秒），包含一次重试，默认 360 |
| `first_chunk_timeout` / `idle_timeout` | 否 | 流式返回迟迟不开始、或中途长时间无新数据时判为卡住，默认各 60 秒 |
| `extra_body` | 否 | 原样传给接口的额外参数，如 DeepSeek 的 `{"thinking": {"type": "enabled"}, "reasoning_effort": "low"}`、Qwen 的 `{"enable_thinking": false}`。开启思考会明显变慢 |
| `vision` | 否 | 显式声明是否支持图片输入，跳过看图能力试探 |
| `provider` | 否 | 取证 DeepAgent 的模型提供方，默认 `OpenAI`；其余 JSON 模型调用仍走 OpenAI 兼容接口 |

**`"session"` 模型**：从 JiuwenSwarm 最近的会话记录读取模型名，再从 `~/.jiuwenswarm/config/config.yaml` 读取地址和密钥。其中的 `${变量名}`（可写作 `${变量名:-默认值}`）依次从环境变量、`~/.jiuwenswarm/config/.env` 取值，都没有时报错。不在 JiuwenSwarm 环境中（没有会话记录或 config.yaml）时直接报错，需要写完整配置。可以用 `{"model": "session", "timeout": 600}` 覆盖个别字段。

**看图能力试探**：未配置 `vision` 时，评测网页或图片前，判定、看图判定、取证三个角色各试探一次（同一模型只试一次），结果按接口地址和模型名缓存在 `~/.reward/vision.json`。模型能力变化（如同名模型升级）后需删除该缓存。结果不明确或调用出错时重试一次；仍不明确时，本次不发送截图、不写缓存，并在回复开头提示。看图判定无法看图时，画面维度整体不计分，不以源码代替外观判定。

## 与 JiuwenSwarm 集成

入口是 `/reward` 命令，对应 JiuwenSwarm 的 `agents/harness/common/tools/reward_tool.py`。`mode=fast` 时，工具先调用本包评分，再把结果 JSON 交给 `fast_attribution` 归因并给出建议；回复由本包的 Markdown 和归因模块的 Markdown 组成。

- 任务（用户原话）、最终回复和本轮写入的文件由工具通过 `fast_attribution.locate_task_output` 从会话记录中取出；用户在 `/reward` 后面写的意见作为 `user_note` 传入，`process_signal` 尚未接入。本包本身不读会话记录。
- 取证 Agent 使用当前对话的模型（`evidence_model` 传入会话的 `ModelPoolEntry`）。
- 评分标准保存在会话目录的 `reward_rubric.json`，之后每轮沿用，由用户意见和过程信号生成的子项保留。`/reward 重新出题` 时，工具先调用 `archive_rubric` 将旧标准存档为 `.1.json`、`.2.json`……，再把会话中最近一次结果作为 `previous_result` 传入，在旧标准之上重新出题。配置 `reward_engine.fast.reuse_rubric: false` 可关闭沿用。
- `reward_engine.fast` 下可以写本包的配置项；`art_judge` 可以只写 `models.defaults` 中的模型名。
- 结果写入 `<会话>/reward_production/<run_id>/reward_result.json`（本包）和 `attribution.json`（归因）。
- 进度写入 `<会话>/reward/<开始毫秒>.json`，供网页进度卡片读取。

## 代码结构

| 文件 | 内容 |
|---|---|
| `__init__.py` | 公开接口：`reward`、`text_reward`、`write_criteria`、`render`、`load_config`、`archive_rubric`、`SCHEMA_VERSION` |
| `__main__.py` | 命令行：调用 `pipeline.reward`，写出 JSON，打印 Markdown |
| `pipeline.py` | 主流程：`reward()` 建临时目录后调用 `score_workspace()`（写标准 → 取证 → 判定 → 计分 → 总评 → 组装 JSON）；另有 `text_reward()`、`write_criteria()`、`feedback_claims()` / `merge_feedback()`、`ensure_visual()`、`evaluate_terminal()`、`cap_by_critique()`、`default_screenshots()`、`load_rubric()` / `save_rubric()` / `archive_rubric()`、`overall_summary()` |
| `evidence_agent.py` | 取证 DeepAgent：`gather()` 创建 DeepAgent 并挂载 `run_checks` / `read_evidence`，运行结束后销毁；`EvidenceRun` 保存结果并按维度分配证据；`EvidenceError` |
| `checks.py` | 检查工具：`run_check()`、`run_experiment()`（离线浏览器）、`image_views()`、`available_tools()` |
| `scoring.py` | `score()`（子项状态 → 维度分）、`total()`、`to_result()`、`claim_gains()` |
| `result.py` | 标准 JSON：`build()`；Markdown：`render()`、`progress()`、`bar()` |
| `prompts.py` | 各环节提示词：出题、取证（`EVIDENCE_AGENT_PROMPT`）、判定、看图判定、并入用户意见、总评 |
| `guides.py`、`metrics/` | 评测知识的选取逻辑和自带文档 |
| `workspace.py` | 临时目录：`make_workspace()`、`copy_work_folder()`、`linked_files()`、`workspace_files()` |
| `files.py` | 将文件读取为文本 |
| `models.py` | 流式 JSON 客户端、`"session"` 模型解析、看图能力试探 |
| `session.py` | 定位 JiuwenSwarm 会话目录（查找会话模型、写进度文件，不读轨迹） |
| `progress.py` | 网页进度卡片读取的进度文件 |
| `config.py` | 配置加载 |

测试位于 `tests/unit_tests/agent_evolving/reward/`：模型调用使用脚本替身，临时目录、Python 检查和离线浏览器真实运行。

## 局限

- 取证受轮数和时间上限约束：默认最多 12 轮模型调用（`evidence_rounds`）、300 秒（`evidence_seconds`），实际起作用的通常是时间；设置了总时限 `deadline_seconds` 时，取证时间还会被缩短，为判定和总评留出余量（在归因诊断中总时限默认 600 秒，可用环境变量 `REWARD_DEADLINE` 修改）。复杂交互中仍可能有子项判为无证据。
- 每次浏览器检查都启动新的浏览器并重新加载页面，不保留上一次检查的状态；要到达某个状态，需要在同一次检查中重复前面的操作。
- `python` 检查只能使用标准库，不能运行其他程序或联网，不适用于需要登录或联网才能验证的内容。
- 网页副本会复制 HTML/CSS 中显式引用的资源（包括 CSS 中的图片和嵌套样式），但不会推断 JavaScript 动态拼接的文件路径。依赖这类路径的作品需确认副本完整，不能把副本缺少资源当作原作品缺陷。资源超出复制上限时明确报错。
- 主动挑战项和「质量」维度包含主观判断，同一作品两次评测可能相差几分；比较作品时建议多次运行。
