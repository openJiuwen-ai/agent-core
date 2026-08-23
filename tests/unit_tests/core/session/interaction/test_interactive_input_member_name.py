from openjiuwen.core.session.interaction.interactive_input import InteractiveInput


def test_member_name_default_none() -> None:
    ii = InteractiveInput()
    assert ii.member_name is None


def test_member_name_assignable_after_construction() -> None:
    """自定义 __init__ 不吃 kwargs —— 构造后赋值（不改 __init__ 签名）。"""
    ii = InteractiveInput()
    ii.member_name = "teammate-1"
    assert ii.member_name == "teammate-1"


def test_member_name_backward_compat_existing_construction() -> None:
    """既有 raw_inputs 构造路径不破。"""
    ii = InteractiveInput(raw_inputs={"x": 1})
    assert ii.member_name is None
    ii.member_name = "m"
    assert ii.member_name == "m"
