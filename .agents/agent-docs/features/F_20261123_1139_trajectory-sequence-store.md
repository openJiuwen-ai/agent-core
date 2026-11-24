---
id: F_20261123_1139_trajectory-sequence-store
title: 演进轨迹消息序列共享
type: F
author: zhangzhongteng
author_email: zhangzhongteng1@huawei.com
modules:
  - openjiuwen/agent_evolving/trajectory
  - openjiuwen/harness/rails/evolution
  - openjiuwen/extensions/observability
files:
  - openjiuwen/extensions/observability/content_addressing.py
  - openjiuwen/extensions/observability/sequence_registry.py
  - openjiuwen/agent_evolving/trajectory/processor.py
  - openjiuwen/agent_evolving/trajectory/model.py
  - openjiuwen/agent_evolving/trajectory/serialization.py
  - openjiuwen/agent_evolving/trajectory/spans.py
  - openjiuwen/agent_evolving/trajectory/windows.py
  - openjiuwen/agent_evolving/trajectory/messages.py
  - openjiuwen/agent_evolving/trajectory/team.py
  - openjiuwen/agent_evolving/trajectory/store.py
  - openjiuwen/harness/rails/evolution/evolution_rail.py
  - openjiuwen/harness/rails/evolution/team_skill_evolution_rail.py
  - openjiuwen/harness/rails/evolution/trajectory_rail.py
  - openjiuwen/harness/rails/evolution/symphony_execution_fragments.py
  - openjiuwen/harness/rails/evolution/symphony_edge_evidence.py
  - openjiuwen/harness/rails/evolution/symphony_graph_evolution_rail.py
  - openjiuwen/harness/rails/evolution/review/runtime.py
designs: []
design_note: 依据用户在本次任务中提供并要求执行的方案，无独立 D 文件。
---

# 演进轨迹消息序列共享

演进轨迹在采集时保留每次模型调用实际使用的序列。共享正文和前缀减少了多轮历史的重复占用，窗口与旧快照各自持有仍可读的记录，完整 OTLP 在导出时生成。当前契约见 [S_04](../specs/harness/S_04_rails-contract.md) 不变量 16。

## 实施与取舍

本次变更于 2026-11-23 在 #3848 的发布线基线上实施。#3848 在采集时复制完整的标准属性，使旧快照可以独立于 observability 注册表读取。现在采集阶段固定同一份内容的序列头，保留该读取保证，同时将全文拼接移到消费和导出阶段。

每个 processor 拥有两张弱引用索引。存活的序列头会持有当前正文与前驱节点。相同前缀复用节点，压缩后的独立分支仍可共享相同正文。首次命中源序列时在存储锁外获取源数据，在锁内复查并按前缀顺序补齐。持有关系跟随对应 span 属性，随 pending、窗口及快照的生命周期自然释放。

紧凑属性把头对象放在私有 OTLP 树的 `stringValue` 槽位。头同时提供哈希和强引用，合并、裁剪及快照复制只处理一棵树，也能保留重复身份记录各自的属性。全量解码会把重复分配移到窗口投影阶段。按轨迹另建强引用表需要在每次投影后同步移除失去对应记录的引用。两种做法均会增加窗口链路的成本。公开 OTLP 导出及自定义裁剪回调接收标准字符串，review scope 仍取得独立的快照对象。

## 验证与复现

仓内定向回归覆盖轨迹、演进 Rail、observability 与 RL 轨迹转换，共 1,202 项通过。并发用例让四个线程同时查询共享前缀和分叉，验证得到的头完整且对象共享。生命周期用例通过 SDK span 结束事件、双订阅和 Rail drain 检查注册表清空后的可读性、逐属性解码及弱引用回收。事件链用例在禁止解码的条件下执行裁剪、重建及模型消息投影。

定向回归命令如下，运行结果为 `1202 passed, 2 warnings`。两条警告来自既有 Pydantic 配置。

```bash
PYTHONPATH=. python -m pytest -q -o addopts='' \
  tests/unit_tests/agent_evolving/trajectory \
  tests/unit_tests/harness/rails/evolution \
  tests/unit_tests/extensions/observability \
  tests/unit_tests/agent_evolving/agent_rl/test_trajectory_to_rollouts.py
```

