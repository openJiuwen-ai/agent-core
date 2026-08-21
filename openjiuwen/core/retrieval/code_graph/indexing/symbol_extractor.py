# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Extract symbols and relations from a tree-sitter syntax tree."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openjiuwen.core.retrieval.code_graph.indexing.language_registry import SourceLanguage
from openjiuwen.core.retrieval.code_graph.models import Symbol, SymbolKind

_DEFINITION_KEYWORDS = (
    "function",
    "method",
    "class",
    "constructor",
    "interface",
    "module",
    "struct",
    "trait",
    "type",
)
_DEFINITION_SUFFIXES = ("definition", "declaration", "specifier", "item", "spec")

_PYTHON_DEF_TYPES = frozenset({"class_definition", "function_definition"})
_CLASS_LIKE_TYPES = frozenset(
    {
        "class_definition",
        "class_declaration",
        "class_specifier",
        "interface_declaration",
        "interface_type",
        "trait_item",
        "struct_item",
        "struct_specifier",
        "type_spec",
    }
)
_FUNCTION_LIKE_TYPES = frozenset(
    {
        "function_definition",
        "function_declaration",
        "function_item",
        "method_declaration",
        "method_definition",
        "constructor_declaration",
        "arrow_function",
    }
)

_SKIP_BASES = frozenset(
    {
        "object",
        "type",
        "ABC",
        "ABCMeta",
        "Protocol",
        "Generic",
        "TypedDict",
        "NamedTuple",
        "Enum",
        "Exception",
        "BaseException",
        "dict",
        "list",
        "tuple",
        "set",
        "str",
        "int",
        "float",
        "bool",
        "Object",
        "Record",
        "error",
        "any",
        "Send",
        "Sync",
        "Copy",
        "Clone",
        "Debug",
        "Default",
    }
)

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SKIP_ANNOTATION_NAMES = frozenset(
    {
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "None",
        "Any",
        "Optional",
        "Union",
        "List",
        "Dict",
        "Set",
        "Tuple",
        "Callable",
        "Type",
        "type",
        "object",
        "Self",
        "ClassVar",
        "Final",
        "Iterable",
        "Mapping",
        "Sequence",
        "Literal",
    }
)
_PY_CLASS_RE = re.compile(
    r"^\s*class\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*(?:\(([^)]*)\))?\s*:",
    re.M,
)


@dataclass
class PendingCall:
    """A call site awaiting target resolution.

    ``receiver`` is the textual object the call was made on (``self``, ``cls``,
    a variable, or a class name). It is kept verbatim: the resolver decides what
    it means, and an unknown receiver must not become a guessed edge.
    """

    caller_id: str
    callee_name: str
    file: str
    callee_expression: str = ""
    receiver: str = ""
    start_line: int = 0
    end_line: int = 0
    language: str = ""
    caller_class_id: str = ""


@dataclass
class PendingImport:
    file_id: str
    module_path: str
    names: tuple[str, ...]
    file: str


@dataclass
class PendingInherit:
    subclass_id: str
    base_name: str
    file: str


@dataclass
class PendingAssignment:
    """A local ``name = Type(...)`` binding inside ``caller_id``.

    Used so ``wrapper = SlicedLowLevelWCS(); wrapper.world_to_pixel_values()``
    still produces a call edge. Without it the receiver is an intermediate
    variable and the chain breaks.
    """

    caller_id: str
    name: str
    type_name: str
    file: str


@dataclass
class ExtractedFile:
    """Symbols and unresolved relations for a single source file."""

    symbols: list[Symbol] = field(default_factory=list)
    contains: list[tuple[str, str]] = field(default_factory=list)
    calls: list[PendingCall] = field(default_factory=list)
    imports: list[PendingImport] = field(default_factory=list)
    inherits: list[PendingInherit] = field(default_factory=list)
    assignments: list[PendingAssignment] = field(default_factory=list)


