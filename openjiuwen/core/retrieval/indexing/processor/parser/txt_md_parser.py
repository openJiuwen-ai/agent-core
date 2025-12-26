# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.

from typing import Any, Optional

import aiofiles
from charset_normalizer import detect

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser


@register_parser([".txt", ".TXT", ".md", ".MD", ".markdown", ".MARKDOWN"])
class TxtMdParser(Parser):
    """本地文件解析器，txt/md格式"""

    def __init__(self, **kwargs: Any):
        pass

    async def _parse(self, file_path: str) -> Optional[str]:
        """解析 TXT/MD 文件"""
        try:
            async with aiofiles.open(file_path, "rb") as f:
                raw_data = await f.read()
                # 使用 charset-normalizer 检测编码
                detected = detect(raw_data)
                # 处理不同的返回类型：CharsetMatch对象、dict或None
                if detected is None:
                    encoding = "utf-8"
                elif isinstance(detected, dict):
                    # 如果返回的是dict，尝试获取encoding字段
                    encoding = detected.get("encoding", "utf-8") or "utf-8"
                elif hasattr(detected, "encoding"):
                    # 如果是CharsetMatch对象，获取encoding属性
                    encoding = detected.encoding if detected.encoding else "utf-8"
                else:
                    encoding = "utf-8"

            async with aiofiles.open(
                file_path, "r", encoding=encoding, errors="ignore"
            ) as f:
                content = await f.read()

            return content.strip() if content else None
        except Exception as e:
            logger.error(f"Failed to parse TXT/MD {file_path}: {e}")
            return None
