#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""从 Symphony 的 Pydantic 字段元数据生成带中文注释的配置树。"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Annotated, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "openjiuwen/symphony/config.py"
OUTPUT_PATH = CONFIG_PATH.with_name("config.tree.txt")


def load_config() -> ModuleType:
    """仅加载纯配置文件，避免导入 Symphony 包根及其运行模块。"""
    spec = importlib.util.spec_from_file_location("_symphony_config_documentation", CONFIG_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载配置文件：{CONFIG_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _nested_models(annotation: object) -> tuple[type[BaseModel], ...]:
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return (annotation,)
    arguments = get_args(annotation)
    if get_origin(annotation) is Annotated:
        arguments = arguments[:1]
    models = [model for argument in arguments for model in _nested_models(argument)]
    return tuple(dict.fromkeys(models))


def _literal_values(annotation: object) -> tuple[object, ...]:
    if get_origin(annotation) is Literal:
        return get_args(annotation)
    if get_origin(annotation) is Annotated:
        return _literal_values(get_args(annotation)[0])
    return ()


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _default_value(info: FieldInfo, defaults: BaseModel | None, name: str) -> tuple[str, object]:
    if defaults is not None:
        value = getattr(defaults, name)
    else:
        if info.is_required():
            return "必填", None
        if info.default_factory_takes_validated_data:
            return "默认值由前序字段计算", None
        value = info.get_default(call_default_factory=True)
    if isinstance(value, BaseModel):
        overrides = value.model_dump(mode="json", exclude_unset=True)
        details = json.dumps(overrides, ensure_ascii=False) if overrides else ""
        return f"默认={type(value).__name__}({details})", value
    if info.repr is False and value not in (None, ""):
        return "默认值不展示", value
    if value is None:
        return "默认=None", value
    if isinstance(value, (list, tuple)) and len(value) > 12:
        return f"默认=列表（{len(value)} 项，完整内容见 config.py 默认定义）", value
    return "默认=" + json.dumps(value, ensure_ascii=False), value


def render_fields(
    model: type[BaseModel],
    *,
    expand: bool = False,
    prefix: str = "",
    ancestors: tuple[type[BaseModel], ...] = (),
    defaults: BaseModel | None = None,
) -> list[str]:
    """保留字段顺序；公共子树递归展开，内部子模型以类型引用表达。"""
    result = []
    fields = list(model.model_fields.items())
    for index, (name, info) in enumerate(fields):
        last = index == len(fields) - 1
        marker = "└── " if last else "├── "
        models = _nested_models(info.annotation)
        reference = " → " + " | ".join(child.__name__ for child in models) if models else ""
        default, value = _default_value(info, defaults, name)
        description = _one_line(info.description or info.title or "未提供字段说明")
        choices = _literal_values(info.annotation)
        if len(choices) > 1:
            description += " 可选：" + " / ".join(str(value) for value in choices) + "。"
        result.append(f"{prefix}{marker}{name}{reference} # {default}；{description}")
        if expand and len(models) == 1 and models[0] not in (*ancestors, model):
            result.extend(render_fields(
                models[0], expand=True, prefix=prefix + ("    " if last else "│   "), ancestors=(*ancestors, model),
                defaults=value if isinstance(value, BaseModel) else None,
            ))
    return result


def render_document(config: ModuleType, *, include_internal: bool = False) -> str:
    """默认只导出用户配置树；按需附加内部模型参考，不列只读派生属性。"""
    command = "scripts/export_symphony_config.py" + (" --include-internal" if include_internal else "")
    lines = [
        f"# 由 {command} 自动生成，请修改 config.py 后重新生成。",
        "# 目标配置，尚未接入运行链路；当前运行行为仍以原有模块配置为准。",
        "# 注释标明默认值或必填；→ 表示子模型类型，用户可调子模型在根树完整展开。",
        "# 不列只读派生属性；长列表展示数量，完整默认内容保留在 config.py。",
        f"# 用户配置树覆盖 {len(config.PUBLIC_CONFIG_MODELS)} 个模型。",
        "",
        "# 全量配置",
        "SymphonyConfig # " + _one_line(inspect.getdoc(config.SymphonyConfig) or "公共配置"),
        *render_fields(config.SymphonyConfig, expand=True),
    ]
    if include_internal:
        lines.extend([
            "",
            f"# 内部模型参考：{len(config.INTERNAL_CONFIG_MODELS)} 个；保留独立用途，不属于公共输入。",
        ])
        for model in config.INTERNAL_CONFIG_MODELS:
            lines.extend([
                "",
                model.__name__ + " # " + _one_line(inspect.getdoc(model) or "内部配置"),
                *render_fields(model),
            ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """生成用户文档或内部参考；内容相同时不重写文件，支持从任意目录启动。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-internal", action="store_true", help="附加保留的内部模型参考")
    parser.add_argument("--output", type=Path, help="输出路径；默认 config.tree.txt，含内部参考时为 config.reference.txt")
    args = parser.parse_args(argv)
    output = args.output or (OUTPUT_PATH.with_name("config.reference.txt") if args.include_internal else OUTPUT_PATH)
    document = render_document(load_config(), include_internal=args.include_internal)
    if output.is_file() and output.read_text(encoding="utf-8") == document:
        print(f"配置树已是最新：{output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(f"已导出配置树：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
