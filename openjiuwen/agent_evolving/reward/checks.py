"""Evidence tools and the checks that use them: read, python (guarded) and browser.

The evidence agent chooses checks; they run in the scratch workspace and only measure.
"""

import asyncio
import base64
import mimetypes
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

from .files import clip, extract_text
from .workspace import INPUTS_DIR, inside

PAGE_CODE = (".html", ".htm", ".js", ".mjs", ".css")   # the source of work used in a browser: it is run, not read


ORIGIN = "http://reward-product.local/"

# A library pinned to an exact version on a public CDN (three@0.160.0, /libs/three.js/0.160.0/) is the same file
# every time: it is fetched once, kept in the cache and served to the page, which stays offline otherwise.
PINNED_CDN = re.compile(r"^https://(?:cdn\.jsdelivr\.net/npm/(?:@[\w.-]+/)?[\w.-]+@\d+\.\d+\.\d+/"
                        r"|unpkg\.com/(?:@[\w.-]+/)?[\w.-]+@\d+\.\d+\.\d+/"
                        r"|cdnjs\.cloudflare\.com/ajax/libs/[\w.-]+/\d+\.\d+\.\d+/)")

MAX_WAIT_MS = 60000      # total "wait" per browser check

PYTHON_SECONDS = 60

CHECK_OUTPUT = 6000      # characters kept from one check's output


def cdn_file(url):
    """The bytes of a pinned CDN file: from the cache ($REWARD_HOME/cdn, default ~/.reward/cdn), else
    downloaded once into it. None when it cannot be had (the page then sees a failed request, as offline).
    """
    import hashlib
    import urllib.request

    folder = Path(os.environ.get("REWARD_HOME") or Path.home() / ".reward") / "cdn"
    cached = folder / hashlib.sha256(url.encode()).hexdigest()
    if cached.is_file():
        return cached.read_bytes()
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            body = response.read()
    except Exception:  # noqa: BLE001 - no network: the request fails like any other offline request
        return None
    folder.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(body)
    return body


