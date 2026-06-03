#!/usr/bin/env python3
import base64
import json
import os
import pathlib
import re
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT / ".env")

API_BASE = os.environ["API_BASE"]
API_KEY = os.environ["API_KEY"]
MODEL = os.environ["MODEL_NAME"]

STEALTH_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MIME_TO_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
MIN_DIMENSION = 80
MAX_IMAGE_BYTES = 5 * 1024 * 1024
FETCH_WORKERS = 10
FILTER_BATCH = 5
FILTER_WORKERS = 3
VIDEO_FRAMES = 4

# ── Prompts ──────────────────────────────────────────────────────────────────

FILTER_PROMPT = """\
You are reviewing screenshots from a software guide or manual.
Some images may be frames extracted from a video tutorial.

KEEP an image if it shows: application windows, UI controls, menus, dialogs, buttons,
settings screens, or any screenshot that demonstrates the software in use —
including screens that show people's faces or video feeds if that is part of the software UI.

SKIP an image if it is: a small icon, a standalone logo, a purely decorative graphic,
a promotional advertisement, or something clearly unrelated to the software task.
Do NOT skip an image just because it contains a person's face or a video feed —
these are often part of the software interface being demonstrated.

SKIP an image if its content clearly belongs to a different independent feature or task
than the one specified in the title — even if it appears in the same document.

If an image is marked "Source: subpage", apply a stricter standard: only KEEP it if it
directly and unambiguously illustrates a step in the main task named in the title.
When in doubt about a subpage image, SKIP it.

Use both the image content AND the surrounding context (the heading block above and the
text blocks immediately before and after the image in DOM order) to make your decision.
When in doubt about a main-page image, KEEP.

Reply with ONLY a JSON array of strings, one per image, each exactly "KEEP" or "SKIP".
Example for 3 images: ["KEEP", "SKIP", "KEEP"]
"""

SKILL_PROMPT = """\
You are building a Skill file for an AI agent to learn and execute a software task.

You will receive:
1. TITLE — the name of the software task
2. BLOCKS — an ordered list of content blocks extracted from the source page in DOM order.
   Each block has one of three types:
   - {"type": "heading", "level": 1-4, "text": "...", "source": "main"|"subpage"}
   - {"type": "text",    "text": "...", "source": "main"|"subpage"}
   - {"type": "image",   "path": "references/img_NN.ext", "alt": "...", "source": "main"|"subpage"}
   Images appear inline between text blocks exactly where they occur on the original page.
   Blocks with source "subpage" require stricter relevance filtering.

Output format (strict):

---
name: <snake_case_skill_name>
description: <1-3 sentences in English: what this Skill does and when to use it>
---

# <Skill Name>

## Steps

GROUPING RULES (apply top-down, pick the first rule that matches):
1. BLOCKS contain level-2 heading blocks (h2):
   - Each h2 → ### group header. Restart step numbering from 1 per group.
   - If any h3 blocks appear within that h2 group → each h3 → #### sub-section header.
     Restart step numbering from 1 per sub-section.
2. BLOCKS contain level-3 heading blocks (h3) but no h2:
   - Each h3 → ### group header. Restart step numbering from 1 per group.
3. No h2 and no h3 — single continuous workflow:
   - FLAT format: one numbered list, no ### or #### headers.

FORMAT EXAMPLES:

With h2 + h3 (two-level grouping):
### <h2 heading text>

#### <h3 heading text>

1. <verb> **<UI label>**

![alt text](references/img_NN.ext)

2. ...

#### <next h3 heading text>

1. ...

### <next h2 heading text>

#### <h3 heading text>

1. ...

With h2 only (one-level grouping):
### <h2 heading text>

1. <verb> **<UI label>**

![alt text](references/img_NN.ext)

2. ...

### <next h2 heading text>

1. ...

FLAT format (no h2, no h3):
1. <verb> **<UI label>**

![alt text](references/img_NN.ext)

2. ...

RULES:
- YAML frontmatter (--- ... ---) is mandatory and must be the very first thing in output.
- name must be snake_case. description must be 1-3 sentences in English.
- STRICT IMAGE RULE: Only reference images whose exact "path" appears in BLOCKS.
  If no image block has a path: output must contain ZERO lines starting with ![.
  Never invent, rename, or fabricate any path. Not crop.png, not button.png, nothing.
- EVERY image block with a valid path must appear somewhere in the output.
- Place each image on its own line with a blank line BEFORE and a blank line AFTER it.
- Image syntax: ![alt text](path) — copy "path" verbatim from the block's path field.
- NO HALLUCINATION: Every step must be grounded in a text or heading block in BLOCKS.
  Do not add steps, UI labels, button names, or workflows from training knowledge.
  Fewer accurate steps is better than more invented ones.
- FOCUS RULE: The core task is defined by TITLE. Skip any block whose content clearly
  belongs to a different independent feature — even if it appears in BLOCKS.
  Example: title is "Create a PivotChart" -> skip any blocks about PivotTable setup.
- TEXT-ONLY STEPS: If a text block describes a clear procedural step but no image
  block immediately follows it, include the step as text-only (no image tag).
- Subpage blocks: apply the same FOCUS RULE — only include if directly relevant to TITLE.
- NO SOURCE LINKS: Do not append source URLs, reference links, or footnotes.
- Include all content that helps the user perform the task: main steps, conditional
  branches ("if X, do Y"), notes, tips, warnings, and troubleshooting.
  Let the FOCUS RULE handle relevance — do not exclude entire content categories.
  Exclude only: standalone FAQ sections (Q&A unrelated to the task flow),
  promotional/marketing copy, and "learn more / visit link" navigation text.

Output ONLY the Skill markdown. No preamble, no explanation.
"""