def extract_file(
    *,
    rel_path: str,
    language: SourceLanguage,
    tree: Any,
    max_depth: int,
) -> ExtractedFile:
    """Walk ``tree`` and return symbols plus pending cross-file relations."""
    result = ExtractedFile()
    file_id = rel_path
    file_symbol = Symbol(
        symbol_id=file_id,
        name=Path(rel_path).name,
        kind=SymbolKind.FILE,
        file=rel_path,
        start_line=1,
        end_line=_end_line(tree.root_node) if tree is not None else 1,
        qualified_name=rel_path,
        language=language.value,
    )
    result.symbols.append(file_symbol)

    if tree is None or tree.root_node is None:
        return result

    root = tree.root_node
    module_id = f"{rel_path}::__module__"
    module_symbol = Symbol(
        symbol_id=module_id,
        name=Path(rel_path).stem,
        kind=SymbolKind.MODULE,
        file=rel_path,
        start_line=1,
        end_line=_end_line(root),
        qualified_name=file_to_dotted_module(rel_path),
        language=language.value,
        parent_id=file_id,
    )
    result.symbols.append(module_symbol)
    result.contains.append((file_id, module_id))

    if language == SourceLanguage.PYTHON:
        _walk_python(root, result, rel_path, language, module_id, max_depth)
    else:
        _walk_generic(root, result, rel_path, language, module_id, max_depth)
    return result


def is_definition_type(ast_type: str) -> bool:
    """Heuristic: is this tree-sitter node a definition across languages?"""
    t = (ast_type or "").lower()
    if not any(keyword in t for keyword in _DEFINITION_KEYWORDS):
        return False
    if t.endswith(_DEFINITION_SUFFIXES):
        return True
    return t in {"function", "method", "class", "module", "type_spec"}


def kind_from_ast_type(ast_type: str, *, inside_class: bool) -> SymbolKind:
    """Map a tree-sitter node type onto ``SymbolKind``."""
    t = (ast_type or "").lower()
    if "interface" in t:
        return SymbolKind.INTERFACE
    if "trait" in t:
        return SymbolKind.TRAIT
    if "struct" in t:
        return SymbolKind.STRUCT
    if "class" in t:
        return SymbolKind.CLASS
    if "method" in t or (inside_class and ("function" in t)):
        return SymbolKind.METHOD
    if "function" in t or t in {"arrow_function", "constructor_declaration"}:
        return SymbolKind.METHOD if inside_class else SymbolKind.FUNCTION
    return SymbolKind.VARIABLE


def _walk_python(
    root: Any,
    result: ExtractedFile,
    rel_path: str,
    language: SourceLanguage,
    module_id: str,
    max_depth: int,
) -> None:
    def_stack: list[str] = [module_id]
    class_stack: list[str] = []

    def visit(node: Any, depth: int) -> None:
        if depth > max_depth:
            return
        ntype = node.type
        if ntype == "decorated_definition":
            inner = _child_by_types(node, _PYTHON_DEF_TYPES)
            if inner is not None:
                visit(inner, depth + 1)
            return
        if ntype in _PYTHON_DEF_TYPES:
            symbol = _make_python_symbol(
                node,
                rel_path,
                language,
                def_stack[-1],
                class_stack,
            )
            if symbol is not None:
                result.symbols.append(symbol)
                result.contains.append((def_stack[-1], symbol.symbol_id))
                if ntype == "class_definition":
                    for base in _python_bases(node, source_text=_node_text(node)):
                        result.inherits.append(
                            PendingInherit(
                                subclass_id=symbol.symbol_id,
                                base_name=base,
                                file=rel_path,
                            )
                        )
                    def_stack.append(symbol.symbol_id)
                    class_stack.append(symbol.symbol_id)
                    for child in node.children:
                        visit(child, depth + 1)
                    class_stack.pop()
                    def_stack.pop()
                    return
                def_stack.append(symbol.symbol_id)
                result.assignments.extend(
                    _python_typed_parameters(
                        node,
                        caller_id=symbol.symbol_id,
                        rel_path=rel_path,
                    )
                )
                for child in node.children:
                    visit(child, depth + 1)
                def_stack.pop()
                return
        if ntype == "assignment" and def_stack:
            binding = _python_constructor_assignment(node, caller_id=def_stack[-1], rel_path=rel_path)
            if binding is not None:
                result.assignments.append(binding)
        if ntype == "call" and def_stack:
            target = _python_call_target(node)
            if target is not None:
                callee, receiver = target
                result.calls.append(
                    PendingCall(
                        caller_id=def_stack[-1],
                        callee_name=callee,
                        file=rel_path,
                        callee_expression=_call_expression(node),
                        receiver=receiver,
                        start_line=_start_line(node),
                        end_line=_end_line(node),
                        language=language.value,
                        caller_class_id=class_stack[-1] if class_stack else "",
                    )
                )
        if ntype in {"import_statement", "import_from_statement"}:
            pending = _python_import(node, file_id=rel_path, rel_path=rel_path)
            if pending is not None:
                result.imports.append(pending)
        for child in node.children:
            visit(child, depth + 1)

    for child in root.children:
        visit(child, 1)


