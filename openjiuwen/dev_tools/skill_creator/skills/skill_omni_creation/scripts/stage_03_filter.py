#!/usr/bin/env python3
import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

from openai import OpenAI

import common as common


def get_image_context(blocks: list[dict], idx: int) -> tuple[str, str, str]:
    """Return (heading, text_before, text_after) for the image block at idx."""
    heading = ""
    text_before = ""
    for i in range(idx - 1, -1, -1):
        b = blocks[i]
        if not heading and b["type"] == "heading":
            heading = b["text"]
        if not text_before and b["type"] == "text":
            text_before = b["text"]
        if heading and text_before:
            break

    text_after = ""
    for i in range(idx + 1, len(blocks)):
        b = blocks[i]
        if b["type"] == "text":
            text_after = b["text"]
            break

    return heading, text_before, text_after


def filter_batch(
    client: OpenAI,
    batch_items: list[tuple[dict, str, str, str]],  # (block, heading, text_before, text_after)
    batch_images: list[tuple[bytes, str]],
    page_title: str,
) -> list[bool]:
    content: list[dict] = [{
        "type": "text",
        "text": (
            f'The user is looking for screenshots that illustrate how to: "{page_title}"\n\n'
            f"There are {len(batch_images)} images numbered 1 to {len(batch_images)}. "
            f"Surrounding text context is provided alongside each image.\n"
            f'Reply with ONLY a JSON array of {len(batch_images)} strings, each "KEEP" or "SKIP".'
        ),
    }]

    for idx, ((block, heading, text_before, text_after), (data, mime)) in enumerate(
        zip(batch_items, batch_images), 1
    ):
        ctx_parts = []
        if block.get("source") == "subpage":
            ctx_parts.append("Source: subpage (apply stricter relevance check)")
        if heading:
            ctx_parts.append(f"Section heading: {heading}")
        if text_before:
            ctx_parts.append(f"Context before: {text_before[:200]}")
        if text_after:
            ctx_parts.append(f"Context after: {text_after[:200]}")
        ctx_str = "\n".join(ctx_parts) if ctx_parts else "(no text context)"
        content.append({"type": "text", "text": f"Image {idx}:\n{ctx_str}"})
        content.append({"type": "image_url", "image_url": {"url": common.encode_b64(data, mime)}})

    try:
        resp = client.chat.completions.create(
            model=common.MODEL,
            messages=[
                {"role": "system", "content": common.FILTER_PROMPT},
                {"role": "user", "content": content},
            ],
            temperature=0.0,
            max_tokens=128,
        )
        raw = common.strip_json_fence(resp.choices[0].message.content)
        return [str(item).upper() == "KEEP" for item in json.loads(raw)]
    except Exception as exc:
        logger.warning("[filter] batch failed (%s), keeping all", exc)
        return [True] * len(batch_images)


def filter_image_blocks(
    client: OpenAI,
    blocks: list[dict],
    fetched: dict[str, tuple[bytes, str]],
    page_title: str,
) -> list[dict]:
    # Collect image blocks with their context
    image_items: list[tuple[int, dict, str, str, str]] = []
    for i, b in enumerate(blocks):
        if b["type"] != "image":
            continue
        if b["url"] not in fetched:
            continue
        heading, text_before, text_after = get_image_context(blocks, i)
        image_items.append((i, b, heading, text_before, text_after))

    if not image_items:
        return blocks

    # Batch the items
    batch_size = common.FILTER_BATCH
    batches = [image_items[i:i + batch_size] for i in range(0, len(image_items), batch_size)]

    keep_flags: dict[int, bool] = {}
    with ThreadPoolExecutor(max_workers=common.FILTER_WORKERS) as executor:
        future_map = {}
        for batch in batches:
            items_for_filter = [(b, h, tb, ta) for _, b, h, tb, ta in batch]
            images_for_filter = [fetched[b["url"]] for _, b, *_ in batch]
            future = executor.submit(filter_batch, client, items_for_filter, images_for_filter, page_title)
            future_map[future] = [block_idx for block_idx, *_ in batch]

        for future in as_completed(future_map):
            indices = future_map[future]
            results = future.result()
            for block_idx, keep in zip(indices, results):
                keep_flags[block_idx] = keep

    # Print decisions and rebuild blocks
    skip_indices: set[int] = set()
    for block_idx, keep in keep_flags.items():
        label = "KEEP" if keep else "SKIP"
        url = blocks[block_idx]["url"]
        logger.info("[%s] %s", label, url[:80])
        if not keep:
            skip_indices.add(block_idx)

    return [b for i, b in enumerate(blocks) if i not in skip_indices]


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3 (new): filter image blocks by relevance.")
    parser.add_argument("input_json")
    parser.add_argument("--title", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    data = common.load_json(Path(args.input_json))
    slug = data["slug"]
    out = Path(args.out) if args.out else common.work_path(slug, "stage_03_filter.json")
    title = args.title or data.get("title") or ""
    if not title:
        parser.error("--title is required when the input JSON has no 'title' field.")

    asset_dir = Path(data.get("asset_dir") or common.work_path(slug, "assets"))
    fetched = common.load_fetched_assets(asset_dir, data.get("fetched_assets", {}))
    client = OpenAI(api_key=common.API_KEY, base_url=common.API_BASE)

    before = sum(1 for b in data["blocks"] if b["type"] == "image")
    blocks = filter_image_blocks(client, data["blocks"], fetched, title)
    after = sum(1 for b in blocks if b["type"] == "image")

    common.write_json(out, {**data, "title": title, "blocks": blocks})
    logger.info("[stage 3] wrote %s: %d / %d image blocks kept", out, after, before)


if __name__ == "__main__":
    main()
