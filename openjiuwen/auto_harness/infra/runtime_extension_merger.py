# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Merge multiple verified runtime extensions into a single merged extension."""

from __future__ import annotations

import ast
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openjiuwen.harness.harness_config.loader import HarnessConfigLoader
from openjiuwen.harness.harness_config.schema import HarnessConfig

if TYPE_CHECKING:
    from openjiuwen.auto_harness.schema import RuntimeExtensionArtifact

_DEFAULT_MERGED_NAME = "merged_extensions"


def _build_merged_prefix(merged_name: str) -> str:
    """Build the module prefix for a merged extension."""
    return f"openjiuwen.extensions.harness.{merged_name}"


class MergedExtensionError(Exception):
    """Raised when merging runtime extensions fails fatally."""


@dataclass
class MergeRuntimeExtensionsResult:
    """Output of merge_runtime_extensions."""

    runtime_ext: "RuntimeExtensionArtifact"
    rename_map: dict[tuple[str, str], str]
    skill_rename_map: dict[tuple[str, str], str]
    source_exts_summary: list[dict[str, str]]


@dataclass
class _SourceFileInfo:
    """One file from a source extension."""

    ext_name: str
    rel_posix: str  # normalized relative path
    abs_path: Path


def _lookup_renamed_rel(
    src_ext: str,
    module_rel_posix: str,
    rename_map: dict[tuple[str, str], str],
) -> str:
    """Map import-style relative path to merged-tree path.

    ``rename_map`` keys use filesystem paths (e.g. ``tools/x.py``).  Manifest
    entries and import suffixes use dotted module segments without ``.py``.
    """
    if not module_rel_posix:
        return module_rel_posix
    for cand in (
        module_rel_posix,
        f"{module_rel_posix}.py",
        f"{module_rel_posix}/__init__.py",
    ):
        mapped = rename_map.get((src_ext, cand))
        if mapped is not None:
            return mapped
    return module_rel_posix


def _merged_file_rel_to_dotted(merged_rel: str) -> str:
    """Turn merged relative file path into dotted module path (no ``.py``)."""
    p = Path(merged_rel.replace("\\", "/"))
    if p.suffix == ".py":
        p = p.with_suffix("")
    return p.as_posix().replace("/", ".")


def merge_runtime_extensions(
    artifacts: list["RuntimeExtensionArtifact"],
    session_root: Path,
    merged_name: str = _DEFAULT_MERGED_NAME,
) -> MergeRuntimeExtensionsResult:
    """Deterministically merge multiple verified runtime extensions.

    Conflict detection uses full relative paths (including tools/ rails/
    etc. prefixes).  Conflicting files get a ``__<src_ext>`` suffix;
    non-conflicting files keep their original name.  All AST import
    rewrites and manifest rewrites consult the same rename_map.

    Args:
        artifacts: Source runtime extension artifacts to merge.
        session_root: Session runtime directory.
        merged_name: Name for the merged extension (default: "merged_extensions").

    Raises ``MergedExtensionError`` on hard failures (bad source
    manifest, syntax error, M1 self-check failure).  Partial merged
    directories are cleaned up on error.
    """
    merged_root = session_root / merged_name
    if not artifacts:
        raise MergedExtensionError("no artifacts to merge")

    # ---- Step 1: validate source manifests ----
    source_exts_summary: list[dict[str, str]] = []
    for art in artifacts:
        ext_name = art.extension_name
        loader = HarnessConfigLoader.load(art.config_path)
        for spec in (loader.config.resources.rails if loader.config.resources else []) + (
            loader.config.resources.tools if loader.config.resources else []
        ):
            if spec.type != "package" or not spec.module:
                continue
            expected_prefix = f"openjiuwen.extensions.harness.{ext_name}"
            if not (spec.module == expected_prefix or spec.module.startswith(f"{expected_prefix}.")):
                _cleanup(merged_root)
                raise MergedExtensionError(
                    f"Source extension '{ext_name}' has module "
                    f"'{spec.module}' not under expected prefix "
                    f"'{expected_prefix}'"
                )
        src_summary: dict[str, str] = {"name": ext_name}
        if loader.config.description:
            src_summary["description"] = str(loader.config.description)
        source_exts_summary.append(src_summary)

    # ---- Step 2: create merged directory ----
    try:
        merged_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise MergedExtensionError(
            f"Cannot create merged directory: {exc}"
        ) from exc

    try:
        return _do_merge(artifacts, merged_root, source_exts_summary, merged_name)
    except Exception as exc:
        _cleanup(merged_root)
        raise MergedExtensionError(
            f"Merge error: {exc}"
        ) from exc


