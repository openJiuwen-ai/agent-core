# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply the Xiaoyi Work 1.2.2 format handlers to a staged file."""

import hashlib
import importlib
import os
import shutil
import tempfile
from pathlib import Path

from openjiuwen.harness.tools.aigc_marker._formats import FORMAT_HANDLERS


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def mark_file(path: Path, signature: str, visible: bool, original_digest: str) -> bool:
    extension = path.suffix.lower()
    module_name, class_name = FORMAT_HANDLERS[extension]
    module = importlib.import_module(f".{module_name}", package=__package__)
    decorator = getattr(module, class_name)()

    with tempfile.TemporaryDirectory(prefix=".aigc-marker-", dir=path.parent) as staging:
        candidate = Path(staging) / f"marked{extension}"
        shutil.copyfile(path, candidate)
        if file_digest(candidate) != original_digest:
            raise ValueError("File changed while preparing its AIGC mark; retry on the current file")
        decorator.decorate(str(candidate), signature, visible)
        if module_name == "_html_decorator":
            from openjiuwen.harness.tools.aigc_marker._common import is_aigc_complete

            if not is_aigc_complete(decorator.get_aigc_data(candidate)):
                raise ValueError("Written HTML AIGC metadata could not be verified")
        elif module_name not in {"_audio_decorator", "_image_decorator", "_video_decorator"}:
            from openjiuwen.harness.tools.aigc_marker._text_metadata import inspect_text_metadata

            if not inspect_text_metadata(candidate)["guide_storage_verified"]:
                raise ValueError("Written AIGC metadata did not match the text metadata guide")
        if file_digest(candidate) == original_digest:
            return False
        if file_digest(path) != original_digest:
            raise ValueError("File changed while adding its AIGC mark; retry on the current file")
        shutil.copymode(path, candidate)
        os.replace(candidate, path)
        return True
