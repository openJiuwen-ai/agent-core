# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Disk projection of the TTSE bank by business-scenario category.

Authoritative source remains bank.json. These files are a save-time snapshot
for ``ttse_consult`` / humans; the model never sees the paths.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any, Dict, Iterable

from openjiuwen.core.common.logging import logger

from .categories import OTHER_CATEGORY, category_by_id, category_ids, normalize_category
from .render import build_section_text

_SAFE_DIR = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


def catalog_dir(store_path: str) -> str:
    """Directory that holds bank.json (``.ttse/``)."""
    path = str(store_path or "").strip()
    directory = os.path.dirname(path)
    return directory if directory else "."


def _safe_category_id(category: str) -> str:
    cid = normalize_category(category)
    if _SAFE_DIR.match(cid):
        return cid
    return OTHER_CATEGORY


def render_catalog_markdown(counts: Dict[str, int], *, categories: Iterable[Dict[str, Any]] | None = None) -> str:
    """Directory listing: preset scenarios with a non-zero count."""
    by_id = category_by_id(categories)
    lines = [
        "# TTSE catalog",
        "",
        "Business-scenario categories with learned FACT/TIP counts.",
        "Call ttse_consult(category=<id>) to open one class.",
        "",
    ]
    any_row = False
    for cid in category_ids(categories):
        n = int(counts.get(cid, 0) or 0)
        if n <= 0:
            continue
        any_row = True
        name = by_id.get(cid, {}).get("name", cid)
        lines.append(f"- `{cid}` — {name} ({n})")
    if not any_row:
        lines.append("(empty)")
    return "\n".join(lines) + "\n"


def project_catalog(store: Any) -> None:
    """Rewrite CATALOG.md / index.json / by_cat/* next to bank.json.

    Best-effort: failures are logged and must not raise into induction.
    """
    store_path = str(getattr(getattr(store, "_config", None), "store_path", "") or "")
    if not store_path:
        return
    root = catalog_dir(store_path)
    try:
        os.makedirs(root, exist_ok=True)
        counts = store.catalog_counts() if hasattr(store, "catalog_counts") else {}
        catalog_md = render_catalog_markdown(counts)
        with open(os.path.join(root, "CATALOG.md"), "w", encoding="utf-8") as f:
            f.write(catalog_md)
        index = {
            "updated_at": os.path.getmtime(store_path) if os.path.exists(store_path) else None,
            "counts": {k: int(v) for k, v in counts.items() if int(v) > 0},
        }
        with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=1)
        by_cat = os.path.join(root, "by_cat")
        os.makedirs(by_cat, exist_ok=True)
        live_dirs = set()
        for cid, n in counts.items():
            if int(n) <= 0:
                continue
            safe = _safe_category_id(cid)
            live_dirs.add(safe)
            cat_dir = os.path.join(by_cat, safe)
            os.makedirs(cat_dir, exist_ok=True)
            facts, tips = store.records_for_category(cid)
            body = build_section_text(facts, tips, retrieved=False)
            with open(os.path.join(cat_dir, "SUMMARY.md"), "w", encoding="utf-8") as f:
                f.write(body or "(empty)\n")
        for name in os.listdir(by_cat):
            path = os.path.join(by_cat, name)
            if os.path.isdir(path) and name not in live_dirs:
                shutil.rmtree(path, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TTSERail] catalog projection failed: %s", exc)


__all__ = ["catalog_dir", "project_catalog", "render_catalog_markdown"]
