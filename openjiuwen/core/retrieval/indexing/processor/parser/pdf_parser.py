# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import os
from typing import Any, Optional

import pdfplumber

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser


@register_parser([".pdf", ".PDF"])
class PDFParser(Parser):
    """Local file parser for PDF format"""

    def __init__(self, **kwargs: Any):
        pass

    async def _parse(self, file_path: str) -> Optional[str]:
        """Parse PDF file"""
        try:
            def _sync_parse_pdf():
                content = []
                with pdfplumber.open(file_path) as pdf:
                    for page_num, page in enumerate(pdf.pages, 1):
                        page_text = page.extract_text() or ""
                        if page_text:
                            content.append(page_text)
                return os.linesep.join([line for line in content if line.strip()])

            result = await asyncio.to_thread(_sync_parse_pdf)
            return result if result else None
        except Exception as e:
            logger.error(f"Failed to parse PDF {file_path}: {e}")
            return None
