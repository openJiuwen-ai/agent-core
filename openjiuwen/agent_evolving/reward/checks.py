"""Evidence tools and the checks that use them: read, python (guarded) and browser.

The evidence agent chooses checks; they run in the scratch workspace and only measure.
"""

import asyncio
import base64
import mimetypes
import os
import re
import shutil
import struct
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

# A pinned CDN library is downloaded once (then cached); a page whose <head> waits for it loads only after that, so
# both limits leave room for a slow first download (1 MB from jsdelivr/unpkg can take tens of seconds).
CDN_SECONDS = 60         # downloading one pinned CDN file
PAGE_LOAD_MS = 60000     # a page reaching DOMContentLoaded
# Limits on the work, not on the model: a probe runs the agent's script inside the work's page, and a loop that never
# ends there (play until game over, a promise that never settles) would hold the page, and the whole reward, forever.
PROBE_SECONDS = 20               # one probe script
BROWSER_CHECK_SECONDS = 240      # one whole browser check (start, load, every action, screenshots)

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
        with urllib.request.urlopen(url, timeout=CDN_SECONDS) as response:
            body = response.read()
    except Exception:  # noqa: BLE001 - no network: the request fails like any other offline request
        return None
    folder.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(body)
    return body


# Playwright's own Chromium first, then the Chrome or Edge installed on the machine (the browser JiuwenSwarm's own
# browser tools use), so a missing `playwright install chromium` does not leave the work unseen.
BROWSER_CHANNELS = (None, "chrome", "msedge")


async def launch_browser(pw):
    """A started browser: the first of BROWSER_CHANNELS that starts. Raises the first failure when none does."""
    from playwright.async_api import Error as PlaywrightError

    first = None
    for channel in BROWSER_CHANNELS:
        try:
            return await pw.chromium.launch(**({"channel": channel} if channel else {}))
        except PlaywrightError as error:   # not installed, or not startable here: try the next one
            first = first or error
    raise RuntimeError(f"no browser could start (Playwright's Chromium, Chrome, Edge): "
                       f"{type(first).__name__}: {str(first)[:300]}") from first


async def close_browser(browser):
    """Close a browser, even one whose page is stuck in an endless script, waiting 15 s at most. False when it did not
    close (its process then goes with the playwright context).
    """
    from playwright.async_api import Error as PlaywrightError

    try:
        await asyncio.wait_for(browser.close(), 15)
    except (asyncio.TimeoutError, PlaywrightError):
        return False
    return True


def _field(action, name):
    """A required field of an action; a missing one fails that action (reported as evidence), not the run."""
    value = action.get(name)
    if value is None:
        raise ValueError(f"action {action.get('type')!r} needs '{name}'")
    return value


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
            return await r.fulfill(status=404, body="not found")   # an empty 404 makes Chrome fail goto()
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
        browser = await launch_browser(pw)
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=2)
            await context.set_offline(True)
            await context.route("**/*", route)
            page = await context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            response = await page.goto(page_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_MS)
            if response is not None and not response.ok:  # the check failed, not the work
                raise RuntimeError(f"the page did not load: HTTP {response.status} for {page_url}")
            await page.wait_for_timeout(500)
            shots.append(
                ("loaded", base64.b64encode(await page.screenshot(type="jpeg", quality=80, scale="css")).decode())
            )
            waited, stuck = 0, None
            for index, action in enumerate(experiment.get("actions") or []):
                result = {"index": index, "action": action}
                if action.get("type") == "wait":
                    action = {**action, "ms": max(0, min(int(action.get("ms", 0)), MAX_WAIT_MS - waited))}
                    waited += action["ms"]
                try:
                    kind = _field(action, "type")
                    if kind == "click":
                        await page.click(_field(action, "selector"), timeout=3000)
                    elif kind == "click_at":
                        box = await page.locator(_field(action, "selector")).first.bounding_box(timeout=3000)
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
                        key = _field(action, "key")
                        await page.keyboard.down(key)
                        await page.wait_for_timeout(int(action.get("hold_ms", 0)))
                        await page.keyboard.up(key)
                    elif kind == "wait":
                        await page.wait_for_timeout(action.get("ms", 0))
                    elif kind == "probe":
                        try:
                            result["value"] = await asyncio.wait_for(page.evaluate(_field(action, "script")),
                                                                     PROBE_SECONDS)
                        except asyncio.TimeoutError:
                            stuck = (f"the probe did not return within {PROBE_SECONDS} s: the page is stuck (a loop "
                                     "that never ends, or a promise that never settles); the rest of this check "
                                     "did not run")
                            raise TimeoutError(stuck) from None
                    elif kind == "closeup":
                        box = await page.locator(action.get("selector") or "body").first.bounding_box(timeout=3000)
                        x, y, w, h = (list(action.get("region") or [0, 0, 1, 1]) + [0, 0, 1, 1])[:4]
                        region = {"x": box["x"] + box["width"] * x, "y": box["y"] + box["height"] * y,
                                  "width": max(1, box["width"] * w), "height": max(1, box["height"] * h)}
                        data = await page.screenshot(type="jpeg", quality=90, clip=region, scale="device")
                        shots.append((f"close-up at action {index}", base64.b64encode(data).decode()))
                    elif kind == "scroll":   # to an element, or down by viewport heights (pages stacked in one page)
                        target = action.get("selector")
                        if target:
                            await page.locator(target).first.scroll_into_view_if_needed(timeout=3000)
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
                if stuck:   # the page no longer answers: later actions and screenshots would wait on it
                    break
                if index in snapshots:
                    data = await page.screenshot(type="jpeg", quality=80, scale="css")
                    shots.append((f"after action {index}", base64.b64encode(data).decode()))
        finally:
            await close_browser(browser)
    return {"note": experiment.get("note", ""), "opened_as": page_url.split(":", 1)[0] + "://",
            "actions": facts, "page_errors": errors[:20], **({"page_stuck": stuck} if stuck else {})}, shots

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
PICTURE_SIDE = 1600          # longest side, in pixels, of a picture sent to a model
PICTURE_BYTES = 3_000_000    # a picture file larger than this is shrunk before it is sent
MODEL_FORMATS = ("PNG", "JPEG", "GIF", "WEBP")   # what vision APIs take as they are


