# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Visible ProcessOn canvas labels, using the public native editor definitions.

POSM roots use title/children/root; POSF definitions use elements/page.
Both native renderers read watermark/showWatermark on their root/page.
Unknown layouts fail instead of claiming that metadata is a visible mark.
"""

import json
import math
from pathlib import PurePosixPath

from openjiuwen.harness.tools.aigc_marker._common import read_pos_json

VISIBLE_TEXT = "内容由AI生成"
MARKER_ID = "aigc-marker-visible"
WRAPPERS = ("diagram", "def", "definition", "content", "data", "mindTree", "rootTopic", "root")
COLLECTIONS = ("canvases", "pages", "definitions", "defs")


def _kind(value):
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("elements"), dict) and isinstance(value.get("page"), dict):
        return "flow"
    if (
        value.get("root") in (True, "true")
        and isinstance(value.get("title"), str)
        and isinstance(value.get("children"), list)
    ):
        return "mind"
    return None


def _walk(value, expected, visit, depth=0):
    if depth > 30:
        raise ValueError("ProcessOn canvas nesting is too deep")
    kind = _kind(value)
    if kind:
        if kind != expected:
            raise ValueError("ProcessOn canvas type does not match the file extension")
        visit(value, kind)
        return 1
    if isinstance(value, str):
        decoded = read_pos_json(value.encode("utf-8"))
        if not isinstance(decoded, (dict, list)):
            raise ValueError("Serialized ProcessOn definition must be an object or array")
        count = _walk(decoded, expected, visit, depth + 1)
        # The caller retains serialized definitions in their original string type.
        return count, json.dumps(decoded, ensure_ascii=False)
    if isinstance(value, list):
        count = 0
        for index, child in enumerate(value):
            result = _walk(child, expected, visit, depth + 1)
            if isinstance(result, tuple):
                found, rewritten = result
                if found:
                    value[index] = rewritten
            else:
                found = result
            if not found:
                raise ValueError("ProcessOn contains an unrecognized canvas; visible marking stopped")
            count += found
        return count
    if not isinstance(value, dict):
        return 0
    count = 0
    for key in WRAPPERS:
        child = value.get(key)
        if isinstance(child, (dict, list)) or (isinstance(child, str) and child.lstrip().startswith(("{", "["))):
            result = _walk(child, expected, visit, depth + 1)
            if isinstance(result, tuple):
                found, rewritten = result
                if found:
                    value[key] = rewritten
            else:
                found = result
            count += found
    for key in COLLECTIONS:
        if key not in value:
            continue
        children = value[key]
        if not isinstance(children, (dict, list)) or not children:
            raise ValueError(f"Unsupported ProcessOn canvas collection: {key}")
        items = list(children.items()) if isinstance(children, dict) else list(enumerate(children))
        for index, child in items:
            result = _walk(child, expected, visit, depth + 1)
            if isinstance(result, tuple):
                found, rewritten = result
                if found:
                    children[index] = rewritten
            else:
                found = result
            if not found:
                raise ValueError("ProcessOn contains an unrecognized canvas; visible marking stopped")
            count += found
    return count


def _watermark(target):
    previous = target.get("watermark", "")
    if previous is None:
        previous = ""
    if not isinstance(previous, str):
        raise ValueError("Unsupported ProcessOn watermark value")
    if VISIBLE_TEXT not in previous:
        target["watermark"] = previous + (" | " if previous else "") + VISIBLE_TEXT
    target["showWatermark"] = True


def _number(value, fallback=0):
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid ProcessOn shape coordinate")
    return value


def _flow_label(canvas):
    elements = canvas["elements"]
    # Use a stable id; a user shape with this id must never be overwritten.
    if MARKER_ID in elements:
        shape = elements[MARKER_ID]
        if not (
            isinstance(shape, dict)
            and shape.get("name") == "text"
            and shape.get("textBlock") == [{"position": {"x": 0, "y": 0, "w": "w", "h": "h"}, "text": VISIBLE_TEXT}]
        ):
            raise ValueError("ProcessOn visible marker id is already used by another shape")
        # Restore the generated label, including visibility, if it was edited/hidden.
        old_props = shape.get("props", {})
        x, y = _number(old_props.get("x"), 20), _number(old_props.get("y"), 20)
        zindex = _number(old_props.get("zindex"), 1)
    else:
        boxes = []
        for shape in elements.values():
            if not isinstance(shape, dict):
                raise ValueError("Invalid ProcessOn element")
            props = shape.get("props", {})
            if not isinstance(props, dict):
                raise ValueError("Invalid ProcessOn shape properties")
            boxes.append(
                (
                    _number(props.get("x")),
                    _number(props.get("y")) + _number(props.get("h")),
                    _number(props.get("zindex")),
                )
            )
        x = max(20, min((b[0] for b in boxes), default=20))
        y = max(20, max((b[1] for b in boxes), default=-4) + 24)
        zindex = max((b[2] for b in boxes), default=0) + 1
    elements[MARKER_ID] = {
        "id": MARKER_ID,
        "name": "text",
        "title": "AIGC显式标识",
        "category": "basic",
        "group": "",
        "parent": "",
        "children": [],
        "locked": True,
        "link": "",
        "resizeDir": ["tl", "tr", "br", "bl", "l", "t", "r", "b"],
        "attribute": {
            "container": False,
            "visible": True,
            "rotatable": True,
            "linkable": False,
            "collapsable": False,  # codespell:ignore collapsable
            "collapsed": False,
            "fixedLink": False,
            "markerOffset": 5,
        },
        "props": {"x": x, "y": y, "w": 260, "h": 40, "zindex": zindex, "angle": 0},
        "shapeStyle": {"alpha": 1},
        "lineStyle": {},
        "fillStyle": {"type": "solid", "color": "255,255,255"},
        "theme": {},
        "fontStyle": {"size": 16, "color": "0,0,0", "fontFamily": "微软雅黑", "bold": False},
        "dataAttributes": [],
        "path": [
            {
                "lineStyle": {"lineWidth": 0},
                "fillStyle": {"type": "solid", "color": "255,255,255"},
                "actions": [
                    {"action": "move", "x": "0", "y": "0"},
                    {"action": "line", "x": "w", "y": "0"},
                    {"action": "line", "x": "w", "y": "h"},
                    {"action": "line", "x": "0", "y": "h"},
                    {"action": "close"},
                ],
            }
        ],
        "textBlock": [{"position": {"x": 0, "y": 0, "w": "w", "h": "h"}, "text": VISIBLE_TEXT}],
        "anchors": [],
    }
    page = canvas["page"]
    for dimension, minimum in [("width", x + 280), ("height", y + 60)]:
        page[dimension] = max(_number(page.get(dimension)), minimum)


def _mark(canvas, kind):
    if kind == "flow":
        _flow_label(canvas)
        _watermark(canvas["page"])
    else:
        # Root-level native watermark remains visible even when branches are folded.
        _watermark(canvas)
        matches = [child for child in canvas["children"] if isinstance(child, dict) and child.get("id") == MARKER_ID]
        if len(matches) > 1 or (matches and matches[0].get("title") != VISIBLE_TEXT):
            raise ValueError("ProcessOn visible marker id is already used by another topic")
        if not matches:
            canvas["children"].append(
                {"id": MARKER_ID, "parent": canvas.get("id", "root"), "title": VISIBLE_TEXT, "children": []}
            )


def _check(canvas, kind):
    target = canvas["page"] if kind == "flow" else canvas
    if target.get("showWatermark") is not True or VISIBLE_TEXT not in str(target.get("watermark", "")):
        raise ValueError("ProcessOn native visible watermark is missing or disabled")
    if kind == "mind":
        if (
            sum(
                isinstance(c, dict) and c.get("id") == MARKER_ID and c.get("title") == VISIBLE_TEXT
                for c in canvas["children"]
            )
            != 1
        ):
            raise ValueError("ProcessOn visible topic is missing or duplicated")
    else:
        shape = canvas["elements"].get(MARKER_ID, {})
        if (
            shape.get("attribute", {}).get("visible") is not True
            or shape.get("shapeStyle", {}).get("alpha") != 1
            or shape.get("textBlock") != [{"position": {"x": 0, "y": 0, "w": "w", "h": "h"}, "text": VISIBLE_TEXT}]
        ):
            raise ValueError("ProcessOn visible text shape is missing or hidden")


def canvas_updates(archive, extension, mark=True):
    """Read recognized JSON canvas parts, reject unknown primary layouts, verify all found canvases."""
    expected = "mind" if extension == ".posm" else "flow"
    updates, count = {}, 0
    for info in archive.infolist():
        name = info.filename
        path = PurePosixPath(name)
        if (
            info.is_dir()
            or name == "meta.json"
            or any(p.casefold() in ("resources", "assets", "attachments") for p in path.parts[:-1])
        ):
            continue
        raw = archive.read(info)
        if not raw.lstrip(b"\xef\xbb\xbf \r\n\t").startswith((b"{", b"[")):
            if path.stem.lower() in ("content", "diagram", "definition", "canvas", "canvases", "defs"):
                raise ValueError(f"Unrecognized ProcessOn canvas encoding: {name}")
            continue
        data = read_pos_json(raw)
        before = json.dumps(data, ensure_ascii=False)
        found = _walk(data, expected, _mark if mark else _check)
        if not found and path.stem.lower() in ("content", "diagram", "definition", "canvas", "canvases", "defs"):
            raise ValueError(f"Unrecognized ProcessOn canvas structure: {name}")
        count += found
        if found:
            _walk(data, expected, _check)
            after = json.dumps(data, ensure_ascii=False)
            if before != after:
                updates[name] = after.encode("utf-8")
    if not count:
        raise ValueError(
            "No supported ProcessOn canvas found; visible marking stopped (metadata-only tasks may use --skip-visible)"
        )
    return updates, count
