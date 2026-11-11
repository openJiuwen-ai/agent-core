---
id: B_20261111_2006_sandbox-shell-output
title: 本地沙箱内回读 Shell 超长输出
type: B
author: "羿寒 王"
author_email: 2127132571@qq.com
modules:
  - openjiuwen/harness
files:
  - openjiuwen/harness/tools/shell/_output_directory.py
  - openjiuwen/harness/tools/shell/bash/_output.py
  - openjiuwen/harness/tools/shell/bash/_tool.py
  - openjiuwen/harness/tools/shell/powershell/_output.py
  - openjiuwen/harness/tools/shell/powershell/_tool.py
  - tests/unit_tests/harness/tools/test_shell_output_sandbox.py
designs: []
design_note: 按已确认的沙箱内存储方案修复现有工具结果回读契约。
---

# 本地沙箱内回读 Shell 超长输出

关联 Issue [#2319](https://github.com/openJiuwen-ai/agent-core/issues/2319)，对应工具契约
[S_05](../../specs/harness/S_05_tools-contract.md)。

## 现象与根因

开启本地 `restrict_to_sandbox` 后，Bash 和 PowerShell 把超长输出直接保存到系统临时目录，
并把文件路径交给模型。后续 `ReadFileTool` 使用相同运行配置读取时，路径位于允许范围外，
读取返回 `199003`。输出保存链只接收 stdout / stderr，目录选择没有获得运行时沙箱配置。

## 修复决策

- 将运行时 `SysOperation` 从两种 Shell 的普通结果、流式最终结果和超时部分结果传到保存逻辑。
- 本地沙箱开启时，按 workspace、cwd、project root 顺序选择位于有效允许范围内的
  `.outputs`，并以显式沙箱根作为回退。目录解析符号链接后再校验范围。
- 显式沙箱根按照文件读取规则解析相对路径，包含空字符串表示进程当前目录的情况。
- 输出继续按用户分目录、按内容哈希命名。文件跨工具调用保留，供后续读取与搜索使用。
- 原有两参数保存调用使用可选参数兼容，非本地沙箱调用采用原临时目录规则。
- 扩大整个系统临时目录的访问范围会暴露无关临时文件，因此采用授权范围内存储。

## 回归证据

基线为 `3692bba2bb17d67bdf8a215889e01f0b30324200`，环境为 Windows、Python 3.11.9。

初版新增回归在修复前为 32 项失败、2 项通过。失败均发生在保存后通过真实
`ReadFileTool` 回读阶段，错误为路径位于沙箱外。

最终新增回归为 48 项通过，覆盖默认根、无 workspace、自定义根、workspace 和 cwd
位于自定义根外、相对根，以及普通成功、命令错误、流式最终结果和超时部分输出。
其中 4 项执行真实 Python 子进程，分别经过 Bash / PowerShell 的普通和流式入口。
另有短输出按需创建检查，以及无关临时文件持续被沙箱拒绝的检查。

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts= -p no:cacheprovider tests/unit_tests/harness/tools/test_shell_output_sandbox.py -q --tb=short --show-capture=no
```

相关 Shell 基线为 184 项通过、10 项跳过、3 项失败。失败为 Windows 下的
`test_timeout_returns_collected_output` 和
`test_command_deadline_returns_partial_output_and_reaps_process` 的两个参数场景。

扩展文件工具回归时，基线与候选均有 5 项 Grep 用例因结果为空而失败。基线对照使用
Git 中对应版本的 Shell 模块源码加载测试，文件工具结果为 88 项通过、5 项失败。
最终 48 项新增用例与相关测试组合时，结果为 320 项通过、10 项跳过、8 项失败，
失败仍为上述 3 项超时用例和 5 项 Grep 用例。

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts= -p no:cacheprovider tests/unit_tests/harness/tools/test_shell_output_sandbox.py tests/unit_tests/harness/tools/test_filesystem_tools.py tests/unit_tests/harness/tools/test_bash tests/unit_tests/harness/tools/test_powershell -q --tb=line --show-capture=no --disable-warnings
```
