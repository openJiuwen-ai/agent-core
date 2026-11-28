from __future__ import annotations

import re

from video_memory.llm.base import ModelClient
from video_memory.memory.generator import GeneratedMemory, build_memory_nodes
from video_memory.ocr.tesseract import OCRFrameText, TesseractOCR
from video_memory.schemas import FrameRecord, FrameWindow, MemoryNode, NodeFrameEdge


class OCRLLMMemoryGenerator:
    def __init__(
        self,
        ocr: TesseractOCR,
        client: ModelClient,
    ) -> None:
        self.ocr = ocr
        self.client = client

    def generate(self, window: FrameWindow, frames: list[FrameRecord]) -> GeneratedMemory:
        observations_by_key = self.ocr.extract(frames)
        observations = [observations_by_key[frame.frame_key] for frame in frames if frame.frame_key in observations_by_key]
        raw_nodes = self.client.generate_memory_from_ocr(
            window,
            frames,
            [observation.to_dict() for observation in observations],
        )
        nodes, edges, rejected = build_memory_nodes(window, frames, raw_nodes)
        return nodes, edges, observations, rejected


class OCRMemoryGenerator:
    def __init__(
        self,
        ocr: TesseractOCR,
        min_chars: int = 12,
    ) -> None:
        self.ocr = ocr
        self.min_chars = min_chars

    def generate(self, window: FrameWindow, frames: list[FrameRecord]) -> GeneratedMemory:
        observations_by_key = self.ocr.extract(frames)
        observations = [observations_by_key[frame.frame_key] for frame in frames if frame.frame_key in observations_by_key]

        nodes: list[MemoryNode] = []
        edges: list[NodeFrameEdge] = []
        node_index = 0
        detail_texts: list[str] = []
        detail_frame_keys: list[str] = []

        for observation in observations:
            lines = _meaningful_lines(observation.lines)
            if len(" ".join(lines)) < self.min_chars:
                continue
            description = _detail_description(lines)
            if not description:
                continue
            node = MemoryNode(
                node_id=f"{window.window_id}_node_{node_index:03d}",
                node_type="detail",
                description_text=description,
                time_ids=[observation.time_id],
            )
            nodes.append(node)
            edges.append(NodeFrameEdge(node.node_id, observation.frame_key))
            detail_texts.append(description)
            detail_frame_keys.append(observation.frame_key)
            node_index += 1

        if detail_texts:
            summary = _summary_description(detail_texts)
            node = MemoryNode(
                node_id=f"{window.window_id}_node_{node_index:03d}",
                node_type="summary",
                description_text=summary,
                time_ids=sorted({obs.time_id for obs in observations if obs.frame_key in set(detail_frame_keys)}),
            )
            nodes.append(node)
            edges.extend(NodeFrameEdge(node.node_id, frame_key) for frame_key in sorted(set(detail_frame_keys)))
            node_index += 1

            preference = _preference_description(detail_texts)
            if preference is not None:
                node = MemoryNode(
                    node_id=f"{window.window_id}_node_{node_index:03d}",
                    node_type="preference",
                    description_text=preference,
                    time_ids=sorted({obs.time_id for obs in observations if obs.frame_key in set(detail_frame_keys)}),
                )
                nodes.append(node)
                edges.extend(NodeFrameEdge(node.node_id, frame_key) for frame_key in sorted(set(detail_frame_keys)))

        # This generator derives its own frame keys from the OCR observations,
        # so a node can never cite a frame outside the window.
        return nodes, edges, observations, []


def _meaningful_lines(lines: list[str]) -> list[str]:
    meaningful: list[str] = []
    for line in lines:
        text = _normalize_ocr_line(line)
        if not text:
            continue
        lower = text.lower()
        if lower in _NAVIGATION_NOISE:
            continue
        if len(text) <= 6 and _STATUS_RE.match(text):
            continue
        if len(text) <= 3 and not any(char.isdigit() for char in text):
            continue
        meaningful.append(text)
    return _dedupe_adjacent(meaningful)


def _detail_description(lines: list[str]) -> str:
    merged = _merge_split_lines(lines)
    if not merged:
        return ""

    domain = _first_match(merged, _DOMAIN_RE)
    email = _first_match(merged, _EMAIL_RE)
    money_values = _MONEY_RE.findall(" ".join(merged))
    last_updated = _line_containing(merged, "last updated")

    if last_updated:
        return _join_parts(["Article/page text", domain, *merged])
    if email:
        return _join_parts(["Account/sync page text", email, *merged])
    if money_values:
        return _join_parts(["Shopping/product page text", *merged])
    return _join_parts(["Visible screen text", *merged])


def _summary_description(detail_texts: list[str]) -> str:
    snippets = [_shorten(_remove_prefix(text), 160) for text in detail_texts[:5]]
    return "Window summary: " + " | ".join(snippets)


def _preference_description(detail_texts: list[str]) -> str | None:
    text = " ".join(detail_texts).lower()
    if any(term in text for term in ["news", "live updates", "cnn", "abcnews", "abc news"]):
        return "The user may be interested in reading online news or live news updates."
    if any(term in text for term in ["walmart", "amazon", "shopping", "$"]):
        return "The user may be interested in browsing online shopping or product information."
    if any(term in text for term in ["restaurant", "sushi", "burger", "pizza", "hotel", "flight"]):
        return "The user may be interested in travel, local places, or restaurant information."
    return None


def _merge_split_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(lines):
        current = lines[index]
        if index + 1 < len(lines) and _is_split_email(current, lines[index + 1]):
            merged.append(current + lines[index + 1])
            index += 2
            continue
        merged.append(current)
        index += 1
    return merged


def _is_split_email(current: str, following: str) -> bool:
    """True when OCR broke one address across the two lines.

    Concatenating blindly is not enough: "Sync settings" + "iris.brennan@gmail.com"
    yields "Sync settingsiris.brennan@gmail.com", whose tail looks like a valid
    address. Only rejoin when neither line holds a whole address on its own and
    the match produced by joining actually straddles the seam.
    """
    if _EMAIL_RE.search(current) or _EMAIL_RE.search(following):
        return False
    match = _EMAIL_RE.search(current + following)
    return bool(match) and match.start() < len(current) < match.end()


def _normalize_ocr_line(line: str) -> str:
    text = " ".join(line.strip().split())
    text = text.replace("•", "").strip()
    return text


def _dedupe_adjacent(lines: list[str]) -> list[str]:
    output: list[str] = []
    previous = ""
    for line in lines:
        key = line.lower()
        if key != previous:
            output.append(line)
        previous = key
    return output


def _join_parts(parts: list[str]) -> str:
    return "; ".join(part for part in parts if part)


def _first_match(lines: list[str], pattern: re.Pattern[str]) -> str | None:
    for line in lines:
        match = pattern.search(line)
        if match:
            return match.group(0)
    return None


def _line_containing(lines: list[str], needle: str) -> str | None:
    needle = needle.lower()
    for line in lines:
        if needle in line.lower():
            return line
    return None


def _remove_prefix(text: str) -> str:
    return re.sub(r"^(Article/page text|Account/sync page text|Shopping/product page text|Visible screen text);\s*", "", text)


def _shorten(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 3].rstrip() + "..."


_STATUS_RE = re.compile(r"^[0-9:.\sA-Za-z@]+$")
_DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")

_NAVIGATION_NOISE = {
    "app",
    "apps",
    "back",
    "collections",
    "discover",
    "home",
    "maps",
    "menu",
    "next",
    "open",
    "search",
}
