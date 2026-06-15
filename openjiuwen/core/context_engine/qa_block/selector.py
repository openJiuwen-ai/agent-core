# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any, Literal

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.context_engine.qa_block.catalog import build_catalog_text, resolve_catalog_l1_text
from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.schema import QABlockEntry, QABlockRegistry

if TYPE_CHECKING:
    from openjiuwen.core.context_engine.qa_block.history_buffer import HistoryQABuffer

_CONTINUATION_HINTS = (
    "继续",
    "结合",
    "前面",
    "上一",
    "刚才",
    "再读",
    "接着",
    "基于",
    "根据",
    "沿用",
    "同上",
)
_QA_ID_PATTERN = re.compile(r"\bqa_\d{3}\b", re.IGNORECASE)
_TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]{2,}")

_SELECTOR_SYSTEM_PROMPT = """你是 Session QA 块选择器。根据【下一轮用户问题】与【QA 目录 Catalog】决定：需要把哪些历史 QA 块的 L0 原文 preload 进本轮上下文。

原则：
1. Catalog 摘要已在 system prompt 全量可见；仅当本轮回答**必须依赖**某历史 QA 的原文细节（工具链、文件路径、中间结论、续作上下文等）时才选中该 qa_id。
2. 与本轮问题无关的历史 QA（如自我介绍、上一话题、已完成且无关的任务）**不要** preload。
3. 续作/结合前文时，选**最相关**的块，最多 {max_blocks} 个；优先最近且话题连续的块。
4. 若 Catalog 摘要已足够回答、无需原文，返回空列表 []。
5. 用户明确提到 qa_xxx 或 handle=qa_xxx 时，必须包含对应 id。

只输出 JSON，不要其它文字：
{{"qa_ids": ["qa_001"], "reason": "简短中文理由"}}
qa_ids 最多 {max_blocks} 个，按相关度降序。"""


def extract_next_user_query(messages: list) -> str:
    """Return the latest user message text (current invoke query)."""
    for message in reversed(messages):
        role = getattr(message, "role", None)
        if role == "user":
            return (getattr(message, "content", "") or "").strip()
    return ""


def resolve_selector_model(agent: Any) -> Any | None:
    """Resolve the LLM used for QA block selection (same as main ReAct agent when possible)."""
    if agent is None:
        return None
    get_llm = getattr(agent, "_get_llm", None)
    if callable(get_llm):
        try:
            return get_llm()
        except Exception as exc:
            logger.warning("[QABlockSelector] _get_llm failed: %s", exc)
    react_agent = getattr(agent, "react_agent", None) or getattr(agent, "_react_agent", None)
    if react_agent is not None:
        react_get_llm = getattr(react_agent, "_get_llm", None)
        if callable(react_get_llm):
            try:
                return react_get_llm()
            except Exception as exc:
                logger.warning("[QABlockSelector] react_agent._get_llm failed: %s", exc)
    for attr in ("_llm", "model", "_model"):
        candidate = getattr(agent, attr, None)
        if candidate is not None:
            return candidate
    return None


def resolve_summarizer_model(agent: Any) -> Any | None:
    """Resolve the LLM used for QA block freeze summarization.

    Currently, shares the same resolution path as the Selector (main ReAct agent LLM).
    """
    return resolve_selector_model(agent)


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_PATTERN.findall(text or "")}


def _history_entries(registry: QABlockRegistry) -> list[QABlockEntry]:
    return sorted(
        (entry for entry in registry.blocks.values() if entry.is_history),
        key=lambda item: item.qa_index,
    )


def _entry_corpus(entry: QABlockEntry) -> str:
    return resolve_catalog_l1_text(entry)


def _score_entry(next_query: str, entry: QABlockEntry) -> float:
    query_tokens = _tokenize(next_query)
    if not query_tokens:
        return 0.0
    corpus_tokens = _tokenize(_entry_corpus(entry))
    if not corpus_tokens:
        return 0.0
    overlap = len(query_tokens & corpus_tokens) / len(query_tokens)
    if any(hint in next_query for hint in _CONTINUATION_HINTS):
        overlap += 0.05
    return overlap


