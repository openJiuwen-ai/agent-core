---
id: F_20261103_1124_web-retrieval-quality
title: 免费 Web 检索质量、抓取可靠性与调用预算
type: F
author: ShowHsiang
author_email: xiangxiaoneng@h-partners.com
modules:
  - openjiuwen/harness/tools/web
  - openjiuwen/harness/rails
  - openjiuwen/harness/prompts/tools
files:
  - openjiuwen/harness/tools/web/free_search.py
  - openjiuwen/harness/tools/web/fetch_webpage.py
  - openjiuwen/harness/tools/web/_http.py
  - openjiuwen/harness/tools/web/_budget.py
  - openjiuwen/harness/tools/web/_assessment.py
  - openjiuwen/harness/tools/web/_quality.py
  - openjiuwen/harness/tools/web/_common.py
  - openjiuwen/harness/rails/web_research_rail.py
  - openjiuwen/harness/prompts/tools/web_tools.py
  - tests/unit_tests/harness/tools/web/test_web_retrieval_quality.py
  - tests/unit_tests/harness/tools/web/test_web_tools_v2.py
  - tests/unit_tests/harness/tools/web/test_free_search_recall.py
  - tests/unit_tests/harness/tools/web/test_bing_market_routing.py
  - tests/unit_tests/harness/tools/web/test_search_assessment.py
  - tests/unit_tests/harness/tools/web/conftest.py
  - tests/system_tests/harness/search_assessment_benchmark.py
  - tests/system_tests/harness/fixtures/search_assessment_cases.json
  - pyproject.toml
designs: []
design_note: 用户已在对话中确认基于实际案例的调整策略，本次直接落实既有工具链修复，无独立 D。
---

# 免费 Web 检索质量与预算

## 依据与范围

候选基于本地 SDK `6df967b86b`。用户 2026-11-02 的五次实际任务覆盖天气、亚洲新闻、AAPL、
纳斯达克、AI Agent 进展：15 次搜索、15 次抓取、23 次模型调用；搜索网络本身中位约 0.37 秒。
原始 Bing HTML 与解析标题一致，不能归因为单纯 DOM 解析错误。源返回无关内容，SDK 的弱可用性判断
和 best-effort 回退又把它当成功；固定“至少抓取两页”进一步放大成本。
Yahoo 的大响应头触发 aiohttp 默认 8190 字节限制；HTTP 200 WAF/动态壳被当正文；表格扁平化丢失字段关联。
不复制含私密上下文的原始日志，不把外部页面内容当指令。

当前契约见 `S_05_tools-contract`。宿主接入在 Swarm 维护，不在本仓复制应用装配规则。

## 实施和取舍

1. 首轮相关性要求主题和可识别意图同时满足，Latin 短词按词边界匹配，日期、origin、卡片类型不单独证明相关。
   提供中英常用主题别名；已知旧日期和搜索导航链接被过滤；扩大同一响应的候选池后才截取数量。
   不增加一次 LLM 排序调用，不将弱结果伪装成功。后续按下文“召回损失跟进”改为强匹配优先、限量显式不确定候选。
2. 显式 provider 不偷偷跨源；360 默认关闭，只新增无密钥、可选的独立来源，不自动启用付费或绕过验证码。
   DDG 验证码不再尝试同源 reader，允许转向独立且已配置的搜索源。
3. Fetch 区分传输成功和内容可用，保留链接、表格与空值；公开 JS 壳的 reader 是提取回退而非访问控制绕过。
   响应头上限提高到有限的 64 KiB，不关闭防护；TLS 和代理优先级不变。
4. 预算由 Rail 挂载到 invocation 的上下文，原子预留计数后才 await；不能放在共享工具实例或全局环境中。
   从首次检索计时，避免前置思考误耗预算；outer/inner 轮次共享，下一 invocation 重置。
5. `_on_search_results` 供宿主在 SDK 已判定成功后记录来源，消除宿主复制的检索路径；成功返回仍是候选，不是事实证明。

## 验证

- Windows、项目现有 `.venv`，SDK editable 指向 `D:/agent-core`；测试显式使用 uv.lock 对应缓存 protocol 源码，未修改依赖锁。
- 最终定向回归 139 项通过，7 条既有 Pydantic/DefaultResponse 弃用警告。
  命令为 `python -m pytest D:/agent-core/tests/unit_tests/harness/tools/web D:/agent-core/tests/unit_tests/harness/test_deep_agent_rail_event_routing.py D:/agent-core/tests/unit_tests/harness/test_deep_agent_event_executor.py -q -o addopts= -p no:cacheprovider`。
  运行前激活 Swarm `.venv`，显式加入 uv.lock protocol 提交 `9ec0370` 的缓存源码路径；未升级实际环境内的 protocol 安装。
- 回归覆盖真实工具 invoke、真实 AbilityManager 并行调度、跨模型轮预算、outer/inner 上下文、来源隔离、
  CAPTCHA/WAF/空 reader、受限来源不外跳、正文链接/表格、localhost 真 HTTP 大响应头（1 KB / 12 KB / 64 KB 内 / 70 KB）。
- 将用户日志中 12 组历史 Bing 候选重放到实际质量过滤函数，原来接受的 106 条无关候选全部拒绝。
  这是候选过滤回归，不是全部历史任务或 LLM 端到端重放；未重新访问历史候选 URL。
- 少量真实联网检查走 SDK 工具，不调用付费或 LLM。首轮发现 360 聚合入口和旧节目，追加导航/日期过滤回归。
  最终 Bing `西安天气` 返回 5 条相关候选（0.64 秒），`AAPL stock price` 返回 `low_relevance`（0.67 秒）；
  360 `西安天气` 返回 5 条候选（8.17 秒），`亚洲今日新闻` 剩 1 条节目入口（6.56 秒），
  `AI Agent 最新进展 2026年11月` 返回 3 条主题相关候选（8.80 秒）。节目入口和博客候选均不证明新闻/模型信息真实、当日有效，仍需核对正文。
  各结果是单次本机观察，不用于比较引擎稳定性能或保证实时事实可用。
