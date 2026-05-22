# coding: utf-8
"""Tests for MultimodalSkillReadRail and skill-bundle read_file helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage
from openjiuwen.core.sys_operation.cwd import init_cwd
from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
    MULTIMODAL_SKILL_USER_MESSAGE_NAME,
    REFERENCE_IMAGE_NOTE,
    SKILL_TOOL_MARKDOWN_IMAGES_HINT,
    MultimodalSkillReadRail,
    build_skill_bundle_image_lead_text,
    decorate_read_file_skill_bundle_user_messages,
    is_path_under_workspace_skills,
    merge_consecutive_read_file_skill_user_messages,
    parse_markdown_to_blocks,
)

from tests.unit_tests.harness.tools.mobile_gui.conftest import image_user_message


def test_parse_markdown_to_blocks_labels_skill_reference_images(tmp_path: Path):
    """Embedded local images become reference notes plus base64 image_url blocks."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "calendar_search.png"
    image_path.write_bytes(b"fake-image-bytes")

    blocks = parse_markdown_to_blocks(
        "Open Calendar.\n\n![Calendar Search](images/calendar_search.png)\n\nThen tap the event.",
        base_dir=tmp_path,
    )

    assert len(blocks) == 4
    assert blocks[0] == {"type": "text", "text": "Open Calendar."}
    assert blocks[1] == {
        "type": "text",
        "text": REFERENCE_IMAGE_NOTE.format(caption="Calendar Search"),
    }
    assert blocks[2]["type"] == "image_url"
    assert blocks[2]["image_url"]["url"].startswith("data:image/png;base64,")
    assert blocks[2]["image_url"]["detail"] == "low"
    assert blocks[3] == {"type": "text", "text": "Then tap the event."}


def test_parse_markdown_to_blocks_uses_filename_as_missing_alt_caption():
    """Missing alt text falls back to the image filename in the reference note."""
    blocks = parse_markdown_to_blocks("![](images/alarm_time.png)")

    assert len(blocks) == 2
    assert blocks[0] == {
        "type": "text",
        "text": REFERENCE_IMAGE_NOTE.format(caption="alarm_time.png"),
    }
    assert blocks[1] == {
        "type": "image_url",
        "image_url": {"url": "images/alarm_time.png", "detail": "low"},
    }


def test_expand_messages_prepends_short_hint_for_skill_tool_with_images(tmp_path: Path):
    """skill_tool results with markdown images get a one-line read_file reminder, not inlined pixels."""
    rail = MultimodalSkillReadRail(str(tmp_path))
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "name": "skill_tool",
                    "arguments": '{"skill_name": "scheduling", "relative_file_path": "SKILL.md"}',
                }
            ],
        ),
        ToolMessage(
            tool_call_id="call-1",
            name="skill_tool",
            content="data={'skill_content': 'Step one.\\n\\n![Alarm Time](images/alarm_time.png)'}",
        ),
    ]

    expanded = rail._expand_messages(messages)

    assert len(expanded) == 2
    assert expanded[0] is messages[0]
    tool_out = expanded[1]
    assert tool_out.role == "tool"
    assert tool_out.tool_call_id == "call-1"
    content = tool_out.content
    assert isinstance(content, str)
    assert content.startswith(SKILL_TOOL_MARKDOWN_IMAGES_HINT)
    assert "Step one." in content
    assert "![Alarm Time]" in content
    assert "[Skill reference image:" not in content
    assert "data:image" not in content


def test_expand_messages_skill_tool_hint_is_idempotent(tmp_path: Path):
    """Running expansion twice must not duplicate the markdown-images hint."""
    rail = MultimodalSkillReadRail(str(tmp_path))
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "name": "skill_tool",
                    "arguments": '{"skill_name": "scheduling", "relative_file_path": "SKILL.md"}',
                }
            ],
        ),
        ToolMessage(
            tool_call_id="call-1",
            name="skill_tool",
            content="data={'skill_content': '![x](y.png)'}",
        ),
    ]
    once = rail._expand_messages(messages)
    twice = rail._expand_messages(once)

    assert once[1].content == twice[1].content
    assert twice[1].content.count(SKILL_TOOL_MARKDOWN_IMAGES_HINT) == 1