本地性能对照在 macOS 27.0.1 arm64、Python 3.13.14 和同一套依赖上运行。负载为 48 轮逐次追加，每条包含 4,096 个填充字符及轮次前缀，窗口保留 32 个 span，另持有第 8 轮快照。

逐轮输入独立对照脚本实际提交的标准属性。第 8 轮与最终窗口的 OTLP 比较仅归一化随机生成的轨迹 ID，span ID 和时间在输入端固定。保留堆在清空源注册表并回收后测量，峰值包含采集、drain 和窗口投影期间的 Python 分配。耗时是在启用 `tracemalloc` 的条件下测量。

| 指标，五次交替运行的中位数 | #3848 | 候选 | 变化 |
| --- | ---: | ---: | ---: |
| 保留堆 | 4,670,928 B | 429,970 B | -90.8% |
| 峰值堆 | 6,041,003 B | 1,726,660 B | -71.4% |
| 48 次 span 结束与捕获 | 10.417 ms | 10.182 ms | -2.3% |
| 48 次 drain 与窗口投影 | 401.773 ms | 439.504 ms | +9.4% |
| 读取一个 span 的模型输入 | 1.095 ms | 1.137 ms | +3.8% |
| 完整导出最终窗口 | 0.319 ms | 1.264 ms | +295.9% |

候选的正文与节点数量依次为 48/48、8/8、0/0，分别对应窗口加快照、仅快照及全部释放。以上是本地确定性负载与仓内回归结果。全仓回归、外部宿主与真实模型联调未执行。

## 检视修订

Team 筛选先用紧凑记录匹配类别和 trace，再还原命中的对外记录。Team Skill 归因用工具字段读取命中的
工具 span，仅含模型调用的轨迹在筛选工具或执行归因时触发 0 次还原。

轨迹相等性先比较结构、普通字段和紧凑序列的哈希，再对剩余的紧凑值与内联字符串逐项比较。元数据不同、
不同存储中序列内容相同这两种情况均触发 0 次还原。紧凑轨迹与导出再导入的等价轨迹在两个比较方向均相等。

新增的两个回归用例在前一候选上均复现了额外还原，修复后随同上述定向回归运行，结果为
`1204 passed, 2 warnings`。基准负载沿用之前的结果，本次修订用定向断言统计还原次数。

## Symphony 紧凑轨迹消费

Symphony 的片段投影、候选索引和端点摘要直接消费窗口中已有的紧凑 span。片段结构只读取分类所需的属性，
工具参数和结果在识别脚本、有效 Skill 读取和子 Agent 等分支读取。候选索引维持同 continuity 内重复身份的首条
记录。端点摘要只保留候选所引用的记录，保留相同 trace/span 身份的最后一条以及原有错误事件和长度限制。
空候选在遍历前返回。业务消息仍由 `_trajectory_to_messages()` 读取。

基线是在 `c500ced3e` 上已有消息序列存储和 Team/相等性修复的工作树快照。使用 macOS 27.0.1 arm64、
Python 3.13.14 和同一套依赖。确定性输入经过 processor 和真实 Symphony Rail 的 `before_invoke`、
逐轮 drain 与 `after_invoke`，演进结果交给进程内回调。两组负载均追加 48 轮并保留 32 个 span，
第 8 轮快照在各阶段保持存活。混合窗口保留 25 条模型调用、成员和多组 Skill 与工具证据，包含工具异常。
另用同一批 continuities 执行 Team 片段投影。

前后窗口及旧快照的 OTLP 指纹一致，仅将随机轨迹 ID 归一化。Agent/Team 片段和锚点、候选 ID 和顺序、
证据引用、受限候选、端点摘要与业务消息的序列化结果完全一致。空候选回归使用禁止遍历的 continuities，
确认摘要在访问轨迹前返回。

各组交替运行五次。表中峰值是阶段开始后 `tracemalloc` 观测到的新增 Python 堆，耗时包含追踪开销。