- 未运行格式化或静态检查；未跑全仓测试、未重启用户服务、未跑真实模型端到端闭环。

## 限制与重验

### 2026-11-03 召回损失跟进

用户进一步确认补齐 Jina 摘要、减少过滤误杀、保留原始候选和拒绝原因，并使用实际查询对照。
本轮沿用既有工具，不新增搜索服务或付费回退；限定在当前 SDK Web 适配器修复，复用本记录。
首轮只读采集用户今天与前一天的搜索会话，覆盖天气、亚洲/东南亚新闻、AAPL、纳斯达克和 AI Agent 进展。
12 个实际查询的对照固定原始响应后离线重放，避免修复前后网络结果变化干扰结论。
360 两次响应实际重定向到 qcaptcha.so.com，旧代码误报 empty；发现后停止该源的后续请求，不绕过验证码。

实施决策：Jina 按结果块绑定标题、目标链接和摘要；缺少部分词法证据的候选限量降权并明确标注不确定性，
不因索引日期旧就断言正文过时。无主体证据、导航、无效 URL 和标题明确过期仍不可作为成功证据。
调试模式保存原始候选、每条去留/降权原因、解析前响应摘要与最终选中结果；不记录认证头或代理凭据。
新增直接依赖 `markdown-it-py>=3.0.0`，复用环境已存在的 4.0.0；同步 core 与 Swarm 锁中的 openjiuwen 依赖元数据，
未升级依赖、未改变 editable 路径或安装版本。完整 Markdown 解析处理转义、括号 URL、格式、换行与无标题结果，
避免再扩展脆弱的单行链接正则。通用“公司”词不再充当独立必须命中的主题；`Asia/Shanghai` 等时区不充当地域证据。

#### 同响应对照方法

- 读取用户实际会话 `web_1a1a00cfd7b_ae00e38f1e54`、`web_1a19f8a68d9_96680df2548b` 及前一天相关会话。
  最新会话逐个按 `tool_call_id` 对齐调用与 `rendered_result`，不以最后一次调用猜测并发结果归属。
- 最新会话有 11 次搜索：Bing 5 次（4 次 low_relevance、1 次 empty），360 6 次（天气成功、5 次 empty）。
  360 天气后续抓取成功，说明它并非所有查询都不可用。历史 empty 未保存响应，不能追溯断言全部都是验证码。
- 选取 12 个实际查询/等价复测词，在同一机器与网络、相同语言/地域参数下采集。
  DDG HTML、DDG/Jina、Bing 各 12 份响应；360 前两份为验证码，确认后停止该源，其余 10 项未测。
  不是把 DDG 验证码绕到 Jina；本批 DDG 未出现挑战。只读公开搜索页，不调用付费 API、LLM 或候选页面。
- 修改前保存字节响应、SHA256、请求和最终 URL、状态、允许列出的非认证响应头、原始解析/过滤结果。
  修改后替换 HTTP 传输为这些相同字节进行重放，并断言请求 URL 相同，排除前后源变化。
- “仅过滤”用旧解析候选运行新过滤；“完整”用新解析和新过滤。数量指每查询最多 10 条发现候选，
  不是事实准确率、独立 URL 数、任务完成率，也不能用离线耗时宣称联网加速。

#### 汇总

| 接入 | 响应数 | 解析候选 | 旧筛选保留 | 仅过滤更新 | 完整更新 | 其中 uncertain |
|---|---:|---:|---:|---:|---:|---:|
| DDG HTML | 12 | 119 | 88 | 96 | 96 | 4 |
| DDG/Jina | 12 | 119 | 77 | 93 | 98 | 5 |
| Bing | 12 | 113 | 22 | 25 | 25 | 2 |
| 360 | 2 | 0 | 0（误报 empty） | 不适用 | 0（blocked） | 0 |

Jina 摘要由 **0/119 恢复为 119/119**。在新过滤不变的条件下，新解析又净保留 5 条；
这不是两个独立贡献的准确率加法，摘要和过滤存在交互。Jina 的所有查询均有发现入口，
但“带日期的亚洲新闻”仅恢复 CCTV 栏目入口，不能声称找到了当天具体新闻。

| 查询 | Jina 旧→新 | DDG HTML 旧→新 | Bing 旧→新 | 360 |
|---|---:|---:|---:|---|
| 西安天气 | 10→10 | 9→9 | 10→10 | 验证码 |
| 西安天气 2026年11月3日 | 10→10 | 10→10 | 0→0 | 验证码 |
| 西安天气预报 | 10→10 | 10→10 | 0→0 | 未测 |
| 亚洲今日新闻 | 4→6 | 7→7 | 0→0 | 未测 |
| 亚洲今日新闻 2026年11月3日 | 0→1 | 4→4 | 0→0 | 未测 |
| 东南亚 今日新闻 | 6→8 | 8→8 | 0→0 | 未测 |
| 苹果 AAPL 股价 | 10→10 | 10→10 | 0→0 | 未测 |
| 苹果公司股价 实时行情 | 3→10 | 5→10 | 0→0 | 未测 |
| AAPL stock price today | 10→10 | 10→10 | 10→10 | 未测 |
| Nasdaq Composite index today close | 8→10 | 10→10 | 0→0 | 未测 |
| AI agents latest developments October 2026 | 4→8 | 2→5 | 2→5 | 未测 |
| AI Agent 最新进展 2026年10月 | 2→5 | 3→5 | 0→0 | 未测 |

#### 因果判断与反例

1. **Jina 接入确实有损失**：原 Markdown 已有摘要，旧解析直接置空。CCTV/联合早报栏目页主题在摘要里，
   恢复摘要后才能按完整证据判断。缺摘要不能等同源无相关内容。