def _walk_generic(
    root: Any,
    result: ExtractedFile,
    rel_path: str,
    language: SourceLanguage,
    module_id: str,
    max_depth: int,
) -> None:
    def_stack: list[str] = [module_id]
    class_stack: list[str] = []

    def visit(node: Any, depth: int) -> None:
        if depth > max_depth:
            return
        ntype = node.type
        if is_definition_type(ntype) and ntype not in {"module", "program"}:
            name = _node_name(node)
            if name:
                inside_class = bool(class_stack)
                kind = kind_from_ast_type(ntype, inside_class=inside_class)
                parent_id = def_stack[-1]
                parent_name = parent_id.split("::")[-1] if "::" in parent_id else ""
                qualified = (
                    f"{parent_name}.{name}"
                    if kind == SymbolKind.METHOD and parent_name and parent_name != Path(rel_path).stem
                    else name
                )
                symbol_id = f"{rel_path}::{qualified}"
                symbol = Symbol(
                    symbol_id=symbol_id,
                    name=name,
                    kind=kind,
                    file=rel_path,
                    start_line=_start_line(node),
                    end_line=_end_line(node),
                    qualified_name=qualified,
                    language=language.value,
                    parent_id=parent_id,
                    signature=_node_text(node).split("\n", 1)[0][:200],
                )
                result.symbols.append(symbol)
                result.contains.append((parent_id, symbol_id))
                if ntype in _CLASS_LIKE_TYPES or kind in {
                    SymbolKind.CLASS,
                    SymbolKind.INTERFACE,
                    SymbolKind.STRUCT,
                    SymbolKind.TRAIT,
                }:
                    for base in _generic_bases(_node_text(node), name):
                        result.inherits.append(
                            PendingInherit(subclass_id=symbol_id, base_name=base, file=rel_path)
                        )
                    def_stack.append(symbol_id)
                    class_stack.append(symbol_id)
                    for child in node.children:
                        visit(child, depth + 1)
                    class_stack.pop()
                    def_stack.pop()
                    return
                if ntype in _FUNCTION_LIKE_TYPES:
                    def_stack.append(symbol_id)
                    for child in node.children:
                        visit(child, depth + 1)
                    def_stack.pop()
                    return
        if ntype == "call_expression" and def_stack:
            callee = _node_name(node) or _generic_call_name(node)
            if callee:
                result.calls.append(
                    PendingCall(
                        caller_id=def_stack[-1],
                        callee_name=callee,
                        file=rel_path,
                        callee_expression=_call_expression(node),
                        receiver=_generic_call_receiver(node),
                        start_line=_start_line(node),
                        end_line=_end_line(node),
                        language=language.value,
                        caller_class_id=class_stack[-1] if class_stack else "",
                    )
                )
        for child in node.children:
            visit(child, depth + 1)

    for child in root.children:
        visit(child, 1)