async def run_experiment(product, experiment):
    """Run one experiment on the Product, offline. By default the page is served from a local http origin;
    "via": "file" opens it from disk with file://, as a user who double-clicks it would. Raises if it cannot
    start.
    """
    from playwright.async_api import async_playwright

    path = await asyncio.to_thread(Path(product).resolve)
    from_disk = experiment.get("via") == "file"
    page_url = path.as_uri() if from_disk else ORIGIN + quote(path.name)  # names may be non-ASCII

    async def route(r):
        url = r.request.url.split("#")[0].split("?")[0]
        if url.startswith("file:"):
            return await r.continue_()
        if url.startswith(ORIGIN) and not from_disk:
            target = (path.parent / unquote(url[len(ORIGIN):])).resolve()
            if target.is_relative_to(path.parent) and target.is_file():
                kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                return await r.fulfill(status=200, body=target.read_bytes(), content_type=kind)
            return await r.fulfill(status=404, body="")
        if PINNED_CDN.match(url):
            body = await asyncio.to_thread(cdn_file, url)
            if body is not None:
                kind = mimetypes.guess_type(url)[0] or "application/octet-stream"
                kind = "text/javascript" if url.endswith((".js", ".mjs")) else kind
                return await r.fulfill(status=200, body=body, content_type=kind,
                                       headers={"Access-Control-Allow-Origin": "*"})
        await r.abort()

    facts, shots, errors = [], [], []
    snapshots = set(experiment.get("snapshots") or [])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
            await context.set_offline(True)
            await context.route("**/*", route)
            page = await context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            response = await page.goto(page_url, wait_until="domcontentloaded", timeout=10000)
            if response is not None and not response.ok:  # the check failed, not the work
                raise RuntimeError(f"the page did not load: HTTP {response.status} for {page_url}")
            await page.wait_for_timeout(500)
            shots.append(
                ("loaded", base64.b64encode(await page.screenshot(type="jpeg", quality=80, scale="css")).decode())
            )
            waited = 0
            for index, action in enumerate(experiment.get("actions") or []):
                result = {"index": index, "action": action}
                if action.get("type") == "wait":
                    action = {**action, "ms": max(0, min(int(action.get("ms", 0)), MAX_WAIT_MS - waited))}
                    waited += action["ms"]
                try:
                    kind = action["type"]
                    if kind == "click":
                        await page.click(action["selector"], timeout=3000)
                    elif kind == "click_at":
                        box = await page.locator(action["selector"]).first.bounding_box(timeout=3000)
                        await page.mouse.click(box["x"] + box["width"] * action.get("x", 0.5),
                                               box["y"] + box["height"] * action.get("y", 0.5))
                    elif kind == "drag" or kind == "wheel":   # rotate or zoom a 3D view, drag a piece
                        box = await page.locator(action.get("selector") or "canvas, body").first.bounding_box(
                            timeout=3000,
                        )
                        from_x, from_y = action.get("from") or [0.5, 0.5]
                        await page.mouse.move(
                            box["x"] + box["width"] * float(from_x),
                            box["y"] + box["height"] * float(from_y),
                        )
                        if kind == "wheel":
                            await page.mouse.wheel(0, float(action.get("dy", 300)))
                        else:
                            await page.mouse.down()
                            to_x, to_y = action.get("to") or [0.7, 0.5]
                            await page.mouse.move(
                                box["x"] + box["width"] * float(to_x),
                                box["y"] + box["height"] * float(to_y),
                                steps=max(2, min(60, int(action.get("steps", 12)))),
                            )
                            await page.mouse.up()
                        await page.wait_for_timeout(300)
                    elif kind == "key":
                        await page.keyboard.down(action["key"])
                        await page.wait_for_timeout(int(action.get("hold_ms", 0)))
                        await page.keyboard.up(action["key"])
                    elif kind == "wait":
                        await page.wait_for_timeout(action["ms"])
                    elif kind == "probe":
                        result["value"] = await page.evaluate(action["script"])
                    elif kind == "closeup":
                        box = await page.locator(action.get("selector") or "body").first.bounding_box(timeout=3000)
                        x, y, w, h = (list(action.get("region") or [0, 0, 1, 1]) + [0, 0, 1, 1])[:4]
                        region = {"x": box["x"] + box["width"] * x, "y": box["y"] + box["height"] * y,
                                  "width": max(1, box["width"] * w), "height": max(1, box["height"] * h)}
                        data = await page.screenshot(type="jpeg", quality=90, clip=region, scale="device")
                        shots.append((f"close-up at action {index}", base64.b64encode(data).decode()))
                    elif kind == "scroll":   # to an element, or down by viewport heights (pages stacked in one page)
                        if action.get("selector"):
                            await page.locator(action["selector"]).first.scroll_into_view_if_needed(timeout=3000)
                        else:
                            await page.evaluate("n => window.scrollBy(0, window.innerHeight * n)",
                                                float(action.get("pages", 1)))
                        await page.wait_for_timeout(300)
                    elif kind == "screenshot" or kind == "snapshot":   # "snapshot": the name models often use for it
                        data = await page.screenshot(type="jpeg", quality=80, scale="css")
                        shots.append((f"after action {index}", base64.b64encode(data).decode()))
                    else:
                        raise ValueError(f"unknown action type {kind!r}")
                    result["ok"] = True
                except Exception as error:  # noqa: BLE001 - an action failure is evidence, not a crash
                    result.update(ok=False, error=f"{type(error).__name__}: {str(error)[:300]}")
                facts.append(result)
                if index in snapshots:
                    data = await page.screenshot(type="jpeg", quality=80, scale="css")
                    shots.append((f"after action {index}", base64.b64encode(data).decode()))
        finally:
            await browser.close()
    return {"note": experiment.get("note", ""), "opened_as": page_url.split(":", 1)[0] + "://",
            "actions": facts, "page_errors": errors[:20]}, shots

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def natural(path):
    """Sort key that puts page2 before page10."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(path))]


async def image_views(paths, max_images=8, width=1280):
    """[(label, base64 jpeg)] for delivered picture files (pages of a book, posters), each shown whole on a light
    background, so a work made of images can be judged by eye like a page.
    """
    from playwright.async_api import async_playwright

    shots = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await browser.new_context(viewport={"width": width, "height": 800})).new_page()
            for path in sorted(paths, key=natural)[:max_images]:
                data = base64.b64encode(await asyncio.to_thread(Path(path).read_bytes)).decode()
                kind = mimetypes.guess_type(Path(path).name)[0] or "image/png"
                await page.set_content(f"<body style='margin:0;background:#f4f4f4'><img src='data:{kind};base64,"
                                       f"{data}' style='display:block;max-width:100%;margin:auto'></body>")
                await page.wait_for_timeout(100)
                shot = await page.screenshot(type="jpeg", quality=85, full_page=True)
                shots.append((Path(path).name, base64.b64encode(shot).decode()))
        finally:
            await browser.close()
    return shots


def available_tools(root, config):  # noqa: ARG001 - config kept for callers
    tools = ["read", "python"]
    if any(p.suffix.lower() in (".html", ".htm") for p in root.rglob("*") if INPUTS_DIR not in p.parts):
        try:
            import playwright  # noqa: F401
        except ImportError as error:   # a page judged without a browser would be judged from its source: refuse
            raise RuntimeError("The work has web pages, and judging them needs playwright: "
                               "pip install 'openjiuwen[reward-browser]' && playwright install chromium") from error
        tools.append("browser")
    return tools

# Runs before a python check's own code (python -I -c GUARD script): an audit hook that refuses, for the rest of the
# process, starting programs, network connections, native code (ctypes), changing files outside the work's scratch
# copy (cwd) and the script's own folder, and reading files outside those and the Python installation.
# It stops a check from doing harm by mistake or by an
# obvious attempt; it is not an operating-system sandbox (see the README).
GUARD = r"""
import os, sys
def _real(p):
    try:
        return os.path.realpath(os.fsdecode(p))
    except Exception:
        return ""
