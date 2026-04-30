# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Generic Web Page Parser

Fetches and parses generic web page URLs (e.g. blog posts, articles) into Document objects.
Uses BeautifulSoup; main content is extracted via common selectors (article, main, etc.).
For WeChat articles use WeChatArticleParser instead.
"""

import re
import ssl
import uuid
from typing import List, Optional

import httpx

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.html_file_parser import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT, \
    HTMLFileParser

# http(s) URL pattern
HTTP_URL_PATTERN = re.compile(r"^https?://\S+", re.IGNORECASE)

# Recognized WeChat article URL pattern
WECHAT_MP_URL_PATTERN = re.compile(
    r"^https?://(?:mp\.weixin\.qq\.com|.*?\.weixin\.qq\.com)/s\b.*",
    re.IGNORECASE,
)


def _is_wechat_article_url(url: str) -> bool:
    return bool(url and WECHAT_MP_URL_PATTERN.match(url.strip()))


class WebPageParser(HTMLFileParser):
    """
    Parser for generic web page URLs (blogs, articles, etc.).
    Use AutoParser or AutoLinkParser for auto-dispatch; WeChat URLs are routed to WeChatArticleParser by pattern.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        verify: bool | str | ssl.SSLContext = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.timeout = timeout
        self.user_agent = user_agent
        self.verify = verify

    @staticmethod
    def _validate_url(url: str):
        """
        Checks if the input url is valid. If not, it raises an exception
        Raises:
            build_error(StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR): On invalid URL or fetch/parse failure.
        """
        url = (url or "").strip()
        if not url or not HTTP_URL_PATTERN.match(url):
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Not a valid HTTP URL: {url!r}",
            )
        if _is_wechat_article_url(url):
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg="Use WeChatArticleParser for WeChat URLs",
            )

    @staticmethod
    async def _do_get(url: str, c: httpx.AsyncClient, *, headers_for_request: Optional[dict] = None) -> str:
        response = await c.get(url, headers=headers_for_request)
        response.raise_for_status()
        return response.text

    @classmethod
    async def _download_html(
        cls,
        url: str,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        verify: bool | str | ssl.SSLContext = True,
        client: Optional[httpx.AsyncClient] = None,
    ):
        """
        Downloads the HTML content from a web page URL.

        Args:
            url: Web page URL (http or https).
            timeout: Request timeout in seconds (used only when ``client`` is not provided).
            user_agent: HTTP User-Agent header.
            verify: SSL verification for the httpx client: ``True`` (default CA bundle),
                ``False`` to disable (e.g. corporate TLS inspection), a path to a CA bundle, or a custom
                ``ssl.SSLContext`` — same semantics as :class:`httpx.AsyncClient`.
            client: Optional shared :class:`httpx.AsyncClient`; if omitted, a client is created with
                ``verify``, ``timeout``, and ``User-Agent`` and closed after the request.

        Returns:
            HTML content string

        Raises:
            build_error(StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR): On invalid URL or fetch/parse failure.
        """
        request_headers = {"User-Agent": user_agent}

        try:
            if client is not None:
                return await cls._do_get(url, client, headers_for_request=request_headers)
            else:
                async with httpx.AsyncClient(
                    verify=verify,
                    timeout=httpx.Timeout(timeout),
                    headers=request_headers,
                ) as http_client:
                    return await cls._do_get(url, http_client, headers_for_request=None)

        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else "?"
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Web page request failed: {status} for {url}",
                cause=e,
            ) from e
        except httpx.RequestError as e:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Web page fetch failed for {url}: {e}",
                cause=e,
            ) from e
        except Exception as e:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
                error_msg=f"Web page fetch failed for {url}: {e}",
                cause=e,
            ) from e

    @classmethod
    async def parse_url(
        cls,
        url: str,
        doc_id: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        verify: bool | str | ssl.SSLContext = True,
        client: Optional[httpx.AsyncClient] = None,
    ) -> List[Document]:
        """
        Fetch a web page URL and parse it into one or more Document objects.

        Args:
            url: Web page URL (http or https).
            doc_id: Optional document ID; defaults to URL or generated UUID.
            timeout: Request timeout in seconds (used only when ``client`` is not provided).
            user_agent: HTTP User-Agent header.
            verify: SSL verification for the httpx client: ``True`` (default CA bundle),
                ``False`` to disable (e.g. corporate TLS inspection), a path to a CA bundle, or a custom
                ``ssl.SSLContext`` — same semantics as :class:`httpx.AsyncClient`.
            client: Optional shared :class:`httpx.AsyncClient`; if omitted, a client is created with
                ``verify``, ``timeout``, and ``User-Agent`` and closed after the request.

        Returns:
            List of Document instances (typically one).

        Raises:
            build_error(StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR): On invalid URL or fetch/parse failure.
        """

        cls._validate_url(url)
        html = await cls._download_html(url, timeout=timeout, user_agent=user_agent, verify=verify, client=client)
        effective_id = doc_id or url or str(uuid.uuid4())
        docs = await cls._parse_html(html, effective_id)
        for doc in docs:
            doc.metadata["source_url"] = url
            logger.info(
                "Parsed web page: url=%s title=%s",
                url,
                doc.metadata.get("title") or "(无标题)",
            )

        return docs

    async def parse(self, doc: str, doc_id: str = "", **kwargs) -> List[Document]:
        timeout = kwargs.get("timeout", self.timeout)
        user_agent = kwargs.get("user_agent", self.user_agent)
        verify = kwargs.get("verify", self.verify)
        client = kwargs.get("client")
        return await self.parse_url(
            doc,
            doc_id=doc_id or doc,
            timeout=timeout,
            user_agent=user_agent,
            verify=verify,
            client=client,
        )

    def supports(self, doc: str) -> bool:
        """True for http(s) URLs that are not WeChat article URLs."""
        if not doc or not HTTP_URL_PATTERN.match(doc.strip()):
            return False
        return not _is_wechat_article_url(doc)


parse_web_page_url = WebPageParser.parse_url
