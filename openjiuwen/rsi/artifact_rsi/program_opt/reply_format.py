# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""How a model is asked to reply, and how that reply is read — as one thing.

A task may bring its own prompt wording (``run_dir/prompts/mutation.md``). Until
this module the *output protocol* was not part of that: the instructions lived
in the built-in template and the parser lived in `program.extract_files`, and a
task that changed the wording could quietly change what it asked the model to
return while the parser went on expecting the old shape.

That failed in the worst way available. `extract_files` treats an unrecognised
reply as the program itself — deliberately, so a junk reply still becomes a
node that scores `-inf` rather than shrinking the rank denominator. So a task
asking for `<PROGRAM>…</PROGRAM>` got the tags written into the candidate file,
a syntax error, a `-inf` node — and the next expansion did it again, for the
whole budget, with every step reporting success.

So a format is a **pair**: the sentences that ask for it and the reader that
understands it, named together and chosen together. `scorecard.json` picks one
by name; the name is refused before the run if it is not one of these; and a
task template that does not carry ``${reply_format}`` is refused at load,
because a prompt that never says how to reply is the same silent failure
arriving by omission instead of by mismatch.

Two are shipped. `files` is what this engine has always used and stays the
default: file-level granularity, whole files inside each block. `tagged` is the
single-file shape the OpenEvolve-style ports use, kept faithful to their
wording so their prompts can be reproduced here rather than approximated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Tuple

from .program import (
    DEFAULT_ENTRYPOINT,
    extract_files,
    extract_program,
    reply_carries_program,
)


class ReplyFormatError(ValueError):
    """The run named an output protocol this engine does not have."""


#: ``(reply, parent_files, entrypoint) -> (files, change_summary)``
Parse = Callable[[str, Mapping[str, str], str], Tuple[Dict[str, str], str]]


@dataclass(frozen=True)
class ReplyFormat:
    """One output protocol: what the prompt asks for, what shows it, and what
    reads it back.

    The listing is part of the protocol, not decoration. The instructions say
    to answer like the program above, so a prompt that displays the parent in
    one shape while asking for another is two instructions in conflict — and
    the demonstration wins. Measured: a `tagged` run whose parent was still
    rendered as a labelled fence got eight replies in a row as labelled
    fences, none of which the tagged reader could see, and every expansion was
    recorded as an empty draw.
    """

    name: str
    #: The prompt section that states the protocol. Rendered into the mutation
    #: prompt through the ``${reply_format}`` slot — the template's only way to
    #: tell the model how to answer, and therefore a required slot.
    instructions: str
    #: ``(code, entrypoint) -> str``: the parent program, in this protocol's
    #: own shape, for the ``${parent_code}`` slot.
    render: Callable[[str, str], str]
    parse: Parse
    #: Whether a reply proposed anything at all. Separate from `parse` because
    #: merging nothing onto the parent yields the parent — a valid program that
    #: costs a full evaluation to learn the parent's own score.
    carries_program: Callable[[str], bool]


# --- files: whole files, one fenced block each -------------------------------

_FILES_INSTRUCTIONS = """Output **only the files you changed**, each as its own fenced block labelled
with its path, exactly like the listing above:

   ```python name=path/to/file.py
   ...the complete new contents of that file...
   ```

   A file you do not output is kept as it is. Every block you do output replaces
   that whole file, so give complete contents — never a patch or a fragment.
   A path that is not in the listing creates a new file. To remove one, put
   `DELETE path/to/file.py` on a line of its own."""


# --- tagged: one program between tags, summary beside it ---------------------

_PROGRAM_TAG = re.compile(r"<PROGRAM>(.*?)</PROGRAM>", re.DOTALL | re.IGNORECASE)
_SUMMARY_TAG = re.compile(r"<CHANGE_SUMMARY>(.*?)</CHANGE_SUMMARY>", re.DOTALL | re.IGNORECASE)

_TAGGED_INSTRUCTIONS = """Return the complete program between tags, and one sentence about what you
   changed beside it:

   <PROGRAM>
   ...the complete Python source...
   </PROGRAM>
   <CHANGE_SUMMARY>one concise sentence</CHANGE_SUMMARY>

   The program replaces the whole file — give complete, runnable source, never a
   patch or a fragment."""


def _render_tagged(code: str, entrypoint: str = DEFAULT_ENTRYPOINT) -> str:
    """The parent between the same tags the reply is asked for."""
    from .program import files_of

    files = files_of(code, entrypoint)
    body = files.get(entrypoint) or next(iter(files.values()), "")
    return f"<PROGRAM>\n{body.strip()}\n</PROGRAM>"


def _parse_tagged(
    reply: str, parent: Mapping[str, str], entrypoint: str = DEFAULT_ENTRYPOINT,
) -> Tuple[Dict[str, str], str]:
    """One program, from between the tags, onto the entrypoint.

    Single-file by construction: the shape has nowhere to say which file it is,
    which is exactly why it suits a one-module genome and not a tree. The rest
    of the parent is inherited untouched, the same as `files`.
    """
    files = dict(parent)
    match = _PROGRAM_TAG.search(reply or "")
    if match:
        code = match.group(1).strip()
        # A model that fences inside the tags is being helpful, not wrong.
        code, _ = extract_program(code) if code.lstrip().startswith("```") else (code, "")
        if code.strip():
            files[entrypoint] = code
    summary = ""
    said = _SUMMARY_TAG.search(reply or "")
    if said:
        summary = " ".join(said.group(1).split())[:200]
    return files, summary


def _tagged_carries_program(reply: str) -> bool:
    match = _PROGRAM_TAG.search(reply or "")
    return bool(match and match.group(1).strip())


def _render_files(code: str, entrypoint: str = DEFAULT_ENTRYPOINT) -> str:
    """Every file in its own labelled block — the shape the reply is asked for."""
    from .prompt import render_tree

    return render_tree(code, entrypoint)


_FORMATS: Dict[str, ReplyFormat] = {
    "files": ReplyFormat(
        name="files",
        instructions=_FILES_INSTRUCTIONS,
        render=_render_files,
        parse=extract_files,
        carries_program=reply_carries_program,
    ),
    "tagged": ReplyFormat(
        name="tagged",
        instructions=_TAGGED_INSTRUCTIONS,
        render=_render_tagged,
        parse=_parse_tagged,
        carries_program=_tagged_carries_program,
    ),
}

#: What a run gets when its scorecard says nothing. The shape every existing
#: task was written against.
DEFAULT_FORMAT = "files"


def format_for(name: str | None) -> ReplyFormat:
    """The named protocol, or a refusal that lists the ones there are.

    Refused by name and before the run rather than discovered at the first
    expansion: a run whose replies cannot be read spends its whole budget
    producing `-inf` nodes and reports them as candidates that failed.
    """
    chosen = (name or DEFAULT_FORMAT).strip() or DEFAULT_FORMAT
    if chosen not in _FORMATS:
        raise ReplyFormatError(
            f"{chosen!r} is not an output protocol this engine has; available: "
            f"{', '.join(sorted(_FORMATS))}"
        )
    return _FORMATS[chosen]


__all__ = [
    "DEFAULT_FORMAT",
    "Parse",
    "ReplyFormat",
    "ReplyFormatError",
    "format_for",
]
