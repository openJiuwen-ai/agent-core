# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import os
from typing import Optional, List
import io
from PIL import Image

from docx import Document
from docx.oxml.ns import qn
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.captioner import ImageCaptioner
from openjiuwen.core.foundation.llm.model import Model


@register_parser([".docx", ".DOCX"])
class WordParser(Parser):
    """Local file parser for DOCX format"""

    def __init__(self, **kwargs):
        pass

    @staticmethod
    def _extract_images_from_paragraph(
        element: CT_P, doc: Document, paragraph_num: int, filename: str, output_dir: str = "images"
    ) -> List[str]:
        """Extract images from a DOCX paragraph and save them to the output directory.

        Args:
            element (CT_P): The paragraph element to extract images from.
            doc (Document): _description_
            paragraph_num (int): _description_
            filename (str): _description_
            output_dir (str, optional): _description_. Defaults to "images".

        Returns:
            List[str]: _description_
        """
        images = []
        images_per_paragraph = 0
        for blip in element.findall(".//a:blip", element.nsmap):
            r_embed = blip.get(qn("r:embed"))
            if not r_embed:
                continue

            image_part = doc.part.related_parts.get(r_embed)
            if image_part:
                tmp_image_blob = image_part.blob

                with Image.open(io.BytesIO(tmp_image_blob)) as image:
                    image = image.convert("RGB") if image.mode in ("RGBA", "P") else image
                os.makedirs(output_dir, exist_ok=True)

                image_path = os.path.join(
                    output_dir, f"{filename}__para_{paragraph_num}__img_{images_per_paragraph}.png"
                )
                image.save(image_path, format="PNG")
                images.append(image_path)
                images_per_paragraph += 1
        return images

    async def _parse(self, file_path: str, llm_client: Optional[Model] = None) -> Optional[str]:
        """Parse DOCX file"""
        image_captioner = ImageCaptioner(llm_client=llm_client)
        try:
            doc = await asyncio.to_thread(Document, file_path)
            content = []
            elements = await asyncio.to_thread(lambda: list(doc.element.body))
            for element_idx, element in enumerate(elements):
                elem_text = await self._parse_docx_element(
                    element,
                    doc,
                    image_captioner=image_captioner,
                    paragraph_num=element_idx,
                    filename=os.path.basename(file_path),
                )
                if elem_text:
                    content.extend(elem_text)
            result = os.linesep.join([line for line in content if line.strip()])
            return result if result else None
        except Exception as e:
            logger.error(f"Failed to parse DOCX {file_path}: {e}")
            return None

    async def _parse_docx_element(
        self,
        element: CT_P | CT_Tbl,
        doc: Document,
        paragraph_num: int,
        filename: str,
        image_captioner: ImageCaptioner | None,
    ) -> List[str]:
        """Parse DOCX element (paragraph or table)

        Args:
            element (CT_P | CT_Tbl): _description_
            doc (Document): The document containing the element.
            paragraph_num (int): The paragraph number (if applicable).
            filename (str): The name of the file being parsed.
            image_captioner (ImageCaptioner | None): The image captioner instance (if available).

        Returns:
            List[str]: _description_
        """

        if element.tag == qn("w:p"):
            para_text = []
            elem_text = element.text.strip()
            if elem_text:
                para_text.append(elem_text)
            extracted_images = self._extract_images_from_paragraph(
                element=element, doc=doc, paragraph_num=paragraph_num, filename=filename
            )

            if image_captioner:
                captions = await image_captioner.caption_images(extracted_images)

            for caption in captions:
                if caption:
                    para_text.append(caption)
            return para_text

        elif element.tag == qn("w:tbl"):
            for table in doc.tables:
                if table._element == element:
                    table_text = []
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells]
                        table_text.append("\t".join(row_cells))
                    return [os.linesep.join(table_text)]
        return []