def _all_small_history_preload(
    registry: QABlockRegistry,
    config: QABlockConfig,
) -> list[str] | None:
    """Return all history qa_ids when every block is small uncompressed native L0."""
    if not config.selector_all_small_enabled:
        return None
    entries = _history_entries(registry)
    if not entries:
        return None
    if len(entries) > config.selector_all_small_max_blocks:
        return None

    qa_ids: list[str] = []
    total = 0
    for entry in entries:
        if entry.had_full_compact_in_qa or entry.l0_content_mode != "delta":
            return None
        tokens = entry.approx_tokens
        if tokens <= 0:
            return None
        if tokens > config.selector_all_small_per_block_tokens:
            return None
        total += tokens
        qa_ids.append(entry.qa_id)

    if total > config.selector_all_small_total_tokens:
        return None
    return qa_ids


def _explicit_qa_ids(next_query: str, registry: QABlockRegistry) -> list[str]:
    found: list[str] = []
    for match in _QA_ID_PATTERN.findall(next_query):
        qa_id = match.lower()
        entry = registry.blocks.get(qa_id)
        if entry is not None and entry.is_history and qa_id not in found:
            found.append(qa_id)
    return found


def _rule_select(
    next_query: str,
    registry: QABlockRegistry,
    *,
    config: QABlockConfig,
) -> list[str]:
    """Deterministic fallback when LLM is unavailable or failed to parse."""
    explicit = _explicit_qa_ids(next_query, registry)
    if explicit:
        return explicit[: config.max_preload_blocks]

    entries = _history_entries(registry)
    if not entries:
        return []

    scored = [(entry.qa_id, _score_entry(next_query, entry)) for entry in entries]
    scored.sort(key=lambda item: item[1], reverse=True)
    selected = [qa_id for qa_id, score in scored if score >= config.selector_min_relevance][: config.max_preload_blocks]

    if selected:
        return selected

    if config.selector_fallback == "last_n" and any(hint in next_query for hint in _CONTINUATION_HINTS):
        return [entries[-1].qa_id]

    if config.selector_fallback == "last_n" and config.selector_fallback_on_empty:
        return [entries[-1].qa_id]

    return []


def _apply_last_n_fallback(
    next_query: str,
    registry: QABlockRegistry,
    selected: list[str],
    *,
    config: QABlockConfig,
) -> list[str]:
    if selected:
        return selected
    if config.selector_fallback != "last_n":
        return selected
    entries = _history_entries(registry)
    if not entries:
        return selected
    if any(hint in next_query for hint in _CONTINUATION_HINTS) or config.selector_fallback_on_empty:
        return [entries[-1].qa_id]
    return selected


def _normalize_llm_qa_ids(
    payload: Any,
    registry: QABlockRegistry,
    *,
    max_blocks: int,
) -> tuple[list[str], str]:
    if not isinstance(payload, dict):
        return [], ""
    reason = str(payload.get("reason") or payload.get("explanation") or "").strip()
    value = payload.get("qa_ids") or payload.get("selected_qa_ids")
    if not isinstance(value, list):
        return [], reason
    valid: list[str] = []
    for item in value:
        qa_id = str(item).lower()
        entry = registry.blocks.get(qa_id)
        if entry is None or not entry.is_history:
            continue
        if qa_id not in valid:
            valid.append(qa_id)
        if len(valid) >= max_blocks:
            break
    return valid, reason


def _parse_llm_qa_ids(raw: str, registry: QABlockRegistry, *, max_blocks: int) -> tuple[list[str], str]:
    text = (raw or "").strip()
    if not text:
        return [], ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return [], ""
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        return _normalize_llm_qa_ids(payload, registry, max_blocks=max_blocks)
    candidates = [match.lower() for match in _QA_ID_PATTERN.findall(text)]
    valid: list[str] = []
    for qa_id in candidates:
        entry = registry.blocks.get(qa_id)
        if entry is None or not entry.is_history:
            continue
        if qa_id not in valid:
            valid.append(qa_id)
        if len(valid) >= max_blocks:
            break
    return valid, ""


def fallback_rule_last_n(
    next_query: str,
    registry: QABlockRegistry,
    *,
    config: QABlockConfig | None = None,
) -> list[str]:
    """Deterministic selector fallback (rule + last_n) when LLM path fails catastrophically."""
    cfg = config or QABlockConfig()
    selected = _rule_select(next_query, registry, config=cfg)
    return _apply_last_n_fallback(next_query, registry, selected, config=cfg)