def _do_merge(
    artifacts: list["RuntimeExtensionArtifact"],
    merged_root: Path,
    source_exts_summary: list[dict[str, str]],
    merged_name: str,
) -> MergeRuntimeExtensionsResult:
    """Core merge logic.  merged_root already exists."""
    from openjiuwen.auto_harness.schema import RuntimeExtensionArtifact

    # ---- Step 3: collect source files ----
    all_files: list[_SourceFileInfo] = []
    for art in sorted(artifacts, key=lambda a: a.extension_name):
        src_root = Path(art.runtime_path).resolve()
        for f in sorted(src_root.rglob("*"), key=lambda p: p.as_posix()):
            if not f.is_file():
                continue
            rel = f.relative_to(src_root).as_posix()
            if rel == "harness_config.yaml" or f.name == "__init__.py":
                continue
            all_files.append(
                _SourceFileInfo(
                    ext_name=art.extension_name,
                    rel_posix=rel,
                    abs_path=f,
                )
            )

    # ---- Step 4: conflict detection + rename_map ----
    rel_counter: Counter = Counter(info.rel_posix for info in all_files)
    conflict_rels = {rel for rel, cnt in rel_counter.items() if cnt > 1}
    rename_map: dict[tuple[str, str], str] = {}
    for info in all_files:
        if info.rel_posix in conflict_rels:
            stem = Path(info.rel_posix).stem
            suffix = Path(info.rel_posix).suffix
            parent = str(Path(info.rel_posix).parent)
            new_name = f"{stem}__{info.ext_name}{suffix}"
            new_rel = f"{parent}/{new_name}" if parent != "." else new_name
            rename_map[(info.ext_name, info.rel_posix)] = new_rel

    # ---- skill conflict detection ----
    skill_dirs_map: dict[str, list[str]] = {}
    for art in sorted(artifacts, key=lambda a: a.extension_name):
        src_root = Path(art.runtime_path).resolve()
        skills_dir = src_root / "skills"
        if not skills_dir.is_dir():
            continue
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir():
                skill_dirs_map.setdefault(d.name, []).append(art.extension_name)
    skill_rename_map: dict[tuple[str, str], str] = {}
    for skill_name, ext_names in sorted(skill_dirs_map.items()):
        if len(ext_names) > 1:
            for ext_name in sorted(ext_names):
                skill_rename_map[(ext_name, skill_name)] = f"{skill_name}__{ext_name}"

    # ---- Step 5: copy files ----
    _copy_source_files(all_files, merged_root, rename_map)
    _copy_skill_directories(artifacts, merged_root, skill_rename_map)

    # ---- Step 5.5: merge requirements.txt files ----
    _merge_requirements_files(merged_root)

    # ---- Step 6: write empty __init__.py for all package dirs ----
    _write_empty_inits(merged_root)

    # ---- Step 7: AST import rewrite ----
    _rewrite_imports(merged_root, all_files, rename_map, merged_name)

    # ---- Step 8: generate new harness_config.yaml ----
    _write_merged_manifest(merged_root, artifacts, rename_map, skill_rename_map, merged_name)

    return MergeRuntimeExtensionsResult(
        runtime_ext=RuntimeExtensionArtifact(
            extension_name=merged_name,
            runtime_path=str(merged_root),
            config_path=str(merged_root / "harness_config.yaml"),
        ),
        rename_map=rename_map,
        skill_rename_map=skill_rename_map,
        source_exts_summary=source_exts_summary,
    )


def _copy_source_files(
    all_files: list[_SourceFileInfo],
    merged_root: Path,
    rename_map: dict[tuple[str, str], str],
) -> None:
    """Copy source files into merged directory."""
    for info in all_files:
        dest_rel = rename_map.get((info.ext_name, info.rel_posix), info.rel_posix)
        dest_path = merged_root / dest_rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(info.abs_path, dest_path)


