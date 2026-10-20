# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentTemplate card: display fields read from manifest.json alone."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.harness.resources import (
    AgentTemplateCard,
    localized_text,
    parse_agent_template_card,
    read_agent_template_card,
)


def _manifest(**fields: object) -> dict[str, object]:
    payload: dict[str, object] = {"package_type": "agent_template", "name": "finance", "description": "Finance"}
    payload.update(fields)
    return payload


@pytest.mark.level0
def test_card_keeps_display_fields_and_renders_them_by_language() -> None:
    card = parse_agent_template_card(
        _manifest(
            version=" 1.2.0 ",
            category="IndustryConsultant",
            avatar="avatars/a.png",
            display_name={"zh": "理财专家", "en": "Finance Expert"},
            display_description={"en": "Funds and stocks"},
            tags=[{"zh": "基金", "en": "Funds"}, "plain", {"en": ""}, 3],
            skills=[{"dir": "./skills/a-share"}, "skills/fund", {"dir": "skills/fund"}],
            mcps=[{"connector": "github"}, {"name": "local", "command": "x"}],
        )
    )
    assert card.version == "1.2.0"
    assert card.display_label("cn") == "理财专家", "cn reads the manifest's zh key"
    assert card.display_label("en") == "Finance Expert"
    assert card.display_text("cn") == "Funds and stocks", "missing language falls back to English"
    assert card.tag_labels("cn") == ("基金", "plain")
    assert card.tag_labels("en") == ("Funds", "plain")
    assert card.tags == ({"zh": "基金", "en": "Funds"}, "plain", {"en": ""}), "non-text tags are dropped"
    assert card.skills == ("a-share", "fund")
    assert card.connectors == ("github",)
    assert (card.runtime, card.runtime_provider) == ("native", None)


@pytest.mark.level0
def test_display_name_falls_back_to_name() -> None:
    card = parse_agent_template_card(_manifest())
    assert card.display_label("cn") == "finance"
    assert card.display_text("en") == "Finance"
    assert card.display_name == ""


@pytest.mark.level0
def test_runtime_marks_an_external_template() -> None:
    card = parse_agent_template_card(
        _manifest(runtime={"provider_name": " claudecode ", "config": {"skill_conflict": "replace"}})
    )
    assert (card.runtime, card.runtime_provider) == ("external", "claudecode")
    assert card.runtime_config == {"skill_conflict": "replace"}
    assert parse_agent_template_card(_manifest(runtime={})).runtime == "native"


@pytest.mark.level1
def test_card_is_lenient_except_for_package_type() -> None:
    card = parse_agent_template_card({"package_type": "agent_template"})
    assert card == AgentTemplateCard(name="", description="")
    with pytest.raises(ValueError, match="expected 'agent_template'"):
        parse_agent_template_card({"package_type": "plugin"})


@pytest.mark.level1
def test_localized_text_order() -> None:
    assert localized_text({"cn": "中", "zh": "文"}, "cn") == "中"
    assert localized_text({"fr": "bonjour"}, "cn") == "bonjour"
    assert localized_text("  plain ", "en") == "plain"
    assert localized_text({}, "en") == ""


@pytest.mark.level0
@pytest.mark.asyncio
async def test_read_card_reads_only_the_manifest(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(_manifest(persona={"dir": "missing-dir"}, tools=[{"file": "absent.py"}])),
        encoding="utf-8",
    )
    card = await read_agent_template_card(tmp_path / "manifest.json")
    assert card.name == "finance", "a card does not validate persona / tools, the full loader does"


@pytest.mark.level1
@pytest.mark.asyncio
async def test_read_card_maps_unreadable_manifests_to_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not readable"):
        await read_agent_template_card(tmp_path / "manifest.json")
    (tmp_path / "manifest.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        await read_agent_template_card(tmp_path / "manifest.json")
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        await read_agent_template_card(tmp_path / "manifest.json")