_script = sys.argv[1]
_HERE = [_real(os.getcwd()), _real(os.path.dirname(_script))]
_READ = _HERE + [_real(p) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix, *sys.path} if p] + [
    "/usr", "/lib", "/lib64", "/etc/localtime", "/etc/ssl", "/dev/null", "/dev/urandom", "/proc/self",
    "/System/Library", "/Library/Frameworks", "/opt/homebrew", "/private/var/db/timezone"]
def _inside(p, roots):
    p = _real(p)
    return bool(p) and any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots if r)
_DENY = ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty",
         "os.kill", "os.killpg", "pty.spawn", "socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg",
         "ctypes.dlopen", "ctypes.dlsym", "ctypes.cdata", "webbrowser.open", "sys.addaudithook")
_CHANGE = ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate", "os.utime", "os.link",
           "os.symlink", "shutil.rmtree", "shutil.copyfile", "shutil.move")
_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
def _guard(event, args):
    if event.startswith(_DENY):
        raise PermissionError(f"reward check: {event} is not allowed in a check")
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path, mode, flags = (list(args) + [None, None])[:3]
        writing = any(c in (mode or "") for c in "wax+") or bool((flags or 0) & _WRITE)
        if not _inside(path, _HERE if writing else _READ):
            raise PermissionError(f"reward check: {'writing' if writing else 'reading'} {path} is not allowed")
    elif event in ("os.listdir", "os.scandir") and args and args[0] is not None and not _inside(args[0], _READ):
        raise PermissionError(f"reward check: listing {args[0]} is not allowed")
    elif event == "os.rename" or event.startswith(_CHANGE):
        for p in [a for a in args[:2] if isinstance(a, (str, bytes, os.PathLike))]:
            if not _inside(p, _HERE):
                raise PermissionError(f"reward check: changing {p} is not allowed")
