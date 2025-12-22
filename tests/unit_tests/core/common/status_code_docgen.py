import re
from collections import defaultdict
from enum import Enum
from typing import (
    Dict,
    List,
    Type,
)

# statuscode_ranges.py

STATUS_CODE_RANGES = [
    (0, 0, "通用"),
    (100000, 109999, "Workflow Components"),
    (110000, 119999, "Workflow"),
    (120000, 129999, "Agent Orchestration"),
    (130000, 139999, "Multi-Agent"),
    (140000, 149999, "GraphEngine"),
    (150000, 159999, "ContextEngine"),
    (160000, 169999, "Development Toolchain"),
    (170000, 179999, "Optimization Toolchain"),
    (180000, 189999, "Common Capabilities"),
    (190000, 199999, "Session"),
]

FAILURE_CN_MAP = {
    "INVALID": "非法",
    "NOT_FOUND": "未找到",
    "NOT_SUPPORTED": "不支持",
    "CONFIG_ERROR": "配置错误",
    "PARAM_ERROR": "参数错误",
    "INIT_FAILED": "初始化失败",
    "CALL_FAILED": "调用失败",
    "EXECUTION_ERROR": "执行异常",
    "RUNTIME_ERROR": "运行时异常",
    "PROCESS_ERROR": "处理异常",
    "TIMEOUT": "超时",
    "INTERRUPTED": "被中断",
}


def extract_placeholders(msg: str) -> List[str]:
    return re.findall(r"\{(\w+)}", msg)


def split_failure(name: str) -> str:
    for k in FAILURE_CN_MAP:
        if name.endswith(k):
            return FAILURE_CN_MAP[k]
    return "异常"


def cn_description(name: str) -> str:
    subject = name.replace("_", " ").lower()
    return f"{subject} {split_failure(name)}"


def locate_range(code: int) -> str:
    for start, end, title in STATUS_CODE_RANGES:
        if start <= code <= end:
            return title
    return "Uncategorized"


def generate_markdown(enum_cls: Type[Enum]) -> str:
    grouped: Dict[str, List[Enum]] = defaultdict(list)

    for item in enum_cls:
        code = item.value[0]
        section = locate_range(code)
        grouped[section].append(item)

    md = []
    md.append("# 错误码文档\n")
    md.append("## 1. 总览\n")
    md.append(f"- 错误码总数：**{len(list(enum_cls))}**\n")

    md.append("## 2. 错误码范围说明\n")
    for start, end, title in STATUS_CODE_RANGES:
        md.append(f"- **{title}**：`{start}–{end}`")
    md.append("")

    section_no = 3
    for title, items in grouped.items():
        md.append(f"## {section_no}. {title}\n")
        section_no += 1

        for item in sorted(items, key=lambda x: x.value[0]):
            code, msg = item.value
            placeholders = extract_placeholders(msg)

            md.append(f"### {item.name}")
            md.append(f"- **Code**：`{code}`")
            md.append(f"- **中文说明**：{cn_description(item.name)}")
            md.append(f"- **English Message**：`{msg}`")
            md.append(
                f"- **模板参数**：{', '.join(placeholders) if placeholders else '无'}"
            )
            md.append("")

    return "\n".join(md)
