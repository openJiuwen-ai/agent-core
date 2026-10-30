"""Reading delivered files as text: office files by their XML text, other files if they decode; clipping."""

import re
from pathlib import Path

FILE_LIMIT = 20000        # characters kept per deliverable or reference file

EVIDENCE_LIMIT = 60000    # characters kept per evidence part (deliverable, references, log)

OFFICE_TEXT = {".docx": "word/", ".pptx": "ppt/slides/", ".xlsx": "xl/"}


def clip(text, limit):
    return text if len(text) <= limit else text[:limit] + f"\n...[cut {len(text) - limit} characters]"


def extract_text(name, data):
    """Readable text from file bytes: office files by their XML text, other files if they decode."""
    import io
    import zipfile

    prefix = OFFICE_TEXT.get(Path(name).suffix.lower())
    if prefix:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                parts = []
                for member in sorted(archive.namelist()):
                    if member.startswith(prefix) and member.endswith(".xml"):
                        xml = archive.read(member).decode("utf-8", "ignore")
                        words = re.findall(r">([^<>]+)<", xml)
                        if words:
                            parts.append(member + "\n" + " ".join(w.strip() for w in words if w.strip()))
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
                text = extract_text(path.name, path.read_bytes())
                files[name] = "[binary file, not shown]" if text is None else text
    return files


def bundle(files, limit=EVIDENCE_LIMIT):
    text = "\n\n".join(f"===== {name} =====\n{clip(body, FILE_LIMIT)}" for name, body in files.items())
    return clip(text, limit)
