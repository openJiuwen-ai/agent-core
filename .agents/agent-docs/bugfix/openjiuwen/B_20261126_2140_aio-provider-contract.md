---
id: B_20261126_2140_aio-provider-contract
title: AIO sandbox provider 忽略 overwrite 与 cwd 契约并用 chunk_size 截断读取
type: B
author: linghb
author_email: 957733934@qq.com
modules:
  - openjiuwen/extensions
files:
  - openjiuwen/extensions/sys_operation/sandbox/providers/aio.py
  - tests/unit_tests/core/sys_operation/sandbox/test_aio_provider_contract.py
designs: []
design_note: 对 AIO provider 与兄弟 provider 契约对齐的修复,不改变公共接口,无需独立设计。
---

# AIO sandbox provider 忽略 overwrite 与 cwd 契约并用 chunk_size 截断读取

## 现象与根因

AIO sandbox provider 与基契约及兄弟 provider(jiuwenbox / adx)存在四处偏差:

1. **upload_file / upload_file_stream 忽略 overwrite**。基契约默认
   `overwrite=False`(core fs 契约透传),jiuwenbox 与 adx 在 overwrite=False
   时检查目标存在并抛 `FileExistsError`;aio 两个上传方法的签名里有
   `overwrite` 但函数体一次未引用,静默覆盖远端目标,构成跨 provider 契约
   违背与数据丢失风险。

2. **execute_code / execute_code_stream 写死 cwd**。签名接受 `cwd` 但固定
   传 `"."`(aio)或忽略;adx 正确透传 `cwd or "/tmp"`,调用方的工作目录
   参数被静默丢弃。

3. **_read_file_bytes 把 chunk_size 当截断长度**。下载完整内容后执行
   `content[:chunk_size]`;基契约中 `chunk_size` 是传输提示
   (`DEFAULT_READ_CHUNK_SIZE = 0`),不是读取上限,显式传入即静默丢数据
   (本地路径的 file_access.py 已有 `mode="bytes"` 消费方)。

## 修复决策

- 新增 `_target_may_exist`:用 SDK `client.file.find_files(path=父目录,
  glob=精确文件名)` 做存在性探测,三态返回(True=存在/False=不存在/
  None=探测失败)。**探测失败(fail-closed)与探测命中同样拒绝上传并返回
  错误结果**——与 jiuwenbox/adx 的失败方向一致(检查 API 抖动时宁可拒绝
  也不能把 overwrite 保护退化成静默覆盖),失败记 warning 日志。两个上传
  方法在 `not overwrite` 且探测命中时返回 `File already exists` 错误
  结果,探测失败时返回"refusing to upload"错误结果。
  报错通道说明:aio 与 jiuwenbox/adx 一样通过**返回错误结果**向调用方
  报告(并非抛 `FileExistsError`),调用方按返回值 code 判定。
- `execute_code` / `execute_code_stream` 改为 `cwd=cwd or "."`,保持原
  默认行为不变,仅在调用方显式传入时生效。
- 删除 `_read_file_bytes` 的 `content[:chunk_size]` 截断;默认
  chunk_size=0 时行为完全不变,显式传入时从静默丢数据变为完整返回。

不改动 upload_file_stream 的 UTF-8 解码问题(二进制经 `errors="replace"`
损坏属另一个更复杂的缺陷,base64 化涉及 write_file encoding 通道验证,
单独立项),本修复只做契约对齐。

## 回归证据

基线为 `46014823e`(develop HEAD),环境为 Windows、Python 3.11、uv 同步
测试环境。SDK `agent_sandbox` 为可选依赖,测试全部用 mock client,无网络。

新增测试 `tests/unit_tests/core/sys_operation/sandbox/test_aio_provider_contract.py`
共 12 项,初版修复前 2 失败(overwrite 不检查、chunk 截断)。检视意见
落地后为 12 项(补探测失败 fail-closed、对象形状条目、cwd 透传 3 类用例),
覆盖:
- overwrite=False 且目标存在 → 拒绝且不上传;目标不存在 → 正常上传;
- overwrite=True → 跳过存在性探测(不多花一次往返);
- 探测 API 报错 → 拒绝上传(fail-closed,与兄弟 provider 失败方向一致);
- find_files 返回无 path 属性的对象条目 → 拒绝上传,不再被吞成"不存在";
- 对象条目带 .path 字符串 → 与字符串条目同样匹配"已存在";
- stream 版同语义 + 首 chunk append=False、后续 append=True;
- bytes 读取显式 chunk_size=4 时返回完整 10 字节;默认参数行为不变;
- execute_code 显式 cwd="/data" 透传;无 cwd 时默认保持 "."。

既有回归:

```powershell
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/core/sys_operation/ -q
# 332 passed, 3 skipped
```

`tests/unit_tests/extensions/sys_operation/` 中 adx_http 的 2 项失败
(`test_sys_operation_uses_the_adx_http_provider[adx-environment/yuanrong]`)
在干净 develop 工作区同样失败,为预存在问题,与本修复无关。

## 遗留

- `upload_file_stream` 对非 UTF-8 二进制仍走 `decode(errors="replace")`,
  上传二进制文件会被损坏,需按 `_write_file_bytes` 的 base64 通道单独立项。
- `mock/test_local_provider.py::test_local_and_aio_providers_coexist`
  单独运行失败(测试未导入 extensions providers,依赖同目录其他测试的导入
  副作用),同样单独立项修复。