2. **过滤确实有误杀**：“苹果公司”拆出的“公司”不应成为第二个必需主题；旧策略丢掉雪球等行情入口。
   当前放回的是可核验入口，未把摘要中的数字当实时行情。
3. **也存在误放行风险**：一条全球新闻聚合页仅因更新时间写了 Asia/Shanghai 而匹配亚洲，复查后排除并加负向测试。
   “AI 新闻”不一定讲 Agent，最多保留两条并明确 partial_subject_evidence；未经原文验证不声称相关性确定。
4. **Bing 多数问题在解析前**：12 项中 8 项响应正文已跑题，1 项明确无结果，3 项有主题候选。
   “西安天气预报”的原始 li.b_algo 是蓝牙故障；“亚洲今日新闻”是微软商店；带日期新闻是 Storage Sense；
   “苹果 AAPL 股价”是 GPT 教程；纳指是元谋人。请求 URL、页面标题及搜索框仍保留正确查询。
   放宽过滤不会生成缺失的正确结果，因此这 8 项继续拒绝。AI 英文查询由 2→5，其中仅 1 条新增强匹配、2 条待核验。
5. **360 首要是访问与错误分类**：同机确有过天气成功，也确有 qcaptcha HTTP 200 被报 empty。
   不同时间的验证码状态会变化，本批无法评价其 12 项相关性，更不能据此把它排在 DDG 或 Bing 后。

“Bing 源响应异常”限定本机本次路径；未做独立出口、浏览器登录态、不同地域参数或代理对照，
不能进一步断言是全局索引差、代理污染还是服务端风控/路由问题。8 项无关响应不是过滤制造出来的，
这与 Bing 在另几项能提供候选并不矛盾。新闻博客、聚合站、旧栏目页仍需核验权威性和发布时间。

#### 本轮验证与复现

- SDK Web 目录回归 **152 passed**；新增 21 项涵盖摘要归属、无标题/转义/括号、去留原因、限额、时区负例、
  uncertain、真实 invoke 到来源钩子、HTTP 200 验证码、响应截断和 Reader 源错误。两条既有 Pydantic 弃用警告。
- Swarm 的搜索来源适配及设置测试单独运行，避免两个仓库同名 tests.conftest 冲突；最终计数见 Swarm 跟进记录。
- 本地证据保留在 `C:/Users/27897/Desktop/jiuwenclaw_shawn/tmp/search-quality-20261103/`，
  `NN-<engine>.json/.body` 为原始采集，`replay-summary.json` 含过滤前/后候选和逐条原因。
  工具为同目录上级 `tmp/search_quality_probe.py`；`replay` 只读取固定响应，无实际联网，`capture` 才联网。
  原始响应与运行数据不提交。开启 `FREE_SEARCH_DEBUG=true` 可在下一次真实任务保存 response/candidate/filtered/decision，
  `FREE_SEARCH_DEBUG_DIR` 可定目录；默认不开启，摘要有界截断且标记，不记录认证头。
- 未修改真实用户配置、未重启服务、未绕过验证码、未跑真实 LLM 完整闭环、未做桌面重打包。
  本轮证明接入与过滤修复有效，不证明模型总调用数或总耗时已经下降。

源质量不可由代码保证。相关性是词法启发式，非事实核验；搜索日期可能是索引更新时间，原文仍需核对。
64 KiB 以上响应头仍拒绝；任意 div 布局不能保证恢复表格语义。预算只覆盖挂载 Rail 的 SDK 工具调用，
不作为安全权限边界或整个团队的统一配额；模型仍可能选择其他工具或继续推理。
候选修改后需重跑相关行为测试；搜索 DOM 或网络环境变化需重跑小样本探针；声明链路速度改进前需用户真实模型复测。

## Bing 请求路径对照（2026-11-03 14:58-15:16）

用户要求继续区分源、请求方式与网络路径，随后明确取消代理对比。本轮仅诊断，不修改产品代码、
真实配置或默认市场，不重启服务、不提交数据。补充此前未覆盖的地区与浏览器对照；仍不能判断独立出口差异。

### 方法与有效样本

- HTTP 共 28 次：6 查询 x 3 地区的主矩阵 18 次，单参数对照 5 次、Chrome 主要请求头对照 3 次、
  浏览器首页生成 URL 的 HTTP 重放 2 次。均返回 HTTP 200，无截断、无观察到的验证码。
- HTTP 使用本机 aiohttp，`trust_env=False`、`DummyCookieJar`、无显式代理，TLS 验证开启；
  复用当前 SDK Bing 解析和候选选择，仅在探针中替换传输，保持查询 q 不变。原始 HTML 的结果另行提取核对。
  不读取代理凭据、不调用模型、付费接口或候选页面；每请求超时 25 秒、响应最多 2 MB，串行间隔 2 秒。
- Chrome 使用现有 Playwright CLI 和本机 Chrome 133，headless、`--no-proxy-server`、隔离上下文、
  zh-CN locale；每个矩阵项新建 context，未使用用户登录态或个人浏览器配置。
  有效直接 URL 矩阵 4 查询 x 3 地区 = 12 次，记录初始导航响应、最终 DOM、URL 和截图。
- 首页搜索另有 3 次有效观察：先前逐步操作的天气查询，以及完整加载首页后的亚洲新闻、中文 AAPL 查询。
  完整加载批次的天气项因探针的 combobox 定位超时，未提交查询，不能计为 Bing 搜索失败。
  初次矩阵截图取早了、仅 DOMContentLoaded 后提前提交首页表单的批次均保留，但不纳入有效浏览器质量结论。
- 本轮是单机短时间小样本，不是随机化、多日或跨网络实验；以下“有候选”不等于答案正确、当日有效或任务闭环。
  TLS 验证和禁用应用层代理不能证明不存在透明出口设备；没有做代理、独立出口、用户登录态对照。