import runpy
sys.argv = sys.argv[1:]
sys.addaudithook(_guard)   # from here on, for good: an audit hook cannot be removed
runpy.run_path(_script, run_name="__main__")
"""


def check_environment():
    """Environment for python checks: no proxy route out, so a check cannot reach the network by accident."""
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env.update(HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", PYTHONIOENCODING="utf-8",
               PYTHONDONTWRITEBYTECODE="1")
    return env


async def run_process(arguments, cwd, seconds, temp=None):
    env = {**check_environment(), **({"TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)} if temp else {})}
    process = await asyncio.create_subprocess_exec(*arguments, cwd=cwd, env=env,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(process.communicate(), seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {"exit_code": None, "timed_out": seconds, "stdout": "", "stderr": ""}
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    return {"exit_code": process.returncode,
            "stdout": clip(out.decode("utf-8", "replace"), CHECK_OUTPUT),
            "stderr": clip(err.decode("utf-8", "replace"), CHECK_OUTPUT // 2)}


def whole_lines(numbered):
    """Numbered lines, as many whole lines as fit in CHECK_OUTPUT: never a line cut in half, so the reader knows
    exactly which lines it has seen.
    """
    out, size = [], 0
    for number, line in numbered:
        row = f"{number}: {line}"
        if out and size + len(row) + 1 > CHECK_OUTPUT:
            break
        out.append(row[:CHECK_OUTPUT])
        size += len(row) + 1
    return "\n".join(out)


async def run_check(check, root, tools, number=1):
    """Run one check in the workspace. Returns (facts, screenshots). Raises when the check itself cannot run."""
    tool = check.get("tool")
    facts = {"tool": tool, "note": check.get("note", "")}
    if tool not in tools:
        return {**facts, "skipped": f"tool {tool!r} is not available in this run"
                + ("; read_evidence is its own tool, not a check" if tool == "read_evidence" else "")}, []
    if tool == "read":
        if not check.get("path"):
            return {**facts, "skipped": "read needs a \"path\"; a saved result is read with the read_evidence tool"}, []
        path = inside(root, check["path"])
        if path.is_dir():   # a folder: say what is in it rather than fail
            names = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())[:200]
            return {**facts, "path": check.get("path"), "output": "folder; files:\n" + "\n".join(names)}, []
        if "browser" in tools and path.suffix.lower() in PAGE_CODE:   # work that is used, not read
            return {**facts, "skipped": "the source of a page is not read: use the page in the browser; find the names "
                                        "a probe needs with a probe (Object.keys(window), the page's buttons)"}, []
        text = extract_text(path.name, path.read_bytes()) or "[binary file]"
        lines = text.splitlines()
        if check.get("pattern"):
            pattern = re.compile(check["pattern"], re.I)
            context = max(0, min(5, int(check.get("context") or 0)))
            hits = [i for i, line in enumerate(lines) if pattern.search(line)][:60]
            shown = sorted({j for i in hits for j in range(max(0, i - context), min(len(lines), i + context + 1))})
            body = whole_lines([(j + 1, lines[j]) for j in shown])
            extra = {"matches": len(hits)}
            if body.count("\n") + 1 < len(shown):
                extra["cut"] = "too many matches to show; narrow the pattern"
            body = body or "(no line matches)"
        else:
            start = max(1, int(check.get("start") or 1))
            count = max(1, min(400, int(check.get("count") or 200)))
            wanted = list(enumerate(lines[start - 1:start - 1 + count], start))
            body = whole_lines(wanted)
            last = start + body.count("\n") if body else start - 1
            extra = {"lines": f"{start}-{last} of {len(lines)}"}
        return {**facts, "path": check.get("path"), **extra, "output": body}, []
    if tool == "python":
        # the script lives outside the workspace, so checks that list files see only the work
        folder = Path(tempfile.mkdtemp(prefix="reward_check_"))
        script = folder / f"check_{number}.py"
        script.write_text(check.get("code", ""), encoding="utf-8")
        try:
            result = await run_process([sys.executable, "-I", "-c", GUARD, str(script)], root, PYTHON_SECONDS,
                                   temp=folder)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return {**facts, "code": clip(check.get("code", ""), 3000), **result}, []
    if tool == "browser":
        page = inside(root, check.get("page", ""))
        result, shots = await run_experiment(page, check)
        return {**facts, "page": check.get("page"), **result}, shots
    return {**facts, "skipped": f"unknown tool {tool!r}"}, []

