---
id: B_20261104_1359_search-assessment-runtime
title: 搜索评估截断、降级候选丢失与伪正文成功修复
type: B
author: ShowHsiang
author_email: xiangxiaoneng@h-partners.com
modules:
  - openjiuwen/harness/tools/web
  - openjiuwen/core/foundation/llm
files:
  - openjiuwen/harness/tools/web/_assessment.py
  - openjiuwen/harness/tools/web/free_search.py
  - openjiuwen/harness/tools/web/fetch_webpage.py
  - openjiuwen/harness/tools/web/_quality.py
  - openjiuwen/core/foundation/llm/reasoning.py
  - tests/unit_tests/harness/tools/web/test_search_assessment.py
  - tests/unit_tests/harness/tools/web/test_web_retrieval_quality.py
  - tests/unit_tests/core/foundation/llm/test_reasoning_resolver.py
designs: []
design_note: 用户根据日志评估建议明确要求继续修复；沿用既有检索方案，不另起独立设计。
---

# 搜索评估运行时修复

## 现象与证据

2026-11-04 10:33-11:21 本机 Swarm 两个会话共 13 个任务，12 个使用免费搜索。
会话为 `web_1a1a46771e6_4df2b0199759` 和 `web_1a1a48d4dfa_430038464155`。
32 次有效候选批次评估中 20 次成功、11 次无效输出、1 次超时。模型为现有默认
`deepseek/deepseek-v4.1-flash`，通过 OpenRouter 访问；没有增加新配置或读取其他凭据来源。

- 11 次失败的响应 `finish_reason=length`，completion/reasoning tokens 均为 1728，content 为空。
  旧版 10 候选输出预算正好是 `128 + 160 * 10 = 1728`，预算全被思考消耗。
- 5 次 DDG 评估均失败，未评估批次沿用 uncertain 两条上限。薪资案例原排序第 3 条官方来源
  `stats.mom.gov.sg/Pages/Occupational-Wages-Tables2025.aspx` 被标为 `uncertain_limit`，没有交给 Agent。
- session A 的 history 第 116、173 行分别出现 WAF 载荷和原始 `%PDF-1.4`，均被报告为成功正文。
  原检测只检查直接响应开头，未再检查 HTML 提取后的 WAF JSON；fetch 没有区分 PDF 与文本。

证据位于用户数据目录的 history、`logs/llm.log` 及原 `free_search_debug`。
运行数据不复制进仓库；不记录原始模型思考、凭据或完整响应。

## 当前契约与决策

本记录补充 `F_20261103_1124_web-retrieval-quality`，替代其中“评估不可用也只留两条”的旧行为。
有效契约维护在 `specs/harness/S_05_tools-contract.md`，中英文 tools API 文档同步。

1. 评估单次调用使用已有 ReasoningConfig 能力映射，优先 disabled，其次 minimal/low，否则 auto。
   输出上限按候选数为 4096-8192；长度截断单独报告，不能因残留 JSON 可解析而接受不完整评估。
   不修改主 Agent 设置、不另外创建客户端、不增加评估层重试，仍受单批和原总时限约束。
