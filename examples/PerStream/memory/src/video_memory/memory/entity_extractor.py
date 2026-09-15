from __future__ import annotations

import re

from video_memory.schemas import EntityMention


class SpacyEntityExtractor:
    def __init__(
        self,
        model_name: str,
        allowed_labels: list[str] | None = None,
        conditional_labels: list[str] | None = None,
        blocked_labels: list[str] | None = None,
        blocklist: list[str] | None = None,
    ) -> None:
        self.model_name = model_name
        self.allowed_labels = set(allowed_labels or [])
        self.conditional_labels = set(conditional_labels or [])
        self.blocked_labels = set(blocked_labels or [])
        self.blocklist = {_normalize_for_filter(value) for value in (blocklist or [])}
        self._nlp = _load_spacy_model(model_name)

    def extract(self, text: str) -> list[EntityMention]:
        doc = self._nlp(text)
        mentions = []
        for ent in doc.ents:
            mention = EntityMention(
                text=ent.text,
                label=ent.label_,
                start_char=ent.start_char,
                end_char=ent.end_char,
            )
            if self._keep(mention, text):
                mentions.append(mention)
        return mentions

    def _keep(self, mention: EntityMention, context: str) -> bool:
        normalized = _normalize_for_filter(mention.text)
        if not normalized:
            return False
        if normalized in self.blocklist:
            return False
        if len(normalized) <= 1:
            return False
        if mention.label in self.blocked_labels:
            return False
        if self.allowed_labels and mention.label in self.allowed_labels:
            return True
        if mention.label in self.conditional_labels:
            return _keep_conditional(normalized, context)
        return False


def _load_spacy_model(model_name: str):
    """Load the spaCy pipeline, failing loudly when it is unavailable.

    This used to swallow the error and fall back to a capitalisation regex
    whose mentions were labelled FALLBACK — a label the shipped config then
    blocked, so extraction silently returned nothing. That left every node
    without entity edges and disabled graph propagation entirely, with no
    warning and no non-zero exit.
    """
    try:
        import spacy
    except ImportError as exc:
        raise RuntimeError(
            "Entity extraction requires spaCy, which is not installed. "
            "Install it with: pip install spacy"
        ) from exc

    try:
        return spacy.load(model_name)
    except OSError as exc:
        raise RuntimeError(
            f"spaCy model {model_name!r} is not installed. "
            f"Install it with: python -m spacy download {model_name}"
        ) from exc


def _normalize_for_filter(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.strip(".,:;!?()[]{}\"'")
    return text


def _keep_conditional(normalized: str, context: str) -> bool:
    if not normalized:
        return False
    if normalized.isdigit():
        keywords = {
            "age",
            "dead at",
            "price",
            "cost",
            "$",
            "rank",
            "rating",
            "option",
            "first",
            "second",
            "third",
            "middle",
            "last",
        }
        context_lower = context.lower()
        return any(keyword in context_lower for keyword in keywords)
    return True