### HTTP 地区矩阵

当前 `_search_bing` 按 `_contains_cjk(query)` 强制附加 `setlang=zh-Hans&mkt=zh-CN&cc=CN`，
否则附加 `setlang=en-US&mkt=en-US&cc=US`。本轮分别强制两组参数及完全不附加地区参数，查询及 SDK 请求头不变。
表中数字是当前筛选器每查询最多保留 10 条的发现候选数，同时人工核对原始标题；不是准确率。

| 查询 | 不带地区参数 | 中国区组合 | 美国区组合 |
|---|---|---|---|
| 西安天气 | 10，天气入口 | 10，天气入口 | 10，天气入口 |
| 西安天气预报 | 0，城市百科/旅游 | 0，腾讯 ima | 10，天气入口 |
| 亚洲今日新闻 | 0，祁字/姓氏 | 0，Psyche 神话 | 6，亚洲新闻栏目/媒体入口 |
| 苹果 AAPL 股价 | 0，Apple 产品/商店 | 0，中国石油招投标 | 10，雪球等行情入口 |
| AAPL stock price today | 10，行情入口 | 10，行情入口 | 10，行情入口 |
| Nasdaq Composite index today close | 7，金融入口 | 0，Windows 故障排除 | 5，金融入口 |

主矩阵有发现候选的查询分别为 3/6、2/6、6/6。查询 q、页面标题和搜索框没有变成这些无关主题；
无关结果已存在于 HTTP 响应的 `li.b_algo`，不是后处理将正确结果替换成了错误结果。

“西安天气预报”的单参数对照：仅 `mkt=en-US` 得到天气候选；仅 `mkt=zh-CN` 得到 Network Share Issues，
仅 `cc=CN` 得到阿根廷探戈；仅 `setlang=zh-Hans` 或仅 `form=QBLH` 得到城市百科/旅游。
这使市场/地区请求参数成为可复现的重点线索，不支持只去掉全部参数就必然修复。

### 浏览器与请求头对照

| 查询 | Chrome 无地区参数 | Chrome 中国区组合 | Chrome 美国区组合 |
|---|---|---|---|
| 西安天气预报 | 城市百科/旅游 | 天津中医药大学 | 天气入口 |
| 亚洲今日新闻 | 西藏旅游 | 钢板缺陷资料 | CCTV、联合早报等新闻入口 |
| 苹果 AAPL 股价 | Apple 产品/商店 | Norah Jones | 雪球、Yahoo Finance 等行情入口 |
| AAPL stock price today | 物理 arXiv、zxcvbn 等 | 行情入口 | 行情入口 |

同一错误参数组合在 Chrome 中也跑题，不能归为 aiohttp 独有问题或 BeautifulSoup 解析丢失。
仅将 HTTP 的 User-Agent、Accept-Language、Accept 换成捕获的 Chrome 值，天气查询在美国区仍正常、
中国区仍为 WiFiDirect 等无关结果、无地区参数仍为城市百科。该对照未复制 Cookie、全部请求头或 TLS 指纹，
只能证明替换这三项头不足以解决，不能声称所有客户端差异均已排除。

原始导航 HTML 与最终可见 DOM 也不能混为一谈：12 项浏览器矩阵中 11 项最终 URL 新增 `rdr=1/rdrig`，
查询仍保持不变；其中 5 项结果列表发生变化，但前后都跑题，例如天气查询从蓝牙故障变成大学网站。
这些项不能当作“同一文档的解析器差异”。其余 7 项标题列表经空白归一化一致；完整首页搜索成功的 2 项也一致。
本轮未记录所有后续导航/资源的完整网络轨迹，不能进一步指定跳转或动态替换由哪个组件触发。

逐步加载首页、输入并提交时，天气、亚洲新闻和中文 AAPL 均观察到主题候选，说明这些查询并非没有可检索内容。
但是将亚洲新闻和 AAPL 的完整首页生成 URL 原样交给无 Cookie 的 HTTP 请求，分别变为上海自然科学基金和
Apple 产品页；因此不能只复制 `form/cvid` 等参数就认定接入已修复。Cookie、会话、客户端与时间交互仍未单独隔离。
未规避验证码或添加反检测逻辑。

### 收敛判断与后续建议

1. 维持此前“跑题发生在过滤前”的结论，但把“源的问题”收窄为本机特定 Bing 请求路径返回异常，
   不能把异常泛化为 Bing 全局索引质量。浏览器首页和美国区路径已经提供反例。
2. 当前将 CJK 查询语言和 CN 市场强绑定是可调整的接入策略。建议分离查询语言、界面语言和市场配置，
   将 en-US 列为待验证的替代市场，而不是声称某个市场永久正确。本轮没有实施该配置或改变默认值。
3. 若采用低相关时切换市场，最多增加一次同源请求，并消耗既有检索预算；仍低相关就明确失败，
   不让模型通过重复改写无限重试。遇到验证码/访问限制停止，不按低相关重试，不偷偷转浏览器或付费源。
4. 保留质量检查和原始响应/拒绝原因；先验证市场选择，再考虑是否需要浏览器路径。单纯放松过滤或修改 UA
   已不能解释和解决本批异常。此前同机美国区纳指也出现过无关响应，因此本批 6/6 不能当作可靠性保证。
5. 用户取消了代理对比，故无法最终区分 Bing 服务端市场路由、实验/反自动化策略与网络出口交互；
   不把地区相关性误写成已证实的具体内部根因。需要恢复独立出口或长期交错复测时再扩大归因范围。

### 本地证据与复现

- 探针：`C:/Users/27897/Desktop/jiuwenclaw_shawn/tmp/bing_path_probe.py`，支持地区、请求头和首页 URL 对照；
  `tmp/bing_browser_probe.js` / `tmp/bing_browser_form_probe.js` 是通过 Playwright CLI 运行的隔离浏览器脚本。
