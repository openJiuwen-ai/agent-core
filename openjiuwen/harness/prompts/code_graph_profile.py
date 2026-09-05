# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt sections for the Code Graph profiles attached to a coding agent.

Product (``prompt_mode=product``): find_* tools help locate code, then the same
agent edits and tests. Eval (``prompt_mode=locate``): ContextBench locate exam
wording; testers inject this from ``scripts/eval``.
"""

from __future__ import annotations

from typing import Dict, Optional

from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.schema.code_graph import (
    CodeGraphProfile,
    CodeGraphRetrievalInterface,
    PROMPT_MODE_LOCATE,
    PROMPT_MODE_PRODUCT,
    resolve_code_graph_profile,
    resolve_code_graph_retrieval_interface,
)

GRAPH_PROFILE_PROMPT_EN = """\
Code Graph (profile: graph):
These tools index the repository so you can find code before you edit it.

- Named class, function, or method: resolve_symbol first, then read_symbol.
- Unknown name: find_code_symbols (candidates only), then read_symbol. Do not
  reword the same search repeatedly.
- Exact literals — error messages, config keys, decorators, registry calls:
  search_source_text. Do not use it to approximate callers or inheritance.
- Structure of a file or class: inspect_code_structure. Prefer it over reading
  a whole file.
- Neighbours: find_callers, find_callees, find_importers, find_base_classes,
  find_subclasses. Pass a full symbol_id.
- Multi-hop call paths: trace_call_paths with direction=callers or callees.
- Numbered source: read_symbol or read_code. context_before/after on
  read_symbol are capped at 5.
- select_code_context can mark spans you intend to change; it does not finish
  the task. After locating, edit the current source and run the relevant tests.
  Do not look for a submit tool — keep going with edit_file / write_file.
- A truncated or timed-out query is one query cut short: narrow the symbol or
  path and continue, or move to read_file.
- If a Code Graph tool returns BUILDING, keep using these tools (retry shortly).
  grep stays hidden while Code Graph is enabled.
- If a tool returns STALE, trust the old graph for what it did find and
  continue. Do not wait for a perfect refresh.
- If a Code Graph tool returns UNAVAILABLE, the repository exceeded indexing
  limits (file count, source bytes, symbols, edges, or process memory) or the
  parser is missing. Graph tools are then removed and grep/glob come back.
  Use grep, glob, and read_file. Do not call find_* or search_source_text
  again. A single ERROR is not that: narrow the query and retry the graph,
  or read_file.
"""

GRAPH_PROFILE_PROMPT_CN = """\
Code Graph（profile: graph）：
这些工具给仓库建索引，用来在改代码之前定位。

- 已知类名/函数名/方法名：先 resolve_symbol，再 read_symbol。
- 不知道精确名：用 find_code_symbols 生成候选，再 read_symbol。不要反复改写查询。
- 精确字面量（报错、配置键、decorator、registry）：用 search_source_text。
  不要用它去近似 callers 或继承。
- 查看文件或类结构：用 inspect_code_structure，优先于读取整个文件。
- 邻居：find_callers / find_callees / find_importers / find_base_classes /
  find_subclasses。请传完整 symbol_id。
- 多跳调用链：trace_call_paths，必须传 direction=callers 或 callees。
- 带行号的源码：read_symbol 或 read_code。read_symbol 的 context_before/after
  上限为 5。
- select_code_context 可以标记打算修改的 span，不结束任务。定位完后修改当前
  源码并跑相关测试。没有 submit 工具：继续用 edit_file / write_file。
- 单次查询被截断或超时只是这一次被裁剪：缩小 symbol 或路径后继续，或改用
  read_file。
- 若工具返回 BUILDING，继续用这些图工具（稍后重试）。启用 Code Graph 时
  grep 保持隐藏。
- 若工具返回 STALE，旧图找到的结果可以继续用，不要为了 100% 新鲜而停住。
- 若 Code Graph 工具返回 UNAVAILABLE，说明仓库超过索引上限（文件数、源码
  字节、符号、边或进程内存）或缺少 parser。这时会摘掉图工具并恢复
  grep/glob。用 grep、glob、read_file 继续，不要再调 find_* 或
  search_source_text。单次 ERROR 不是这种失败：缩小查询再试图工具，或改用
  read_file。
