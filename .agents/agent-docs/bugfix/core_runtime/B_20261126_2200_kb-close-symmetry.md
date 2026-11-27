---
id: B_20261126_2200_kb-close-symmetry
title: KnowledgeBase.close 异步分支无保护,单组件失败泄漏其余组件
type: B
author: linghb
author_email: 957733934@qq.com
modules:
  - openjiuwen/core
files:
  - openjiuwen/core/retrieval/knowledge_base.py
  - tests/unit_tests/core/retrieval/test_knowledge_base.py
designs: []
design_note: 异常保护对称性补齐,不改变契约与公开接口,无需独立设计。
---

# KnowledgeBase.close 异步分支无保护,单组件失败泄漏其余组件

## 现象与根因

`KnowledgeBase.close` 的 `_maybe_await` 里,同步 `obj.close()` 分支有
try/except + `logger.warning`,异步 `await obj.close()` 分支没有任何保护:

```python
if inspect.iscoroutinefunction(getattr(obj, "close", None)):
    await obj.close()              # 无保护
elif hasattr(obj, "close"):
    try:
        obj.close()
    except Exception:
        logger.warning("Failed to close object", exc_info=True)
```

`vector_store.close()` 是异步方法且抛错(如后端连接已断)时,异常直接传播:
`index_manager` 永不关闭(资源泄漏),且调用方从 `close()` 收到异常——close
的语义应是尽力释放全部资源,而不是中途放弃。同步分支抛错却静默告警继续,
两个分支行为不对称,是明显的疏漏而非设计。

## 修复决策

给异步分支加与同步分支完全相同的 try/except + `logger.warning`
(`exc_info=True`),两个组件顺序保持不变。close 的失败语义从
"首个失败即中止并抛出"变为"逐个尽力关闭,失败记日志",与同步分支既有
语义一致。

## 回归证据

基线为 `46014823e`(develop HEAD),环境为 Windows、Python 3.11、uv
同步测试环境。

新增测试 `test_close_async_failure_still_closes_remaining_components`:
vector_store 的异步 close 抛 `RuntimeError`,断言 `close()` 不抛出且
`index_manager.close` 仍被调用。修复前实测失败:RuntimeError 逃出,
index_manager.close 从未调用;修复后通过。

```powershell
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/core/retrieval/test_knowledge_base.py -q
# 9 passed
.venv/Scripts/python.exe -B -m pytest tests/unit_tests/core/retrieval/test_simple_knowledge_base.py tests/unit_tests/core/retrieval/test_graph_knowledge_base.py -q
# 33 passed
```

既有 4 个 close 用例(async/sync/None 组件/同步异常)不回归。

## 遗留

无。
