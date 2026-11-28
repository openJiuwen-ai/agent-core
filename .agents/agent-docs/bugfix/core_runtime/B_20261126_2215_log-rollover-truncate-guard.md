---
id: B_20261126_2215_log-rollover-truncate-guard
title: 日志滚动备份复制失败仍截断活动文件造成日志段丢失
type: B
author: linghb
author_email: 957733934@qq.com
modules:
  - openjiuwen/core
files:
  - openjiuwen/core/common/logging/default/default_impl.py
  - tests/unit_tests/core/common/log/test_safe_rotating_handler.py
designs: []
design_note: 单点数据安全修复,不改变契约与公开接口,无需独立设计。
---

# 日志滚动备份复制失败仍截断活动文件造成日志段丢失

## 现象与根因

`SafeRotatingFileHandler.doRollover` 采用 copy + truncate 策略(为规避
Windows 文件锁)。滚动时的顺序是:

```python
if os.path.exists(self.baseFilename):
    try:
        shutil.copy2(self.baseFilename, dfn)   # 复制到 .1 备份
    except OSError:  # Copy failed, continue
        pass

# Truncate active log file               # 无条件截断
try:
    with open(self.baseFilename, "r+b") as f:
        f.seek(0); f.truncate(0)
except OSError:
    pass
```

复制失败(典型场景:磁盘满 ENOSPC;注释"Copy failed, continue"明示该
分支可达)后,截断依然无条件执行——该日志段既不在备份里也不在活动文件中,
唯一副本被销毁,且无任何日志线索。周围对 rename/remove 的失败都有
回退/跳过处理,唯独截断不依赖复制结果。

## 修复决策

复制成功(置 `copied` 标志)才执行截断;复制失败时保留活动文件原样。
不选择"复制失败抛出/告警中断滚动"——该 handler 本身定位 best-effort,
中断会阻塞调用方的日志写入路径;保留文件即保留了数据,这是代价最低且
零风险的次序修正。

**重试节流(检视意见落地)**:由于失败时不再截断,stdlib
`RotatingFileHandler.emit` 会在活动文件持续超过 maxBytes 时对**每条**
日志重新触发 `doRollover`,把每行日志变成一次整文件复制尝试。为此
`doRollover` 开头加了闸门:复制失败时记录当时活动文件长度
(`_rollover_failed_size`),后续调用在文件未增长超过该值 2 倍前直接
跳过滚动尝试(重开 stream 即返回),增长后重试;复制成功即重置状态。
避免日志热路径在磁盘满/文件被锁场景下放大为平方级 IO。

## 回归证据

基线为 `46014823e`(develop HEAD),环境为 Windows、Python 3.11、uv
同步测试环境。

新增测试 `tests/unit_tests/core/common/log/test_safe_rotating_handler.py`
共 3 项(此前该 handler 无任何直接测试):

1. `test_truncation_skipped_when_copy_fails`:patch `shutil.copy2` 抛
   `OSError(28, "No space left on device")`,断言活动文件内容完好。
   修复前实测失败:内容被截断为空。
2. `test_truncation_runs_when_copy_succeeds`:正常路径行为不变——
   备份产生、活动文件清空。
3. `test_handler_remains_usable_after_failed_rollover`:失败滚动后
   handler 仍可继续写入活动文件。
4. `test_consecutive_failures_are_throttled` / `test_successful_rollover_resets_throttle`:
   连续失败被闸门节流(文件未增长不重试、增长超 2 倍重试一次)、复制成功
   后节流状态重置。

```powershell
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/core/common/log/ tests/unit_tests/core/common/ -q
# common 全量 322 passed, 4 failed(security_url_utils 4 项为干净 develop 上同样失败的预存在问题)
# 新增 3 项全部通过
```

## 遗留

无。