def _merge_requirements_files(merged_root: Path) -> None:
    """Merge all requirements*.txt files into a single requirements.txt.

    After _copy_source_files, multiple extensions' requirements.txt files
    may be renamed to requirements__<ext_name>.txt due to conflicts.
    This function merges them into one deduplicated requirements.txt.
    """
    # Find all requirements files (including renamed ones)
    req_files = list(merged_root.glob("requirements*.txt"))
    if not req_files:
        return

    # If only one file and it's named requirements.txt, nothing to do
    if len(req_files) == 1 and req_files[0].name == "requirements.txt":
        return

    # Collect all dependencies, deduplicate while preserving order
    all_deps: list[str] = []
    seen_deps: set[str] = set()

    for req_file in sorted(req_files):
        try:
            content = req_file.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            # Try other encodings for robustness
            content = req_file.read_text(encoding="utf-8", errors="replace").strip()

        for line in content.splitlines():
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            # Normalize: remove version specifiers for dedup check
            # but keep original line for output
            pkg_name = line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("[")[0].strip()
            if pkg_name and pkg_name not in seen_deps:
                seen_deps.add(pkg_name)
                all_deps.append(line)

    # Write merged requirements.txt
    merged_content = "\n".join(all_deps) + "\n" if all_deps else ""
    merged_req = merged_root / "requirements.txt"
    merged_req.write_text(merged_content, encoding="utf-8")

    # Remove original requirements files
    for req_file in req_files:
        if req_file != merged_req:
            req_file.unlink()


def _copy_skill_directories(
    artifacts: list["RuntimeExtensionArtifact"],
    merged_root: Path,
    skill_rename_map: dict[tuple[str, str], str],
) -> None:
    """Copy skill directories into merged/skills/."""
    for art in sorted(artifacts, key=lambda a: a.extension_name):
        src_root = Path(art.runtime_path).resolve()
        src_skills = src_root / "skills"
        if not src_skills.is_dir():
            continue
        for skill_dir in sorted(src_skills.iterdir()):
            if not skill_dir.is_dir():
                continue
            new_skill_name = skill_rename_map.get((art.extension_name, skill_dir.name), skill_dir.name)
            dest_skill = merged_root / "skills" / new_skill_name
            if not dest_skill.exists():
                shutil.copytree(skill_dir, dest_skill)


def _write_empty_inits(root: Path) -> None:
    """Write empty __init__.py for every package directory."""
    seen_dirs: set[Path] = set()
    for py_file in sorted(root.rglob("*.py")):
        pkg_dir = py_file.parent
        if pkg_dir in seen_dirs:
            continue
        seen_dirs.add(pkg_dir)
        init_path = pkg_dir / "__init__.py"
        if not init_path.exists():
            init_path.write_text("", encoding="utf-8")
    root_init = root / "__init__.py"
    if not root_init.exists():
        root_init.write_text("", encoding="utf-8")


