---
id: F_20261104_2034_converge-tpot-attr-key
title: TPOT 属性键收敛（inter_token_latency_ms → tpot_ms）
type: F
author: weixin_39960157
author_email: 1046386000@qq.com
modules:
  - openjiuwen/extensions/tracer_otel
files:
  - openjiuwen/extensions/tracer_otel/handler.py
  - openjiuwen/extensions/tracer_otel/semconv.py
  - openjiuwen/extensions/tracer_otel/AGENTS.md
  - tests/unit_tests/extensions/tracer_otel/test_handler.py
designs: []
design_note: 键名纠错类变更；"one fact, one key / no reader fallback chain" 的规范依据为 observability/semconv.py 成文评注（b0134c30d 引入、544ea2838 重申），无独立方案取舍需要设计文档。
---

# TPOT 属性键收敛（inter_token_latency_ms → tpot_ms）

## 背景与决策

同一事实（TPOT，每输出 token 平均时延）此前在两栈以两个属性键并存：

- tracer_otel 写 `openjiuwen.gen_ai.response.inter_token_latency_ms`（#1833，
  2026-10-12 引入，读取 agent-studio !2524 的同名 usage 载荷键）；
- observability 已发布 `openjiuwen.gen_ai.response.tpot_ms`（b0134c30d，
  2026-08-21，含成文定义：mean time per output token between the first and
  the last token-carrying chunk）。

observability semconv 的成文约定是"one fact, one key … no reader ever needs
a fallback chain"，双键并存迫使读侧维护 fallback 链，属键名错误而非有意
设计（tracer_otel 侧键晚于 observability 键两个月独立起名）。收敛到已发布键
`tpot_ms`：

- `semconv.py` 常量换名换值 `OJ_GEN_AI_RESPONSE_TPOT_MS`；
- 写点读侧双读：载荷键 `tpot_ms` 优先、`inter_token_latency_ms`（agent-studio
  !2524 既有生产键）兜底——任意 studio 版本组合不丢数；
- 载荷契约零变更（studio 侧 `usage_metadata` 键不动），仅 OTel 属性名收敛。

## 改名安全性

旧键存续三周，生产者与消费者全部为本方近期工作（core #1833 +
agent-studio !2524），无外部读者：全仓（py/js/ts/java/md）检索无其它引用；
模块 AGENTS.md 所述 swarm 投影 COMPATIBILITY 表未收录该键。按 AGENTS.md
"If a key here must change, treat it as a wire-format break" 的要求，本次
属无外部读者的窗口期纠错，模块键清单已同步（含移除同 PR 已删除的
`usage.*_cost` 旧条目）。

## 回归

- develop 侧：tracer 47 / tracer_otel 157（含新增 canonical 载荷键直写
  用例）/ observability + trajectory 182 passed 1 skipped / react streaming
  载荷回填 6——全绿；
- 0.1.17_hotfix 侧：tracer_otel 148 passed；tracer 套件文件未触碰。
- 旧键残留：`INTER_TOKEN_LATENCY` / `response.inter_token_latency_ms` 于
  openjiuwen/ + tests/ 源码零引用（载荷键引用按设计保留）。
