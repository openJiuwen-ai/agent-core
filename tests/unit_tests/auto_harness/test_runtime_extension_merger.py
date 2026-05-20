# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime extension merger tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openjiuwen.auto_harness.infra.runtime_extension_merger import (
    MergedExtensionError,
    merge_runtime_extensions,
)
from openjiuwen.auto_harness.schema import RuntimeExtensionArtifact


def _write_minimal_ext(
    base_dir: Path,
    *,
    extension_name: str,
    extra_files: dict[str, str] | None = None,
    extra_rail: bool = False,
    extra_tool: bool = False,
) -> RuntimeExtensionArtifact:
    """Write a minimal extension for merge testing."""
    root = base_dir / extension_name
    (root / "tools").mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "tools" / "__init__.py").write_text(
        "", encoding="utf-8"
    )

    files: dict[str, str] = {
        "tools/helper.py": 'VALUE = "ok"\n',
    }
    if extra_files:
        files.update(extra_files)
    if extra_rail:
        (root / "rails").mkdir(parents=True, exist_ok=True)
        (root / "rails" / "__init__.py").write_text(
            "", encoding="utf-8"
        )
        (root / "rails" / "demo_rail.py").write_text(
            "from openjiuwen.harness.rails.base import "
            "DeepAgentRail\n\nclass DemoRail(DeepAgentRail):\n"
            "    pass\n",
            encoding="utf-8",
        )
        files["rails/demo_rail.py"] = ""
    if extra_tool:
        (root / "tools" / "demo_tool.py").write_text(
            "from __future__ import annotations\n"
            "from typing import Any, AsyncIterator, Dict\n"
            "from .helper import VALUE\n"
            "from openjiuwen.core.foundation.tool import "
            "Tool, ToolCard\n"
            "\nclass DemoTool(Tool):\n"
            "    def __init__(self) -> None:\n"
            "        super().__init__(ToolCard("
            "id='tool', name='demo', description=VALUE))\n"
            "    async def invoke(self, inputs: Dict[str, "
            "Any], **kwargs: Any) -> Dict[str, Any]:\n"
            "        return {'value': VALUE}\n"
            "    async def stream(self, inputs: Dict[str, "
            "Any], **kwargs: Any) -> AsyncIterator[Dict[str, "
            "Any]]:\n"
            "        yield await self.invoke(inputs, **kwargs)\n",
            encoding="utf-8",
        )
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    if extra_rail:
        rails_section = (
            f"\n    - type: package\n"
            f"      module: openjiuwen.extensions.harness."
            f"{extension_name}.rails.demo_rail\n"
            f"      class: DemoRail"
        )
    else:
        rails_section = " []"
    if extra_tool:
        tools_section = (
            f"\n    - type: package\n"
            f"      module: openjiuwen.extensions.harness."
            f"{extension_name}.tools.demo_tool\n"
            f"      class: DemoTool"
        )
    else:
        tools_section = " []"

    (root / "harness_config.yaml").write_text(
        f"schema_version: harness_config.v0.1\n"
        f"name: {extension_name}\n"
        f"resources:\n"
        f"  rails:{rails_section}\n"
        f"  tools:{tools_section}\n",
        encoding="utf-8",
    )
    return RuntimeExtensionArtifact(
        extension_name=extension_name,
        runtime_path=str(root),
        config_path=str(root / "harness_config.yaml"),
    )