def _rewrite_imports(
    root: Path,
    all_files: list[_SourceFileInfo],
    rename_map: dict[tuple[str, str], str],
    merged_name: str,
) -> None:
    """Rewrite absolute and relative imports in all .py files."""
    merged_prefix = _build_merged_prefix(merged_name)
    # Build a map: merged relative path -> source extension name
    merged_to_src: dict[str, str] = {}
    for info in all_files:
        dest_rel = rename_map.get((info.ext_name, info.rel_posix), info.rel_posix)
        merged_to_src[dest_rel] = info.ext_name

    for py_file in sorted(root.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise MergedExtensionError(
                f"Source .py is not valid UTF-8: {py_file}"
            ) from exc
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            raise MergedExtensionError(
                f"Syntax error in {py_file}: {exc}"
            ) from exc

        rel_posix = py_file.relative_to(root).as_posix()
        src_ext = merged_to_src.get(rel_posix)
        if src_ext is None:
            continue

        rel_dot = str(Path(rel_posix).with_suffix("")).replace("/", ".")
        new_source = _rewrite_tree_imports(tree, src_ext, rel_dot, rename_map, merged_prefix)
        if new_source is not None:
            py_file.write_text(new_source, encoding="utf-8")


def _rewrite_tree_imports(
    tree: ast.Module,
    src_ext: str,
    rel_dot: str,
    rename_map: dict[tuple[str, str], str],
    merged_prefix: str,
) -> str | None:
    """Rewrite imports in one AST tree.  Returns new source or None."""
    rewriter = _ImportRewriter(
        src_ext_name=src_ext,
        rel_dot=rel_dot,
        rename_map=rename_map,
        merged_prefix=merged_prefix,
    )
    new_tree = rewriter.visit(tree)
    return ast.unparse(new_tree)


class _ImportRewriter(ast.NodeTransformer):
    """Rewrite imports for a single merged .py file."""

    def __init__(
        self,
        src_ext_name: str,
        rel_dot: str,
        rename_map: dict[tuple[str, str], str],
        merged_prefix: str,
    ) -> None:
        self.src_ext = src_ext_name
        self.rel_dot = rel_dot
        self.rename_map = rename_map
        self.merged_prefix = merged_prefix

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        if node.module is None:
            return node

        # --- Step 6a: absolute prefix replacement ---
        old_prefix = f"openjiuwen.extensions.harness.{self.src_ext}"
        if node.module == old_prefix or node.module.startswith(f"{old_prefix}."):
            suffix = node.module[len(old_prefix):]
            # suffix is like ".tools.helper" or "" (root module)
            rel_posix = suffix.lstrip(".").replace(".", "/") if suffix else ""
            if rel_posix:
                new_rel = _lookup_renamed_rel(
                    self.src_ext, rel_posix, self.rename_map,
                )
                if new_rel != rel_posix:
                    new_dot = _merged_file_rel_to_dotted(new_rel)
                    new_module = f"{self.merged_prefix}.{new_dot}"
                else:
                    # non-conflict: just prefix swap
                    new_module = f"{self.merged_prefix}{suffix}"
            else:
                # importing the root module of the extension
                new_module = self.merged_prefix

            return ast.copy_location(
                ast.ImportFrom(
                    module=new_module,
                    names=[ast.alias(name=a.name, asname=a.asname) for a in node.names],
                    level=0,
                ),
                node,
            )

        # --- Step 6b: relative import rewrite ---
        if node.level >= 1:
            target_posix = self._resolve_relative_target_posix(node.level, node.module)
            if target_posix is not None:
                # rename_map keys use full paths with .py suffix
                file_key = f"{target_posix}.py" if not target_posix.endswith(".py") else target_posix
                new_rel = self.rename_map.get((self.src_ext, file_key))
                if new_rel is not None:
                    new_dot = _merged_file_rel_to_dotted(new_rel)
                    new_module = f"{self.merged_prefix}.{new_dot}"
                    return ast.copy_location(
                        ast.ImportFrom(
                            module=new_module,
                            names=[
                                ast.alias(
                                    name=a.name,
                                    asname=a.asname,
                                )
                                for a in node.names
                            ],
                            level=0,
                        ),
                        node,
                    )

        return node

    def _resolve_relative_target_posix(self, level: int, module: str | None) -> str | None:
        """Resolve a relative import to a POSIX path within src_ext."""
        if level < 1 or module is None:
            return None
        parts = self.rel_dot.split(".") if self.rel_dot else []
        up = level - 1
        base_parts = parts[: max(0, len(parts) - up)]
        target_parts = base_parts + module.split(".")
        return ".".join(target_parts).replace(".", "/")


def _merge_manifest_yaml_scalar(value: str) -> str:
    """One-line YAML scalar; quote when needed for safe round-trips."""
    if not value:
        return '""'
    safe = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._")
    if all(c in safe for c in value):
        return value

    return json.dumps(value, ensure_ascii=False)


def _format_merged_harness_config_yaml(
    *,
    schema_version: str,
    name: str,
    deduped_tools: list[dict[str, str]],
    deduped_rails: list[dict[str, str]],
    include_skills: bool,
) -> str:
    """Build merged ``harness_config.yaml`` text with correct list indentation.

    PyYAML's ``yaml.dump`` aligns block sequence ``-`` with the mapping key
    (``tools:`` / ``-`` same column); single-design manifests use one extra
    indent under the key.
    """
    lines: list[str] = [
        f"schema_version: {_merge_manifest_yaml_scalar(schema_version)}",
        f"name: {_merge_manifest_yaml_scalar(name)}",
    ]
    if not (deduped_tools or deduped_rails or include_skills):
        return "\n".join(lines) + "\n"

    lines.append("resources:")
    for section_key, specs in (
        ("tools", deduped_tools),
        ("rails", deduped_rails),
    ):
        if not specs:
            continue
        lines.append(f"  {section_key}:")
        for spec in specs:
            lines.append("    - type: package")
            lines.append(
                f"      module: {_merge_manifest_yaml_scalar(spec['module'])}",
            )
            lines.append(
                f"      class: {_merge_manifest_yaml_scalar(spec['class'])}",
            )
    if include_skills:
        lines.append("  skills:")
        lines.append("    dirs:")
        lines.append("      - skills/")
    return "\n".join(lines) + "\n"


def _write_merged_manifest(
    root: Path,
    artifacts: list["RuntimeExtensionArtifact"],
    rename_map: dict[tuple[str, str], str],
    skill_rename_map: dict[tuple[str, str], str],
    merged_name: str,
) -> None:
    """Generate harness_config.yaml for the merged extension.

    Shape matches implement-stage single-extension manifests: ``schema_version``,
    ``name``, then ``resources`` with only non-empty sections, keys in order
    ``tools`` → ``rails`` → ``skills``, and ``skills.dirs`` using ``skills/``.
    """
    merged_prefix = _build_merged_prefix(merged_name)
    merged_rails: list[dict[str, str]] = []
    merged_tools: list[dict[str, str]] = []
    include_skills: bool = False

    for art in sorted(artifacts, key=lambda a: a.extension_name):
        loader = HarnessConfigLoader.load(art.config_path)
        res = loader.config.resources
        if res:
            for spec in res.rails or []:
                if spec.type != "package" or not spec.module:
                    continue
                new_module = _rewrite_manifest_module(art.extension_name, spec.module, rename_map, merged_prefix)
                merged_rails.append(
                    {
                        "type": "package",
                        "module": new_module,
                        "class": spec.class_name,
                    }
                )
            for spec in res.tools or []:
                if spec.type != "package" or not spec.module:
                    continue
                new_module = _rewrite_manifest_module(art.extension_name, spec.module, rename_map, merged_prefix)
                merged_tools.append(
                    {
                        "type": "package",
                        "module": new_module,
                        "class": spec.class_name,
                    }
                )
            if res.skills:
                include_skills = True

    # Deduplicate while preserving order
    seen_rails: set[tuple[str, str]] = set()
    deduped_rails: list[dict[str, str]] = []
    for r in merged_rails:
        key = (r["module"], r["class"])
        if key not in seen_rails:
            seen_rails.add(key)
            deduped_rails.append(r)
    seen_tools: set[tuple[str, str]] = set()
    deduped_tools: list[dict[str, str]] = []
    for t in merged_tools:
        key = (t["module"], t["class"])
        if key not in seen_tools:
            seen_tools.add(key)
            deduped_tools.append(t)

    schema_version = HarnessConfig.model_fields["schema_version"].get_default()
    manifest_body = _format_merged_harness_config_yaml(
        schema_version=str(schema_version),
        name=merged_name,
        deduped_tools=deduped_tools,
        deduped_rails=deduped_rails,
        include_skills=include_skills,
    )

    manifest_path = root / "harness_config.yaml"
    manifest_path.write_text(manifest_body, encoding="utf-8")

    # ---- M1 self-check ----
    _verify_m1(root, merged_prefix)


def _rewrite_manifest_module(
    src_ext: str,
    module: str,
    rename_map: dict[tuple[str, str], str],
    merged_prefix: str,
) -> str:
    """Rewrite a manifest module field to the merged prefix."""
    old_prefix = f"openjiuwen.extensions.harness.{src_ext}"
    if not (module == old_prefix or module.startswith(f"{old_prefix}.")):
        raise MergedExtensionError(
            f"Module '{module}' not under source extension prefix '{old_prefix}'"
        )
    suffix = module[len(old_prefix):]
    rel_posix = suffix.lstrip(".").replace(".", "/")
    new_rel = _lookup_renamed_rel(src_ext, rel_posix, rename_map)
    if new_rel != rel_posix:
        new_dot = _merged_file_rel_to_dotted(new_rel)
        return f"{merged_prefix}.{new_dot}"
    return f"{merged_prefix}{suffix}"


def _verify_m1(root: Path, merged_prefix: str) -> None:
    """M1 self-check: all package modules must start with merged prefix and map to real files."""
    import yaml

    manifest_path = root / "harness_config.yaml"
    with open(manifest_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    resources = data.get("resources", {}) or {}
    for kind in ("rails", "tools"):
        for spec in resources.get(kind, []) or []:
            if spec.get("type") != "package":
                continue
            mod = spec.get("module", "")
            if not mod:
                raise MergedExtensionError(
                    "M1 violation: empty module in merged manifest"
                )
            if not mod.startswith(merged_prefix):
                raise MergedExtensionError(
                    f"M1 violation: module '{mod}' does not start with '{merged_prefix}'"
                )
            # Verify module maps to a real file
            rel_dot = mod[len(merged_prefix):].lstrip(".")
            rel_path = rel_dot.replace(".", "/")
            py_path = root / f"{rel_path}.py"
            pkg_init = root / rel_path / "__init__.py"
            if not py_path.is_file() and not pkg_init.is_file():
                raise MergedExtensionError(
                    f"Manifest module '{mod}' does not map to a real file (tried {py_path} and {pkg_init})"
                )


def _cleanup(path: Path) -> None:
    """Remove a directory tree, ignoring errors."""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "MergedExtensionError",
    "MergeRuntimeExtensionsResult",
    "merge_runtime_extensions",
]