- HTTP 原始 `.body`、请求元数据、SDK 候选与去留原因在
  `C:/Users/27897/Desktop/jiuwenclaw_shawn/tmp/bing-path-20261103/`，离线汇总为 `analysis-summary.json`；
  `tmp/bing_path_analyze.py` 只读取这些快照，不联网。
- 浏览器证据在 `C:/Users/27897/Desktop/jiuwenclaw_shawn/output/playwright/bing-direct-20261103/`。
  有效矩阵是 `browser-matrix.json`；`browser-form-loaded.json` 保留 2 成功与 1 定位失败，
  `browser-form-weather.json` 是此前逐步操作成功的天气样本。勿使用 initial/early 文件计算有效矩阵。
- 同查询截图 `browser-visible-0-cn.png` 与 `browser-visible-0-us.png` 已人工查看，分别显示大学和天气结果，
  搜索框均为“西安天气预报”。原始公开页面含临时访客标识，所有数据只留本地忽略目录，不提交。
- 所有探针运行结束，本次独立 Chrome 会话已关闭。没有重跑 UT、全仓测试或真实模型闭环；
  前文 152/47 项通过属于前一轮代码验证，不代表本轮重新执行。

## Bing 市场路由实施（2026-11-03）

本节记录初版实施与当时的验证；其中“备用市场额外扣一次”的计数规则已由下节用户确认的规则取代。

用户确认按诊断建议直接实施，随后明确要求保留现有预算。最终不增加“首次 + 3 次”的总次数机制，
不修改 `WebResearchBudget`、Rail 或默认的每源 2 次/最多 2 源/3 fetch/90 秒；本节替代上节尚未实施的状态，
但不改写历史探针结论。新行为只属于 SDK 搜索路径，不迁移遗留 MCP 的 DDG/Bing 实现。

### 决策与边界

- Bing 主市场默认 `en-US`，由 `FREE_SEARCH_BING_MARKET` 配置，不再由查询是否含 CJK 决定。
  当前只提供已对照的 `en-US`、`zh-CN` 两种市场 profile；profile 决定 `mkt/setlang/cc`，
  查询原文和按查询语言生成的 Accept-Language 不变，不做隐式翻译或改词。
- `FREE_SEARCH_BING_FALLBACK_MARKET` 未设置时为另一市场，`none` 禁用备用；相同市场去重，
  配置去首尾空白且不区分大小写，非法值报 `invalid_input` 而不是静默发往其他地区。
- 每次工具调用只有“原始候选非空、筛选后全部拒绝”才试一次备用市场；空结果、网络错误、
  访问限制、成功或显式 uncertain 候选都不触发。每个请求先预留原预算、再使用剩余时间，不给备用请求额外额度。
  两个市场都低相关时明确失败；`provider=bing` 不跨源，`auto` 可继续其他已启用且未超预算的来源。
- 失败聚合优先呈现 `budget_exhausted`，避免第一次低相关掩盖第二次实际未能取得额度。
  两次的请求、原始响应、候选和拒绝原因使用带市场的独立诊断文件，不能相互覆盖。
- 拒绝加入新的共享“1+3”计数、自动查询改写、默认扩大原预算、自动浏览器/付费回退或验证码规避。
  不修改用户真实配置、服务或环境依赖；Swarm 只补后台配置模板/文档和真实 SDK 消费回归。

### 验证

- 本次 SDK Web 目录 **174 passed**，包括新增 22 项路由回归；有 2 条既有 Pydantic 弃用警告。
  通过实际 `WebFreeSearchTool.invoke` 验证中文/英文同市场、备用成功和双失败、禁用/去重/非法配置、
  访问限制/网络错误/empty/uncertain 不回退、次数/时间/来源上限、剩余超时、显式来源与 auto、
  原始诊断不覆盖、域约束不重复附加、既有预算默认值不变。
- 既有真实 AbilityManager 并行调用、跨轮预算和新 invocation 隔离测试在同一 Web 目录回归中继续通过。
  Swarm 的实际来源包装与配置测试 **49 passed**，包括新增 2 项备用市场成功/失败后的来源台账与共享预算验证。
- 使用前一节采集的固定 HTML，通过完整工具 invoke 离线重放 6 查询 x 3 路由；断言生成请求 URL 与
  相应捕获 URL 完全相同，任何网络请求都由本地响应替代。语言绑定基线用原语言选择市场并禁用备用，
  新默认均用 en-US，另测 CN 主市场/US 备用。过滤器保持本次相同版本，隔离路由差异。

| 查询 | 语言绑定基线候选数 | 新默认候选数 | CN 主市场/US 备用候选数（请求数） |
|---|---:|---:|---:|
| 西安天气 | 10 | 10 | 10（1） |
| 西安天气预报 | 0 | 10 | 10（2） |
| 亚洲今日新闻 | 0 | 6 | 6（2） |
| 苹果 AAPL 股价 | 0 | 10 | 10（2） |
| AAPL stock price today | 10 | 10 | 10（1） |
| Nasdaq Composite index today close | 5 | 5 | 5（2） |

有发现候选的查询由基线路由的 3/6 变为这批新路由的 6/6。没有新采集在线响应，不能声称源的长期稳定性、
事实准确率或 Agent 总执行时长已验证改善。证据在 Swarm 本地忽略目录
`tmp/bing-path-20261103/market-routing-replay.json`，重放脚本 `tmp/bing_market_replay.py`。