class QABlockSelector:
    """Pick history QA blocks to preload for the current invoke."""

    def __init__(self, config: QABlockConfig | None = None):
        self._config = config or QABlockConfig()

    async def select(
        self,
        next_query: str,
        registry: QABlockRegistry,
        history: HistoryQABuffer | None = None,
        *,
        model: object | None = None,
        catalog_text: str | None = None,
    ) -> list[str]:
        _ = history
        start = time.perf_counter()
        path = "unknown"
        result: list[str] = []
        session_id = registry.session_id
        try:
            cfg = self._config
            if not cfg.selector_enabled:
                path = "disabled"
                result = [entry.qa_id for entry in _history_entries(registry)]
                return result

            if not _history_entries(registry):
                path = "empty_history"
                result = []
                return result

            query = (next_query or "").strip()
            explicit = _explicit_qa_ids(query, registry)
            if explicit:
                path = "explicit"
                result = explicit[: cfg.max_preload_blocks]
                logger.info(
                    "[QABlockSelector] explicit qa_ids session_id=%s qa_ids=%s",
                    session_id,
                    result,
                )
                return result

            all_small = _all_small_history_preload(registry, cfg)
            if all_small is not None:
                path = "all_small"
                result = all_small
                total_tokens = sum(registry.blocks[qa_id].approx_tokens for qa_id in all_small)
                logger.info(
                    "[QABlockSelector] all_small shortcut session_id=%s blocks=%s total_tokens=%s qa_ids=%s",
                    session_id,
                    len(all_small),
                    total_tokens,
                    all_small,
                )
                return result

            mode: Literal["rule", "llm", "hybrid"] = cfg.selector_mode
            use_llm = mode in ("llm", "hybrid")
            rule_on_fail = mode == "rule" or cfg.selector_rule_fallback or mode == "hybrid"

            if use_llm and model is not None:
                llm_ids, reason, ok = await self._select_with_llm(
                    query,
                    registry,
                    model=model,
                    catalog_text=catalog_text,
                )
                if ok:
                    path = "llm"
                    result = llm_ids
                    logger.info(
                        "[QABlockSelector] llm selected session_id=%s qa_ids=%s reason=%s",
                        session_id,
                        llm_ids,
                        reason[:200] if reason else "",
                    )
                    return result
                logger.warning(
                    "[QABlockSelector] llm select failed session_id=%s fallback_rule=%s",
                    session_id,
                    rule_on_fail,
                )

            if mode == "llm" and not rule_on_fail:
                path = "last_n"
                result = _apply_last_n_fallback(query, registry, [], config=cfg)
                return result

            if rule_on_fail:
                selected = _rule_select(query, registry, config=cfg)
                result = _apply_last_n_fallback(query, registry, selected, config=cfg)
                if selected:
                    path = "rule"
                elif result:
                    path = "last_n"
                else:
                    path = "rule"
                logger.info(
                    "[QABlockSelector] rule fallback session_id=%s qa_ids=%s mode=%s",
                    session_id,
                    result,
                    mode,
                )
                return result

            path = "last_n"
            result = _apply_last_n_fallback(query, registry, [], config=cfg)
            return result
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "[QABlockSelector] done session_id=%s elapsed_ms=%.1f path=%s qa_ids=%s",
                session_id,
                elapsed_ms,
                path,
                result,
            )

    async def _select_with_llm(
        self,
        next_query: str,
        registry: QABlockRegistry,
        *,
        model: object,
        catalog_text: str | None = None,
    ) -> tuple[list[str], str, bool]:
        """Returns (qa_ids, reason, llm_succeeded)."""
        catalog = catalog_text if catalog_text is not None else build_catalog_text(registry)
        system_prompt = _SELECTOR_SYSTEM_PROMPT.format(
            max_blocks=self._config.max_preload_blocks,
        )
        user_prompt = f"## QA Catalog（L1 目录）\n{catalog}\n\n## 下一轮用户问题\n{next_query}\n"
        try:
            from openjiuwen.core.foundation.llm import JsonOutputParser, SystemMessage, UserMessage

            invoke = getattr(model, "invoke", None)
            if not callable(invoke):
                return [], "", False

            messages = [
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt),
            ]
            response = await invoke(messages, output_parser=JsonOutputParser())
            if isinstance(response, dict):
                qa_ids, reason = _normalize_llm_qa_ids(
                    response,
                    registry,
                    max_blocks=self._config.max_preload_blocks,
                )
                return qa_ids, reason, True

            content = getattr(response, "content", None) or str(response)
            qa_ids, reason = _parse_llm_qa_ids(
                content,
                registry,
                max_blocks=self._config.max_preload_blocks,
            )
            return qa_ids, reason, True
        except Exception as exc:
            logger.warning(
                "[QABlockSelector] llm invoke error session_id=%s error=%s",
                registry.session_id,
                exc,
            )
            return [], "", False
