# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""内置工具权限确认提示的文案模板。

``PermissionLevel.ASK`` 走内置确认时，用户看到的文字来自两处：
:func:`~openjiuwen.harness.security.permission_engine.approve.ask_presentation.build_permission_ask_presentation`
给出的分类标题与摘要，以及
:class:`~openjiuwen.harness.rails.security.tool_security_rail.PermissionInterruptRail`
拼出的「记住」提示。两处都从本模块的模板取词，模板由宿主经
:attr:`~openjiuwen.harness.security.permission_engine.host.ToolPermissionHost.prompt_texts`
注入：openjiuwen 是被嵌入的库，无从得知终端用户使用哪种语言，因此不替宿主挑选语言。

除 :attr:`PermissionPromptTexts.remember_tool` 与 :attr:`PermissionPromptTexts.path_scope`
外，各字段默认值与这两处历来输出逐字一致，未注入 ``prompt_texts`` 的宿主行为不变。那两个
字段的措辞是照代码改正过的：历来的写法称放行限于本次的路径，而放行实际以工具为单位，
详见这两个字段的说明。

每个字段都是 :meth:`str.format` 模板，占位符见各字段说明；模板内的字面花括号需写成
``{{`` / ``}}``。构造时按 :data:`_FIELD_PLACEHOLDERS` 逐字段试渲染一次：拼错占位符、
用了别的字段才有的占位符、写错格式串、字段不是 ``str``，都在这里抛
``DEEPAGENT_CONFIG_PARAM_ERROR``，并指出是哪个字段、错在哪里。抛错点在宿主构造文案的
地方，即集成阶段，而不是某次 ASK 渲染时——护栏的 ``before_tool_call`` 只有抛
``AbortError`` 或标记跳过才能拦下工具调用，其余异常被回调框架逐个吞掉并继续，渲染期
抛错等于这次调用没有任何权限判定就直接放行。
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

_FIELD_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "title_path": (),
    "title_network": (),
    "title_finding": (),
    "title_shell": (),
    "title_tool": (),
    "title_generic": (),
    "summary_tool": ("tool_name",),
    "finding_download_and_execute": (),
    "finding_dynamic_or_encoded_execution": (),
    "finding_shell_risky_structure": (),
    "finding_shell_too_complex": (),
    "finding_other": (),
    "remember_command": ("command_key",),
    "remember_command_session_only": ("command_key",),
    "remember_tool": ("tool_name", "path_scope"),
    "path_scope": ("path_hint",),
}
"""每个字段各自允许的占位符——各不相同，故逐字段校验，不取并集。

取并集会让 ``title_path="...{tool_name}..."`` 这类真错误蒙混过关：渲染标题时调用方
一个占位符都不传。键集与 :class:`PermissionPromptTexts` 的字段一一对应，缺项会在本模块
导入时构造默认实例就抛 :exc:`KeyError`。
"""


def _allowed_text(placeholders: tuple[str, ...]) -> str:
    if not placeholders:
        return "this field takes no placeholders"
    return "allowed placeholders: " + ", ".join(f"{{{name}}}" for name in placeholders)


