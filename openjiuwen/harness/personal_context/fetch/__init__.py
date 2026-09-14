"""Context fetch provider contracts and built-in providers."""

from openjiuwen.harness.personal_context.fetch.base import ContextFetchService
from openjiuwen.harness.personal_context.fetch.local_files import LocalFilesFetchService
from openjiuwen.harness.personal_context.fetch.toutiao_reader import ToutiaoReaderFetchService
from openjiuwen.harness.personal_context.fetch.zhihu_reader import ZhihuReaderFetchService

__all__ = [
    "ContextFetchService",
    "LocalFilesFetchService",
    "ZhihuReaderFetchService",
    "ToutiaoReaderFetchService",
]