"""

LOCATE_EXAM_PROMPT_EN = """\
Code Graph (locate exam):
Resolve the code that must change, then submit it.
Do not write a patch. Do not rewrite File/Lines by hand.

1. If the issue names a class, function, or method, call resolve_symbol first.
2. Do not use text search to approximate callers, callees, imports, or inheritance.
3. Use search_source_text only for exact literals, messages, configuration keys,
   decorators, or relations the graph does not store.
4. Once a unique symbol is found, read_symbol before searching again.
5. Prefer the smallest symbol span that explains the change.
6. Never submit a large class body. If read_symbol says large_class, call
  inspect_code_structure and read_symbol on the methods that change.
7. Do not include tests unless the issue or selected implementation requires them.
8. For frames / transforms / registration across modules: after resolve, call
  find_importers, then search_source_text for register/decorator, then read.
9. Submit as soon as the primary location (and required supporting relations) are read.
10. context_before/context_after on read_symbol are capped at 5; do not request more.

Known method/function:
  resolve_symbol -> read_symbol -> submit_code_context
Large class with several methods:
  resolve_symbol -> inspect_code_structure -> read_symbol on 1-3 methods -> submit
Who calls a function:
  resolve_symbol -> find_callers -> read_symbol on relevant callers -> submit
Exact error string:
  search_source_text -> resolve containing symbol -> read_symbol -> submit
Cross-module registration:
  resolve_symbol -> find_importers -> search_source_text for registry/decorator -> read -> submit

find_code_symbols is candidate generation only (default 5). It is not the answer.
inspect_code_structure lists members of a file or class. Prefer it over reading the whole file.
trace_call_paths is for multi-hop paths only; pass direction=callers or callees.
submit_code_context generates <PATCH_CONTEXT>; never type the tags yourself.
"""

LOCATE_EXAM_PROMPT_CN = """\
Code Graph（定位考试）：
解析必须修改的代码然后提交。
不要写补丁。不要手写 File/Lines。

1. issue 里出现类名、函数名或方法名时，先 resolve_symbol。
2. 不要用文本搜索去近似 callers、callees、imports 或继承。
3. search_source_text 只用于精确字面量、报错、配置键、decorator，或图尚未覆盖的关系。
4. 唯一 symbol 一旦解析到，先 read_symbol，不要继续换措辞搜。
5. 尽量选能解释改动的最小 symbol span。
6. 禁止提交巨大 class。若 read_symbol 返回 large_class，先 inspect_code_structure，
   再 read_symbol 真正要改的 method。
7. 除非 issue 需要测试，否则不要纳入测试文件。
8. 跨模块 frame/transform/注册：resolve 后先 find_importers，再搜 register/decorator。
9. 主位置和必要支撑关系读完就 submit。
10. read_symbol 的 context_before/after 上限为 5，不要请求更多。

已知方法：resolve_symbol -> read_symbol -> submit_code_context
大类多方法：resolve_symbol -> inspect_code_structure -> 读 1-3 个 method -> submit
谁调用：resolve_symbol -> find_callers -> 读相关 callers -> submit
精确报错：search_source_text -> 解析所在 symbol -> read_symbol -> submit
跨模块注册：resolve_symbol -> find_importers -> 搜 registry/decorator -> 读 -> submit

find_code_symbols 只生成候选（默认 5 条），不是最终答案。
inspect_code_structure 列出文件或类的成员，优先于读取整个文件。
trace_call_paths 只用于多跳路径，必须传 direction=callers 或 callees。
submit_code_context 会生成 <PATCH_CONTEXT>，不要自己打标签。
"""

GRAPH_FOCUSED_PROFILE_PROMPT_EN = """\
Code Graph (profile: graph, retrieval_interface: focused):
Search returns short candidates. Do not read every hit. Focus, then edit.

1. Named class / function / method: resolve_symbol, then focus_code.
2. Unknown name: find_code_symbols (at most 5 summaries). Pick one candidate.
3. Exact literals (quoted text, errors, config keys, decorators):
   search_source_text with match_mode=exact. Use match_mode=lexical for
   natural-language behaviour. auto chooses for you.
