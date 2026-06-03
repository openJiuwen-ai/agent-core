#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

from bs4 import BeautifulSoup
from openai import OpenAI

import common as common

try:
    from playwright_stealth import Stealth as _Stealth
    _has_stealth = True
except ImportError:
    _Stealth = None
    _has_stealth = False

UTILITY_PATHS = {
    "/login", "/signin", "/signup", "/register", "/logout",
    "/privacy", "/terms", "/tos", "/cookies", "/legal",
    "/about", "/contact", "/faq", "/help", "/support", "/careers",
    "/cart", "/checkout", "/payment", "/subscribe",
    "/search", "/sitemap", "/404", "/403",
}

AD_DOMAINS = {
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "googletagmanager.com", "google-analytics.com",
    "adnxs.com", "criteo.com", "criteo.net", "outbrain.com", "taboola.com",
    "moatads.com", "rubiconproject.com", "pubmatic.com", "openx.net",
    "scorecardresearch.com", "quantserve.com", "hotjar.com",
    "facebook.com", "connect.facebook.net",
    "cookielaw.org", "onetrust.com",
}

AD_PATH_KEYWORDS = {
    "/ads/", "/ad/", "/banner/", "/banners/",
    "/tracking/", "/pixel/", "/beacon/",
    "/analytics/", "/telemetry/",
    "/sponsored/", "/promo/",
}

PLATFORM_PATTERNS = [
    r"youtube\.com/watch", r"youtu\.be/",
    r"bilibili\.com/video", r"vimeo\.com/\d+",
    r"twitter\.com/.+/status", r"x\.com/.+/status",
]

COOKIE_SELECTORS = [
    "button#onetrust-accept-btn-handler",
    "button[id*='accept-all']",
    "button[id*='accept_all']",
    "button[class*='accept-all']",
    "button[aria-label*='Accept all']",
    "button[aria-label*='accept all']",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('I agree')",
    "button:has-text('Agree')",
]

NOISE_IDS = (
    "onetrust-consent-sdk", "onetrust-banner-sdk", "onetrust-pc-sdk",
    "cookie-law-info-bar", "gdpr-cookie-notice", "CybotCookiebotDialog",
)

NOISE_TABPANEL_LABELS = {
    "discover", "community", "contact us", "windows insiders",
    "related resources", "more resources",
}

NOISE_SUBPAGE_PATHS = (
    "/accessibility", "/security", "/rss", "/windows-insiders",
)

NOISE_CLASSES = {
    "uhf", "c-uhfh", "c-footer", "c-nav", "breadcrumb",
    "feedback", "social", "c-heading-4", "ocr",
}


# ── URL helpers ───────────────────────────────────────────────────────────────

def is_utility_url(url: str) -> bool:
    try:
        path = urlparse(url).path.lower().rstrip("/")
        return any(path == p or path.startswith(p + "/") for p in UTILITY_PATHS)
    except Exception:
        return False


def is_ad_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().lstrip("www.")
        parts = host.split(".")
        if any(".".join(parts[i:]) in AD_DOMAINS for i in range(len(parts) - 1)):
            return True
        if any(kw in parsed.path.lower() for kw in AD_PATH_KEYWORDS):
            return True
    except Exception as exc:
        logger.debug("is_ad_url failed for %r: %s", url, exc)
    return False


def is_platform_url(url: str) -> bool:
    return any(re.search(p, url) for p in PLATFORM_PATTERNS)


# ── DOM helpers ───────────────────────────────────────────────────────────────

