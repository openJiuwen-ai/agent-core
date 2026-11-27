---
id: B_20261126_2165_jiuwenbox-windows-killpg
title: jiuwenbox 本地超时清理在 Windows 上因 AttributeError 失效留下孤儿进程
type: B
author: linghb
author_email: 957733934@qq.com
modules:
  - openjiuwen/extensions
files:
  - openjiuwen/extensions/sys_operation/sandbox/providers/jiuwenbox.py
  - tests/unit_tests/extensions/sys_operation/sandbox/test_jiuwenbox_kill_process_group.py
designs: []
design_note: 单点异常类型修正,不改变任何契约,无需独立设计。
---

# jiuwenbox 本地超时清理在 Windows 上因 AttributeError 失效留下孤儿进程

## 现象与根因

jiuwenbox provider 的本地回退执行路径
`_run_local_subprocess_process_group` 用
`subprocess.Popen(..., start_new_session=True)` 启动子进程(该参数在
Windows 上合法,进程可正常创建),超时后调用 `_kill_process_group` 清理。
该函数的回退链为:

```python
try:
    os.killpg(proc.pid, signal.SIGTERM)
except OSError:
    proc.terminate()
```

Windows 上 `os.killpg` 属性不存在,`os.killpg(...)` 抛出的是
`AttributeError` 而非 `OSError`,不被捕获直接向上传播——`proc.terminate()`
回退与后续 `proc.wait()` 都不执行,超时子进程成为孤儿继续运行(实测:
Windows + Python 3.14 上 `Popen(start_new_session=True)` 正常创建进程,
`os.killpg` 访问抛 `AttributeError: module 'os' has no attribute 'killpg'`,
`hasattr(os, "killpg")` 为 False)。外层 `_run` 的 `except Exception` 会兜住
异常并返回错误 dict,但进程本身没有被杀掉,长时间运行的任务会不断积累孤儿
进程。

同函数的 SIGKILL 升级路径存在完全相同的问题。

## 修复决策

两处 `except OSError` 扩为 `except (AttributeError, OSError)`,使 Windows
(或任何无 killpg 的平台)走 terminate/kill 回退,与 POSIX 行为对齐。
最小改动,不改任何成功路径。

## 回归证据

基线为 `46014823e`(develop HEAD),环境为 Windows(即缺陷触发平台)、
Python 3.11、uv 同步测试环境。

新增测试 `tests/unit_tests/extensions/sys_operation/sandbox/test_jiuwenbox_kill_process_group.py`
共 3 项:

1. `test_missing_killpg_still_terminates_child`:移除 `os.killpg` 模拟
   Windows,断言子进程被 fallback 终止。修复前实测失败于
   `jiuwenbox.py:1491: AttributeError`,进程残留;修复后通过。
2. `test_killpg_oserror_falls_back_to_terminate`:killpg 抛 OSError 时
   回退生效(POSIX 平台执行,Windows 自动跳过)。
3. `test_timeout_run_stops_the_child_group`:端到端,1 秒超时杀 30 秒
   sleep 子进程,exit_code=124。

修复前 1、3 失败(2 在 Windows 上因无 killpg 属性无法 patch,自动 skip),
修复后 2 通过 1 跳过。

```powershell
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/extensions/sys_operation/sandbox/test_jiuwenbox_kill_process_group.py -q
# 2 passed, 1 skipped
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/core/sys_operation/sandbox tests/unit_tests/extensions/sys_operation -q
# 128 passed, 162 skipped, 2 failed (adx_http 2 项为干净 develop 上同样失败的预存在问题)
```

## 遗留

无。`signal.SIGKILL` 在 Windows 上也不存在,但该升级路径同样已被
`except (AttributeError, OSError)` 覆盖(访问 `signal.SIGKILL` 的
AttributeError 与 os.killpg 的传播路径一致,先命中 os.killpg 一侧)。
