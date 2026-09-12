from __future__ import annotations

import re

from video_memory.retrieval.ranker import _jaccard, _tokens
from video_memory.schemas import MemoryNode, QAItem, QAParseResult, RankedNode


class FieldAwareRanker:
    def rank(self, qa: QAItem, parsed: QAParseResult, candidate_nodes: list[MemoryNode]) -> list[RankedNode]:
        question_main, options = split_multiple_choice(qa.question)
        question_tokens = _tokens(question_main)
        entity_tokens = set().union(*(_tokens(entity) for entity in parsed.entities)) if parsed.entities else set()
        ranked: list[RankedNode] = []

        for node in candidate_nodes:
            node_tokens = _tokens(node.description_text)
            lexical = _jaccard(question_tokens, node_tokens)
            entity_overlap = _jaccard(entity_tokens, node_tokens) if entity_tokens else 0.0
            option_overlap = _jaccard(_tokens(options), node_tokens) if options else 0.0
            field_bonus = field_bonus_score(question_main, node.description_text, qa.raw_type)
            type_bonus = _type_bonus(parsed, node, qa.raw_type)
            score = min(1.0, 0.15 + lexical * 0.45 + entity_overlap * 0.25 + option_overlap * 0.1 + field_bonus + type_bonus)
            ranked.append(RankedNode(node.node_id, score, f"field_rule bonus={field_bonus:.2f}"))

        return sorted(ranked, key=lambda item: item.score, reverse=True)


def field_bonus_score(question: str, node_text: str, raw_type: list[str] | None = None) -> float:
    q = question.lower()
    text = node_text.lower()
    bonus = 0.0

    if "last updated" in q and "last updated" in text:
        bonus += 0.35
    if any(term in q for term in ["price", "cost", "cheapest", "lowest", "total cost"]) and (
        "$" in text or re.search(r"\b\d+[,.]\d{2}\b", text)
    ):
        bonus += 0.28
    if any(term in q for term in ["email", "gmail", "address"]) and re.search(
        r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text
    ):
        bonus += 0.35
    if any(term in q for term in ["account number", "passport number"]) and re.search(r"\b[a-z]{1,3}\d{6,}\b|\b\d{8,}\b", text):
        bonus += 0.32
    if any(term in q for term in ["selected", "finally", "final", "key frames", "process"]) and any(
        term in text for term in ["selected", "created", "saved", "turned on", "finally", "alarm", "timer"]
    ):
        bonus += 0.18
    if any(term in q for term in ["most often", "more likely", "prefer", "preference"]) and any(
        term in text for term in ["may be interested", "may prefer", "searched", "viewed", "visited", "opened"]
    ):
        bonus += 0.15
    if any(term in q for term in ["summary", "most accurate summary", "based on"]) and any(
        term in text for term in ["viewed", "showed", "capital", "weather", "time", "flight", "hotel"]
    ):
        bonus += 0.12

    return min(0.45, bonus)


def split_multiple_choice(question: str) -> tuple[str, str]:
    match = re.search(r"\nA\.", question)
    if not match:
        return question, ""
    return question[: match.start()].strip(), question[match.start() :].strip()


def _type_bonus(parsed: QAParseResult, node: MemoryNode, raw_type: list[str]) -> float:
    raw = " ".join(raw_type).lower()
    if "preference" in raw and node.node_type == "preference":
        return 0.12
    if "summarization" in raw and node.node_type == "summary":
        return 0.1
    if parsed.qa_types and node.node_type in parsed.qa_types:
        return 0.04
    return 0.0

