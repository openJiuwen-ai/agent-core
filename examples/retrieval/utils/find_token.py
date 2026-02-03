# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Utility to get the token ids for "yes" and "no" required for ChatReranker
"""

import re
import tempfile
from pathlib import Path
from typing import Any


def load_tokens_from_huggingface(hf_repo: str, filename: str = "tokenizer.json", token: Any = None, **kwargs):
    """Load token config for reranking from huggingface"""
    import huggingface_hub

    hf_repo = hf_repo.rstrip("/")

    with tempfile.TemporaryDirectory() as tmp_dir:
        huggingface_hub.hf_hub_download(repo_id=hf_repo, filename=filename, local_dir=tmp_dir, token=token, **kwargs)
        tmp_file = Path(tmp_dir) / filename
        tokenizer_content = tmp_file.read_text(encoding="utf-8")
        token_ids, token_texts = [None] * 2, [None] * 2
        suffix_pattern = r"\s*([0-9]+)"
        for i, prefix_pattern in enumerate([r'"(yes)"\s*', r'"(no)"\s*']):
            token_match = re.search(prefix_pattern + ":" + suffix_pattern, tokenizer_content)
            if token_match is None:
                token_match = re.search(prefix_pattern + ":" + suffix_pattern, tokenizer_content, flags=re.IGNORECASE)
            token_ids[i] = int(token_match.group(2))
            token_texts[i] = token_match.group(1)
        return dict(zip(token_texts, token_ids))


def load_tokens_from_tiktoken(model_name: str):
    """Load token config for reranking from tiktoken"""
    import tiktoken

    encoding = tiktoken.encoding_for_model(model_name)
    return dict(yes=encoding.encode_single_token("yes"), no=encoding.encode_single_token("no"))


if __name__ == "__main__":
    from output import write_output

    MODEL = "Qwen/Qwen3-8B"
    write_output("Token ids for %s: %r", MODEL, load_tokens_from_huggingface(MODEL))
