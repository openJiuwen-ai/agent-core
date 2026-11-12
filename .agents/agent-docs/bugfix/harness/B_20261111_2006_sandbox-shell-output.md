---
id: B_20261111_2006_sandbox-shell-output
title: 本地沙箱内回读 Shell 超长输出
type: B
author: "羿寒 王"
author_email: 2127132571@qq.com
modules:
  - openjiuwen/harness
files:
  - openjiuwen/core/sys_operation/sys_operation.py
  - openjiuwen/harness/tools/shell/_output_directory.py
  - openjiuwen/harness/tools/shell/bash/_output.py
  - openjiuwen/harness/tools/shell/bash/_tool.py
  - openjiuwen/harness/tools/shell/powershell/_output.py
  - openjiuwen/harness/tools/shell/powershell/_tool.py
  - tests/unit_tests/harness/tools/test_shell_output_sandbox.py
  - tests/unit_tests/harness/tools/test_bash/test_output.py
  - tests/unit_tests/harness/tools/test_powershell/test_output.py
  - tests/unit_tests/core/sys_operation/test_sys_operation.py
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

## PR 审查后的异常与目录保护修复

审查对象为 PR [#2641](https://github.com/openJiuwen-ai/agent-core/pull/2641) 的初版提交
`259ff120c83a7c2eb55540bdb0df4b4854c0cfc1`。两条意见分别指出
[保存失败缺少异常保护](https://github.com/openJiuwen-ai/agent-core/pull/2641#discussion_r4537894838)和
[符号链接可能造成越界写入](https://github.com/openJiuwen-ai/agent-core/pull/2641#discussion_r4537895156)。
本节续写该修复的证据，上文保留初版验证结果。

### 复现与根因

- `PermissionError` / `OSError` 会从保存逻辑直接冒泡，影响普通结果、流式最终结果和超时部分输出。
  审查新增用例在旧实现的初轮结果为 22 项失败、2 项跳过、48 项未选中。
- 初版只检查最终目录，父目录在解析后被替换为 Windows 目录联接时，仍会在临时测试沙箱的
  `outside` 目录创建目录及文件。悬空文件符号链接也会被 `Path.exists()` 与 `write_bytes()` 组合跟随。
- Windows 目录句柄只禁止删除共享时，目录仍可被原地设置为 reparse point。
  新增两种 Shell 的原地修改测试均复现了越界写入。仅收紧共享标志仍不足以启用保护，
  目录句柄还必须请求 `FILE_LIST_DIRECTORY`，让系统执行共享检查。

### 修复决策

- 两种 Shell 的渲染层捕获保存过程中的路径、权限与 I/O 异常，返回有限的首尾预览及长度受限的
  错误说明。命令执行状态和退出码继续来自执行结果。
- 无授权目录时保留 `PermissionError`，交由渲染层降级。返回 `None` 会触发当前接口的系统临时目录
  回退，因此受限调用不能用该值表示保存失败。
- 写入逐级锚定目录。POSIX 使用 `O_DIRECTORY | O_NOFOLLOW` 及目录描述符，Windows 使用
  `FILE_LIST_DIRECTORY` 和仅允许读取共享的目录句柄，拒绝 reparse point 并阻止目录替换。
- 完整内容先写入随机且排他创建的暂存文件，再发布到内容哈希文件名。POSIX 使用相对目录描述符的
  `os.replace`。Windows 的普通重命名会与上述目录保护冲突，因此通过源文件句柄执行同父目录的
  `NtSetInformationFile(FileRenameInformation)`，在发布期间保留目录保护。
- 拒绝现有符号链接和非普通文件，发布后核对路径与文件身份。预先存在的硬链接会被替换目录项，
  外部原文件的内容保持原值。部分写入失败会清理暂存文件并释放句柄。

### 审查修复验证

环境继续使用 Windows、Python 3.11.9。输出相关定向回归为 118 项通过、12 项跳过。
原有真实子进程执行及 `ReadFileTool` 回读场景继续通过。

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts= -p no:cacheprovider tests/unit_tests/harness/tools/test_shell_output_sandbox.py tests/unit_tests/harness/tools/test_bash/test_output.py tests/unit_tests/harness/tools/test_powershell/test_output.py -q --tb=line --show-capture=no --disable-warnings
```

新增覆盖包括两种 Shell 的保存失败降级、目录被文件占用、目录解析后的联接替换、写入期间原地设置
联接、发布期间替换父目录、无授权目录时的预览返回、部分写入清理、长异常说明及已有硬链接。

完整相关回归使用上文的组合命令，结果为 354 项通过、12 项跳过、8 项失败。
失败项仍为初版已对照的 5 项 Grep 空结果与 3 项 Windows 超时用例。
12 项跳过包括原有 10 项平台权限测试，以及创建文件符号链接所需权限不足的 2 项测试。
Windows 目录联接的复现与修复测试均实际执行。POSIX 实现尚未在 Linux 环境运行，需由对应平台的 CI 验证。

## 2026-11-12 门禁详情核查与修正

门禁报告对应 `528bc4b7586879e40c8c6da807f99c243dfe732d`。报告中的 UT 与 Ruff 已通过，
CodeCheck 静态检查和开源合规检查失败。

### CodeCheck 报告

[静态检查详情](https://www.openlibing.com/apps/entryCheckDashCode/MR_7c84f9039374494889745a88c7973321/4ebb970c29dd6422d3c0f455c7f69cce?projectId=300075&codeHostingPlatformFlag=gitcode)
列出四项问题，均位于 `_output_directory.py`。

- `G.CLS.11` 指出第 26 行跨类访问 `_run_config`。新增 `SysOperation.work_config` 只读属性，
  提供实际生效的本地配置；远程沙箱模式返回 `None`。输出目录选择改用该公开接口。
- `G.FIO.01` 指出第 88 行 `os.open` 缺少权限参数。该调用只读打开已有目录，未使用 `O_CREAT`；
  修订后的统一打开函数显式传入 `0o700`。
- `G.PRM.03` 指出第 88、104 行未关闭描述符。原实现已通过 `ExitStack.callback` 释放，
  本次将打开和 `finally` 中的关闭集中到 `_open_posix_directory`，由 `ExitStack` 管理该上下文。
  描述符仍在逐级遍历和输出写入期间保持打开。

新增六项测试覆盖配置默认值、显式配置、只读属性、远程模式，以及描述符在正常和异常退出时的释放。
这些测试在修改前为六项失败；修订后的定向组合为 124 项通过、12 项跳过。
描述符释放测试使用受控替身，POSIX 目录描述符的真实平台运行仍需 Linux CI 验证。

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts= -p no:cacheprovider tests/unit_tests/core/sys_operation/test_sys_operation.py tests/unit_tests/harness/tools/test_shell_output_sandbox.py tests/unit_tests/harness/tools/test_bash/test_output.py tests/unit_tests/harness/tools/test_powershell/test_output.py -q --tb=line --show-capture=no --disable-warnings
```

完整相关回归为 360 项通过、12 项跳过、8 项失败，失败项与前述基线记录一致。
Windows 超时用例退出时另有异步任务取消与管道清理告警。

```powershell
.venv/Scripts/python.exe -B -m pytest -o addopts= -p no:cacheprovider tests/unit_tests/core/sys_operation/test_sys_operation.py tests/unit_tests/harness/tools/test_shell_output_sandbox.py tests/unit_tests/harness/tools/test_filesystem_tools.py tests/unit_tests/harness/tools/test_bash tests/unit_tests/harness/tools/test_powershell -q --tb=line --show-capture=no --disable-warnings
```

### 开源合规报告比对

[合规详情](https://www.openlibing.com/apps/personalScandTaskInfor/person/a373cf7e-8749-400f-bb78-185f62e821e3?projectId=300075&codeHostingPlatformFlag=gitcode)
包含七条记录，其中四条已自动确认，三条待分析。

- `_output_directory.py` 第 49–57、126–134 行被标记为与两个项目各有 7% 相似度。
  公开版本比对显示，匹配区域为 `ctypes` 对 `CreateFileW` 的参数和返回类型声明。
  对应文件为
  [hermes-feishu-streaming-card v4.7.1](https://github.com/baileyh8/hermes-feishu-streaming-card/blob/v4.7.1/hermes_feishu_card/cli.py#L6177)
  和 [image2editable v0.2.0](https://github.com/dsy-xueai/image2editable/blob/v0.2.0/image2editable/runtime_models.py#L459)。
- 测试文件第 302–329、336–340 行被标记为与 `seedance-2.0 v6.7.0` 有 5% 相似度。
  公开文件第 1309 行附近为 `DeviceIoControl` 的标准调用；报告同时列出的第 539–566 行
  为其他目录链接测试，不能仅据该行号对应关系认定复制了完整片段。
  [公开文件](https://github.com/emily2040/seedance-2.0/blob/v6.7.0/tests/test_install_payload.py#L1309)

这些记录的页面状态仍为未确认。分析入口要求通过 GitCode 登录，并在表单中指定审核人；
访客状态无法完成该审核。本次保留比对依据供项目审核人确认。
接口原型可对照 Microsoft 的 [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
和 [DeviceIoControl](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-deviceiocontrol) 文档。

## 2026-11-12 Windows 句柄生命周期整理

`c5b0c7f` 的门禁中 CodeCheck、UT、Ruff 均已通过，合规报告仍有三条待分析记录。
目录保护、暂存文件发布和目录联接测试辅助函数重复声明了 `CreateFileW` / `CloseHandle`，
并各自处理句柄取得和释放。

本轮将句柄生命周期集中到 `_open_windows_file`，通过 `WINFUNCTYPE` 绑定 Windows 调用约定、
参数和返回类型，使用 `use_last_error=True` 获取打开失败的错误码。
各调用方传入各自的访问权限、共享权限和文件标志，上下文正常退出或抛出异常时均关闭句柄。
目录联接测试辅助函数通过该上下文取得写句柄，并执行实际的 `DeviceIoControl` 设置操作。

新增三项真实 Windows 用例检查上下文内禁止重命名、正常与异常退出后可重命名，
以及文件不存在时在进入上下文前返回 `FileNotFoundError`。
新增测试在实现前均因缺少该上下文管理器失败。实现后与两项目录联接竞态测试组合为五项通过。

配置、沙箱输出、Bash 输出与 PowerShell 输出的定向组合为 127 项通过、12 项跳过。
完整相关回归为 363 项通过、12 项跳过、8 项失败。
失败用例名称与既有基线一致，为五项 Grep 空结果用例、
`test_timeout_returns_collected_output` 和
`test_command_deadline_returns_partial_output_and_reaps_process` 的两个参数场景。
