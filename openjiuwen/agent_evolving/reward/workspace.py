"""The scratch copy every check runs in; optional batch reference texts live under _inputs/."""

import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote

from .files import extract_text

INPUTS_DIR = "_inputs"   # used only when a batch caller explicitly supplies reference texts


def copy_work_folder(source, root):
    """A folder whose work is its top-level page(s): copy the top-level files and what those pages link to, not
    sibling folders such as other versions of the same work (v2/, baseline_v3/) kept in the same project folder.
    """
    for item in source.iterdir():
        if item.is_file():
            shutil.copy2(item, root / item.name)
    for page in source.glob("*.htm*"):
        for name, asset in linked_files(page).items():
            target = root / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset, target)


def make_workspace(products=(), texts=None, reference_texts=None):
    """A scratch copy of the work: products (files or directories) at the top. texts / reference_texts
    ({name: text}) are written as files (the latter under _inputs/), for batch runs on work that exists only as
    text.
    """
    sources = list(map(Path, products))
    for source in sources:
        if not source.is_file() and not source.is_dir():
            raise FileNotFoundError(f"artifact does not exist or is not a file/directory: {source}")
    root = Path(tempfile.mkdtemp(prefix="reward_"))
    for source in sources:
        if source.is_dir() and any(source.glob("*.htm*")):
            copy_work_folder(source, root)
        elif source.is_dir():
            shutil.copytree(source, root, dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, root / source.name)
            for name, asset in linked_files(source).items():  # a page's own images, styles and scripts
                target = root / name
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(asset, target)
    for name, body in (texts or {}).items():
        target = root / safe_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    inputs = root / INPUTS_DIR
    for name, body in (reference_texts or {}).items():
        target = inputs / safe_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def safe_name(name):
    """A relative path that stays inside the workspace."""
    parts = [p for p in re.split(r"[\\\\/]+", str(name)) if p not in ("", ".", "..")]
    return Path(*[re.sub(r'[<>:"|?*]', "_", p) for p in parts]) if parts else Path("unnamed")


def inside(root, relative):
    target = (root / str(relative)).resolve()
    if not str(target).startswith(str(root.resolve()) + os.sep) and target != root.resolve():
        raise ValueError(f"path leaves the workspace: {relative}")
    return target


def workspace_files(root):
    """({deliverable name: text}, {input name: text}) of a workspace."""
    deliverable, inputs = {}, {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith(".check_"):
            continue
        relative = path.relative_to(root)
        text = extract_text(path.name, path.read_bytes())
        body = "[binary file, not shown]" if text is None else text
        if relative.parts[0] == INPUTS_DIR:
            inputs[str(Path(*relative.parts[1:]))] = body
        else:
            deliverable[str(relative)] = body
    return deliverable, inputs

LINK = re.compile(r"""(?:src|href|poster|data-src)\s*=\s*["']([^"'#?]+)|url\(\s*["']?([^"')#?]+)""", re.I)
CSS_IMPORT = re.compile(r"""@import\s*["']([^"'#?]+)""", re.I)


def linked_files(page, max_files=200, max_bytes=200_000_000):
    """Local HTML/CSS dependencies under the page's folder, including linked stylesheets' assets.

    Follow explicit references, not neighboring version folders. Dynamic JavaScript loads are not inferred.
    """
    page = Path(page)
    if page.suffix.lower() not in (".html", ".htm"):
        return {}
    base, found, total = page.parent.resolve(), {}, 0
    pending, scanned = [page.resolve()], set()
    while pending:
        document = pending.pop()
        if document in scanned:
            continue
        scanned.add(document)
        try:
            text = document.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs = [match.group(1) or match.group(2) for match in LINK.finditer(text)]
        if document.suffix.lower() == ".css":
            refs.extend(CSS_IMPORT.findall(text))
        for value in refs:
            ref = unquote(value.strip())
            if not ref or re.match(r"^[a-z][a-z0-9+.-]*:|^/|^//", ref, re.I):
                continue  # data:, http:, absolute paths
            try:
                target = (document.parent / ref).resolve()
                if not target.is_file() or not target.is_relative_to(base) or target == page.resolve():
                    continue
                name = str(target.relative_to(base))
                if name in found:
                    continue
                size = target.stat().st_size
            except OSError:
                continue
            if len(found) >= max_files or total + size > max_bytes:
                raise ValueError("linked artifact resources exceed the workspace copy limit")
            found[name] = target
            total += size
            if target.suffix.lower() in (".html", ".htm", ".css"):
                pending.append(target)
    return found