测试沿用现有 Swarm venv 与 uv.lock 指定的 protocol `9ec0370` 缓存源码，未安装或升级依赖。
SDK 命令为 `python -m pytest D:/agent-core/tests/unit_tests/harness/tools/web -q -o addopts= -o log_cli=false -p no:cacheprovider`
并指定仓库 tmp 内独立 basetemp；Swarm 测试单独调用 `test_trusted_web_search.py` 与 `test_free_search_settings.py`，
加 `-o asyncio_mode=auto`。未运行格式化、静态检查、全仓测试、真实 LLM 闭环或桌面重打包。
交付需实际运行该 SDK 代码；既有进程须重启加载。改完市场配置不意味着可以绕开当前每源次数限制。

## Bing Query 额度语义修订（2026-11-03）

用户进一步明确：备用市场不额外消耗 Bing 次数；默认两次额度用于原 query 和改词后的新 query，
两者各自允许一次低相关市场回退，最多四个市场请求。这一明确要求局部替代上节按市场请求扣次的实现，
其余来源、总时限、每源/来源数/fetch 默认上限以及质量筛选保持不变，不新增设计或全局计数机制。

- 只在每次 Bing 逻辑搜索的主市场前调用 `reserve_search`；备用分支复用该次已取得的额度，不退款、不另扣次数。
  次数/来源额度不足时，主请求失败退出，不能跳到免费备用分支。改词后的工具调用重新取得一次额度。
- 每个市场请求仍调用 `remaining` 获取剩余超时。并行调用各自拥有本次回退资格，但共用原子预留的既有 budget。
  同一 invocation 的默认 Bing 额度允许最多两个 query，各至多两个市场请求；第三个 query 不再发送请求。
- 只有低相关才回退一次；成功、uncertain、空结果、验证码、网络错误等不增加市场尝试。其他引擎计数语义不变。

本次最终回归：SDK Web **177 passed**（两条既有 Pydantic 弃用警告），Swarm 来源包装与设置 **49 passed**。
新增或更新的行为断言涵盖：一份额度足够执行主/备用、回退失败也不退还主额度、改词后 q 参数在两个市场均为新值、
三个并发调用最多预留两个 query 并发出四个市场请求、第三个 query 零请求、时间与来源上限不绕过、
Swarm 两组 query 的来源台账仅记录通过筛选的 URL，第三次被预算拒绝后不新增来源。
测试路径与环境沿用上节，basetemp 独立置于仓库 tmp；未改 `_budget.py` / Rail、真实配置或运行服务。
本次未再做线上 Bing、模型闭环或离线质量矩阵重放，不将上节源质量结果作为新一轮网络验证。

## 实际案例召回与弱主题修订（2026-11-03）

用户在新预算逻辑下复测后确认继续修复接入损失，沿用本记录，不另建 D。
17:25 至 17:29 的原始候选再次出现纳指结果大批 uncertain、繁体 Yahoo 财经被拒绝、
AI Agent 查询命中 Gemini/泛 AI 科普并提前结束市场回退。切换市场后的原始无关响应仍存在，
本次修复不声称改善搜索源本身，也不调整每源次数、来源数、抓取次数、超时或真实用户配置。

实施边界：补齐已支持主题与意图的繁体别名，将“点位/點位”归入行情意图；
AI Agent 复合查询缺少 Agent 主体时明确拒绝，不能用 AI + research 等泛词匹配替代。
保留真正证据不足候选的两条上限和已有 uncertain 语义，不把所有 uncertain 都升级成更多请求，
不增加 LLM 评分、通用中文转换依赖、自动查询改写或验证码规避。原始标题、链接和摘要保持不变。
360 验证页继续 blocked，原始跑题结果继续 low_relevance；Swarm 只通过原 SDK 路径消费。

验证计划：使用保存的两轮候选离线比较去留；通过实际 invoke 验证泛 AI → 备用成功/双失败、
两 query 最多四市场和第三 query 无网络；保留具主体但缺意图、访问限制、超时等负向边界。
Swarm 同步验证最终工具文本与来源台账。执行结果在本节续写，不能将此前 177/49 当作本次通过。

### 本轮实施与验证结果

- 生产改动仅在 `free_search.py`：已知主体简繁别名、意图词与对应查询清理、AI Agent 复合主体证据检查。
  未改 `_rows_are_usable`、市场循环、预算、Rail、来源启用项或 HTTP transport；泛 AI 的提前返回通过
  修正分类解决，不把所有 uncertain 都变成网络重试。`missing_specific_subject` 进入原逐条诊断记录。
- 新增回归先在修复前复现 **8 failed**（6 组点位/繁体候选、2 组泛 AI 市场回退），修复后 SDK Web
  目录 **206 passed**，仍为 2 条既有 Pydantic 弃用警告。含 29 项新增断言组合：简繁双向匹配、
  泛 AI 加通用研究词仍不能替代 Agent、真正 provisional 继续可核验、标的区分、两 query 四市场上限。
- Swarm 实际 SDK 包装与设置回归 **52 passed**。新增 3 项覆盖最终工具文本、备用成功/双失败、
  来源台账排除泛 AI URL、繁体行情来源保留和 callback 预算。首次新增繁体夹具未声明 UTF-8，
  触发既有编码猜测路径导致 1 项失败；补齐该模拟响应的 charset 后全量重跑通过，未修改生产解码器。
- 只读两轮本地捕获候选（16:43–16:56、17:25–17:29），以原请求数量限制离线重放 **20 份响应**。
  两份纳指结果均从 2 条 provisional 恢复为 10 条主题候选，繁体 Yahoo / Investing 不再误拒绝；
  两份泛 AI 科普从 2 条 provisional 改为 0 条，并保留 `missing_specific_subject` 拒绝原因；
  新一轮亚洲新闻由 6 条增加至 7 条，恢复繁体 CCTV 入口。其余 15 份的去留结果不变，含全部 4 份 DDG。
  原本跑题的 Bing 响应仍为 0 候选，不因市场或日期改变而放行。
- 重放脚本和生成结果仅在 Swarm 已忽略目录 `tmp/search_recall_replay.py`、
  `tmp/search-recall-replay-20261103.json`，不提交查询、来源摘录或用户日志。输入是已解析候选，不是
  完整网络重放；此前 primary 为 uncertain 的真实案例没有捕获备用响应，不能据此声称备用会成功。
