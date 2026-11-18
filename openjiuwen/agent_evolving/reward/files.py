"""Reading delivered files as text: office files by their XML text, other files if they decode; clipping."""

import re
from pathlib import Path

FILE_LIMIT = 20000        # characters kept per deliverable or reference file

EVIDENCE_LIMIT = 60000    # characters kept per evidence part (deliverable, references, log)

MAX_FILE_BYTES = 10 * 1024 * 1024          # one file read into memory
MAX_OFFICE_UNCOMPRESSED = 40 * 1024 * 1024  # one office archive's XML text
TOO_LARGE = "[file too large, not shown]"

OFFICE_TEXT = {".docx": "word/", ".pptx": "ppt/slides/", ".xlsx": "xl/"}


def clip(text, limit):
    return text if len(text) <= limit else text[:limit] + f"\n...[cut {len(text) - limit} characters]"


def modified_between(path, since_ns=None, until_ns=None):
    """Whether the file's mtime is inside the task's time range; no bounds means every file."""
    if since_ns is None and until_ns is None:
        return True
    try:
        stamp = Path(path).stat().st_mtime_ns
    except OSError:
        return False
    return (since_ns is None or stamp >= since_ns) and (until_ns is None or stamp <= until_ns)


def file_too_large(path, limit=MAX_FILE_BYTES):
    """Whether a file exceeds the read budget; an unreadable file counts as too large."""
    try:
        return Path(path).stat().st_size > limit
    except OSError:
        return True


def extract_text(name, data):
    """Readable text from file bytes: office files by their XML text, other files if they decode."""
    import io
    import zipfile

    prefix = OFFICE_TEXT.get(Path(name).suffix.lower())
    if prefix:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = [info for info in archive.infolist()
                           if info.filename.startswith(prefix) and info.filename.endswith(".xml")]
                if sum(info.file_size for info in members) > MAX_OFFICE_UNCOMPRESSED:
                    return None
                parts, remaining = [], MAX_OFFICE_UNCOMPRESSED
                for info in sorted(members, key=lambda item: item.filename):
                    if info.file_size > remaining:
                        return None
                    with archive.open(info) as handle:
                        raw = handle.read(remaining + 1)   # trust bytes read, not the archive header
                    if len(raw) > remaining:
                        return None
                    remaining -= len(raw)
                    xml = raw.decode("utf-8", "ignore")
                    words = re.findall(r">([^<>]+)<", xml)
                    if words:
                        parts.append(info.filename + "\n" + " ".join(w.strip() for w in words if w.strip()))
                return "\n\n".join(parts)
        except zipfile.BadZipFile:
            return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def read_files(paths):
    """{name: text} for files and directories (recursively); undecodable files are listed as such."""
    files = {}
    for root in map(Path, paths):
        for path in sorted(root.rglob("*")) if root.is_dir() else [root]:
            if path.is_file():
                name = str(path.relative_to(root)) if root.is_dir() else path.name
                if file_too_large(path):
                    files[name] = TOO_LARGE
                    continue
                text = extract_text(path.name, path.read_bytes())
                files[name] = "[binary file, not shown]" if text is None else text
    return files


def bundle(files, limit=EVIDENCE_LIMIT):
    text = "\n\n".join(f"===== {name} =====\n{clip(body, FILE_LIMIT)}" for name, body in files.items())
    return clip(text, limit)