def natural(path):
    """Sort key that puts page2 before page10."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(path))]


def picture_data(path):
    """(base64 picture, "WxH") of a picture file, ready for a model: the file itself when it is small and in a format
    models take, else a JPEG whose longest side is PICTURE_SIDE. No browser is needed. Raises when it is not a
    picture.
    """
    import io

    from PIL import Image

    data = Path(path).read_bytes()
    with Image.open(io.BytesIO(data)) as picture:
        size = f"{picture.width}x{picture.height}"
        if picture.format not in MODEL_FORMATS or len(data) > PICTURE_BYTES or max(picture.size) > PICTURE_SIDE:
            shrunk = picture.convert("RGB")
            shrunk.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
            buffer = io.BytesIO()
            shrunk.save(buffer, "JPEG", quality=85)
            data = buffer.getvalue()
    return base64.b64encode(data).decode(), size


def picture_or_none(path):
    """picture_data(path), or None when the file is not a usable picture."""
    from PIL import Image

    try:
        return picture_data(path)
    except (OSError, ValueError, SyntaxError, EOFError, struct.error, Image.DecompressionBombError):
        return None   # Pillow: OSError (unidentified, truncated), SyntaxError (a broken PNG), struct.error


async def image_views(paths, max_images=8):
    """[(label, base64 picture)] for delivered picture files (pages of a book, posters), read straight from the files
    (no browser), in page order, so a work made of images is judged by eye like a page. A file that is not a picture
    is left out.
    """
    shots = []
    for path in sorted(paths, key=natural):
        if len(shots) >= max_images:
            break
        found = await asyncio.to_thread(picture_or_none, path)
        if found:   # a file that is not a picture after all is left out
            shots.append((Path(path).name, found[0]))
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
        if path.suffix.lower() in IMAGE_SUFFIXES:   # a picture is looked at: it goes to the Judge as a picture
            found = await asyncio.to_thread(picture_or_none, path)
            if found is None:
                return {**facts, "path": check.get("path"), "output": "not a readable picture"}, []
            data, size = found
            facts = {**facts, "path": check.get("path"), "picture": size,
                     "output": f"picture file ({size}); the Judge sees it as a picture"}
            return facts, [(f"file {check['path']}", data)]
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
        try:
            result, shots = await asyncio.wait_for(run_experiment(page, check), BROWSER_CHECK_SECONDS)
        except asyncio.TimeoutError:   # stuck beyond the probe limit (a page that never loads its scripts, ...)
            return {**facts, "page": check.get("page"), "timed_out": BROWSER_CHECK_SECONDS}, []
        return {**facts, "page": check.get("page"), **result}, shots
    return {**facts, "skipped": f"unknown tool {tool!r}"}, []

