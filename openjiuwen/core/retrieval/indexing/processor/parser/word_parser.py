# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import asyncio
from typing import Any, Optional
from docx import Document
from docx.oxml.ns import qn

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser


@register_parser([".docx", ".DOCX"])
class WordParser(Parser):
    """Local file parser for DOCX format"""

    def __init__(self, **kwargs: Any):
        pass

    async def _parse(self, file_path: str) -> Optional[str]:
        """Parse DOCX file"""
        try:
            doc = await asyncio.to_thread(Document, file_path)
            content = []
            elements = await asyncio.to_thread(lambda: list(doc.element.body))
            for element in elements:
                elem_text = self._parse_docx_element(element, doc)
                if elem_text:
                    content.append(elem_text)
            result = os.linesep.join([line for line in content if line.strip()])
            return result if result else None
        except Exception as e:
            logger.error(f"Failed to parse DOCX {file_path}: {e}")
            return None

    def _parse_docx_element(self, element, doc: Document) -> str:
        """Parse DOCX element (paragraph or table)"""
        if element.tag == qn("w:p"):
            para_text = element.text.strip()
            return para_text if para_text else ""
        elif element.tag == qn("w:tbl"):
            for table in doc.tables:
                if table._element == element:
                    table_text = []
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells]
                        table_text.append("\t".join(row_cells))
                    return os.linesep.join(table_text)
        return ""