| 负载 | 阶段 | 还原次数（前 → 后） | 峰值 KiB（前 → 后） | 耗时 ms（前 → 后） |
| --- | --- | ---: | ---: | ---: |
| 纯模型 | Agent 片段 | 32 → 0 | 4,675.6 → 62.6 | 2.930 → 1.646 |
| 纯模型 | Team 片段 | 32 → 0 | 4,675.6 → 62.6 | 2.399 → 1.245 |
| 纯模型 | 候选 | 32 → 0 | 4,678.3 → 0.3 | 2.416 → 0.008 |
| 纯模型 | 摘要 | 32 → 0 | 4,678.0 → 0.1 | 2.328 → 0.006 |
| 纯模型 | 业务消息 | 32 → 32 | 4,928.4 → 4,928.4 | 35.387 → 34.983 |
| 混合 | Agent 片段 | 25 → 0 | 4,117.6 → 86.7 | 6.017 → 3.801 |
| 混合 | Team 片段 | 25 → 0 | 4,117.6 → 72.7 | 2.795 → 1.611 |
| 混合 | 候选 | 25 → 0 | 4,120.3 → 75.4 | 2.652 → 1.495 |
| 混合 | 摘要 | 25 → 0 | 4,119.9 → 71.0 | 4.429 → 2.934 |
| 混合 | 业务消息 | 25 → 25 | 4,311.2 → 4,311.2 | 31.492 → 31.542 |

性能脚本和基线补丁保存在工作树本地。源码 UT 独立验证结构投影按需读取及空候选零遍历。
调整后复跑四组定向回归，结果为 `1206 passed, 2 warnings`。

### 跨模块接口整改

生产调用使用明确命名的 `SequenceHead`、`compact_payload_for()`、`trim_compact_trajectory()`、
`Trajectory.copy_compact_payload()` 和 `Trajectory.snapshot()`。紧凑 payload 副本的容器可独立修改，共享的
序列节点保持不可变。Symphony 使用 `read_tool_input()` 和 `read_tool_output()` 分别读取所需字段，
底层结构化解码留在 `spans.py`。白盒测试继续访问内部状态以验证生命周期。

对照 `c500ced3e` 核查了生产改动及新增模块中的跨文件用法。四组定向回归通过，结果为
`1206 passed, 2 warnings`。本地 Symphony 基准以更新后的反向补丁恢复基线，纯模型和混合负载的
序列化输出逐字一致，结构阶段均为 0 次模型正文还原。

## Observability 与轨迹共用序列头

Observability 在登记成功后持有共享的不可变正文和序列头。捕获直接按哈希取得头，双订阅、多个 processor、
窗口和快照各自持有同一个对象。轨迹侧的弱索引及前缀导入路径已移除。完整序列仍可由存储编码器查询，
序列引用、OTLP 字段和内联回退沿用原格式。查找、TTL 与计费口径由 [S_14](../specs/agent_teams/S_14_monitor-and-observability.md)
不变量 19 维护。

实现选择在 sweep 时统一检查可达链，成功登记与完整查询刷新所涉及的前缀，快速查询只刷新命中的头。
并发登记测试检查了追加前缀、压缩分叉和跨分支正文共享。可控时钟测试覆盖前缀单独过期时仍被后继持有并计费，
完整查询恢复前缀的可查找状态，末个 registry 引用过期后只由快照持有的数据退出容量统计。弱引用断言验证了
registry、窗口和快照依次释放后的对象寿命。

### 同负载前后对照

直接基线为本轮修改前的 `trajectory-sequence-store` 工作树，已经包含双存储、选择性读取和 Symphony 紧凑投影。
使用同一个 Python 3.13.14（macOS 27.0.1 arm64），每种配置在新进程交替运行五次。每次注册 48 轮逐次追加的
模型输入，每条正文含 4,096 个填充字符，轨迹消费者保留 32-span 窗口和第 8 轮快照。0 processor 仅执行
观测 handler 和 SDK span 生命周期。登记包含在 handler 时间内，处理总计包含 handler、span 结束与捕获、
drain 与窗口投影。计时含 `tracemalloc` 开销。

0、1、4 processor 均对照 48 轮可读 OTLP 和存储编码输出。窗口与旧快照清空 registry 后仍可导出。
归一化轨迹 ID 后，窗口和旧快照的完整 OTLP 结果一致。独立运行 Symphony 的纯模型和混合负载，
Agent/Team 片段、候选及证据、摘要和业务消息逐字一致，结构阶段各为 0 次模型正文还原。

