# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``PermissionInterruptRail`` ASK 确认界面的文案来源。

三组断言：默认文案与该确认界面历来的输出逐字一致（升级不改变既有部署所见），
``remember_tool`` / ``path_scope`` 除外——那两段措辞照代码改正，另有一条断言给出改正的
依据；宿主经 ``ToolPermissionHost.prompt_texts`` 注入后，标题、摘要与「记住」提示都改用
宿主文案，不残留内置措辞；以及模板校验发生在构造时——宿主写错的模板到不了任何一次工具
调用。

用户可见的文字分处两地：分类标题与摘要来自
``permission_engine.approve.ask_presentation``，「记住」提示由护栏拼出。标题不进正文，
只经 ``_build_interrupt_metadata`` 的 ``ask_title`` 交给宿主，故标题断言走元数据。
"""

from __future__ import annotations

import json
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
from openjiuwen.harness.security.permission_engine.core import build_permission_interrupt_rail
from openjiuwen.harness.security.permission_engine.host import ToolPermissionHost
from openjiuwen.harness.security.permission_engine.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.permission_engine.prompt_texts import (
    _FIELD_PLACEHOLDERS,
    DEFAULT_PERMISSION_PROMPT_TEXTS,
    ENGLISH_PERMISSION_PROMPT_TEXTS,
    PermissionPromptTexts,
)

CJK_RANGE = range(0x4E00, 0xA000)


def _tool_call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(id="call-1", type="function", name=name, arguments=json.dumps(arguments))


def _ask(**kwargs) -> PermissionResult:
    findings = kwargs.pop("findings", None)
    result = PermissionResult(permission=PermissionLevel.ASK, **kwargs)
    if findings is not None:
        # ``PermissionResult`` declares no ``findings`` field; the ``finding`` category is
        # driven by an attribute a producer sets from outside, as it is in
        # ``test_ask_presentation.py``.
        result.findings = findings  # type: ignore[attr-defined]
    return result


def _rail(texts: PermissionPromptTexts | None = None) -> PermissionInterruptRail:
    host = ToolPermissionHost() if texts is None else ToolPermissionHost(prompt_texts=texts)
    return PermissionInterruptRail(config={"enabled": True}, host=host)


def _finding(reason: str, severity: str = "MEDIUM") -> list:
    return [SimpleNamespace(severity=severity, reason=reason)]


# --- one case per ASK category, plus the hint branches ------------------------

_PATH_CALL = ("write_file", {"file_path": "/data/x.txt"})
_PATH_RESULT = {"matched_rule": "file_guard:defaults", "external_paths": ["/data/x.txt"]}
_NETWORK_CALL = ("mcp_fetch_webpage", {"url": "https://evil.test/a"})
_NETWORK_RESULT = {"matched_rule": "tools.mcp_fetch_webpage"}
_FINDING_CALL = ("bash", {"command": "echo hi > out.txt"})
_FINDING_RESULT = {
    "matched_rule": "tiered_policy:defaults.*",
    "findings": _finding("shell_risky_structure"),
}
_SHELL_CALL = ("bash", {"command": "ls -la"})
_SHELL_RESULT = {"matched_rule": "tiered_policy:defaults.*"}
_TOOL_CALL = ("todo_list", {})
_TOOL_RESULT = {"matched_rule": "tiered_policy:defaults.*"}
_GENERIC_CALL = ("noop", {})
_GENERIC_RESULT: dict = {"matched_rule": None}

_CATEGORIES = [
    pytest.param(_PATH_CALL, _PATH_RESULT, "path", id="path"),
    pytest.param(_NETWORK_CALL, _NETWORK_RESULT, "network", id="network"),
    pytest.param(_FINDING_CALL, _FINDING_RESULT, "finding", id="finding"),
    pytest.param(_SHELL_CALL, _SHELL_RESULT, "shell", id="shell"),
    pytest.param(_TOOL_CALL, _TOOL_RESULT, "tool", id="tool"),
    pytest.param(_GENERIC_CALL, _GENERIC_RESULT, "generic", id="generic"),
]


# =============================================================================
# 1. 默认文案与历来输出逐字一致（「记住」提示的措辞除外）
# =============================================================================

def test_default_path_body_scopes_the_remember_hint_to_the_tool() -> None:
    """默认文案下 path 类的完整正文（摘要 + 「记住」提示）。

    摘要逐字不变；「记住」提示的措辞则与历来输出不同：历来写作「``write_file`` 类工具在
    ``/data/x.txt`` 下的调用」，把放行说成限于提示里那个路径。放行并不按路径生效，依据见
    ``test_the_session_remember_is_keyed_on_the_bare_tool_name``，故改写为「所有调用」，
    并让 ``path_scope`` 明说本次的路径不是界限。
    """
    message = _rail()._build_message(_tool_call(*_PATH_CALL), _ask(**_PATH_RESULT))

    assert message == (
        "write /data/x.txt\n\n\n"
        "> 选择「会话内记住」可在本会话内自动放行 ``write_file`` 的所有调用，"
        "而不只是本次针对 ``/data/x.txt`` 的调用；"
        "选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。\n"
    )


def test_the_session_remember_is_keyed_on_the_bare_tool_name() -> None:
    """「会话内记住」按工具名生效，与提示里的路径无关——上一条措辞改正的依据。

    ``_get_auto_confirm_key`` 只为 shell 类工具拼上子命令，其余工具直接返回工具名，
    而会话放行的写入与命中都用这个键。于是在某个路径上按下「会话内记住」，此后同一
    工具在任何路径上的 ASK 都被自动放行。
    """
    rail = _rail()
    asked = rail._get_auto_confirm_key(_tool_call("write_file", {"file_path": "/data/x.txt"}))
    later = rail._get_auto_confirm_key(_tool_call("write_file", {"file_path": "/etc/hosts"}))

    assert asked == "write_file"
    assert later == asked
    # 在 /data/x.txt 上记住，随后 /etc/hosts 的调用命中同一条会话放行。
    assert rail._is_auto_confirmed({asked: True}, later) is True


def test_default_shell_hint_names_the_subcommand() -> None:
    """可归纳为持久化规则的简单命令，提示里给出 ``<工具>:<子命令>`` 形式的键。"""
    message = _rail()._build_message(_tool_call(*_SHELL_CALL), _ask(**_SHELL_RESULT))

    assert message == (
        "bash: ls -la\n\n\n"
        "> 选择「会话内记住」可在本会话内自动放行 ``bash:ls -la`` 类调用；"
        "选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。\n"
    )


def test_default_body_has_no_hint_for_a_compound_command() -> None:
    """结构复杂、无法归纳为规则的命令没有可记住的键，正文止于摘要。"""
    message = _rail()._build_message(
        _tool_call("bash", {"command": "ls -la && rm -rf /tmp/x"}), _ask(**_SHELL_RESULT)
    )

    assert message == "bash: ls -la && rm -rf /tmp/x\n"


def test_default_tool_summary_keeps_the_historical_parenthetical() -> None:
    """``tool`` 类摘要是唯一带模板的摘要，其默认值应逐字不变。"""
    message = _rail()._build_message(_tool_call(*_TOOL_CALL), _ask(**_TOOL_RESULT))

    assert message.startswith("todo_list（当前模式默认需确认）\n")


@pytest.mark.parametrize(
    ("reason", "expected_label"),
    [
        pytest.param("download_and_execute", "下载并执行", id="download_and_execute"),
        pytest.param("dynamic_or_encoded_execution", "动态或编码执行", id="dynamic_or_encoded"),
        pytest.param("shell_risky_structure", "含重定向或命令替换等结构", id="risky_structure"),
        pytest.param("shell_too_complex", "命令结构过复杂", id="too_complex"),
        pytest.param("brand_new_reason", "风险命令行为", id="unmapped_reason_falls_back"),
    ],
)
def test_default_finding_labels_are_unchanged(reason: str, expected_label: str) -> None:
    """五个风险名称（四个已知 + 兜底）的默认值应逐字不变。"""
    message = _rail()._build_message(
        _tool_call("bash", {"command": "echo hi > out.txt"}),
        _ask(matched_rule="tiered_policy:defaults.*", findings=_finding(reason, "HIGH")),
    )

    assert message.startswith(f"{expected_label}: echo hi > out.txt")


@pytest.mark.parametrize(
    ("call", "result_kwargs", "category", "expected_title"),
    [
        pytest.param(_PATH_CALL, _PATH_RESULT, "path", "检测到受保护的文件路径访问", id="path"),
        pytest.param(_NETWORK_CALL, _NETWORK_RESULT, "network", "检测到需确认的网络访问", id="network"),
        pytest.param(_FINDING_CALL, _FINDING_RESULT, "finding", "检测到风险命令结构", id="finding"),
        pytest.param(_SHELL_CALL, _SHELL_RESULT, "shell", "检测到需确认的命令执行", id="shell"),
        pytest.param(_TOOL_CALL, _TOOL_RESULT, "tool", "工具需要授权后才能使用", id="tool"),
        pytest.param(_GENERIC_CALL, _GENERIC_RESULT, "generic", "操作需要授权", id="generic"),
    ],
)
def test_default_titles_are_unchanged(
    call: tuple, result_kwargs: dict, category: str, expected_title: str
) -> None:
    """六个分类标题的默认值应逐字不变；标题只经中断元数据交给宿主。"""
    metadata = _rail()._build_interrupt_metadata(_tool_call(*call), _ask(**result_kwargs))

    assert metadata["ask_category"] == category
    assert metadata["ask_title"] == expected_title


# =============================================================================
# 2. 宿主注入后每一段都改用宿主文案
# =============================================================================

def _sentinel_texts() -> PermissionPromptTexts:
    return PermissionPromptTexts(
        title_path="TITLE-PATH",
        title_network="TITLE-NETWORK",
        title_finding="TITLE-FINDING",
        title_shell="TITLE-SHELL",
        title_tool="TITLE-TOOL",
        title_generic="TITLE-GENERIC",
        summary_tool="SUMMARY-TOOL {tool_name}",
        finding_download_and_execute="FINDING-DOWNLOAD",
        finding_dynamic_or_encoded_execution="FINDING-DYNAMIC",
        finding_shell_risky_structure="FINDING-RISKY",
        finding_shell_too_complex="FINDING-COMPLEX",
        finding_other="FINDING-OTHER",
        remember_command="\n\nREMEMBER-COMMAND {command_key}",
        remember_command_session_only="\n\nREMEMBER-COMMAND-SESSION {command_key}",
        remember_tool="\n\nREMEMBER-TOOL {tool_name}{path_scope}",
        path_scope=" SCOPE {path_hint}",
    )


@pytest.mark.parametrize(("call", "result_kwargs", "category"), _CATEGORIES)
def test_host_titles_replace_the_builtin_titles(
    call: tuple, result_kwargs: dict, category: str
) -> None:
    """六个分类的标题都取自宿主，元数据里不残留内置措辞。"""
    metadata = _rail(_sentinel_texts())._build_interrupt_metadata(
        _tool_call(*call), _ask(**result_kwargs)
    )

    assert metadata["ask_title"] == f"TITLE-{category.upper()}"
    assert not any(ord(ch) in CJK_RANGE for ch in metadata["ask_title"])


@pytest.mark.parametrize(
    ("reason", "expected_label"),
    [
        pytest.param("download_and_execute", "FINDING-DOWNLOAD", id="download_and_execute"),
        pytest.param("dynamic_or_encoded_execution", "FINDING-DYNAMIC", id="dynamic_or_encoded"),
        pytest.param("shell_risky_structure", "FINDING-RISKY", id="risky_structure"),
        pytest.param("shell_too_complex", "FINDING-COMPLEX", id="too_complex"),
        pytest.param("brand_new_reason", "FINDING-OTHER", id="unmapped_reason_falls_back"),
    ],
)
def test_host_finding_labels_replace_the_builtin_labels(
    reason: str, expected_label: str
) -> None:
    """五个风险名称都取自宿主，含未登记原因的兜底。"""
    message = _rail(_sentinel_texts())._build_message(
        _tool_call("bash", {"command": "echo hi > out.txt"}),
        _ask(matched_rule="tiered_policy:defaults.*", findings=_finding(reason, "HIGH")),
    )

    assert message.startswith(f"{expected_label}: echo hi > out.txt")
    assert not any(ord(ch) in CJK_RANGE for ch in message)


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "expected_tail"),
    [
        pytest.param("write_file", {"file_path": "/data/x.txt"},
                     "REMEMBER-TOOL write_file SCOPE /data/x.txt", id="path_tool"),
        pytest.param("web_search", {"query": "openjiuwen"},
                     "REMEMBER-TOOL web_search", id="tool_without_path"),
        pytest.param("bash", {"command": "ls -la"},
                     "REMEMBER-COMMAND bash:ls -la", id="simple_command"),
    ],
)
def test_host_texts_replace_the_remember_hint(
    tool_name: str, tool_args: dict, expected_tail: str
) -> None:
    """三条可达的「记住」提示都取自宿主，正文不残留中文内置措辞。"""
    message = _rail(_sentinel_texts())._build_message(
        _tool_call(tool_name, tool_args), _ask(matched_rule="tools.any"),
    )

    assert message.rstrip("\n").endswith(expected_tail)
    assert not any(ord(ch) in CJK_RANGE for ch in message)


def test_host_summary_replaces_the_builtin_parenthetical() -> None:
    """``tool`` 类摘要取自宿主模板。"""
    message = _rail(_sentinel_texts())._build_message(
        _tool_call(*_TOOL_CALL), _ask(**_TOOL_RESULT)
    )

    assert message.startswith("SUMMARY-TOOL todo_list\n")
    assert not any(ord(ch) in CJK_RANGE for ch in message)


def test_factory_carries_host_texts_into_the_rail() -> None:
    """``build_permission_interrupt_rail`` 应把宿主文案透传给护栏。"""
    rail = build_permission_interrupt_rail(
        permissions={"enabled": True},
        host=ToolPermissionHost(prompt_texts=_sentinel_texts()),
    )

    assert rail is not None
    metadata = rail._build_interrupt_metadata(_tool_call(*_TOOL_CALL), _ask(**_TOOL_RESULT))
    assert metadata["ask_title"] == "TITLE-TOOL"


def test_default_host_texts_are_the_builtin_defaults() -> None:
    """未注入时 ``ToolPermissionHost`` 携带内置默认文案。"""
    assert ToolPermissionHost().prompt_texts == PermissionPromptTexts()
    assert DEFAULT_PERMISSION_PROMPT_TEXTS == PermissionPromptTexts()


@pytest.mark.parametrize(("call", "result_kwargs", "category"), _CATEGORIES)
def test_english_texts_render_without_any_chinese(
    call: tuple, result_kwargs: dict, category: str
) -> None:
    """传入随附的英文文案后，任一分类的正文与元数据都不含中文。"""
    rail = _rail(ENGLISH_PERMISSION_PROMPT_TEXTS)
    tool_call = _tool_call(*call)
    result = _ask(**result_kwargs)

    message = rail._build_message(tool_call, result)
    metadata = rail._build_interrupt_metadata(tool_call, result)

    assert not any(ord(ch) in CJK_RANGE for ch in message)
    assert not any(ord(ch) in CJK_RANGE for ch in metadata["ask_title"])
    assert not any(ord(ch) in CJK_RANGE for ch in metadata["ask_summary"])


def test_english_texts_are_not_the_default() -> None:
    """英文文案只是随附数据；不注入时不会自动生效。"""
    assert ToolPermissionHost().prompt_texts != ENGLISH_PERMISSION_PROMPT_TEXTS


def test_a_valid_partial_override_still_renders() -> None:
    """合法的部分覆盖照旧渲染，未覆盖的字段仍取内置默认值。"""
    rail = _rail(PermissionPromptTexts(title_tool="Tool needs your approval"))
    tool_call = _tool_call(*_TOOL_CALL)
    result = _ask(**_TOOL_RESULT)

    assert rail._build_interrupt_metadata(tool_call, result)["ask_title"] == (
        "Tool needs your approval"
    )
    assert rail._build_message(tool_call, result).startswith("todo_list（当前模式默认需确认）")


# =============================================================================
# 3. 模板校验发生在构造时
# =============================================================================

def test_every_field_declares_its_own_placeholders() -> None:
    """占位符表与字段一一对应：新增字段若漏登记，构造默认实例即报错，不会漏过校验。"""
    assert set(_FIELD_PLACEHOLDERS) == {spec.name for spec in fields(PermissionPromptTexts)}


@pytest.mark.parametrize(
    ("field_name", "template", "bad_placeholder"),
    [
        pytest.param("summary_tool", "{tool} needs approval", "tool", id="misspelt"),
        pytest.param("title_tool", "{tool_name} needs approval", "tool_name",
                     id="placeholder_in_a_field_that_takes_none"),
        pytest.param("remember_tool", "> {tool_name} under {path_hint}", "path_hint",
                     id="borrowed_from_path_scope"),
        pytest.param("path_scope", " under {tool_name}", "tool_name",
                     id="borrowed_from_remember_tool"),
    ],
)
def test_an_unknown_placeholder_raises_at_construction(
    field_name: str, template: str, bad_placeholder: str
) -> None:
    """宿主写错占位符在构造处就报错，且报错点名字段与那个占位符。

    每个字段只用自己那一份占位符校验，所以「别的字段有、本字段没有」同样算写错——
    取并集会放过 ``title_tool`` 里的 ``{tool_name}``，而渲染标题时调用方一个占位符
    都不传。
    """
    with pytest.raises(BaseError) as excinfo:
        PermissionPromptTexts(**{field_name: template})

    assert excinfo.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    assert f"PermissionPromptTexts.{field_name}" in str(excinfo.value)
    assert f"{{{bad_placeholder}}}" in str(excinfo.value)


@pytest.mark.parametrize(
    ("field_name", "template"),
    [
        pytest.param("summary_tool", "{tool_name", id="unclosed_brace"),
        pytest.param("path_scope", " under {}", id="positional_placeholder"),
        pytest.param("title_path", "50{}% risk", id="positional_in_a_field_that_takes_none"),
        pytest.param("remember_command", "key {command_key:d}", id="bad_format_spec"),
    ],
)
def test_a_template_that_cannot_render_raises_at_construction(
    field_name: str, template: str
) -> None:
    """占位符拼写之外，格式串本身不合法也在构造处拦下，报错点名字段。"""
    with pytest.raises(BaseError) as excinfo:
        PermissionPromptTexts(**{field_name: template})

    assert excinfo.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    assert f"PermissionPromptTexts.{field_name}" in str(excinfo.value)


def test_a_non_string_template_raises_at_construction() -> None:
    """字段不是 ``str`` 时同样在构造处报错，而不是渲染时抛 ``AttributeError``。"""
    with pytest.raises(BaseError) as excinfo:
        PermissionPromptTexts(title_tool=None)  # type: ignore[arg-type]

    assert excinfo.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    assert "PermissionPromptTexts.title_tool" in str(excinfo.value)


@pytest.mark.parametrize(
    "texts",
    [
        pytest.param(PermissionPromptTexts(), id="builtin_defaults"),
        pytest.param(ENGLISH_PERMISSION_PROMPT_TEXTS, id="bundled_english"),
    ],
)
def test_shipped_texts_pass_their_own_validation(texts: PermissionPromptTexts) -> None:
    """随本仓库发布的两份文案自身通过校验——逐字段再各渲染一次，与构造时同一条路径。"""
    for spec in fields(texts):
        placeholders = _FIELD_PLACEHOLDERS[spec.name]
        getattr(texts, spec.name).format(**{name: "" for name in placeholders})


def test_a_broken_override_never_reaches_a_rail() -> None:
    """错模板到不了工具调用：文案对象都构造不出来，护栏无从携带它。

    这条是本文件里唯一有安全含义的断言。护栏的 ``before_tool_call`` 只有抛
    ``AbortError`` 或标记跳过才拦得住工具调用；渲染时抛出的其它异常被回调框架逐个
    吞掉、链继续，那一次调用于是没有任何权限判定就执行。把校验放到构造处，宿主的
    模板错误便成了集成期的启动失败。
    """
    with pytest.raises(BaseError):
        ToolPermissionHost(prompt_texts=PermissionPromptTexts(summary_tool="{tool}"))

    # ``dataclasses.replace`` 也走 ``__init__``，改坏既有实例同样在这里挡下。
    with pytest.raises(BaseError):
        replace(PermissionPromptTexts(), summary_tool="{tool}")
