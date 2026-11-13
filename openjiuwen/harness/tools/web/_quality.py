# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Conservative content-failure signals, distinct from HTTP status."""

from __future__ import annotations

import re


class WebContentError(ValueError):
    """A readable tool failure with a machine-readable content status."""

    def __init__(self, status: str, reason: str):
        self.status = status
        super().__init__(f"Content status: {status}. {reason}")


def challenge_reason(text: str, *, title: str = "", status: int = 200) -> str | None:
    """Recognize challenge responses, not articles merely discussing CAPTCHA."""
    if status in {401, 403, 407, 429, 499}:
        return f"HTTP {status}: access, authentication, or rate-limit restriction."
    normalized_title = title.strip().casefold()
    if normalized_title in {"just a moment...", "access denied", "attention required! | cloudflare", "百度安全验证", "安全验证"}:
        return "The site returned an access-verification page."
    sample = text[:100_000]
    if re.match(r'[\ufeff\s]*\{\s*["\']_waf_', sample, re.IGNORECASE):
        return "The site returned a WAF challenge payload."
    if "SourceVerifyCode" in sample or "anomaly.js" in sample:
        return "The search engine requires human verification."
    if re.search(r"(?im)^Warning:.*(?:CAPTCHA|returned error (?:401|403|429|499))", sample):
        return "The reader reported a challenge or access restriction at the source."
    if "captcha-delivery.com" in sample and "Please enable JS and disable any ad blocker" in sample:
        return "The site returned a CAPTCHA challenge."
    return None
