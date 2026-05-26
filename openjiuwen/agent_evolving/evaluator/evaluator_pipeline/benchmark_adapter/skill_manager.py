import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger


class SkillEvolutionManager:
    """Manages skill evolution across iterations"""

    def __init__(self, config):
        self.config = config
        self.skill_dir = config.skill_persistence_dir / config.task_id
        self.skill_dir.mkdir(parents=True, exist_ok=True)
        self.current_skill: str | None = None
        self.current_evolutions: str | None = None
        self.skill_history: list[Path] = []
        self.resolved_skill_name: str = self._load_resolved_skill_name()
        self.all_skills: dict[str, str] = {}
        self.all_evolutions: dict[str, str] = {}
        self.all_evolution_files: dict[str, dict[str, str]] = {}

    def _resolved_name_path(self) -> Path:
        return self.skill_dir / ".resolved_skill_name"

    def _load_resolved_skill_name(self) -> str:
        name_path = self._resolved_name_path()
        if name_path.exists():
            saved_name = name_path.read_text(encoding="utf-8").strip()
            if saved_name:
                return saved_name
        return self.config.task_id

    def _save_resolved_skill_name(self) -> None:
        name_path = self._resolved_name_path()
        name_path.write_text(self.resolved_skill_name, encoding="utf-8")

    def save_resolved_skill_name(self) -> None:
        self._save_resolved_skill_name()

    def get_skill_path(self, iteration: int | None = None) -> Path:
        if iteration is None:
            return self.skill_dir / "latest" / "SKILL.md"
        return self.skill_dir / f"iteration_{iteration:03d}" / "SKILL.md"

    def get_evolutions_path(self, iteration: int | None = None) -> Path:
        if iteration is None:
            return self.skill_dir / "latest" / "evolutions.json"
        return self.skill_dir / f"iteration_{iteration:03d}" / "evolutions.json"

    def get_skill_dir_path(self, iteration: int | None = None) -> Path:
        if iteration is None:
            return self.skill_dir / "latest"
        return self.skill_dir / f"iteration_{iteration:03d}"

    def load_skill(self, iteration: int | None = None) -> str | None:
        skill_path = self.get_skill_path(iteration)
        if skill_path.exists():
            self.current_skill = skill_path.read_text(encoding="utf-8")
            evo_path = self.get_evolutions_path(iteration)
            if evo_path.exists():
                self.current_evolutions = evo_path.read_text(encoding="utf-8")
            else:
                self.current_evolutions = None
            return self.current_skill
        return None

    def load_evolution_files(self, iteration: int | None = None) -> dict[str, str]:
        skill_dir = self.get_skill_dir_path(iteration)
        evolution_dir = skill_dir / "evolution"

        evolution_files: dict[str, str] = {}
        if evolution_dir.exists() and evolution_dir.is_dir():
            for md_file in evolution_dir.glob("*.md"):
                try:
                    evolution_files[md_file.name] = md_file.read_text(encoding="utf-8")
                    logger.info("  Loaded evolution file: %s", md_file.name)
                except Exception as e:
                    logger.error("  Failed to load evolution file %s: %s", md_file.name, e)

        return evolution_files

    def _merge_evolutions(self, new_evo_content: str) -> str:
        existing_evo_path = self.skill_dir / "evolutions.json"
        existing_entries: dict[str, dict] = {}

        if existing_evo_path.exists():
            try:
                existing_data = json.loads(existing_evo_path.read_text(encoding="utf-8"))
                for entry in existing_data.get("entries", []):
                    entry_id = entry.get("id", "")
                    if entry_id:
                        existing_entries[entry_id] = entry
            except Exception as e:
                logger.debug("Failed to load existing evolutions: %s", e)

        try:
            new_data = json.loads(new_evo_content)
        except Exception:
            return new_evo_content

        new_entries = new_data.get("entries", [])
        for entry in new_entries:
            entry_id = entry.get("id", "")
            if entry_id:
                existing_entries[entry_id] = entry

        merged_entries = list(existing_entries.values())
        merged_data = {
            "entries": merged_entries,
            "skill_id": new_data.get("skill_id", ""),
            "updated_at": new_data.get("updated_at", "")
        }
        return json.dumps(merged_data, ensure_ascii=False, indent=2)

    def save_skill(self, content: str, iteration: int, evolutions_content: str | None = None,
                   evolution_files: dict[str, str] | None = None) -> Path:
        skill_dir = self.get_skill_dir_path(iteration)
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")

        latest_dir = self.get_skill_dir_path(None)
        latest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(skill_path, latest_dir / "SKILL.md")

        root_skill_path = self.skill_dir / "SKILL.md"
        shutil.copy(skill_path, root_skill_path)

        if evolutions_content is not None:
            merged_evo_content = self._merge_evolutions(evolutions_content)
            evo_path = skill_dir / "evolutions.json"
            evo_path.write_text(merged_evo_content, encoding="utf-8")
            shutil.copy(evo_path, latest_dir / "evolutions.json")
            shutil.copy(evo_path, self.skill_dir / "evolutions.json")
            self.current_evolutions = merged_evo_content

        if evolution_files:
            evolution_dir = skill_dir / "evolution"
            evolution_dir.mkdir(parents=True, exist_ok=True)
            latest_evolution_dir = latest_dir / "evolution"
            latest_evolution_dir.mkdir(parents=True, exist_ok=True)
            root_evolution_dir = self.skill_dir / "evolution"
            root_evolution_dir.mkdir(parents=True, exist_ok=True)

            for filename, file_content in evolution_files.items():
                file_path = evolution_dir / filename
                file_path.write_text(file_content, encoding="utf-8")
                shutil.copy(file_path, latest_evolution_dir / filename)
                shutil.copy(file_path, root_evolution_dir / filename)

        self.skill_history.append(skill_path)
        self.current_skill = content

        logger.info("Skill saved: %s", skill_path)
        return skill_path

    async def render_evolution_to_skill_md(self) -> None:
        """Inject How-to-Read guidance into SKILL.md.

        Only injects the reading guidance block; the Experience Index table
        itself is managed by the Agent's ``_update_skill_md_index()`` inside
        the ``<!-- evolution-index-start -->...<!-- evolution-index-end -->``
        block.  Pipeline does NOT re-render that table.
        """
        skill_md_path = self.skill_dir / "SKILL.md"

        if not skill_md_path.exists():
            return

        howtoread_block = "\n".join([
            "<!-- evolution-howtoread-start -->",
            "### How to Read Evolution Details",
            "",
            "**IMPORTANT**: Before applying this skill, review the Experience Index below. "
            "If any experience summary matches your current task or a failure "
            "you encountered, you MUST read the linked detail section for specific guidance.",
            "",
            "1. Check the **Summary** column below for relevant experiences",
            "2. Click or read the **Detail** path to find the full guidance",
            "3. Read the evolution file using: `cat <skill-dir>/evolution/<filename>.md`",
            "4. Look for the specific experience ID anchor (e.g., `#ev_xxxxxxxx`)",
            "",
            "For narrative guidance, read the relevant `evolution/*.md` detail section. "
            "For reusable helper code, first review `evolution/scripts/_index.md`, "
            "then inspect the specific script source before adapting or running it.",
            "<!-- evolution-howtoread-end -->",
        ])

        content = skill_md_path.read_text(encoding="utf-8")
        howtoread_pattern = re.compile(
            r'<!--\s*evolution-howtoread-start\s*-->.*?<!--\s*evolution-howtoread-end\s*-->',
            re.DOTALL,
        )
        index_pattern = re.compile(
            r'<!--\s*evolution-index-start\s*-->',
        )

        if howtoread_pattern.search(content):
            content = howtoread_pattern.sub(howtoread_block, content)
        elif index_pattern.search(content):
            content = index_pattern.sub(howtoread_block + "\n\n<!-- evolution-index-start -->", content)
        else:
            content = content.rstrip() + "\n\n" + howtoread_block + "\n"

        skill_md_path.write_text(content, encoding="utf-8")
        latest_path = self.get_skill_dir_path(None) / "SKILL.md"
        shutil.copy(skill_md_path, latest_path)
        self.current_skill = content
        logger.info("    ✓ How-to-Read guidance injected into SKILL.md")

    async def render_evolution_to_skill_md_for(self, skill_name: str) -> None:
        """Inject How-to-Read guidance into SKILL.md for a specific skill.

        Only injects the reading guidance block; the Experience Index table
        itself is managed by the Agent's ``_update_skill_md_index()`` inside
        the ``<!-- evolution-index-start -->...<!-- evolution-index-end -->``
        block.  Pipeline does NOT re-render that table.
        """
        skill_sub_dir = self.skill_dir / skill_name
        skill_md_path = skill_sub_dir / "SKILL.md"

        if not skill_md_path.exists():
            return

        howtoread_block = "\n".join([
            "<!-- evolution-howtoread-start -->",
            "### How to Read Evolution Details",
            "",
            "**IMPORTANT**: Before applying this skill, review the Experience Index below. "
            "If any experience summary matches your current task or a failure "
            "you encountered, you MUST read the linked detail section for specific guidance.",
            "",
            "1. Check the **Summary** column below for relevant experiences",
            "2. Click or read the **Detail** path to find the full guidance",
            "3. Read the evolution file using: `cat <skill-dir>/evolution/<filename>.md`",
            "4. Look for the specific experience ID anchor (e.g., `#ev_xxxxxxxx`)",
            "",
            "For narrative guidance, read the relevant `evolution/*.md` detail section. "
            "For reusable helper code, first review `evolution/scripts/_index.md`, "
            "then inspect the specific script source before adapting or running it.",
            "<!-- evolution-howtoread-end -->",
        ])

        content = skill_md_path.read_text(encoding="utf-8")
        howtoread_pattern = re.compile(
            r'<!--\s*evolution-howtoread-start\s*-->.*?<!--\s*evolution-howtoread-end\s*-->',
            re.DOTALL,
        )
        index_pattern = re.compile(
            r'<!--\s*evolution-index-start\s*-->',
        )

        if howtoread_pattern.search(content):
            content = howtoread_pattern.sub(howtoread_block, content)
        elif index_pattern.search(content):
            content = index_pattern.sub(howtoread_block + "\n\n<!-- evolution-index-start -->", content)
        else:
            content = content.rstrip() + "\n\n" + howtoread_block + "\n"

        skill_md_path.write_text(content, encoding="utf-8")
        latest_path = skill_sub_dir / "latest" / "SKILL.md"
        if latest_path.parent.exists():
            shutil.copy(skill_md_path, latest_path)
        if skill_name in self.all_skills:
            self.all_skills[skill_name] = content
        if skill_name == self.resolved_skill_name:
            self.current_skill = content
        logger.info("    ✓ How-to-Read guidance injected into %s/SKILL.md", skill_name)

    @staticmethod
    def compute_skill_hash(content: str | None) -> str:
        if not content:
            return ""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def has_skill_changed(self, new_content: str) -> bool:
        if not self.current_skill:
            return True
        return self.compute_skill_hash(self.current_skill) != self.compute_skill_hash(new_content)

    def load_all_skills(self) -> dict[str, str]:
        self.all_skills = {}
        self.all_evolutions = {}
        self.all_evolution_files = {}

        if not self.skill_dir.exists():
            return self.all_skills

        for child in sorted(self.skill_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            skill_name = child.name
            if skill_name in ("latest",) or skill_name.startswith("iteration_"):
                continue
            self.all_skills[skill_name] = skill_md.read_text(encoding="utf-8")

            evo_path = child / "evolutions.json"
            if evo_path.exists():
                self.all_evolutions[skill_name] = evo_path.read_text(encoding="utf-8")

            evo_dir = child / "evolution"
            if evo_dir.exists() and evo_dir.is_dir():
                files: dict[str, str] = {}
                for md_file in evo_dir.glob("*.md"):
                    try:
                        files[md_file.name] = md_file.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.debug("Failed to read evolution file %s: %s", md_file.name, e)
                if files:
                    self.all_evolution_files[skill_name] = files

        if self.all_skills:
            if self.resolved_skill_name in self.all_skills:
                self.current_skill = self.all_skills[self.resolved_skill_name]
            else:
                first_name = next(iter(self.all_skills))
                self.current_skill = self.all_skills[first_name]
            self.current_evolutions = self.all_evolutions.get(
                self.resolved_skill_name,
                next(iter(self.all_evolutions.values())) if self.all_evolutions else None,
            )

        logger.info("  Loaded %d skills: %s", len(self.all_skills), list(self.all_skills.keys()))
        return self.all_skills

    def save_all_skills(
        self,
        skills: dict[str, str],
        iteration: int,
        evolutions: dict[str, str] | None = None,
        evolution_files: dict[str, dict[str, str]] | None = None,
    ) -> list[Path]:
        evolutions = evolutions or {}
        evolution_files = evolution_files or {}
        saved_paths: list[Path] = []

        for skill_name, skill_content in skills.items():
            skill_sub_dir = self.skill_dir / skill_name
            skill_sub_dir.mkdir(parents=True, exist_ok=True)

            iter_dir = skill_sub_dir / f"iteration_{iteration:03d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            skill_path = iter_dir / "SKILL.md"
            skill_path.write_text(skill_content, encoding="utf-8")

            latest_dir = skill_sub_dir / "latest"
            latest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(skill_path, latest_dir / "SKILL.md")

            root_skill_path = skill_sub_dir / "SKILL.md"
            shutil.copy(skill_path, root_skill_path)

            evo_content = evolutions.get(skill_name)
            if evo_content is not None:
                merged = self._merge_evolutions_for_skill(skill_name, evo_content)
                evo_path = iter_dir / "evolutions.json"
                evo_path.write_text(merged, encoding="utf-8")
                shutil.copy(evo_path, latest_dir / "evolutions.json")
                shutil.copy(evo_path, skill_sub_dir / "evolutions.json")
                self.all_evolutions[skill_name] = merged

            skill_evo_files = evolution_files.get(skill_name, {})
            if skill_evo_files:
                evo_dir = iter_dir / "evolution"
                evo_dir.mkdir(parents=True, exist_ok=True)
                latest_evo_dir = latest_dir / "evolution"
                latest_evo_dir.mkdir(parents=True, exist_ok=True)
                root_evo_dir = skill_sub_dir / "evolution"
                root_evo_dir.mkdir(parents=True, exist_ok=True)
                for filename, file_content in skill_evo_files.items():
                    (evo_dir / filename).write_text(file_content, encoding="utf-8")
                    shutil.copy(evo_dir / filename, latest_evo_dir / filename)
                    shutil.copy(evo_dir / filename, root_evo_dir / filename)

            self.all_skills[skill_name] = skill_content
            self.skill_history.append(skill_path)
            saved_paths.append(skill_path)
            logger.info("    Skill saved: %s -> %s", skill_name, skill_path)

        if self.resolved_skill_name in self.all_skills:
            self.current_skill = self.all_skills[self.resolved_skill_name]
        elif self.all_skills:
            self.current_skill = next(iter(self.all_skills.values()))
        self.current_evolutions = self.all_evolutions.get(self.resolved_skill_name)

        return saved_paths

    def _merge_evolutions_for_skill(self, skill_name: str, new_evo_content: str) -> str:
        existing_evo_path = self.skill_dir / skill_name / "evolutions.json"
        existing_entries: dict[str, dict] = {}

        if existing_evo_path.exists():
            try:
                existing_data = json.loads(existing_evo_path.read_text(encoding="utf-8"))
                for entry in existing_data.get("entries", []):
                    entry_id = entry.get("id", "")
                    if entry_id:
                        existing_entries[entry_id] = entry
            except Exception as e:
                logger.debug("Failed to load existing evolutions: %s", e)

        try:
            new_data = json.loads(new_evo_content)
        except Exception:
            return new_evo_content

        new_entries = new_data.get("entries", [])
        for entry in new_entries:
            entry_id = entry.get("id", "")
            if entry_id:
                existing_entries[entry_id] = entry

        merged_entries = list(existing_entries.values())
        merged_data = {
            "entries": merged_entries,
            "skill_id": new_data.get("skill_id", ""),
            "updated_at": new_data.get("updated_at", "")
        }
        return json.dumps(merged_data, ensure_ascii=False, indent=2)

    def get_all_skill_names(self) -> list[str]:
        return list(self.all_skills.keys())


def extract_specific_errors(test_output: str) -> dict[str, str]:
    errors: dict[str, str] = {}

    pattern = re.compile(
        r"FAILED\s+(.+?)\s*-\s*(.*?)(?=\nFAILED|\n={3,}|\nPASSED|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(test_output):
        test_name = match.group(1).strip()
        error_body = match.group(2).strip()
        lines = error_body.split("\n")
        filtered = []
        for ln in lines:
            if ln.strip() and not ln.strip().startswith(("---", "+++", "@@")):
                filtered.append(ln)
        core = "\n".join(filtered[:8])
        if len(core) > 400:
            core = core[:400] + "..."
        errors[test_name] = core

    if not errors:
        assertion_pattern = re.compile(
            r"(test_\w+.*?)\n.*?(AssertionError|assert\s+.*?)\n(.*?)(?=\n\n|\nFAILED|\Z)",
            re.DOTALL,
        )
        for match in assertion_pattern.finditer(test_output):
            test_name = match.group(1).strip().split("\n")[-1].strip()
            assertion_line = match.group(2).strip()
            detail = match.group(3).strip()
            combined = f"{assertion_line}\n{detail}"
            if len(combined) > 400:
                combined = combined[:400] + "..."
            errors[test_name] = combined

    if not errors:
        error_block_pattern = re.compile(
            r"_{3,}\s*(.*?)\s*_{3,}\n(.*?)(?=_{3,}|\Z)",
            re.DOTALL,
        )
        for match in error_block_pattern.finditer(test_output):
            header = match.group(1).strip()
            body = match.group(2).strip()
            if "FAILED" in header or "ERROR" in header:
                lines = body.split("\n")
                filtered = []
                for ln in lines:
                    if ln.strip():
                        filtered.append(ln)
                core = "\n".join(filtered[:6])
                if len(core) > 400:
                    core = core[:400] + "..."
                test_name = header.split()[0] if header.split() else "unknown"
                errors[test_name] = core

    return errors
