# -*- coding: utf-8 -*-
"""
LSP publishDiagnostics 诊断捕获演示

演示 textDocument/didOpen 和 textDocument/didChange 两种触发诊断的方式：

  Demo 1 — open_file (textDocument/didOpen)
    创建一个含故意类型错误的 Python 文件，通过 open_file() 通知 pyright，
    等待并捕获 publishDiagnostics 通知中的诊断信息。

  Demo 2a — change_file, new errors (textDocument/didChange)
    将文件内容替换为另一批错误，通过 change_file() 触发增量诊断，
    捕获新出现的诊断（跨轮次去重确保旧诊断不重复出现）。

  Demo 2b — change_file, fix all errors (textDocument/didChange)
    修复所有错误，再次调用 change_file()，确认诊断队列为空。

运行前提：
    pyright 已安装（npm install -g pyright）

运行方式：
    uv run python examples/lsp/lsp_diagnostic_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent / "sample_code"))

from openjiuwen.harness.lsp import (
    initialize_lsp,
    shutdown_lsp,
    InitializeOptions,
    LSPServerManager,
    get_pending_lsp_diagnostics,
)

from sample_code.diagnostic_params import (
    BUGGY_FILENAME,
    CODE_V1,
    CODE_V2,
    CODE_CLEAN,
    safe_print,
    Psep,
    Pline,
    show_code,
    wait_for_diagnostics,
    print_diagnostics,
)

# ============================================================
# 配置
# ============================================================

# 示例代码目录（含 pyproject.toml，pyright 需要它来定位项目根）
SAMPLE_CODE = Path(__file__).parent / "sample_code"


# ============================================================
# 主演示逻辑
# ============================================================

async def main() -> None:
    Psep()
    safe_print("LSP publishDiagnostics 诊断捕获演示")
    safe_print("  Demo 1  — textDocument/didOpen  触发诊断")
    safe_print("  Demo 2a — textDocument/didChange 触发诊断（新错误）")
    safe_print("  Demo 2b — textDocument/didChange 修复错误（诊断清空）")
    Psep()
    safe_print(f"工作区: {SAMPLE_CODE}")

    if not SAMPLE_CODE.exists():
        safe_print(f"错误: 示例代码目录不存在: {SAMPLE_CODE}")
        return

    # 初始化 LSP（pyright 服务器将在第一次 open_file 时懒启动）
    safe_print("初始化 LSP...")
    await initialize_lsp(InitializeOptions(cwd=str(SAMPLE_CODE)))

    manager = LSPServerManager.get_instance()
    if manager is None:
        safe_print("错误: LSP 管理器未能初始化。请确认 pyright 已安装（npm install -g pyright）")
        return

    buggy_file = SAMPLE_CODE / BUGGY_FILENAME
    code_v1 = CODE_V1.format(filename=BUGGY_FILENAME)
    code_v2 = CODE_V2.format(filename=BUGGY_FILENAME)
    code_clean = CODE_CLEAN.format(filename=BUGGY_FILENAME)

    try:
        # --------------------------------------------------------
        # Demo 1: textDocument/didOpen -> publishDiagnostics
        # --------------------------------------------------------
        Psep()
        safe_print("Demo 1 — open_file (textDocument/didOpen)")
        Pline()

        buggy_file.write_text(code_v1, encoding="utf-8")
        safe_print(f"创建文件: {buggy_file.name}")
        show_code(code_v1)
        Pline()

        print("调用 open_file() -> textDocument/didOpen 已发送，等待 pyright 诊断", end="", flush=True)
        await manager.open_file(str(buggy_file), "python")
        diags_v1 = await wait_for_diagnostics(timeout=15.0, initial_delay=0.0)

        Pline()
        print_diagnostics("didOpen (v1)", diags_v1)

        # --------------------------------------------------------
        # Demo 2a: textDocument/didChange -> new errors
        # --------------------------------------------------------
        Psep()
        safe_print("Demo 2a — change_file (textDocument/didChange, 新错误)")
        Pline()

        safe_print(f"更新文件内容为 v2（不同位置的新错误）")
        show_code(code_v2)
        Pline()

        print("调用 change_file() -> textDocument/didChange 已发送，等待 pyright 诊断", end="", flush=True)
        await manager.change_file(str(buggy_file), "python", content=code_v2)
        diags_v2 = await wait_for_diagnostics(timeout=15.0, initial_delay=0.0)

        Pline()
        print_diagnostics("didChange (v2, 新错误)", diags_v2)
        safe_print("")
        safe_print("说明: v1 的错误已在上一轮送达，跨轮次去重（cross-round dedup）")
        safe_print("      确保它们不重复出现。只有 v2 中新位置的错误才会出现。")

        # --------------------------------------------------------
        # Demo 2b: textDocument/didChange -> fix all errors
        # --------------------------------------------------------
        Psep()
        safe_print("Demo 2b — change_file (textDocument/didChange, 修复所有错误)")
        Pline()

        safe_print("更新文件内容为 v3（无错误）")
        show_code(code_clean)
        Pline()

        print("调用 change_file() -> textDocument/didChange 已发送", end="", flush=True)
        await manager.change_file(str(buggy_file), "python", content=code_clean)

        # pyright 对已修复文件发送 diagnostics:[]，注册表忽略空列表。
        # 给 pyright 足够时间处理，然后确认队列为空。
        for _ in range(10):
            print(".", end="", flush=True)
            await asyncio.sleep(0.5)
        print(flush=True)

        diags_v3 = get_pending_lsp_diagnostics()
        Pline()
        print_diagnostics("didChange (v3, 已修复)", diags_v3)
        safe_print("")
        safe_print("说明: pyright 对无错误文件发送 diagnostics:[]，注册表忽略空列表。")
        safe_print("      v2 的错误已在上一轮送达，跨轮次去重防止重复出现。")
        safe_print("      结果: 无新诊断 = 文件已干净。")

        # --------------------------------------------------------
        # 总结
        # --------------------------------------------------------
        Psep()
        safe_print("演示完成。")
        v1_count = sum(len(f.diagnostics) for f in diags_v1)
        v2_count = sum(len(f.diagnostics) for f in diags_v2)
        v3_count = sum(len(f.diagnostics) for f in diags_v3)
        safe_print(f"  didOpen  (v1): {v1_count} 条诊断捕获")
        safe_print(f"  didChange (v2): {v2_count} 条诊断捕获（新错误，跨轮次去重生效）")
        safe_print(f"  didChange (v3): {v3_count} 条诊断捕获（已修复，诊断队列为空）")
        Psep()

    finally:
        if buggy_file.exists():
            buggy_file.unlink()
            safe_print(f"[清理] 已删除临时文件: {buggy_file.name}")
        await shutdown_lsp()


if __name__ == "__main__":
    asyncio.run(main())
