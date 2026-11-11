# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the canonical PII masking set (common/security/pii_mask.py)."""

from openjiuwen.core.common.security.pii_mask import (
    luhn_ok,
    mask_pii_text,
    valid_id15_date,
)

MASK = "******"


def test_mobile_masked() -> None:
    assert mask_pii_text("手机号 13800138000") == f"手机号 {MASK}"


def test_email_masked() -> None:
    assert mask_pii_text("邮箱 a.b@example.com") == f"邮箱 {MASK}"


def test_id18_masked() -> None:
    assert mask_pii_text("11010519491231002X") == MASK


def test_id15_valid_date_masked() -> None:
    assert mask_pii_text("证件号 110105491231002") == f"证件号 {MASK}"


def test_id15_invalid_date_kept() -> None:
    assert mask_pii_text("流水号 123456789912345") == "流水号 123456789912345"
    assert valid_id15_date("110105491231002")
    assert not valid_id15_date("123456789912345")


def test_bank_card_luhn() -> None:
    assert luhn_ok("4242424242424242")
    assert not luhn_ok("4242424242424241")
    assert mask_pii_text("卡号 4242424242424242") == f"卡号 {MASK}"
    assert mask_pii_text("单号 4242424242424241") == "单号 4242424242424241"


def test_anchored_account_masked() -> None:
    assert mask_pii_text("联系QQ：12345678") == f"联系QQ{MASK}"
    assert mask_pii_text("订单号 12345678") == "订单号 12345678"


def test_path_username_masked_with_whitelist() -> None:
    assert mask_pii_text(r"C:\Users\Lenovo\a.txt") == r"C:\Users\***\a.txt"
    assert mask_pii_text(r"C:\Users\Public\a.txt") == r"C:\Users\Public\a.txt"
    assert mask_pii_text("/home/zhangsan/x.log") == "/home/***/x.log"


def test_path_masking_can_be_disabled() -> None:
    assert mask_pii_text(r"C:\Users\Lenovo\a.txt", mask_paths=False) == r"C:\Users\Lenovo\a.txt"


def test_address_masked_and_fp_controls() -> None:
    result = mask_pii_text("家住广东省深圳市南山区科技路100号")
    assert "科技路" not in result
    assert mask_pii_text("北京市朝阳区建国路88号") == MASK
    assert mask_pii_text("我在市区路上买了100号电池") == "我在市区路上买了100号电池"


def test_empty_and_idempotent() -> None:
    assert mask_pii_text("") == ""
    once = mask_pii_text("手机号 13800138000")
    assert mask_pii_text(once) == once


def test_pii_preview_five_char_exposure() -> None:
    from openjiuwen.core.common.security.pii_mask import pii_preview

    out = pii_preview("我的手机号是13800138000，地址在南山")
    # 脱敏后只露 5 字符 + 长度；PII 不明文
    assert "13800138000" not in out
    assert out == "我的手机号…(len=23)"


def test_pii_preview_short_value_passthrough() -> None:
    from openjiuwen.core.common.security.pii_mask import pii_preview

    assert pii_preview("ok") == "ok"
    assert pii_preview("") == ""
    assert pii_preview(None) == ""


def test_pii_preview_masks_before_truncate() -> None:
    from openjiuwen.core.common.security.pii_mask import pii_preview

    # 手机号在前 5 字符内：先掩后截。断言精确掩码形态——裸截断会得到
    # "13800…"，只有脱敏后截断才是 "*****"。
    out = pii_preview("13800138000 请联系")
    assert out == "*****…(len=15)"


def test_pii_preview_bounded_window_keeps_boundary_mask() -> None:
    from openjiuwen.core.common.security.pii_mask import pii_preview

    # 手机号起始在暴露窗口内、延伸到窗口外：先截后掩会漏，限窗掩码必须盖住。
    # 断言精确掩码前缀（未脱敏时首 5 字符是 "我的138"，两种实现才可区分）。
    out = pii_preview("我的13800138000请联系" + "x" * 10000)
    assert out == "我的***…(len=10016)"


def test_pii_preview_long_text_output_bounded() -> None:
    """限窗掩码的输出长度与输入无关（恒成本的功能面钉案；耗时只作人工观察）。"""
    from openjiuwen.core.common.security.pii_mask import pii_preview

    long_text = "常规内容12345" + "y" * 200_000
    out = pii_preview(long_text)
    assert len(out) < 40
    assert "len=200009" in out


def test_win_path_cjk_prefix_masked() -> None:
    """检视增补：盘符前紧邻汉字（\\b 不匹配）也要命中。"""
    assert mask_pii_text(r"读取C:\Users\Lenovo\x 完成") == r"读取C:\Users\***\x 完成"