def el_text(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


def is_content_img(img) -> bool:
    src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
    return bool(src) and not src.startswith("data:") and not src.endswith(".svg")


def _best_img_url(img, page_url: str) -> str:
    """Pick the best-resolution URL: srcset last entry, else src/data-src."""
    srcset = img.get("srcset", "")
    if srcset:
        candidates = [p.strip().split()[0] for p in srcset.split(",") if p.strip()]
        if candidates:
            return urljoin(page_url, candidates[-1])
    for attr in ("src", "data-src", "data-lazy-src"):
        val = img.get(attr, "")
        if val and not val.startswith("data:"):
            return urljoin(page_url, val)
    return ""


def resolve_remote_reference(img, soup) -> str:
    for attr in ("aria-describedby", "aria-labelledby"):
        ref_id = img.get(attr, "").strip()
        if ref_id:
            target = soup.find(id=ref_id)
            if target:
                return el_text(target)
    figure = img.find_parent("figure")
    if figure:
        caption = figure.find("figcaption")
        if caption:
            return el_text(caption)
    td = img.find_parent("td")
    if td:
        row = td.find_parent("tr")
        if row:
            sibling_texts = [
                el_text(cell)
                for cell in row.find_all("td")
                if cell is not td and el_text(cell)
            ]
            if sibling_texts:
                return " | ".join(sibling_texts)
    for attr, val in img.attrs.items():
        if attr.startswith("data-") and any(kw in attr for kw in ("caption", "label", "desc", "title")):
            text = str(val).strip()
            if text:
                return text
    return img.get("title", "").strip()


def _build_tabpanel_labels(root) -> dict[str, str]:
    labels: dict[str, str] = {}
    for tab in root.find_all(attrs={"role": "tab"}):
        label = el_text(tab).strip()
        if not label:
            continue
        controls = tab.get("aria-controls", "")
        if controls:
            labels[controls] = label
    for panel in root.find_all(attrs={"role": "tabpanel"}):
        panel_id = panel.get("id", "")
        if panel_id and panel_id not in labels:
            labelledby = panel.get("aria-labelledby", "")
            if labelledby:
                tab_el = root.find(id=labelledby)
                if tab_el:
                    labels[panel_id] = el_text(tab_el).strip()
    return labels


def _tabpanel_info(el, root, tabpanel_labels: dict) -> tuple[str, str]:
    """Return (panel_id, tab_label) for the innermost tabpanel containing el."""
    for parent in el.parents:
        if parent is root:
            break
        if parent.get("role") == "tabpanel":
            panel_id = parent.get("id", "")
            return panel_id, tabpanel_labels.get(panel_id, "")
    return "", ""


# ── Core: build unified blocks[] ─────────────────────────────────────────────

def build_blocks(soup, page_url: str, source: str) -> list[dict]:
    """Walk DOM in order, output interleaved heading / text / image blocks."""
    root = (
        soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find("article")
        or soup.body
        or soup
    )
    tabpanel_labels = _build_tabpanel_labels(root)
    injected_panels: set[str] = set()
    seen_text: set[str] = set()
    blocks: list[dict] = []

    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "img"], recursive=True):
        panel_id, tab_label = _tabpanel_info(el, root, tabpanel_labels)

        # Skip elements inside noise tabpanels entirely
        if tab_label and tab_label.lower() in NOISE_TABPANEL_LABELS:
            continue

        # Inject tab label as a level-2 heading on the first element of each content tabpanel
        if panel_id and panel_id not in injected_panels:
            injected_panels.add(panel_id)
            if tab_label:
                blocks.append({"type": "heading", "level": 2, "text": tab_label, "source": source})

        # Skip noise CSS classes
        if any(cls in " ".join(el.get("class", [])) for cls in NOISE_CLASSES):
            continue

        if el.name in ("h1", "h2", "h3", "h4"):
            text = el_text(el)
            if text and text not in seen_text:
                seen_text.add(text)
                blocks.append({"type": "heading", "level": int(el.name[1]), "text": text, "source": source})

        elif el.name == "img":
            if not is_content_img(el):
                continue
            url = _best_img_url(el, page_url)
            if not url:
                continue
            alt = el.get("alt", "").strip() or resolve_remote_reference(el, soup)
            blocks.append({"type": "image", "url": url, "alt": alt, "source": source, "path": None})

        else:
            text = el_text(el)
            if text and len(text) > 15 and text not in seen_text:
                seen_text.add(text)
                blocks.append({"type": "text", "text": text[:400], "source": source})

    return blocks