def test_multimodal_read_rail_inline_mode_active_by_default(tmp_path: Path):
    """Default consult mode keeps inline read_file expansion enabled."""
    rail = MultimodalSkillReadRail(str(tmp_path))
    assert rail._inline_mode_active()


def test_multimodal_read_rail_noops_in_branch_mode(tmp_path: Path):
    """Branch consult mode disables inline skill read expansion on the main loop."""
    rail = MultimodalSkillReadRail(str(tmp_path), skill_consult_mode="branch")
    assert not rail._inline_mode_active()


def test_build_skill_bundle_image_lead_text_uses_explicit_caption():
    """Explicit caption from read_file tool args is echoed in the reference note."""
    body = build_skill_bundle_image_lead_text(
        "/workspace/skills/foo/images/bar.png",
        reference_caption="Calendar search",
    )
    assert body == REFERENCE_IMAGE_NOTE.format(caption="Calendar search")
    assert "not the current device screen" in body


def test_build_skill_bundle_image_lead_text_falls_back_to_path_stem():
    """Without caption, the file stem becomes the reference label."""
    body = build_skill_bundle_image_lead_text("/workspace/skills/foo/refs/q.png")
    assert body == REFERENCE_IMAGE_NOTE.format(caption="q")
    assert "[Skill reference image: q]" in body


def test_is_path_under_workspace_skills_accepts_bundle_files(tmp_path):
    """Paths under ``<cwd>/skills/`` are treated as skill-bundle assets."""
    workspace = tmp_path / "wk"
    png = workspace / "skills" / "myskill" / "pic.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"x")
    init_cwd(str(workspace))

    assert is_path_under_workspace_skills(str(png.resolve()))


def test_is_path_under_workspace_skills_rejects_outside_bundle(tmp_path):
    """Non-skill paths (e.g. docs/) are not rewritten as reference screenshots."""
    workspace = tmp_path / "wk"
    other = workspace / "docs" / "a.png"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"x")
    init_cwd(str(workspace))

    assert not is_path_under_workspace_skills(str(other.resolve()))


def test_decorate_read_file_rewrites_skills_user_message_and_name(tmp_path):
    """Skill-bundle read_file images are tagged ``multimodal_skill`` with documentation disclaimer."""
    workspace = tmp_path / "wk"
    png = workspace / "skills" / "s1" / "pic.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"x")
    init_cwd(str(workspace))
    resolved = str(png.resolve())

    assistant = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(
                id="t1",
                type="function",
                name="read_file",
                arguments=json.dumps(
                    {"file_path": "skills/s1/pic.png", "caption": "From SKILL alt"}
                ),
            )
        ],
    )
    user_img = image_user_message(resolved_path=resolved)
    messages = [assistant, ToolMessage(tool_call_id="t1", content="ok", name="read_file"), user_img]

    decorate_read_file_skill_bundle_user_messages(messages)

    assert user_img.name == MULTIMODAL_SKILL_USER_MESSAGE_NAME
    lead = user_img.content[0]["text"]
    assert "[Skill reference image: From SKILL alt]" in lead
    assert "example screenshot from the skill documentation" in lead.lower()
    assert user_img.content[1]["type"] == "image_url"


def test_decorate_read_file_noop_for_non_skills_path(tmp_path):
    """read_file images outside ``skills/`` keep the generic user message shape."""
    workspace = tmp_path / "wk"
    png = workspace / "docs" / "a.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"x")
    init_cwd(str(workspace))

    user_img = image_user_message(resolved_path=str(png.resolve()))
    decorate_read_file_skill_bundle_user_messages([user_img])

    assert user_img.name is None
    assert "Image loaded from read_file" in user_img.content[0]["text"]