- 测试命令、现有 venv 与 protocol 缓存路径沿用前节；SDK basetemp 为 `tmp/search-recall-green-20261103`，
  Swarm 最终为 `tmp/search-recall-swarm-final-20261103`。未安装依赖、运行静态检查、重启服务或执行
  新的线上搜索/模型闭环/桌面打包。200/blocked 的既有 360 用例在 SDK 全目录回归中继续通过。

自检范围为本节增量：`invoke → _search_free → _select_search_candidates → _on_search_results` 与 Swarm 包装、
本节测试和中英文说明；未发现新增的公共 API、来源台账或预算分叉。未检视工作区其他历史未提交修改。
仍为有限词法规则，不是任意繁简转换或完整语义检索；模糊“纳斯达克”查询仍需区分综合指数/100 指数，
候选数量不等于实时行情可靠性。默认首个相关结果顺序未改，最终答案和完整 Agent 耗时需要重启后的实测。

## 通用批量语义评估替代（2026-11-03）

用户明确批准复用现有 LLM 批量评估候选，替换特定 query 的硬编码，并测试通用场景效果和耗时。
本节局部替代前述词法相关性、主题别名与 AI Agent 特判；解析、访问限制识别、来源授权和既有预算继续有效。
不新增专用 reranker 服务，不修改真实凭据或启动服务。当前契约见 S_05 的 Web 检索质量与预算。

实施选择：模型从当前工具回调的 Agent 公共 get_llm 获取；无上下文可显式注入。每页一次独立短上下文调用，
严格校验完整候选 ID 和三态分类，保留原始候选与理由。评估故障显式 unavailable，最多返回两条未核验线索，
不当作低相关反复切换来源；取消传播，评估耗时计入原总时限。拒绝逐候选调用模型、隐式新建客户端和硬编码主题回退。
验证分为固定模型响应的协议/路由回归，以及调用前固定标签的跨主题语料调用实际已配置模型；后者单独记录耗时，
不能将模拟模型 UT 当成语义准确率，也不能将固定候选评估当成搜索源召回或完整 Agent 闭环验证。

### 实施与行为验证

- 新增 `_assessment.py`，调用现有 `Model.invoke`，使用独立 system/user 消息、空工具列表、温度 0、
  有界输出与 `asyncio.timeout`。每个源响应非空有效候选只调用一次；模型 SDK 自身的请求行为不另行重写。
  严格校验三态、整数 ID 全覆盖、无重复、无未知字段及理由长度，格式不合法不推断为全部无关。
- 移除主题别名、固定意图词、AI Agent 特判、时效关键词、按站点加分以及 Latin/CJK 关键词相关性排序。
  保留结构检查、结果数量上限、访问限制、原文和 URL。时间语义交给模型结合原任务/查询/参考时间判断，
  不声称搜索日期或相关性标签能够证明事实新鲜度。
- 模型每次从 callback Agent 的公共 getter 解析；显式 `model` 仅作为无活动模型时的调用方注入。
  getter 失败只记录异常类型，返回未评估状态，不把原异常中的配置/凭据放进工具结果。
  原始候选诊断先于评估保存；分类、原因、耗时及脱敏格式错误类别进入原诊断，数量淘汰与语义拒绝仍区分。
- 原始任务通过现有 invocation-local budget 传递，不改变原始 RunContext，不在共享工具缓存模型或任务。
  默认每源两次、Bing 每个 query 可切一次备用、两 query 最多四市场、最多两来源/三 fetch/90 秒不变。
  评估默认 15 秒、范围 1–60 秒且计入总时限；总时限已过则不能发布迟到候选。无效评估不额外重试市场。
- 旧词法实现专属断言已移除；解析、网络、预算、市场与消费者回归使用显式脚本模型，脚本只负责固定回复，
  不能作为语义准确率证据。新增真实 AbilityManager 分发验证当前模型切换、原任务、callback budget；
  另测共享工具并发任务隔离、取消、总时限、模型/格式异常、候选上限、结构拒绝和 URL 凭据剥离。
- 最终 SDK Web 加 DeepAgent rail event routing / event executor 共 **191 passed**，7 条既有弃用警告；
  其中 Web 目录 183 项。Swarm 结果另见其同主题 feature。本次首次全目录回归的一项失败来自固定脚本
  标题标签未对应夹具，修正测试标签后重跑；不通过生产特判修复测试。独立评估协议首次 18 项已通过。

### 真实模型固定候选对照

复用本机现有默认模型 `deepseek/deepseek-v4.1-flash`（OpenRouter），通过 Swarm 已有模型构建函数加载，
不修改配置/凭据。12 组、48 条合成公开候选，预先固定 11 relevant、11 uncertain、26 rejected 标签；
标签不进入模型输入，也未按实际输出调整。包括 Python 超时、Rust 借用、Jaguar 同名车/动物、Mercury
同名天体/元素、热泵、跨语言电池、CSS、简繁鸟类迁徙、明确历史时间、SQLite 中间子问题、时区伪相关和无答案任务。
该集合检验通用语义，不包含用户日志；不是独立人员标注的大规模标准数据集。

| 指标 | 修改前词法规则（1 轮） | 批量 LLM（同一集合 2 轮） |
|---|---:|---:|
| 标签一致率，含无法评估 | 23/48，47.9% | 87/96，90.6% |
| 成功评估内标签一致率 | 47.9% | 87/92，94.6% |
| 预标相关候选保留 | 9/11 | 22/22（含 1 条未评估线索） |
| 预标无关候选被选中 | 13/26 | 0/52 |
| 相关/待核验被误拒绝 | 5/48 | 5/96（均为待核验） |
| 单批耗时中位数 | 0.88 ms | 1,837.92 ms |
| 单批耗时 P95 | 2.52 ms | 3,618.20 ms |