def parse_page_html(html: str, page_url: str, source: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for noise_id in NOISE_IDS:
        el = soup.find(id=noise_id)
        if el:
            el.decompose()
    return build_blocks(soup, page_url, source)


# ── Video detection ───────────────────────────────────────────────────────────

def detect_video_urls_from_html(html: str) -> list[str]:
    video_urls = []
    for match in re.finditer(r"youtube\.com/embed/([A-Za-z0-9_-]+)", html):
        video_urls.append(f"https://www.youtube.com/watch?v={match.group(1)}")
    for match in re.finditer(r"bilibili\.com/video/(BV[A-Za-z0-9]+)", html):
        video_urls.append(f"https://www.bilibili.com/video/{match.group(1)}")
    for match in re.finditer(r"aid=(\d+)", html):
        video_urls.append(f"https://www.bilibili.com/video/av{match.group(1)}")
    for match in re.finditer(r"player\.vimeo\.com/video/(\d+)", html):
        video_urls.append(f"https://vimeo.com/{match.group(1)}")
    return list(dict.fromkeys(video_urls))


# ── Playwright scraping ───────────────────────────────────────────────────────

async def scrape_one_page(page, page_url: str, dismiss_cookie: bool = False) -> tuple[str, list[str], list[str]]:
    """Return (html, video_urls, subpage_links)."""
    html = ""
    subpage_links: list[str] = []
    base_domain = urlparse(page_url).netloc

    try:
        resp = await page.goto(page_url, wait_until="networkidle", timeout=30_000)
        if resp and resp.status >= 400:
            logger.warning("[scrape] HTTP %s for %s", resp.status, page_url)
            return html, [], subpage_links
        await page.wait_for_timeout(1500)

        if dismiss_cookie:
            for selector in COOKIE_SELECTORS:
                try:
                    button = page.locator(selector).first
                    if await button.is_visible(timeout=1000):
                        await button.click()
                        logger.debug("[scrape] Dismissed cookie banner (%s)", selector)
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                        break
                except Exception as exc:
                    logger.debug("[scrape] Cookie selector %s failed: %s", selector, exc)
                    continue

        await page.evaluate("() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })")
        await page.wait_for_timeout(1500)
        html = await page.content()

        try:
            links = await page.eval_on_selector_all(
                "main a[href], article a[href], [role='main'] a[href], .content a[href]",
                "els => els.map(e => e.href).filter(Boolean)",
            )
            if not links:
                links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href).filter(Boolean)")
            for link in dict.fromkeys(links):
                parsed = urlparse(link)
                is_same_domain_new = parsed.netloc == base_domain and link != page_url
                if (
                    is_same_domain_new
                    and not is_utility_url(link)
                    and not is_ad_url(link)
                ):
                    subpage_links.append(link)
        except Exception as exc:
            logger.debug("[scrape] Link extraction failed: %s", exc)

        return html, detect_video_urls_from_html(html), subpage_links

    except Exception as exc:
        logger.warning("[scrape] Playwright error for %s: %s", page_url, exc)
        return html, [], subpage_links


async def scrape_subpage(context, page_url: str) -> tuple[str, list[str]]:
    page = await context.new_page()
    if _has_stealth and _Stealth:
        await _Stealth().apply_stealth_async(page)
    try:
        html, video_urls, _ = await scrape_one_page(page, page_url, dismiss_cookie=False)
    finally:
        await page.close()
    return html, video_urls