def test_merge_consecutive_read_file_skill_user_messages(tmp_path):
    """Back-to-back skill read_file user turns collapse into one multimodal user message."""
    workspace = tmp_path / "wk"
    skill_dir = workspace / "skills" / "s1"
    skill_dir.mkdir(parents=True)
    paths = []
    for name in ("a.png", "b.png", "c.png"):
        p = skill_dir / name
        p.write_bytes(b"x")
        paths.append(str(p.resolve()))
    init_cwd(str(workspace))

    assistant = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(
                id=f"t{i}",
                type="function",
                name="read_file",
                arguments=json.dumps({"file_path": f"skills/s1/{name}", "caption": f"Cap {name}"}),
            )
            for i, name in enumerate(("a.png", "b.png", "c.png"))
        ],
    )
    tool_messages = [
        ToolMessage(tool_call_id=f"t{i}", content="ok", name="read_file") for i in range(3)
    ]
    user_messages = [
        image_user_message(resolved_path=path, image_b64=str(i)) for i, path in enumerate(paths)
    ]

    messages = [assistant, *tool_messages, *user_messages]
    decorate_read_file_skill_bundle_user_messages(messages)
    merged = merge_consecutive_read_file_skill_user_messages(messages)

    assert len(merged) == 5
    assert merged[0] is assistant
    assert all(isinstance(m, ToolMessage) for m in merged[1:4])

    combined = merged[4]
    assert isinstance(combined, UserMessage)
    assert combined.name == MULTIMODAL_SKILL_USER_MESSAGE_NAME

    text_blocks = [b["text"] for b in combined.content if b.get("type") == "text"]
    image_blocks = [b for b in combined.content if b.get("type") == "image_url"]
    assert len(text_blocks) == 3
    assert len(image_blocks) == 3
    assert any("Cap a.png" in t for t in text_blocks)
    assert any("reference image" in t and "c" in t for t in text_blocks)
    assert [b["image_url"]["url"] for b in image_blocks] == [
        "data:image/png;base64,0",
        "data:image/png;base64,1",
        "data:image/png;base64,2",
    ]


def test_merge_consecutive_read_file_leaves_non_skill_user_messages_separate(tmp_path):
    """Only consecutive skill reference users merge; generic read_file users stay separate."""
    workspace = tmp_path / "wk"
    skill_png = workspace / "skills" / "s1" / "a.png"
    skill_png.parent.mkdir(parents=True)
    skill_png.write_bytes(b"x")
    other_png = workspace / "docs" / "b.png"
    other_png.parent.mkdir(parents=True)
    other_png.write_bytes(b"x")
    init_cwd(str(workspace))

    skill_user = image_user_message(resolved_path=str(skill_png.resolve()), image_b64="AA")
    other_user = image_user_message(resolved_path=str(other_png.resolve()), image_b64="BB")
    messages = [skill_user, other_user]

    decorate_read_file_skill_bundle_user_messages(messages)
    merged = merge_consecutive_read_file_skill_user_messages(messages)

    assert len(merged) == 2
    assert merged[0].name == MULTIMODAL_SKILL_USER_MESSAGE_NAME
    assert merged[1].name is None
    assert merged[1].content[1]["image_url"]["url"] == "data:image/png;base64,BB"


def test_is_path_under_workspace_skills_with_patched_get_cwd(tmp_path):
    """Skill detection uses ``get_cwd()`` so relative ``skills/`` roots resolve correctly."""
    skills_dir = tmp_path / "skills"
    (skills_dir / "k").mkdir(parents=True)
    png = skills_dir / "k" / "x.png"
    png.write_bytes(b"x")

    with patch(
        "openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail.get_cwd",
        return_value=str(tmp_path),
    ):
        assert is_path_under_workspace_skills(str(png.resolve()))


