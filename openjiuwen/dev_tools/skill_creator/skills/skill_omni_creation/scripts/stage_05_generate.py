#!/usr/bin/env python3
import argparse
import json
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

from openai import OpenAI

import common as common


def blocks_for_llm(blocks: list[dict], skill_dir: Path) -> list[dict]:
    """Prepare blocks for LLM input: relative paths, drop raw URL, skip images without path."""
    result = []
    for b in blocks:
        if b["type"] == "image":
            path = b.get("path")
            if path is None:
                continue
            path = Path(str(path))
            try:
                rel = path.relative_to(skill_dir).as_posix()
            except ValueError:
                rel = path.name
            result.append({
                "type": "image",
                "path": rel,
                "alt": b.get("alt", ""),
                "source": b.get("source", "main"),
            })
        else:
            result.append({k: v for k, v in b.items() if k not in ("path", "url")})
    return result


def call_skill_agent(client: OpenAI, title: str, blocks_llm: list[dict]) -> str:
    blocks_json = json.dumps(blocks_llm, ensure_ascii=False, indent=2)
    user_msg = f"Title: {title}\n\n=== BLOCKS ===\n{blocks_json}"
    resp = client.chat.completions.create(
        model=common.MODEL,
        messages=[
            {"role": "system", "content": common.SKILL_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=8192,
    )
    return resp.choices[0].message.content.strip()


def append_reference_files(skill_md: str, blocks_llm: list[dict]) -> str:
    """Append a ## Reference Files section listing all image blocks."""
    images = [b for b in blocks_llm if b["type"] == "image" and b.get("path")]
    if not images:
        return skill_md
    lines = ["\n## Reference Files\n",
             "For visual reference, the following screenshots are available:\n"]
    for b in images:
        description = b.get("alt") or "screenshot"
        lines.append(f"- `{b['path']}` — {description}")
    return skill_md.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5 (new): generate SKILL.md from blocks.")
    parser.add_argument("input_json")
    parser.add_argument("--out", default=None)
    parser.add_argument("--clean", action="store_true", help="Delete work/<slug>/ after successful generation.")
    args = parser.parse_args()

    data = common.load_json(Path(args.input_json))
    skill_dir = Path(data["skill_dir"])
    out_path = Path(args.out) if args.out else skill_dir / "SKILL.md"

    blocks = common.blocks_with_paths_as_path(data.get("blocks", []))
    blocks_llm = blocks_for_llm(blocks, skill_dir)
    valid_paths = {b["path"] for b in blocks_llm if b["type"] == "image" and b.get("path")}

    client = OpenAI(api_key=common.API_KEY, base_url=common.API_BASE)
    skill_md = call_skill_agent(client, data.get("title", ""), blocks_llm)

    # Post-processing
    skill_md = common.strip_hallucinated_images(skill_md, valid_paths)
    skill_md = append_reference_files(skill_md, blocks_llm)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(skill_md, encoding="utf-8")
    logger.info("[stage 5] wrote %s", out_path)

    if args.clean:
        work_dir = common.work_path(data["slug"], "")
        if work_dir.exists():
            shutil.rmtree(work_dir)
            logger.info("[stage 5] cleaned %s", work_dir)


if __name__ == "__main__":
    main()
