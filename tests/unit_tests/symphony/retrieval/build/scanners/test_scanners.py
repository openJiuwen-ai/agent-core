from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openjiuwen.symphony.retrieval.build.scanners.common import clean_first_paragraph, parse_frontmatter
from openjiuwen.symphony.retrieval.build.scanners.plugin import PluginScanner
from openjiuwen.symphony.retrieval.build.scanners.skill import SkillScanner


class ScannerFrontmatterTests(unittest.TestCase):
    def test_parse_frontmatter_supports_loose_multiline_values(self) -> None:
        content = "---\nname: loose-skill\ndescription: first line\nsecond line\n---\n\nbody\n"

        frontmatter, body = parse_frontmatter(content)

        self.assertEqual(frontmatter["name"], "loose-skill")
        self.assertEqual(frontmatter["description"], "first line\nsecond line")
        self.assertEqual(body.strip(), "body")

    def test_parse_frontmatter_supports_yaml_block_scalars(self) -> None:
        content = "---\nname: block-skill\ndescription: |\n  first line\n  second line\n---\n\nbody\n"

        frontmatter, body = parse_frontmatter(content)

        self.assertEqual(frontmatter["description"], "first line\nsecond line")
        self.assertEqual(body.strip(), "body")

    def test_clean_first_paragraph_strips_heading_and_links(self) -> None:
        body = "# Demo Skill\n\nUse [docs](https://example.invalid) to transform files."

        self.assertEqual(clean_first_paragraph(body), "Demo Skill")

    def test_skill_scanner_uses_full_description_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "skills.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "id": "block-skill",
                                "github_url": "https://example.invalid/repo",
                                "stars": 7,
                                "is_official": True,
                                "author": "tester",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            skill_dir = root / "block-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: Block Skill\ndescription: |\n  first line\n  second line\n---\n\n# block-skill\n",
                encoding="utf-8",
            )

            item = SkillScanner(root).scan_item_dir(skill_dir)

            self.assertIsNotNone(item)
            assert item is not None
            self.assertEqual(item.id, "block-skill")
            self.assertEqual(item.name, "Block Skill")
            self.assertEqual(item.description, "first line\nsecond line")
            self.assertEqual(item.github_url, "https://example.invalid/repo")
            self.assertEqual(item.stars, 7)
            self.assertTrue(item.is_official)
            self.assertEqual(item.author, "tester")

    def test_plugin_scanner_reads_plugin_yaml_and_legacy_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            yaml_plugin = root / "yaml-plugin"
            yaml_plugin.mkdir()
            (yaml_plugin / "plugin.yaml").write_text(
                "\n".join(
                    [
                        "name: yaml-plugin",
                        "display_name: YAML Plugin",
                        "description: YAML plugin description",
                        "metadata:",
                        "  author: yaml-author",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (yaml_plugin / "README.md").write_text("# YAML Plugin\n\nReadme body", encoding="utf-8")

            legacy_plugin = root / "legacy-plugin"
            (legacy_plugin / ".codex-plugin").mkdir(parents=True)
            (legacy_plugin / ".codex-plugin" / "plugin.json").write_text(
                '{"name": "legacy-plugin", "description": "Legacy plugin description", "author": "legacy-author"}',
                encoding="utf-8",
            )

            scanner = PluginScanner(root)
            yaml_item = scanner.scan_item_dir(yaml_plugin)
            legacy_item = scanner.scan_item_dir(legacy_plugin)

            self.assertIsNotNone(yaml_item)
            self.assertIsNotNone(legacy_item)
            assert yaml_item is not None
            assert legacy_item is not None
            self.assertEqual(yaml_item.name, "YAML Plugin")
            self.assertEqual(yaml_item.description, "YAML plugin description")
            self.assertEqual(yaml_item.author, "yaml-author")
            self.assertEqual(legacy_item.name, "legacy-plugin")
            self.assertEqual(legacy_item.description, "Legacy plugin description")
            self.assertEqual(legacy_item.author, "legacy-author")

    def test_plugin_scanner_falls_back_to_skill_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin_dir = root / "skill-plugin"
            plugin_dir.mkdir()
            (plugin_dir / "SKILL.md").write_text(
                "---\nname: skill-plugin\ndescription: |\n  first line\n  second line\n---\n\n# Body\n",
                encoding="utf-8",
            )

            item = PluginScanner(root).scan_item_dir(plugin_dir)

            self.assertIsNotNone(item)
            assert item is not None
            self.assertEqual(item.description, "first line\nsecond line")


if __name__ == "__main__":
    unittest.main()