def _make_python_symbol(
    node: Any,
    rel_path: str,
    language: SourceLanguage,
    parent_id: str,
    class_stack: list[str],
) -> Symbol | None:
    name = _node_name(node)
    if not name:
        return None
    if node.type == "class_definition":
        kind = SymbolKind.CLASS
        qualified = name
    elif class_stack:
        kind = SymbolKind.METHOD
        class_name = class_stack[-1].rsplit("::", 1)[-1]
        qualified = f"{class_name}.{name}"
    else:
        kind = SymbolKind.FUNCTION
        qualified = name
    return Symbol(
        symbol_id=f"{rel_path}::{qualified}",
        name=name,
        kind=kind,
        file=rel_path,
        start_line=_start_line(node),
        end_line=_end_line(node),
        qualified_name=qualified,
        language=language.value,
        parent_id=parent_id,
        signature=_node_text(node).split("\n", 1)[0][:200],
    )


def _python_bases(node: Any, source_text: str) -> list[str]:
    superclasses = node.child_by_field_name("superclasses") if hasattr(node, "child_by_field_name") else None
    if superclasses is not None:
        names: list[str] = []
        for child in superclasses.children:
            ident = _python_base_ident(child)
            if ident and ident not in _SKIP_BASES:
                names.append(ident)
        if names:
            return names
    match = _PY_CLASS_RE.search(source_text)
    if not match or not match.group(2):
        return []
    bases: list[str] = []
    for raw in match.group(2).split(","):
        ident = raw.strip().split("[", 1)[0].split(".", 1)[-1].strip()
        if ident and ident not in _SKIP_BASES and _IDENT_RE.fullmatch(ident):
            bases.append(ident)
    return bases


def _python_base_ident(node: Any) -> str | None:
    if node.type in {"identifier", "type"}:
        text = _node_text(node).strip()
        return text.split("[", 1)[0] or None
    if node.type == "attribute":
        attr = node.child_by_field_name("attribute") if hasattr(node, "child_by_field_name") else None
        if attr is not None:
            return _node_text(attr).strip() or None
    subscript = node.child_by_field_name("value") if hasattr(node, "child_by_field_name") else None
    if subscript is not None:
        return _python_base_ident(subscript)
    text = _node_text(node).strip()
    if text in {",", "(", ")", "[", "]"}:
        return None
    ident = text.split("[", 1)[0].split(".")[-1].strip()
    return ident if _IDENT_RE.fullmatch(ident) else None


def _python_typed_parameters(
    node: Any,
    *,
    caller_id: str,
    rel_path: str,
) -> list[PendingAssignment]:
    """``schema_editor: SchemaEditor`` bindings, same hop as ``x = SchemaEditor()``."""
    params = node.child_by_field_name("parameters") if hasattr(node, "child_by_field_name") else None
    if params is None:
        return []
    found: list[PendingAssignment] = []
    for child in params.children:
        if child.type not in {"typed_parameter", "typed_default_parameter"}:
            continue
        name, type_name = _python_typed_parameter(child)
        if not name or not type_name:
            continue
        found.append(
            PendingAssignment(
                caller_id=caller_id,
                name=name,
                type_name=type_name,
                file=rel_path,
            )
        )
    return found


def _python_typed_parameter(node: Any) -> tuple[str, str]:
    name = ""
    type_node = node.child_by_field_name("type") if hasattr(node, "child_by_field_name") else None
    for child in node.children:
        if child.type == "identifier" and not name:
            name = _node_text(child).strip()
    if type_node is None:
        for child in node.children:
            if child.type in {":", "=", ",", "identifier"}:
                continue
            type_node = child
            break
    type_name = _python_annotation_class_name(type_node) if type_node is not None else ""
    if not name or not _IDENT_RE.fullmatch(name):
        return "", ""
    return name, type_name


def _python_annotation_class_name(node: Any) -> str:
    idents = _IDENT_RE.findall(_node_text(node))
    for ident in reversed(idents):
        if ident not in _SKIP_ANNOTATION_NAMES:
            return ident
    return ""


