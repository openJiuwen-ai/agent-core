#!/usr/bin/env python3
import argparse
import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

import common as common


def save_image_blocks(
    blocks: list[dict],
    fetched: dict[str, tuple[bytes, str]],
    img_dir: Path,
) -> list[dict]:
    """Copy approved images to img_dir, return updated blocks with path fields set."""
    img_dir.mkdir(parents=True, exist_ok=True)
    new_blocks = []
    counter = 0

    for block in blocks:
        if block["type"] != "image":
            new_blocks.append(block)
            continue

        url = block["url"]
        if url not in fetched:
            # Image block survived stage_03 but somehow not in fetched — skip
            logger.warning("[warn] no fetched data for %s, skipping", url[:80])
            continue

        data, mime = fetched[url]
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in common.SUPPORTED_EXTS:
            ext = common.MIME_TO_EXT.get(mime, ".png")
        dest = img_dir / f"img_{counter:02d}{ext}"
        dest.write_bytes(data)
        logger.info("[save] img_%02d%s ← %s", counter, ext, url[:60])
        new_blocks.append({**block, "path": dest})
        counter += 1

    return new_blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 4 (new): save approved images, update path in blocks.")
    parser.add_argument("input_json")
    parser.add_argument("--slug", default=None)
    parser.add_argument("--skills-dir", default="skills")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    data = common.load_json(Path(args.input_json))
    slug = args.slug or data["slug"]
    out = Path(args.out) if args.out else common.work_path(slug, "stage_04_save.json")
    asset_dir = Path(data.get("asset_dir") or common.work_path(slug, "assets"))
    skill_dir = Path(args.skills_dir) / slug
    img_dir = skill_dir / "references"
    fetched = common.load_fetched_assets(asset_dir, data.get("fetched_assets", {}))

    blocks = save_image_blocks(data.get("blocks", []), fetched, img_dir)
    saved_count = sum(1 for b in blocks if b["type"] == "image" and b.get("path"))

    common.write_json(out, {
        **data,
        "slug": slug,
        "skills_dir": args.skills_dir,
        "skill_dir": skill_dir.as_posix(),
        "blocks": common.blocks_with_paths_as_str(blocks),
    })
    logger.info("[stage 4] wrote %s: %d image(s) saved to %s", out, saved_count, img_dir)


if __name__ == "__main__":
    main()