SCRAPE_FALLBACK_PROMPT = """\
You are a web scraper assistant. Fetch the page at the URL provided and extract its
main content as a JSON object with a "blocks" array in DOM order.

Each block must be exactly one of:
  {"type": "heading", "level": 1-4, "text": "<heading text>"}
  {"type": "text",    "text": "<paragraph or list item, max 300 chars>"}
  {"type": "image",   "url": "<absolute image url>", "alt": "<alt text>"}

Rules:
- Preserve original DOM order — text and images interleaved as they appear on the page.
- Skip: navigation bars, footers, cookie banners, ads, icon/logo images, sidebar widgets.
- Include only the main article/content area.
- For images: skip SVGs, decorative icons, and images under ~80px.
- Text blocks: one entry per paragraph or list item, max 300 chars each.

Return ONLY valid JSON — no markdown fences, no explanation:
{
  "title": "<page title>",
  "blocks": [...],
  "video_urls": ["<youtube/vimeo/bilibili url>", ...]
}
"""


# ── JSON helpers ─────────────────────────────────────────────────────────────

def load_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def strip_json_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def encode_b64(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.standard_b64encode(data).decode()}"


# ── Path / slug helpers ───────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:80]


def url_to_slug(url: str) -> str:
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).strip("/")
    return slugify(raw)


def work_path(slug: str, filename: str) -> pathlib.Path:
    return pathlib.Path("work") / slug / filename


def image_ext(url: str, mime: str) -> str:
    ext = pathlib.Path(urlparse(url).path).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        ext = MIME_TO_EXT.get(mime, ".png")
    return ext


# ── Asset helpers ─────────────────────────────────────────────────────────────

def save_fetched_assets(
    fetched: dict[str, tuple[bytes, str]],
    asset_dir: pathlib.Path,
    prefix: str,
) -> dict[str, dict]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = {}
    for idx, (url, (data, mime)) in enumerate(fetched.items()):
        rel_path = pathlib.Path(f"{prefix}_{idx:03d}{image_ext(url, mime)}")
        out_path = asset_dir / rel_path
        out_path.write_bytes(data)
        manifest[url] = {"path": rel_path.as_posix(), "mime": mime}
    return manifest


def load_fetched_assets(asset_dir: pathlib.Path, manifest: dict[str, dict]) -> dict[str, tuple[bytes, str]]:
    fetched: dict[str, tuple[bytes, str]] = {}
    for url, meta in manifest.items():
        path = asset_dir / meta["path"]
        fetched[url] = (path.read_bytes(), meta["mime"])
    return fetched


# ── Blocks helpers ────────────────────────────────────────────────────────────

def blocks_with_paths_as_str(blocks: list[dict]) -> list[dict]:
    result = []
    for b in blocks:
        if b.get("type") == "image" and b.get("path") is not None:
            result.append({**b, "path": str(b["path"])})
        else:
            result.append(b)
    return result


def blocks_with_paths_as_path(blocks: list[dict]) -> list[dict]:
    result = []
    for b in blocks:
        if b.get("type") == "image" and b.get("path") is not None:
            result.append({**b, "path": pathlib.Path(str(b["path"]))})
        else:
            result.append(b)
    return result


def strip_hallucinated_images(md: str, valid_paths: set[str]) -> str:
    """Remove any ![...](path) lines where path is not in valid_paths."""
    def _check(match: re.Match) -> str:
        path = match.group(2).strip()
        return match.group(0) if path in valid_paths else ""

    cleaned = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _check, md)
    # Drop lines that became blank after stripping an image reference
    lines = []
    for line in cleaned.splitlines():
        if line.strip():
            lines.append(line)
        elif lines and lines[-1].strip():
            lines.append(line)
    return "\n".join(lines).strip()