def _python_constructor_assignment(node: Any, *, caller_id: str, rel_path: str) -> PendingAssignment | None:
    """Capture ``name = ClassName(...)`` so later ``name.method()`` can resolve."""
    left = node.child_by_field_name("left") if hasattr(node, "child_by_field_name") else None
    right = node.child_by_field_name("right") if hasattr(node, "child_by_field_name") else None
    if left is None or right is None:
        return None
    if left.type != "identifier":
        return None
    name = _node_text(left).strip()
    if not name or not _IDENT_RE.fullmatch(name):
        return None
    call = right if right.type == "call" else None
    if call is None:
        return None
    target = _python_call_target(call)
    if target is None:
        return None
    type_name, _receiver = target
    if not type_name or not _IDENT_RE.fullmatch(type_name):
        return None
    return PendingAssignment(
        caller_id=caller_id,
        name=name,
        type_name=type_name,
        file=rel_path,
    )


def _python_call_target(node: Any) -> tuple[str, str] | None:
    """Return ``(callee_name, receiver)`` for a Python call node.

    The receiver is empty for a bare ``foo()`` call. For ``a.b.c()`` it is the
    innermost object text (``a.b``), which the resolver may or may not be able
    to type.
    """
    func = node.child_by_field_name("function") if hasattr(node, "child_by_field_name") else None
    if func is None:
        return None
    if func.type == "identifier":
        return _node_text(func), ""
    if func.type == "attribute":
        attr = func.child_by_field_name("attribute")
        obj = func.child_by_field_name("object")
        attr_name = _node_text(attr) if attr is not None else ""
        if not attr_name:
            return None
        return attr_name, _node_text(obj).strip() if obj is not None else ""
    return None


def _python_import(node: Any, *, file_id: str, rel_path: str) -> PendingImport | None:
    text = _node_text(node).strip()
    if node.type == "import_statement":
        # import a, b as c
        modules: list[str] = []
        for child in node.children:
            if child.type in {"dotted_name", "aliased_import", "identifier"}:
                raw = _node_text(child).split(" as ", 1)[0].strip()
                if raw:
                    modules.append(raw)
        if not modules:
            return None
        return PendingImport(
            file_id=file_id,
            module_path=absolute_module_path(rel_path, modules[0]),
            names=tuple(modules),
            file=rel_path,
        )
    # from x import y
    module_node = node.child_by_field_name("module_name") if hasattr(node, "child_by_field_name") else None
    module = _node_text(module_node).strip() if module_node is not None else ""
    if not module:
        match = re.match(r"from\s+([\w.]+)\s+import", text)
        module = match.group(1) if match else ""
    names: list[str] = []
    for child in node.children:
        if child.type in {"dotted_name", "identifier", "aliased_import"} and child is not module_node:
            raw = _node_text(child).split(" as ", 1)[0].strip()
            if raw and raw not in {"from", "import", "(", ")", ","}:
                names.append(raw)
    if not module:
        return None
    return PendingImport(
        file_id=file_id,
        module_path=absolute_module_path(rel_path, module),
        names=tuple(names) or (module,),
        file=rel_path,
    )


def _generic_bases(text: str, class_name: str) -> list[str]:
    header = text.split("{", 1)[0].split("\n", 1)[0]
    bases: list[str] = []
    if "extends" in header:
        after = header.split("extends", 1)[1]
        after = after.split("implements", 1)[0]
        for raw in after.replace("{", " ").split(","):
            ident = _IDENT_RE.findall(raw.strip())
            if ident and ident[0] != class_name and ident[0] not in _SKIP_BASES:
                bases.append(ident[0])
    if "implements" in header:
        after = header.split("implements", 1)[1]
        for raw in after.replace("{", " ").split(","):
            ident = _IDENT_RE.findall(raw.strip())
            if ident and ident[0] not in _SKIP_BASES:
                bases.append(ident[0])
    match = _PY_CLASS_RE.search(text)
    if match and match.group(2):
        for raw in match.group(2).split(","):
            ident = raw.strip().split("[", 1)[0].split(".")[-1].strip()
            if ident and ident not in _SKIP_BASES and _IDENT_RE.fullmatch(ident):
                bases.append(ident)
    # C++ ``class Foo : public Bar``
    cpp = re.search(
        r"\b(?:class|struct)\s+" + re.escape(class_name) + r"\s*:\s*([^;{]+)",
        header,
    )
    if cpp:
        cleaned = re.sub(r"\b(?:public|protected|private|virtual)\b", " ", cpp.group(1))
        for ident in _IDENT_RE.findall(cleaned):
            if ident not in _SKIP_BASES:
                bases.append(ident)
    seen: set[str] = set()
    unique: list[str] = []
    for name in bases:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def _call_expression(node: Any, limit: int = 120) -> str:
    """First line of the call site text, for evidence display."""
    text = _node_text(node).split("\n", 1)[0].strip()
    return text[:limit]


