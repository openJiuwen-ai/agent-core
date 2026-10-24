from __future__ import annotations

import re
from hashlib import sha1

from video_memory.schemas import Entity, EntityMention


class EntityNormalizer:
    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self.aliases = {self.normalize_text(key): self.normalize_text(value) for key, value in (aliases or {}).items()}

    def normalize(self, mention: EntityMention | str) -> str:
        text = mention.text if isinstance(mention, EntityMention) else mention
        normalized = self.normalize_text(text)
        return self.aliases.get(normalized, normalized)

    @staticmethod
    def normalize_text(text: str) -> str:
        text = text.strip().lower()
        text = re.sub(r"\s+", " ", text)
        text = text.strip(".,:;!?()[]{}\"'")
        return text


def make_entity_id(canonical_name: str) -> str:
    digest = sha1(canonical_name.encode("utf-8")).hexdigest()[:12]
    return f"ent_{digest}"


def entity_from_mention(mention: EntityMention, normalizer: EntityNormalizer) -> Entity:
    canonical = normalizer.normalize(mention)
    return Entity(
        entity_id=make_entity_id(canonical),
        canonical_name=canonical,
        entity_type=mention.label,
        aliases=[mention.text] if mention.text != canonical else [],
    )

