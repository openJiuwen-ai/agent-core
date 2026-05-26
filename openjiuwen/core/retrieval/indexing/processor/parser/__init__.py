# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import TYPE_CHECKING

from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser

if TYPE_CHECKING:
    from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser
    from openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser import AutoLinkParser
    from openjiuwen.core.retrieval.indexing.processor.parser.auto_parser import AutoParser
    from openjiuwen.core.retrieval.indexing.processor.parser.excel_parser import ExcelParser
    from openjiuwen.core.retrieval.indexing.processor.parser.image_parser import ImageParser
    from openjiuwen.core.retrieval.indexing.processor.parser.json_parser import JSONParser
    from openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser import PDFParser
    from openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser import TxtMdParser
    from openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser import (
        WeChatArticleParser,
        parse_wechat_article_url,
    )
    from openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser import (
        WebPageParser,
        parse_web_page_url,
    )
    from openjiuwen.core.retrieval.indexing.processor.parser.word_parser import WordParser

_LAZY_EXPORTS = {
    "AutoFileParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser",
        None,
    ),
    "AutoLinkParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser",
        None,
    ),
    "AutoParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.auto_parser",
        None,
    ),
    "ExcelParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.excel_parser",
        "doc-excel",
    ),
    "JSONParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.json_parser",
        None,
    ),
    "PDFParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser",
        "doc-pdf",
    ),
    "TxtMdParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.txt_md_parser",
        None,
    ),
    "WebPageParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser",
        "doc-html",
    ),
    "WordParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.word_parser",
        "doc-word",
    ),
    "WeChatArticleParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser",
        "doc-html",
    ),
    "ImageParser": (
        "openjiuwen.core.retrieval.indexing.processor.parser.image_parser",
        None,
    ),
    "parse_wechat_article_url": (
        "openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser",
        "doc-html",
    ),
    "parse_web_page_url": (
        "openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser",
        "doc-html",
    ),
}


def __getattr__(name: str):
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, extra = export
    from importlib import import_module

    try:
        attr = getattr(import_module(module_name), name)
    except ImportError as exc:
        if extra is None:
            raise
        raise ImportError(
            f"{name} requires optional dependencies from 'openjiuwen[{extra}]'. "
            f"Install them before importing or using this parser."
        ) from exc

    globals()[name] = attr
    return attr


__all__ = [
    "AutoFileParser",
    "AutoLinkParser",
    "AutoParser",
    "ExcelParser",
    "Parser",
    "JSONParser",
    "PDFParser",
    "TxtMdParser",
    "WebPageParser",
    "WordParser",
    "WeChatArticleParser",
    "ImageParser",
    "parse_wechat_article_url",
    "parse_web_page_url",
]