async def scrape_pages_playwright(page_url: str, max_subpages: int = 5) -> tuple[list[dict], list[str], str]:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=common.STEALTH_UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        main_page = await context.new_page()
        if _has_stealth and _Stealth:
            await _Stealth().apply_stealth_async(main_page)
        try:
            main_html, main_videos, subpage_links = await scrape_one_page(
                main_page, page_url, dismiss_cookie=True
            )
            page_title = await main_page.title()
        finally:
            await main_page.close()

        def _is_noise_subpage(url: str) -> bool:
            path = urlparse(url).path.lower().rstrip("/")
            return any(
                path == kw or path.endswith(kw) or ("/" + kw.lstrip("/")) in path
                for kw in NOISE_SUBPAGE_PATHS
            )

        filtered_links = [u for u in subpage_links if not _is_noise_subpage(u)]
        subpage_urls = filtered_links[:max_subpages]
        logger.info("[subpages] Found %d candidate subpages (%d noise filtered)",
                    len(subpage_links), len(subpage_links) - len(filtered_links))

        sub_results = await asyncio.gather(
            *[scrape_subpage(context, url) for url in subpage_urls],
            return_exceptions=True,
        )
        await context.close()
        await browser.close()

    all_blocks: list[dict] = []
    video_urls: list[str] = list(main_videos)

    if main_html:
        all_blocks.extend(parse_page_html(main_html, page_url, "main"))

    for sub_url, result in zip(subpage_urls, sub_results):
        if isinstance(result, Exception):
            logger.warning("[skip-error] %s: %s", sub_url, result)
            continue
        sub_html, sub_videos = result
        if not sub_html:
            continue
        sub_blocks = parse_page_html(sub_html, sub_url, "subpage")
        img_count = sum(1 for b in sub_blocks if b["type"] == "image")
        logger.info("[found] %s — %d images", sub_url, img_count)
        all_blocks.extend(sub_blocks)
        video_urls.extend(sub_videos)

    # Deduplicate image blocks by URL, preserving first occurrence and surrounding context
    seen_img_urls: set[str] = set()
    deduped: list[dict] = []
    for block in all_blocks:
        if block["type"] == "image":
            if block["url"] in seen_img_urls:
                continue
            seen_img_urls.add(block["url"])
        deduped.append(block)

    video_urls = list(dict.fromkeys(video_urls))
    if is_platform_url(page_url) and page_url not in video_urls:
        video_urls.insert(0, page_url)

    return deduped, video_urls, page_title


def scrape_page(url: str, max_subpages: int = 5) -> tuple[list[dict], list[str], str]:
    return asyncio.run(scrape_pages_playwright(url, max_subpages=max_subpages))


def scrape_page_via_llm(client: OpenAI, url: str) -> tuple[list[dict], list[str], str]:
    logger.info("[scrape] Direct fetch blocked — falling back to LLM web plugin...")
    try:
        resp = client.chat.completions.create(
            model=common.MODEL,
            messages=[
                {"role": "system", "content": common.SCRAPE_FALLBACK_PROMPT},
                {"role": "user", "content": f"Fetch and parse this page: {url}"},
            ],
            extra_body={"plugins": [{"id": "web", "max_results": 1}]},
            temperature=0.0,
            max_tokens=6000,
        )
        data = json.loads(common.strip_json_fence(resp.choices[0].message.content))
        blocks = [
            {**b, "source": b.get("source", "main"), "path": None}
            for b in data.get("blocks", [])
            if b.get("type") in ("heading", "text", "image")
        ]
        return blocks, data.get("video_urls", []), data.get("title", "")
    except Exception as exc:
        logger.warning("[scrape] LLM fallback also failed: %s", exc)
        return [], [], ""


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 1 (new): scrape page into unified blocks[].")
    parser.add_argument("url")
    parser.add_argument("--slug", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--max-subpages", type=int, default=5)
    parser.add_argument("--no-llm-fallback", action="store_true")
    args = parser.parse_args()

    slug = args.slug or common.url_to_slug(args.url)
    out = Path(args.out) if args.out else common.work_path(slug, "stage_01_scrape.json")

    blocks, video_urls, page_title = scrape_page(args.url, max_subpages=args.max_subpages)

    blocked_markers = ("the request is blocked", "access denied", "403 forbidden", "enable javascript")
    img_blocks = [b for b in blocks if b["type"] == "image"]
    text_content = " ".join(b["text"].lower() for b in blocks if b["type"] in ("heading", "text"))
    is_blocked = not img_blocks and any(m in text_content for m in blocked_markers)

    if not args.no_llm_fallback and (not blocks or is_blocked):
        client = OpenAI(api_key=common.API_KEY, base_url=common.API_BASE)
        fallback_blocks, fallback_videos, fallback_title = scrape_page_via_llm(client, args.url)
        if fallback_blocks:
            blocks, video_urls = fallback_blocks, fallback_videos
        if fallback_title and not page_title:
            page_title = fallback_title

    img_count = sum(1 for b in blocks if b["type"] == "image")
    common.write_json(out, {
        "url": args.url,
        "slug": slug,
        "title": page_title,
        "blocks": blocks,
        "video_urls": video_urls,
    })
    logger.info("[stage 1] wrote %s: %d blocks (%d images), title: %r", out, len(blocks), img_count, page_title)


if __name__ == "__main__":
    main()
