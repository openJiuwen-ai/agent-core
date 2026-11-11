# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PII pattern masking for log text.

Canonical home of the patterned-PII masking rules shared by every layer that
emits log text: agent-core call sites (e.g. ``llm/payload_summary``) and the
jiuwenswarm host pipeline (``jiuwenswarm/common/utils.py:_sanitize_log_text``
delegates its PII portion here). One implementation, no per-repo mirrors.

Covered: mainland-CN mobile, 18/15-digit ID cards (15-digit with a YYMMDD
sanity check), bank cards (16-19 digits with Luhn), email, anchored number
accounts (QQ/微信/... — keyword-anchored to avoid masking plain numbers),
path usernames (``C:\\Users\\<name>`` / ``/home/<name>``, system dirs
whitelisted), and anchored addresses (province/city-district chain).
"""

from __future__ import annotations

import re
from typing import Any

PII_MASK = "******"

_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9])")
_MOBILE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_ID_CARD_18_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_ID_CARD_15_RE = re.compile(r"(?<!\d)(\d{15})(?!\d)")
_BANK_CARD_RE = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")
_ANCHORED_ACCOUNT_RE = re.compile(
    r"(?i)(QQ|微信|wechat|旺旺|账号|账户)\s*[:：]?\s*(\d{5,11})(?!\d)"
)
_ADDRESS_RE = re.compile(
    # 两级行政锚定：省/自治区 起，或 市+区/县 组合（单"市区"口语词汇不匹配——
    # 市分支强制要求 市 与 区/县 之间至少一个字符，杀"我在市区路上"类误报）。
    r"(?:[一-龥]{2,6}(?:省|自治区)[一-龥]{1,8}(?:市|区|县)?"
    r"|[一-龥]{2,6}市[一-龥]{1,8}(?:区|县))"
    r"[一-龥]{0,10}(?:路|街|巷|弄|大道|大街)[一-龥A-Za-z0-9]{0,10}?号"
)

_WIN_USER_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])([A-Za-z]:[\\/]Users[\\/])([^\\/\s]+)")
_LINUX_USER_PATH_RE = re.compile(r"(/home/)([^/\s]+)")
_USER_PATH_WHITELIST = frozenset({"public", "default", "all users", "公用", "defaultuser0"})

# 快速预检：PII 各模式都必须命中下列字符之一——数字（手机/
# 身份证/银行卡/锚定账号的值部分）、@（邮箱）、路径分隔符（路径用户名）、
# 地址锚定用字。一个都不含时所有模式必然不匹配，直接原样返回。
# 预检只许"假阳性→走全扫"，不许"假阴性→漏掩"。
_PII_TRIGGER_RE = re.compile(r"[\d@/\\省市区县路街巷弄道号]")


def luhn_ok(digits: str) -> bool:
    """Luhn check: bank-card signature. Most non-card 16-19 digit runs fail."""
    total = 0
    for index, ch in enumerate(reversed(digits)):
        digit = ord(ch) - 48
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def valid_id15_date(digits: str) -> bool:
    """YYMMDD sanity check for 15-digit legacy ID cards."""
    month = int(digits[8:10])
    day = int(digits[10:12])
    return 1 <= month <= 12 and 1 <= day <= 31


def _mask_user_path(match: re.Match[str]) -> str:
    if match.group(2).lower() in _USER_PATH_WHITELIST:
        return match.group(0)
    return f"{match.group(1)}***"


def mask_pii_text(text: str, *, mask_paths: bool = True) -> str:
    """Mask patterned PII in log text; returns the masked string.

    ``mask_paths=False`` skips the path-username rules (for callers that
    need real paths in diagnostics).
    """
    if not text:
        return text
    if not _PII_TRIGGER_RE.search(text):
        return text
    masked = text
    if mask_paths:
        masked = _WIN_USER_PATH_RE.sub(_mask_user_path, masked)
        masked = _LINUX_USER_PATH_RE.sub(_mask_user_path, masked)
    masked = _EMAIL_RE.sub(PII_MASK, masked)
    masked = _MOBILE_RE.sub(PII_MASK, masked)
    # 18 位先于 15 位/银行卡：先吃掉 18 位身份证，避免被 15 位/16-19 位规则截胡。
    masked = _ID_CARD_18_RE.sub(PII_MASK, masked)
    masked = _ANCHORED_ACCOUNT_RE.sub(lambda m: f"{m.group(1)}{PII_MASK}", masked)
    masked = _ID_CARD_15_RE.sub(
        lambda m: PII_MASK if valid_id15_date(m.group(1)) else m.group(0), masked
    )
    masked = _BANK_CARD_RE.sub(
        lambda m: PII_MASK if luhn_ok(m.group(1)) else m.group(0), masked
    )
    masked = _ADDRESS_RE.sub(PII_MASK, masked)
    return masked


# 掩码窗口冗余
_PREVIEW_MASK_EXTRA = 64


def pii_preview(value: Any, *, expose: int = 5) -> str:
    """Minimal-exposure preview: pattern-mask, keep only ``expose`` leading
    chars plus the length. For log lines that need to show *that* a value
    exists without carrying its content。
    """
    text = "" if value is None else str(value)
    if not text:
        return ""
    masked = mask_pii_text(text[: expose + _PREVIEW_MASK_EXTRA])
    if len(masked) <= expose:
        return masked
    return f"{masked[:expose]}…(len={len(text)})"


__all__ = ["PII_MASK", "luhn_ok", "mask_pii_text", "pii_preview", "valid_id15_date"]