24 批中 23 批有效、1 批 `invalid_assessment_response`；最大单批 6,782.3 ms。首次格式失败的原始响应未保存，
无法确认是 JSON、ID 还是字段长度问题；新增的脱敏校验错误类别用于后续排查，不补造失败原因。
两轮仍误拒了 5 条预标 uncertain 的摘要不足候选，集中在观察指南、泛旅行资源和政策目录；
不能将本小样本的零无关放行视为所有任务都无误判，模型也可能受不可信候选影响。
后续同一时区样例单独复测成功（3,984 ms），不覆盖或删除最初失败记录。

另用通用 Python 候选扩展到 10/20/30 条，各测两轮评估协议与长度耗时，6/6 有效：
10 条 3,063–3,297 ms，20 条 3,765–6,204 ms，30 条 3,906–4,782 ms。
扩展通过重复同类候选构造，不作为额外语义准确率样本，也不是多主题长摘要压力测试。
真实测量在添加校验错误类型、getter 失败降级和原始候选提前记录前完成；这些后续变化没有改变 prompt、
模型调用参数、有效输出解析和排序，最终代码通过上述回归。未把单批评估时延等同于完整工具/Agent 时延。

可复用入口 `tests/system_tests/harness/search_assessment_benchmark.py` 的 `benchmark(existing_model)`；
调用方显式传入模型，不默认读取用户密钥或自动联网。固定语料在同目录 `fixtures/search_assessment_cases.json`。
本机隔离脚本/生成明细位于 Swarm 的忽略目录 `tmp/run_search_assessment_benchmark.py`、
`tmp/search_assessment_lexical_baseline.py`、`tmp/search-assessment-live-20261103.json`、
`tmp/run_search_assessment_sizes.py`、`tmp/search-assessment-sizes-20261103.json`，不提交运行数据。

本轮命令沿用上述 venv/protocol 缓存，额外加入
`tests/unit_tests/harness/test_deep_agent_rail_event_routing.py` 与 `test_deep_agent_event_executor.py`，
隔离 basetemp 为 Swarm `tmp/search-assessment-delivery-core`。未安装依赖、变更 uv.lock、运行静态检查、
重启服务、构建桌面包或执行带真实搜索源的完整 Agent 闭环。模型连接由隔离脚本关闭，未留下后台服务。

自检范围为本节增量（基于 `6df967b86b61bb3bd4aad4fb3be01fbc0c742a01` 加既有脏工作区），
比较本轮前的本地词法快照而非把全部 HEAD 差异算作本轮。沿 ReActAgent.get_llm、AbilityManager 的
callback 透传、预算 Rail、最终文本/来源钩子及 Swarm 消费者核对；未覆盖其他脏文件或全仓行为。
新增一次 LLM 调用有实际成本；降低搜索误放行不等于能保证源召回、最终事实正确或完整链路更快。

## 移除 360 来源（2026-11-03）

用户明确要求整体移除持续被 block 的 360 搜索。本节替代此前 360 opt-in 能力，保留历史测试事实，
直接按已确认范围实施，不新建另一套设计。SDK 移除环境开关、请求/解析器、自动回退、provider schema
和专属诊断分支；只保留 DDG（含 Jina reader）与 Bing，以及原有显式国内学术域入口。
旧 `provider=360` 明确失败且不联网，遗留 `FREE_SEARCH_360_ENABLED` 不再影响注册或路由。
不修改真实用户配置、不自动开启剩余来源，不改变当前语义评估、预算、代理/TLS 或通用验证码保护。
拒绝仅隐藏前端/关闭默认值的做法，避免旧配置或直接调用重新触发失效来源；Swarm 消费方同步移除。

验证基于上述 core HEAD 加已有工作区、本节退役增量，沿 schema、`is_free_search_enabled`、
`_search_free`、Swarm 设置/注册/推荐入口核对，没有将其他脏文件作为本节改动。
`python -m pytest D:/agent-core/tests/unit_tests/harness/tools/web -q -o addopts= -o log_cli=false
-p no:cacheprovider --basetemp=tmp/pytest-remove360-core-final --tb=short` 得到 **181 passed**，
使用前节 Swarm venv 与相同 PYTHONPATH/protocol 缓存，只有已有 Pydantic 弃用警告。
负向测试证明旧环境开关、仅含 360 的 `enabled_engines`、显式 360 provider 均不能发请求；
DDG challenge 到 Bing、主/备用市场、两 query 四市场、候选判断、诊断和抓取继续通过。
首轮 **180 passed / 1 failed** 是改写测试误用未导入的 `urlparse`，修正为已有 `urlsplit` 后全量重跑通过，
未为使夹具通过改变路由。保留通用验证码/访问限制测试，不再保留 360 专属解析测试。
配套 Swarm 的设置/来源包装 **58 passed**，启动默认值/配置重载/推荐装配 **13 passed**；
前端设置 **65 passed**，隔离真实设置组件的中英文、宽/窄窗口交互验证通过。
未重新采集搜索源、调用真实模型、测量完整 Agent 链路、修改用户配置、重启已有服务或构建安装包。
上线需交付两仓代码、重启后端并重建设置页资源；未提交或推送。

## 运行时评估与伪正文修复（2026-11-04）

真实日志暴露了思考耗尽输出预算、评估不可用固定两条丢失后排来源，以及 WAF/PDF 被报告正文成功。
本轮实现、替代决策、根因与实际回放证据统一记录在
`B_20261104_1359_search-assessment-runtime`；对应当前契约已同步至 S_05。
该记录替代旧“评估不可用最多两条”的约定，不改正常评估的 uncertain 两条上限或既有检索预算。
历史基准数字仍保留，仅代表当时版本与样本，不作为本轮上线后的全链路性能保证。
