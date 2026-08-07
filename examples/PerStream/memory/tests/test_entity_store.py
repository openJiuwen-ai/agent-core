from video_memory.memory.entity_store import EntityNormalizer, entity_from_mention
from video_memory.schemas import EntityMention


def test_entity_normalization_and_id_stability() -> None:
    normalizer = EntityNormalizer({"Apple Inc.": "Apple"})
    mention = EntityMention(" Apple Inc. ", "ORG")
    entity = entity_from_mention(mention, normalizer)
    same = entity_from_mention(EntityMention("apple", "ORG"), normalizer)
    assert entity.canonical_name == "apple"
    assert entity.entity_id == same.entity_id