2. 回归暴露 OpenRouter 未匹配模型表时会落入标准 `reasoning_effort` 分支。补齐 Chat Completions
   的网关兜底：复用现有 `_openrouter_reasoning_plan`；已知模型能力/档位限制仍优先，不硬编码新模型名称。
   依据为 [OpenRouter reasoning 文档](https://github.com/OpenRouterTeam/docs/blob/main/guides/best-practices/reasoning-tokens.mdx)
   的统一 `reasoning` 字段；不是在搜索工具里自行实现 provider 参数。
3. 评估不可用时结构有效候选按来源顺序保留到 max_results，仍明确 uncertain/unavailable；不输出推荐抓取排名。
   正常评估中的 uncertain 仍最多两条；没有放回模型已拒绝结果，不把评估失败当作市场回退条件。
4. fetch 在解码前检查 MIME/文件头，PDF/二进制明确 unsupported_content；不引入 PDF 解析器或自动 reader 请求。
   直接响应同时检查原文与 HTML 提取文本；reader 解析元数据后检查正文，WAF 返回 blocked。
   普通正文中提及 PDF/WAF 不算阻断，403/202 等访问限制继续优先使用既有失败路径。

不选择只增加重试/时限，因为无法避免纯思考耗尽，也会继续拉长链路。
不按题目、站点或具体模型名打补丁；不关闭相关性过滤。PDF 本轮明确不支持，不宣称新增了 PDF 正文能力。
每源两次、Bing 每 query 一次备用、最多两个来源/三次 fetch/90 秒的预算均未改动。

## 回归与模型回放

候选为 core HEAD `aa0e69bb8c6557109d964dd4f9c2bc3e3320d068` 加既有工作区及本轮增量；
宿主为 Swarm HEAD `4f0f884b7c3b2a0f20565cec69e8bda0b457629e` 加既有工作区。
仅检视本轮增量，不把全部脏文件归为本次改动。
使用 Swarm `.venv`，先执行 Activate.ps1，PYTHONPATH 指向 Swarm 与 `D:/agent-core`，不需要旧 protocol 缓存补丁。
Swarm 导入测试日志重定向到工作区临时数据根，避免接触正在运行的真实日志。

- SDK Web 目录、reasoning_resolver、reasoning_profiles、openai_model_client、model_client_profile_routing：
  **369 passed**，2 条既有 Pydantic 弃用警告。覆盖截断、预算、取消、能力档位、配置不变、降级保留、
  HTML 包装 WAF、reader 包装 WAF/PDF、MIME 错报、合法正文及不触发额外请求。
- Swarm `test_trusted_web_search.py`：**27 passed**。真实 AbilityManager 分发验证未评估第 3/4 条仍可见、
  来源台账正确、数量上限、警告保留、只有一次 Bing 额度和一次评估调用；生产包装未复制新策略。
  扩展至 topic recommendation、free search settings、web fetch metadata/security 的最终组合回归共 **191 passed**，
  包含前述 27 项，确认直接消费搜索结果的推荐链路和遗留 fetch 消费方未出现测试回归。
- 首次 SDK 测试 212 passed / 1 failed 是新 OpenRouter wire 断言定位出的缺口；补齐网关兜底后全量通过。

使用原 8 批候选各回放一次，未再抓取搜索源，query-only 上下文；不能等同于原始完整 Agent 上下文：

| 指标 | 原日志 | 本轮回放 |
|---|---:|---:|
| 有效评估 | 2/8 | 8/8 |
| 不可用 | 6/8 | 0/8 |
| 评估耗时中位数 | 6328 ms | 1937.5 ms |
| Bing openjiuwen en-US 跑题批次 | 10 条拒绝 | 10 条拒绝 |
| Bing 同 query zh-CN 批次 | 10 条相关 | 10 条相关 |

5 批 DDG 原本全部无效，本轮均成功；薪资第 3 条官方来源被保留。整个回放主模型配置前后相等。
原样例 max_results 不完全相同，本轮统一为 8，因此不能把 selected 数变化单独作为召回率提升证明。
这些真实候选未另做独立语义标注，不能报告其分类准确率。

另复用既有 12 组、48 条预标合成语料，标签不进入模型输入、不根据结果修改：
本轮 12/12 有效，48/48 标签一致，11/11 相关保留，26 条无关均未放行；中位 1187.04 ms，最大 6140 ms。
这是单轮小样本结果，不代表真实场景 100% 正确。测试工具复用原模型，每批一次调用，未改默认温度/思考配置。
旧基准启动脚本引用已移除的 360 常量导致 ImportError，未开始模型评估；本轮跳过已归档词法基线后重跑。

本机忽略产物在 Swarm `tmp/replay_search_runtime_fixes.py`、`tmp/search-runtime-replay-20261104.json`、
`tmp/search-assessment-live-20261104-fixed.json`，不提交用户候选和运行数据。

## 完整 Agent 复测与交付验证（2026-11-04）

上述“未重跑完整 Agent”只描述修复当轮。随后通过真实 Web Gateway -> Agent -> 模型 -> 工具 ->
会话历史重跑 13 个原始问题，并增加 4 个 DDG 对照；17 次生命周期均结束，没有 chat.error。
模型继续使用 `deepseek/deepseek-v4.1-flash`，没有新增查询特例。生命周期完成不代表问题已获得有效答案。

- 40 个非空候选批次评估均有效，中位 1.282 秒；本轮未复现纯思考截断导致的评估不可用。
- 33 份 Bing 原始 SERP 的首条标题与解析结果一致。相同天气 query/URL/市场在不同时段返回不同主题，
  说明问题已经出现在源响应而非仅由解析或过滤造成；这些观测不能进一步区分源排序与网络中间层影响。
- 天气、新闻、旅游、薪资等 Bing 查询仍缺乏可用证据。部分技术主题有相关结果，DDG 对照能取得天气正文，
  但新闻聚合页、403/CAPTCHA、PDF 和不完整经济数据仍限制最终核验。没有通过放宽过滤把无关结果变成成功。
- 13 题耗时中位由原日志的 34.558 秒变为 22.671 秒；失败更早结束也会缩短耗时，不能据此宣称成功率或整体性能提升。
  未限定来源的行情任务还触发了约 69 秒的付费搜索，免费搜索预算不控制该耗时。
- 最终回答仍暴露摘要未经正文核验、金额单位错误、仅命中实体的弱候选及正文前段导航截断等风险，
  本次提交不声称这些问题都已解决。保留日志对照作为后续独立修复依据，不提交原始用户数据。

本地忽略产物为 Swarm `tmp/web-search-e2e-comparison-20261104.json` 和
`tmp/web-search-ddg-comparison-20261104.json`。该轮实测在官方 develop 同步前完成。
同步官方 core `8ede5297c` 后，Web 与上述四组 LLM 行为测试重新运行，仍为 **369 passed**；
使用 Swarm 既有 venv、两仓 PYTHONPATH 与隔离数据目录，未运行静态检查或重新请求外部模型。
用户授权按实现、测试、文档三提交交付到 GitHub/GitCode fork，本次无关联 Issue，获准省略 Refs。

## 独立官方 PR 验证（2026-11-04）

用户随后要求将免费搜索改动单独提交官方 develop，关联 agent-core #2465。
本 PR 从 GitHub 官方 `8ede5297c9e6dea59337b6ea9d2fc81c803a7bee` 起步，
仅挑选前述实现、测试、文档三个提交，不包含本地 develop 的合并提交或其他浏览器历史。
新提交统一使用 `Refs #2465`，与上一阶段无关联 Issue 的 fork 同步记录区分。
在独立 PR checkout 上重新运行同一 Web/LLM 定向组合：**369 passed**，2 条既有 Pydantic 警告。
运行前已核对 SDK 导入来自该 PR checkout，不是本机 editable develop；未重跑真实搜索/模型或完整 Agent。
索引按官方基线与本次搜索文档重新生成，避免携带 fork 其他文档的索引项。

## 防错模式与遗留

- 有效 JSON 与 finish_reason 都需核对；“评估不可用”不能等同于语义不相关，也不能伪装成相关。
- 传输 200 不证明正文可用；应在内容类型与提取阶段分别验证，不能把二进制或防护载荷当证据。
- 已同步 SDK/Swarm spec、实现记忆和中英文 API 文档；文档索引使用各仓同步工具刷新。
- 完整 Agent 实测见上节，但未逐项独立核验所有最终答案、未运行全仓测试或静态检查。
  Bing 原始跑题仍可能存在；PDF 需要合适的文档工具；评估模型仍会误判/超时。
- 交付阶段不修改真实配置或重新打包。需重启使用该 SDK 工作区的后端；冻结 exe 仍需重打包。