def _dir_hash(root: Path) -> str:
    """Deterministic hash of a directory tree."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class TestMergeRuntimeExtensions:
    """Tests for merge_runtime_extensions."""

    def test_zero_conflict(self, tmp_path: Path):
        """Two extensions with no overlapping files."""
        ext_a = _write_minimal_ext(
            tmp_path,
            extension_name="ext_a",
            extra_files={"tools/a_only.py": "A=1\n"},
        )
        ext_b = _write_minimal_ext(
            tmp_path,
            extension_name="ext_b",
            extra_files={
                "tools/b_only.py": "B=1\n",
                # Override the shared helper.py to avoid conflict —
                # _write_minimal_ext always writes tools/helper.py,
                # so we give ext_b a different helper name.
                "tools/helper_b.py": 'VALUE_B = "ok"\n',
            },
        )
        # Remove the conflicting helper.py from ext_b
        (tmp_path / "ext_b" / "tools" / "helper.py").unlink()

        result = merge_runtime_extensions(
            [ext_a, ext_b], tmp_path
        )
        assert (
            result.runtime_ext.extension_name
            == "merged_extensions"
        )
        merged_root = Path(result.runtime_ext.runtime_path)
        assert merged_root.is_dir()
        assert (merged_root / "harness_config.yaml").is_file()
        # No renames needed
        assert result.rename_map == {}
        assert result.skill_rename_map == {}
        # Files kept with original names
        assert (merged_root / "tools" / "helper.py").is_file()
        assert (
            merged_root / "tools" / "a_only.py"
        ).is_file()
        assert (
            merged_root / "tools" / "b_only.py"
        ).is_file()
        assert (
            merged_root / "tools" / "helper_b.py"
        ).is_file()
        # All __init__.py are empty
        for init in merged_root.rglob("__init__.py"):
            assert init.read_bytes() == b""
        # M1: manifest modules start with merged prefix
        import yaml
        with open(
            merged_root / "harness_config.yaml",
            encoding="utf-8",
        ) as fh:
            data = yaml.safe_load(fh)
        for kind in ("rails", "tools"):
            for spec in data.get("resources", {}).get(
                kind, []
            ):
                if spec.get("type") == "package":
                    assert spec["module"].startswith(
                        "openjiuwen.extensions.harness."
                        "merged_extensions"
                    )

    def test_conflict_file(self, tmp_path: Path):
        """Two extensions both have tools/helper.py."""
        ext_a = _write_minimal_ext(
            tmp_path,
            extension_name="ext_a",
            extra_files={
                "tools/helper.py": 'VALUE = "from_a"\n'
            },
        )
        ext_b = _write_minimal_ext(
            tmp_path,
            extension_name="ext_b",
            extra_files={
                "tools/helper.py": 'VALUE = "from_b"\n'
            },
        )
        result = merge_runtime_extensions(
            [ext_a, ext_b], tmp_path
        )
        merged_root = Path(result.runtime_ext.runtime_path)
        # Both renamed
        assert (
            merged_root / "tools" / "helper__ext_a.py"
        ).is_file()
        assert (
            merged_root / "tools" / "helper__ext_b.py"
        ).is_file()
        # rename_map only has the conflict
        assert ("ext_a", "tools/helper.py") in (
            result.rename_map
        )
        assert ("ext_b", "tools/helper.py") in (
            result.rename_map
        )
        assert (
            result.rename_map[("ext_a", "tools/helper.py")]
            == "tools/helper__ext_a.py"
        )

    def test_conflict_same_tool_path_manifest_modules(
        self, tmp_path: Path,
    ):
        """Two extensions share ``tools/name.py``; manifest must track renames."""
        ext_a = tmp_path / "ext_a"
        ext_b = tmp_path / "ext_b"
        for root, name, cls in (
            (ext_a, "ext_a", "ToolFromExtA"),
            (ext_b, "ext_b", "ToolFromExtB"),
        ):
            root.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "__init__.py").write_text("", encoding="utf-8")
            (root / "tools" / "__init__.py").write_text("", encoding="utf-8")
            (root / "tools" / "shared_tool.py").write_text(
                f"class {cls}:\n    __slots__ = ()\n",
                encoding="utf-8",
            )
            (root / "harness_config.yaml").write_text(
                f"schema_version: harness_config.v0.1\n"
                f"name: {name}\n"
                f"resources:\n"
                f"  rails: []\n"
                f"  tools:\n"
                f"    - type: package\n"
                f"      module: openjiuwen.extensions.harness.{name}"
                f".tools.shared_tool\n"
                f"      class: {cls}\n",
                encoding="utf-8",
            )
        art_a = RuntimeExtensionArtifact(
            extension_name="ext_a",
            runtime_path=str(ext_a),
            config_path=str(ext_a / "harness_config.yaml"),
        )
        art_b = RuntimeExtensionArtifact(
            extension_name="ext_b",
            runtime_path=str(ext_b),
            config_path=str(ext_b / "harness_config.yaml"),
        )
        result = merge_runtime_extensions(
            [art_a, art_b], tmp_path / "session",
        )
        merged_root = Path(result.runtime_ext.runtime_path)
        assert (merged_root / "tools" / "shared_tool__ext_a.py").is_file()
        assert (merged_root / "tools" / "shared_tool__ext_b.py").is_file()
        import yaml

        with open(merged_root / "harness_config.yaml", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        modules = [
            s["module"]
            for s in data["resources"].get("tools", [])
            if s.get("type") == "package"
        ]
        assert any(m.endswith(".shared_tool__ext_a") for m in modules)
        assert any(m.endswith(".shared_tool__ext_b") for m in modules)

    def test_skill_conflict(self, tmp_path: Path):
        """Same skill name in two extensions."""
        root_a = tmp_path / "sk_a"
        root_b = tmp_path / "sk_b"
        for root, name in [
            (root_a, "ext_a"),
            (root_b, "ext_b"),
        ]:
            ext_root = root / name
            (ext_root / "tools").mkdir(parents=True)
            (ext_root / "tools" / "__init__.py").write_text(
                "", encoding="utf-8"
            )
            (ext_root / "__init__.py").write_text(
                "", encoding="utf-8"
            )
            (ext_root / "skills" / "my_skill").mkdir(
                parents=True
            )
            (
                ext_root / "skills" / "my_skill" / "SKILL.md"
            ).write_text(
                f"---\nname: my_skill\ndescription: "
                f"skill from {name}\n---\nbody\n",
                encoding="utf-8",
            )
            (ext_root / "harness_config.yaml").write_text(
                f"schema_version: harness_config.v0.1\n"
                f"name: {name}\nresources:\n"
                f"  rails: []\n  tools: []\n"
                f"  skills:\n    dirs:\n      - skills/\n",
                encoding="utf-8",
            )
        ext_a = RuntimeExtensionArtifact(
            extension_name="ext_a",
            runtime_path=str(root_a / "ext_a"),
            config_path=str(
                root_a / "ext_a" / "harness_config.yaml"
            ),
        )
        ext_b = RuntimeExtensionArtifact(
            extension_name="ext_b",
            runtime_path=str(root_b / "ext_b"),
            config_path=str(
                root_b / "ext_b" / "harness_config.yaml"
            ),
        )
        result = merge_runtime_extensions(
            [ext_a, ext_b], tmp_path
        )
        merged_root = Path(result.runtime_ext.runtime_path)
        assert (
            merged_root / "skills" / "my_skill__ext_a"
        ).is_dir()
        assert (
            merged_root / "skills" / "my_skill__ext_b"
        ).is_dir()
        assert ("ext_a", "my_skill") in result.skill_rename_map
        assert ("ext_b", "my_skill") in result.skill_rename_map
        # manifest still says skills dir
        import yaml
        with open(
            merged_root / "harness_config.yaml",
            encoding="utf-8",
        ) as fh:
            data = yaml.safe_load(fh)
        assert data["resources"]["skills"]["dirs"] == [
            "skills/"
        ]

    def test_hard_error_cleanup(self, tmp_path: Path):
        """Illegal source manifest module prefix raises and cleans up."""
        root = tmp_path / "bad_ext"
        root.mkdir()
        (root / "harness_config.yaml").write_text(
            "schema_version: harness_config.v0.1\n"
            "name: bad_ext\nresources:\n"
            "  rails:\n"
            "    - type: package\n"
            "      module: some.other.prefix.rail\n"
            "      class: BadRail\n"
            "  tools: []\n",
            encoding="utf-8",
        )
        (root / "tools").mkdir()
        (root / "tools" / "__init__.py").write_text(
            "", encoding="utf-8"
        )
        (root / "__init__.py").write_text("", encoding="utf-8")
        art = RuntimeExtensionArtifact(
            extension_name="bad_ext",
            runtime_path=str(root),
            config_path=str(root / "harness_config.yaml"),
        )
        merged_dir = tmp_path / "merged_extensions"
        with pytest.raises(MergedExtensionError):
            merge_runtime_extensions([art], tmp_path)
        assert not merged_dir.exists()

    def test_deterministic(self, tmp_path: Path):
        """Same input produces byte-identical output."""
        ext_a = _write_minimal_ext(
            tmp_path,
            extension_name="ext_a",
            extra_files={"tools/a.py": "A=1\n"},
        )
        ext_b = _write_minimal_ext(
            tmp_path,
            extension_name="ext_b",
            extra_files={"tools/b.py": "B=1\n"},
        )
        result1 = merge_runtime_extensions(
            [ext_a, ext_b], tmp_path / "run1"
        )
        result2 = merge_runtime_extensions(
            [ext_a, ext_b], tmp_path / "run2"
        )
        hash1 = _dir_hash(Path(result1.runtime_ext.runtime_path))
        hash2 = _dir_hash(Path(result2.runtime_ext.runtime_path))
        assert hash1 == hash2

    def test_absolute_import_rewrite(self, tmp_path: Path):
        """Absolute imports are rewritten to merged prefix."""
        ext_a = _write_minimal_ext(
            tmp_path,
            extension_name="ext_a",
            extra_files={
                "tools/demo_tool.py": (
                    "from openjiuwen.extensions.harness."
                    "ext_a.tools.helper import VALUE\n"
                )
            },
        )
        ext_b = _write_minimal_ext(
            tmp_path,
            extension_name="ext_b",
            extra_files={
                "tools/other.py": "X=1\n"
            },
        )
        (tmp_path / "ext_b" / "tools" / "helper.py").unlink()
        result = merge_runtime_extensions(
            [ext_a, ext_b], tmp_path
        )
        merged_root = Path(result.runtime_ext.runtime_path)
        demo_tool = (
            merged_root / "tools" / "demo_tool.py"
        )
        content = demo_tool.read_text(encoding="utf-8")
        assert (
            "openjiuwen.extensions.harness."
            "merged_extensions.tools.helper"
        ) in content
        assert "ext_a" not in content
