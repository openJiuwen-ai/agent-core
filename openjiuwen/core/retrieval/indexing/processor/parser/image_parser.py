# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
from typing import Any, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import register_parser
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.captioner import ImageCaptioner


@register_parser([".png", ".jpg", ".jpeg", ".webp", ".gif", ".jfif"])
class ImageParser(Parser):
    """Parser for image files to generate captions.

    Exposes the image path in document metadata under ``image_path`` so that
    the knowledge base can store it and use it for image embeddings (e.g. via
    a multimodal embedder) in addition to caption-based text embeddings.
    """

    def __init__(self, **kwargs: Any):
        pass

    async def parse(
        self, doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs
    ) -> List[Document]:
        """Parse image file, generate captions, and return a document with image_path in metadata."""
        try:
            image_captioner = ImageCaptioner(llm_client=llm_client)
            saved_path = image_captioner.cp_image(doc)
            content = await self._parse(doc, llm_client=llm_client)
            if content is None:
                content = ""
            return [
                Document(
                    id_=doc_id,
                    text=content,
                    metadata={"image_path": saved_path},
                )
            ]
        except Exception as e:
            logger.error(f"Failed to parse image {doc}: {e}")
            return []

    async def _parse(self, image_path: str, llm_client: Optional[Model] = None) -> Optional[str]:
        """Parse image file and generate captions."""
        try:
            image_captioner = ImageCaptioner(llm_client=llm_client)
            image_captioner.cp_image(image_path)
            captions = await image_captioner.caption_images([image_path])
            return "\n".join([caption for caption in captions if caption]) if captions else None
        except Exception as e:
            logger.error(f"Failed to parse image {image_path}: {e}")
            return None
