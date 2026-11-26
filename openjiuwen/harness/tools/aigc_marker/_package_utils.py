# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Atomic ZIP updates preserving unrelated entries and archive metadata."""

import os
import tempfile
import zipfile
from pathlib import Path


def replace_zip_entries(file_path: str, updates: dict[str, bytes]) -> None:
    target = Path(file_path)
    fd, temporary = tempfile.mkstemp(prefix=".aigc-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(target) as source, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as output:
            output.comment = source.comment
            written = set()
            for entry in source.infolist():
                if entry.filename in written:
                    raise ValueError(f"duplicate ZIP entry: {entry.filename}")
                written.add(entry.filename)
                output.writestr(entry, updates.get(entry.filename, source.read(entry)))
            for name, data in updates.items():
                if name not in written:
                    output.writestr(name, data)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