4. Results are grouped: implementation / test / build. Edit implementation
   unless the issue is about tests or packaging.
5. Call focus_code(candidate_id) to open a 50-100 line window. That is the
   edit target. Do not keep searching the same query.
6. inspect_code_structure for a large class before focusing a method.
7. After a focused window, edit the current source and run the relevant tests.
   There is no select_code_context or submit tool.
8. Relation tools (find_callers, find_importers, ...) are not on the default
   table. Only use them when the issue itself asks about callers or registration.
9. If a tool returns UNAVAILABLE, graph tools come off and grep/glob return.
   A single ERROR is not that: narrow the query or read_file.
"""

GRAPH_FOCUSED_PROFILE_PROMPT_CN = """\
Code Graph（profile: graph，retrieval_interface: focused）：
检索只返回摘要候选。不要把每条命中都读完。先聚焦，再编辑。

1. 已知类/函数/方法：resolve_symbol，然后 focus_code。
2. 不知道精确名：find_code_symbols（最多 5 条摘要），选一个候选。
3. 精确字面量（引号文本、报错、配置键、decorator）：
   search_source_text，match_mode=exact。自然语言行为用 lexical。auto 会代选。
4. 结果按 implementation / test / build 分组。默认改 implementation，
   除非 issue 本身要求改测试或打包。
5. 用 focus_code(candidate_id) 打开 50–100 行窗口，这就是编辑目标。
   不要对同一查询反复搜索。
6. 大类先 inspect_code_structure，再聚焦具体方法。
7. 聚焦后改当前源码并跑相关测试。没有 select_code_context，也没有 submit。
8. find_callers / find_importers 等不在默认工具表。只有 issue 明确问调用者
   或注册时才用。
9. 若返回 UNAVAILABLE，图工具会摘掉并恢复 grep/glob。单次 ERROR 不是这种
   失败：缩小查询或改用 read_file。
"""

GRAPH_PROFILE_PROMPT: Dict[str, str] = {
    "en": GRAPH_PROFILE_PROMPT_EN,
    "cn": GRAPH_PROFILE_PROMPT_CN,
}

GRAPH_FOCUSED_PROFILE_PROMPT: Dict[str, str] = {
    "en": GRAPH_FOCUSED_PROFILE_PROMPT_EN,
    "cn": GRAPH_FOCUSED_PROFILE_PROMPT_CN,
}

LOCATE_EXAM_PROMPT: Dict[str, str] = {
    "en": LOCATE_EXAM_PROMPT_EN,
    "cn": LOCATE_EXAM_PROMPT_CN,
}


def build_code_graph_profile_prompt(
    profile: object,
    *,
    language: Optional[str] = None,
    prompt_mode: str = PROMPT_MODE_PRODUCT,
    retrieval_interface: object = None,
) -> str:
    """Prompt text for one profile. Empty string when the profile is off."""
    resolved_profile = resolve_code_graph_profile(profile)
    resolved_language = resolve_language(language)
    if resolved_profile == CodeGraphProfile.OFF:
        return ""
    mode = (prompt_mode or PROMPT_MODE_PRODUCT).strip().lower()
    if mode == PROMPT_MODE_LOCATE:
        return LOCATE_EXAM_PROMPT.get(resolved_language, LOCATE_EXAM_PROMPT["cn"])
    if (
        resolve_code_graph_retrieval_interface(retrieval_interface)
        == CodeGraphRetrievalInterface.FOCUSED
    ):
        return GRAPH_FOCUSED_PROFILE_PROMPT.get(
            resolved_language, GRAPH_FOCUSED_PROFILE_PROMPT["cn"]
        )
    return GRAPH_PROFILE_PROMPT.get(resolved_language, GRAPH_PROFILE_PROMPT["cn"])


__all__ = [
    "GRAPH_FOCUSED_PROFILE_PROMPT",
    "GRAPH_PROFILE_PROMPT",
    "LOCATE_EXAM_PROMPT",
    "build_code_graph_profile_prompt",
]
