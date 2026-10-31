"""
Dataset Online Editing System
Flask backend serving JSON data + images and accepting edits.
"""
import json
import os
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, abort

app = Flask(__name__)

# Base directory of this project (used to resolve zipdata path)
BASE_DIR = Path(__file__).resolve().parent
# Image root: zipdata/aitw_images/<category>/<event_id>_<n>.png
IMAGE_ROOT = BASE_DIR / "zipdata" / "aitw_images"

# Track currently loaded files so /api/save knows where to write.
# Set on every /api/load call.
STATE = {
    "format_path": None,
    "summary_path": None,
}


# ---------- helpers ----------

def natural_key(s: str):
    """Natural sort key (so _2 comes before _10)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def get_category_from_qid(qid: str) -> str:
    """
    Derive image subfolder from question_id.
    e.g. 'aitw_install_1' -> 'install'.
    Falls back to '' if it cannot parse.
    """
    if not qid:
        return ""
    parts = qid.split("_")
    if len(parts) >= 2:
        return parts[1]
    return ""


def filter_qa_by_schema(qa_item: dict, schema_qa: dict) -> dict:
    """
    Keep only fields that exist in BOTH schema and qa_item.
    (Fields in schema but missing from qa item -> drop.)
    """
    return {k: qa_item[k] for k in schema_qa.keys() if k in qa_item}


# ---------- routes ----------

@app.route("/")
def index():
    return render_template("index.html")


# Where the canonical schema lives. Always this file, regardless of which
# data file the user loads.
SCHEMA_PATH = BASE_DIR / "format.json"


def load_schema_fields():
    """
    Load the field order from the project's root format.json.
    Returns (schema_fields_list, schema_qa_dict, error_or_None).
    """
    if not SCHEMA_PATH.exists():
        return [], {}, f"Schema file not found: {SCHEMA_PATH}"
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            fmt = json.load(f)
    except json.JSONDecodeError as e:
        return [], {}, f"format.json parse error: {e}"

    if not isinstance(fmt, dict) or not fmt:
        return [], {}, "format.json must be a non-empty object"

    fmt_top_key = next(iter(fmt.keys()))
    fmt_top = fmt[fmt_top_key]
    schema_qa_list = fmt_top.get("qa_list", [])
    if not schema_qa_list:
        return [], {}, "format.json has empty qa_list"
    schema_qa = schema_qa_list[0]
    return list(schema_qa.keys()), schema_qa, None


@app.route("/api/load", methods=["POST"])
def api_load():
    """
    Body: { "format_path": "<absolute or relative path>" }

    Loads whichever JSON file the user pointed at. Uses the project's root
    format.json as the field-schema source regardless of which data file is
    loaded. Only fields present in BOTH the schema and the actual qa item
    are displayed.

    Supports the project's top-level-key structure:
      { "<group_key>": { "event_list": [...], "qa_list": [...] or [[...]] } }
    """
    data = request.get_json(silent=True) or {}
    given_path = data.get("format_path", "").strip()
    if not given_path:
        return jsonify({"error": "path is required"}), 400

    gp = Path(given_path)
    if not gp.is_absolute():
        gp = (BASE_DIR / gp).resolve()

    if not gp.exists() or not gp.is_file():
        return jsonify({"error": f"File not found: {gp}"}), 404

    # Load the schema (always from project root format.json)
    schema_fields, schema_qa, schema_err = load_schema_fields()
    if schema_err:
        return jsonify({"error": schema_err}), 400

    # Load the user-chosen data file
    try:
        with open(gp, "r", encoding="utf-8") as f:
            data_obj = json.load(f)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON parse error in {gp.name}: {e}"}), 400

    if not isinstance(data_obj, dict) or not data_obj:
        return jsonify({"error": f"{gp.name} must be a non-empty object"}), 400

    top_key = next(iter(data_obj.keys()))
    data_top = data_obj[top_key]
    if not isinstance(data_top, dict):
        return jsonify({"error": f"Top-level value under '{top_key}' must be an object"}), 400

    # qa_list shape detection
    raw_qa_list = data_top.get("qa_list", [])
    qa_was_nested = bool(raw_qa_list) and isinstance(raw_qa_list[0], list)
    inner_qa = raw_qa_list[0] if qa_was_nested else raw_qa_list

    # Filter each qa item to fields that exist in schema AND in the item
    filtered_qa = [filter_qa_by_schema(item, schema_qa) for item in inner_qa]

    event_list = data_top.get("event_list", [])

    # Track the loaded file for save-back
    STATE["format_path"] = str(SCHEMA_PATH)
    STATE["summary_path"] = str(gp)  # "summary_path" name kept for back-compat; really "current data file"

    # Determine image-category folder from first qa item with a question_id
    category = ""
    for item in filtered_qa:
        qid = item.get("question_id", "")
        if qid:
            category = get_category_from_qid(qid)
            break

    # List available images for the event_list under that category.
    #
    # Filename patterns we support:
    #   - "<event_id>_<n>.png"
    #       e.g. install/10539254038616526840_5.png
    #   - "<event_id>.<range>.<task_description>_<n>.png"
    #       e.g. single/13894343733197654885.112-185.Go to ..._3.png
    # We extract the event_id by splitting on the first '_' OR '.'.
    images = []
    if category:
        folder = IMAGE_ROOT / category
        if folder.exists():
            event_set = {str(e) for e in event_list}
            for f in folder.iterdir():
                if not f.is_file():
                    continue
                if f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                    continue
                stem = f.stem
                # leading event_id = chars up to first '.' or '_'
                m = re.match(r"^([^._]+)", stem)
                event_id = m.group(1) if m else ""
                if event_id not in event_set:
                    continue
                # Trailing index after last '_' (e.g. "..._3" -> 3)
                tail = stem.rsplit("_", 1)
                try:
                    frame_idx = int(tail[1]) if len(tail) == 2 else None
                except ValueError:
                    frame_idx = None
                # Short key used by reference fields: "<event_id>_<n>"
                short_key = f"{event_id}_{frame_idx}" if frame_idx is not None else event_id
                images.append({
                    "name": stem,           # full filename stem (for display caption)
                    "filename": f.name,
                    "url": f"/api/image/{category}/{f.name}",
                    "event_id": event_id,
                    "frame_idx": frame_idx,
                    "short_key": short_key, # matches reference format
                })
            images.sort(key=lambda x: (natural_key(x["event_id"]), x["frame_idx"] if x["frame_idx"] is not None else -1))

    return jsonify({
        "top_key": top_key,
        "schema_fields": schema_fields,
        "event_list": event_list,
        "qa_list": filtered_qa,
        "qa_was_nested": qa_was_nested,
        "category": category,
        "images": images,
        "loaded_path": str(gp),
        "schema_path": str(SCHEMA_PATH),
    })


@app.route("/api/image/<category>/<filename>")
def api_image(category, filename):
    """Serve an image file from zipdata/aitw_images/<category>/<filename>."""
    # Basic sanitization to prevent path traversal
    if "/" in category or "\\" in category or ".." in category:
        abort(400)
    if "/" in filename or "\\" in filename or ".." in filename:
        abort(400)

    path = IMAGE_ROOT / category / filename
    if not path.exists() or not path.is_file():
        abort(404)
    return send_file(str(path))


@app.route("/api/save", methods=["POST"])
def api_save():
    """
    Body: {
      "top_key": "...",
      "event_list": [...],
      "qa_list": [ {...}, {...}, ... ],
      "qa_was_nested": true
    }
    Writes back to summary_question.json (path stored in STATE).
    Preserves nested qa_list wrapping if it was nested originally.
    """
    if not STATE["summary_path"]:
        return jsonify({"error": "No file loaded yet. Use /api/load first."}), 400

    data = request.get_json(silent=True) or {}
    top_key = data.get("top_key")
    event_list = data.get("event_list", [])
    qa_list = data.get("qa_list", [])
    qa_was_nested = data.get("qa_was_nested", True)

    if not top_key:
        return jsonify({"error": "top_key required"}), 400

    summary_path = Path(STATE["summary_path"])
    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception as e:
        return jsonify({"error": f"Failed to read existing summary: {e}"}), 500

    if top_key not in existing:
        existing[top_key] = {}

    existing[top_key]["event_list"] = event_list
    existing[top_key]["qa_list"] = [qa_list] if qa_was_nested else qa_list

    try:
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"error": f"Failed to write file: {e}"}), 500

    return jsonify({"ok": True, "path": str(summary_path)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