def _generic_call_receiver(node: Any) -> str:
    func = node.child_by_field_name("function") if hasattr(node, "child_by_field_name") else None
    if func is None or func.type in {"identifier", "field_identifier"}:
        return ""
    obj = None
    if hasattr(func, "child_by_field_name"):
        obj = func.child_by_field_name("object") or func.child_by_field_name("operand")
    return _node_text(obj).strip() if obj is not None else ""


def _generic_call_name(node: Any) -> str | None:
    func = node.child_by_field_name("function") if hasattr(node, "child_by_field_name") else None
    if func is None:
        return _node_name(node)
    if func.type in {"identifier", "field_identifier"}:
        return _node_text(func)
    attr = func.child_by_field_name("field") or func.child_by_field_name("attribute")
    if attr is not None:
        return _node_text(attr)
    return None


def _child_by_types(node: Any, types: Iterable[str]) -> Any | None:
    wanted = set(types)
    for child in node.children:
        if child.type in wanted:
            return child
    return None


def _node_name(node: Any) -> str | None:
    if hasattr(node, "child_by_field_name"):
        for field in ("name", "declarator"):
            child = node.child_by_field_name(field)
            if child is None:
                continue
            if child.type in {"identifier", "type_identifier", "field_identifier", "property_identifier"}:
                return _node_text(child)
            nested = child.child_by_field_name("name") if hasattr(child, "child_by_field_name") else None
            if nested is not None:
                return _node_text(nested)
            ident = _first_identifier(child)
            if ident:
                return ident
    return _first_identifier(node)


def _first_identifier(node: Any) -> str | None:
    if node.type in {"identifier", "type_identifier", "field_identifier", "property_identifier"}:
        text = _node_text(node).strip()
        return text or None
    for child in getattr(node, "children", ()):
        if child.type in {"identifier", "type_identifier", "field_identifier"}:
            text = _node_text(child).strip()
            if text:
                return text
    return None


def file_to_dotted_module(rel_path: str) -> str:
    """Repo-relative file path → import-style dotted module name."""
    path = (rel_path or "").replace("\\", "/").strip("/")
    if path.endswith("/__init__.py"):
        path = path[: -len("/__init__.py")]
    elif path.endswith(".py"):
        path = path[:-3]
    else:
        path = str(Path(path).with_suffix("")) if path else ""
    return path.replace("/", ".")


def absolute_module_path(rel_path: str, module: str) -> str:
    """Turn a Python import module string into a repo-relative slash path.

    Relative imports (``.itrs``, ``..utils``) are resolved against the
    importing file's package. Absolute imports keep their dotted path, with
    dots turned into slashes.
    """
    rel = (rel_path or "").replace("\\", "/")
    parent_parts = list(Path(rel).parent.parts)
    if not module or module == ".":
        return "/".join(parent_parts)
    if module.startswith("."):
        dots = len(module) - len(module.lstrip("."))
        remainder = module[dots:].replace(".", "/")
        up = max(0, dots - 1)
        if up:
            parent_parts = parent_parts[:-up] if up <= len(parent_parts) else []
        if remainder:
            return "/".join([*parent_parts, remainder]) if parent_parts else remainder
        return "/".join(parent_parts)
    return module.replace(".", "/")


def _node_text(node: Any) -> str:
    raw = getattr(node, "text", None)
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _start_line(node: Any) -> int:
    point = getattr(node, "start_point", (0, 0))
    return int(point[0]) + 1


def _end_line(node: Any) -> int:
    point = getattr(node, "end_point", (0, 0))
    return int(point[0]) + 1
