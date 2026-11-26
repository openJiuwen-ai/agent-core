# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HTML visible label and AIGC JSON metadata, preserving the original document.

The HTML meta representation is a local interoperability extension. The current
GCMark text page does not offer metadata inspection for HTML/HTM.
"""

import html
import json
import os
import re
import tempfile
from html.parser import HTMLParser

from openjiuwen.harness.tools.aigc_marker._common import is_aigc_complete, parse_aigc_json

AI_MARK_TEXT = "内容由AI生成"


class HtmlMarkers(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata = None
        self.excluded = []
        self.text = []
        self.meta_positions = []

    def handle_starttag(self, tag, attrs):
        values = {key.lower(): value for key, value in attrs}
        if tag == "meta" and (values.get("name") or "").lower() == "aigc":
            self.meta_positions.append((*self.getpos(), len(self.get_starttag_text())))
            candidate = parse_aigc_json(values.get("content"))
            if is_aigc_complete(candidate):
                self.metadata = candidate
        hidden = (
            "hidden" in values
            or (values.get("aria-hidden") or "").lower() == "true"
            or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", values.get("style") or "", re.IGNORECASE)
        )
        if tag in ("head", "title", "script", "style", "template") or (
            hidden
            and tag
            not in (
                "area",
                "base",
                "br",
                "col",
                "embed",
                "hr",
                "img",
                "input",
                "link",
                "meta",
                "param",
                "source",
                "track",
                "wbr",
            )
        ):
            self.excluded.append(tag)

    def handle_endtag(self, tag):
        if tag in self.excluded:
            self.excluded = self.excluded[: len(self.excluded) - 1 - self.excluded[::-1].index(tag)]

    def handle_data(self, data):
        if not self.excluded:
            self.text.append(data)


def inspect_html(text):
    parser = HtmlMarkers()
    parser.feed(text)
    return parser


class HtmlAigcDecorator:
    @staticmethod
    def get_aigc_data(file_path):
        with open(file_path, encoding="utf-8-sig") as source:
            return inspect_html(source.read()).metadata

    @staticmethod
    def decorate(file_path, fallback_signature, add_visible_mark=True):
        with open(file_path, encoding="utf-8-sig") as source:
            original = source.read()
        parsed = inspect_html(original)
        metadata = parsed.metadata or json.loads(fallback_signature)
        # Add optional fields without changing existing provenance identifiers.
        metadata = {
            **metadata,
            "ReservedCode1": metadata.get("ReservedCode1", ""),
            "ReservedCode2": metadata.get("ReservedCode2", ""),
        }
        tag = '<meta name="AIGC" content="' + html.escape(json.dumps(metadata, ensure_ascii=False), quote=True) + '">'
        lines = original.splitlines(keepends=True)
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line))
        spans = [(starts[line - 1] + column, length) for line, column, length in parsed.meta_positions]
        marked = original
        for index in range(len(spans) - 1, -1, -1):
            start, length = spans[index]
            end = start + length
            marked = marked[:start] + (tag if index == 0 else "") + marked[end:]
        found = bool(spans)
        if not found:
            head = re.search(r"<head\b[^>]*>", marked, re.IGNORECASE)
            if head:
                offset = head.end()
                marked = marked[:offset] + "\n" + tag + marked[offset:]
            else:
                opening = re.search(r"<html\b[^>]*>|<!doctype\b[^>]*>", marked, re.IGNORECASE)
                offset = opening.end() if opening else 0
                marked = marked[:offset] + "\n<head>" + tag + "</head>\n" + marked[offset:]
        if add_visible_mark and AI_MARK_TEXT not in "".join(parsed.text):
            footer = (
                '<div data-aigc-marker="visible" style="position:fixed;bottom:8px;left:0;width:100%;'
                'text-align:center;z-index:99999;pointer-events:none;">'
                '<span style="display:inline-block;background:#fff;padding:4px 12px;'
                'font-size:14px;color:#000;">' + AI_MARK_TEXT + "</span></div>"
            )
            closings = list(re.finditer(r"</body\s*>", marked, re.IGNORECASE))
            offset = closings[-1].start() if closings else len(marked)
            marked = marked[:offset] + footer + marked[offset:]
        verified = inspect_html(marked)
        if not is_aigc_complete(verified.metadata):
            raise ValueError("HTML AIGC metadata verification failed")
        if add_visible_mark and AI_MARK_TEXT not in "".join(verified.text):
            raise ValueError("HTML visible label verification failed")
        if marked == original:
            return
        fd, temporary = tempfile.mkstemp(
            prefix=".aigc-", suffix=".html", dir=os.path.dirname(os.path.abspath(file_path))
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(marked)
            os.replace(temporary, file_path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