def test_parse_markdown_to_blocks_plain_text_only():
    blocks = parse_markdown_to_blocks("Just text, no images.")
    assert blocks == [{"type": "text", "text": "Just text, no images."}]


def test_parse_markdown_to_blocks_remote_url_without_base64():
    """HTTP(S) images keep the URL (no base64) but still get a reference note."""
    blocks = parse_markdown_to_blocks("See ![logo](https://example.com/logo.png)")
    assert len(blocks) == 3
    assert blocks[0] == {"type": "text", "text": "See"}
    assert REFERENCE_IMAGE_NOTE.format(caption="logo") in blocks[1]["text"]
    assert blocks[2]["type"] == "image_url"
    assert blocks[2]["image_url"]["url"] == "https://example.com/logo.png"
    assert blocks[2]["image_url"]["detail"] == "low"


def test_expand_messages_leaves_skill_tool_without_markdown_unchanged(tmp_path: Path):
    rail = MultimodalSkillReadRail(str(tmp_path))
    original = ToolMessage(
        tool_call_id="c1",
        name="skill_tool",
        content="data={'skill_content': 'No images here.'}",
    )
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "name": "skill_tool",
                    "arguments": "{}",
                }
            ],
        ),
        original,
    ]
    expanded = rail._expand_messages(messages)
    assert expanded[1].content == original.content
    assert SKILL_TOOL_MARKDOWN_IMAGES_HINT not in expanded[1].content


def test_expand_messages_leaves_non_skill_tool_unchanged(tmp_path: Path):
    rail = MultimodalSkillReadRail(str(tmp_path))
    tool_msg = ToolMessage(
        tool_call_id="c2",
        name="wait",
        content="done",
    )
    expanded = rail._expand_messages(
        [
            AssistantMessage(
                content="",
                tool_calls=[
                    {"id": "c2", "type": "function", "name": "wait", "arguments": "{}"}
                ],
            ),
            tool_msg,
        ]
    )
    assert expanded[1].content == "done"


def test_decorate_read_file_uses_reference_caption_field_alias(tmp_path):
    workspace = tmp_path / "wk"
    png = workspace / "skills" / "s1" / "pic.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"x")
    init_cwd(str(workspace))
    resolved = str(png.resolve())

    assistant = AssistantMessage(
        content="",
        tool_calls=[
            ToolCall(
                id="t1",
                type="function",
                name="read_file",
                arguments=json.dumps(
                    {"file_path": "skills/s1/pic.png", "reference_caption": "Alias caption"}
                ),
            )
        ],
    )
    user_img = image_user_message(resolved_path=resolved)
    decorate_read_file_skill_bundle_user_messages(
        [assistant, ToolMessage(tool_call_id="t1", content="ok", name="read_file"), user_img]
    )
    assert "[Skill reference image: Alias caption]" in user_img.content[0]["text"]


def test_merge_single_skill_read_file_user_is_unchanged(tmp_path):
    workspace = tmp_path / "wk"
    png = workspace / "skills" / "s1" / "only.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"x")
    init_cwd(str(workspace))

    user_img = image_user_message(resolved_path=str(png.resolve()))
    decorate_read_file_skill_bundle_user_messages([user_img])
    merged = merge_consecutive_read_file_skill_user_messages([user_img])

    assert len(merged) == 1
    assert merged[0] is user_img


def test_apply_skill_tool_markdown_images_hint_idempotent():
    from openjiuwen.harness.tools.mobile_gui.rails.multimodal_skill_read_rail import (
        apply_skill_tool_markdown_images_hint,
    )

    body = "skill text"
    once = apply_skill_tool_markdown_images_hint(body)
    twice = apply_skill_tool_markdown_images_hint(once)
    assert twice == once
    assert twice.count(SKILL_TOOL_MARKDOWN_IMAGES_HINT) == 1