| 指标（中位数，前 → 后） | 0 processor | 1 processor | 4 processor |
| --- | ---: | ---: | ---: |
| 48 次登记（ms） | 1.525 → 1.952 | 2.608 → 3.345 | 1.552 → 2.219 |
| 48 次 handler（ms） | 37.654 → 35.952 | 53.088 → 50.004 | 35.428 → 36.084 |
| 48 次 span 结束与捕获（ms） | 0.246 → 0.189 | 15.389 → 12.202 | 33.410 → 18.553 |
| handler 与捕获合计（ms） | 37.899 → 36.131 | 68.477 → 64.767 | 68.837 → 54.655 |
| 处理总计（ms） | 37.997 → 36.222 | 805.984 → 675.057 | 1,777.228 → 1,749.983 |
| 临时峰值（KiB） | 1,261.408 → 1,262.270 | 1,485.155 → 1,461.144 | 1,831.472 → 1,736.205 |
| 清空 registry 前保留堆（KiB） | 231.099 → 229.279 | 447.964 → 422.068 | 795.167 → 697.036 |
| 清空 registry 后保留堆（KiB） | 8.285 → 8.285 | 426.022 → 413.541 | 773.172 → 688.777 |
| 单 span 可读 OTLP 导出（ms） | 1.011 → 0.824 | 1.642 → 0.885 | 0.910 → 0.789 |
| 单 span 业务消息读取（ms） | — | 1.760 → 1.366 | 1.143 → 1.174 |
| 最终窗口导出（ms） | — | 1.440 → 1.429 | 1.252 → 1.357 |

三组负载的 registry 计费均保持 207,024 B。仅观测负载的堆峰值基本持平，登记增加约 0.43 ms，
约合每轮 8.9 µs。4 processor 负载的登记增加约 0.67 ms，捕获减少约 14.86 ms，保留堆减少 12.3%。
数据支持保留共享方案。读写耗时受本地追踪和负载影响，以上为确定性回放结果。

本地脚本、反向补丁和复现入口保存在 `.agents/local/symphony-compact-bench/`。以下命令以采集性能数据时的 rebase 前工作树（本地提交 `31cfab2d6`）为候选源码，
两侧使用同一个已安装测试依赖的 Python。反向补丁可恢复与本次冻结基线逐字相同的七份源文件。

```bash
baseline=$(mktemp -d)
git archive 31cfab2d6 openjiuwen | tar -xf - -C "$baseline"
benchdir="$PWD/.agents/local/symphony-compact-bench"
candidate=$(mktemp -d)
git archive 31cfab2d6 openjiuwen | tar -xf - -C "$candidate"
patch -s -d "$baseline" -p1 < "$benchdir/shared-sequence-baseline.patch"

for n in 0 1 4; do
  PYTHONPATH="$baseline" python "$benchdir/benchmark_shared_sequence_head.py" --processors "$n" --verify "$baseline/base-$n.json"
  PYTHONPATH="$candidate" python "$benchdir/benchmark_shared_sequence_head.py" --processors "$n" --verify "$baseline/new-$n.json"
  cmp "$baseline/base-$n.json" "$baseline/new-$n.json"
  for run in 1 2 3 4 5; do
    PYTHONPATH="$baseline" python "$benchdir/benchmark_shared_sequence_head.py" --processors "$n" | tail -n 1
    PYTHONPATH="$candidate" python "$benchdir/benchmark_shared_sequence_head.py" --processors "$n" | tail -n 1
  done
done
```

验证覆盖 observability、演进轨迹、Harness 演进 Rail（含 Symphony）、Team observability 和 review feedback、
RL 轨迹转换及 SFT 数据和执行器 UT。首次运行有三项环境失败，在冻结基线复现。设置临时 `OPENJIUWEN_HOME`
并把锁定的 `pyarrow==24.0.0` 安装至临时模块目录后，同组重跑得到 `1320 passed, 3 warnings`。
验证未调用真实模型服务，TTL 清理由可控时钟驱动。