@dataclass(frozen=True)
class PermissionPromptTexts:
    """内置 ASK 确认界面的文案模板集合。"""

    title_path: str = "检测到受保护的文件路径访问"
    """``path`` 类确认的标题；无占位符。工具访问了受 file_guard 保护的路径时使用。"""

    title_network: str = "检测到需确认的网络访问"
    """``network`` 类确认的标题；无占位符。"""

    title_finding: str = "检测到风险命令结构"
    """``finding`` 类确认的标题；无占位符。命中 MEDIUM 及以上的命令结构风险时使用。"""

    title_shell: str = "检测到需确认的命令执行"
    """``shell`` 类确认的标题；无占位符。"""

    title_tool: str = "工具需要授权后才能使用"
    """``tool`` 类确认的标题；无占位符。命中权限规则、但不属于以上各类时使用。"""

    title_generic: str = "操作需要授权"
    """``generic`` 类确认的标题；无占位符。未命中任何规则时的兜底。"""

    summary_tool: str = "{tool_name}（当前模式默认需确认）"
    """``tool`` 类确认的摘要；占位符 ``{tool_name}``。其余各类的摘要是路径、URL 或命令
    原文，不经模板。"""

    finding_download_and_execute: str = "下载并执行"
    """``download_and_execute`` 风险的名称；无占位符。作为 ``finding`` 类摘要的前缀。"""

    finding_dynamic_or_encoded_execution: str = "动态或编码执行"
    """``dynamic_or_encoded_execution`` 风险的名称；无占位符。"""

    finding_shell_risky_structure: str = "含重定向或命令替换等结构"
    """``shell_risky_structure`` 风险的名称；无占位符。"""

    finding_shell_too_complex: str = "命令结构过复杂"
    """``shell_too_complex`` 风险的名称；无占位符。"""

    finding_other: str = "风险命令行为"
    """其余风险的名称；无占位符。风险原因不在以上四项时使用。"""

    remember_command: str = (
        "\n\n> 选择「会话内记住」可在本会话内自动放行 ``{command_key}`` 类调用；"
        "选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。"
    )
    """shell 类工具的「记住」提示；占位符 ``{command_key}``。子命令可作为持久化规则时使用。"""

    remember_command_session_only: str = (
        "\n\n> 选择「会话内记住」可在本会话内自动放行 ``{command_key}`` 类调用。"
    )
    """shell 类工具的「记住」提示；占位符 ``{command_key}``。命令结构过于复杂、无法写成持久化规则时使用。"""

    remember_tool: str = (
        "\n\n> 选择「会话内记住」可在本会话内自动放行 ``{tool_name}`` 的所有调用{path_scope}；"
        "选择「永久记住」可将此规则写回磁盘，所有会话均自动放行。"
    )
    """其余工具的「记住」提示；占位符 ``{tool_name}``、``{path_scope}``。

    措辞是「``{tool_name}`` 的所有调用」而不是某个路径下的调用：放行以工具为单位，
    与本次调用的路径无关，理由见 :attr:`path_scope`。
    """

    path_scope: str = "，而不只是本次针对 ``{path_hint}`` 的调用"
    """``remember_tool`` 末尾附加的片段；占位符 ``{path_hint}``。工具入参无路径时该片段为空串。

    该片段点出本次请求涉及的路径，并说明放行**不**以它为界；覆盖本字段的宿主同样不应
    写成限定路径的措辞，因为两种「记住」都不按路径生效：

    - 会话内记住的键来自 ``PermissionInterruptRail._get_auto_confirm_key``，它只为
      shell 类工具拼上子命令，其余工具直接返回工具名，写进会话的因此就是工具名本身，
      此后该工具的每一次 ASK 都命中它；
    - 永久记住经 ``merge_permission_allow_rule_into_permissions`` 落盘，它先滤掉
      ``match_type`` 为 ``path`` 的建议（路径只写 file_guard），非 shell 工具再无其它
      建议时写的是 ``permissions.tools[<工具名>] = "allow"``，同样是整个工具。
    """

    def __post_init__(self) -> None:
        """逐字段试渲染一次，把宿主的模板错误挡在构造处。"""
        for spec in fields(self):
            placeholders = _FIELD_PLACEHOLDERS[spec.name]
            template = getattr(self, spec.name)
            if not isinstance(template, str):
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg=(
                        f"PermissionPromptTexts.{spec.name} must be a str.format template, "
                        f"got {type(template).__name__}"
                    ),
                )
            try:
                template.format(**{name: "" for name in placeholders})
            except KeyError as exc:
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg=(
                        f"PermissionPromptTexts.{spec.name} uses unknown placeholder "
                        f"{{{exc.args[0]}}}; {_allowed_text(placeholders)}"
                    ),
                    cause=exc,
                ) from exc
            except (IndexError, ValueError) as exc:
                raise build_error(
                    StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                    error_msg=(
                        f"PermissionPromptTexts.{spec.name} is not a valid str.format "
                        f"template ({type(exc).__name__}: {exc}); "
                        f"{_allowed_text(placeholders)}. A literal brace must be doubled "
                        f"as {{{{ or }}}}"
                    ),
                    cause=exc,
                ) from exc


DEFAULT_PERMISSION_PROMPT_TEXTS = PermissionPromptTexts()
"""内置默认文案；``ToolPermissionHost`` 未注入时以及调用方不传 ``texts`` 时使用。"""


ENGLISH_PERMISSION_PROMPT_TEXTS = PermissionPromptTexts(
    title_path="Protected file path access detected",
    title_network="Network access needing confirmation detected",
    title_finding="Risky command structure detected",
    title_shell="Command execution needing confirmation detected",
    title_tool="This tool needs approval before it can be used",
    title_generic="This operation needs approval",
    summary_tool="{tool_name} (needs confirmation by default in the current mode)",
    finding_download_and_execute="downloads and executes",
    finding_dynamic_or_encoded_execution="dynamic or encoded execution",
    finding_shell_risky_structure="contains redirection, command substitution or similar",
    finding_shell_too_complex="command structure is too complex",
    finding_other="risky command behaviour",
    remember_command=(
        "\n\n> Remembering this for the session allows ``{command_key}`` calls "
        "automatically until the session ends; remembering it permanently writes "
        "the rule to disk, so every session allows them automatically."
    ),
    remember_command_session_only=(
        "\n\n> Remembering this for the session allows ``{command_key}`` calls "
        "automatically until the session ends."
    ),
    remember_tool=(
        "\n\n> Remembering this for the session allows every ``{tool_name}`` call "
        "automatically until the session ends{path_scope}; remembering it permanently "
        "writes the rule to disk, so every session allows them automatically."
    ),
    path_scope=", not only this one on ``{path_hint}``",
)
"""与默认中文文案对应的英文文案，供英文部署直接传给 ``ToolPermissionHost``。

仅是一份现成的数据，不引入语言选择：仍由宿主决定用哪一份。「记住」提示的措辞刻意描述
**选项的效果**而不引用选项名，因为按钮文案由宿主自己渲染，护栏无从得知它们叫什么。
"""


__all__ = [
    "DEFAULT_PERMISSION_PROMPT_TEXTS",
    "ENGLISH_PERMISSION_PROMPT_TEXTS",
    "PermissionPromptTexts",
]
