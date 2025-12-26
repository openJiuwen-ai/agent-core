# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Processor abstract base class test cases
"""
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.retrieval.indexing.processor.base import Processor


class ConcreteProcessor(Processor):
    """Concrete processor implementation for testing abstract base class"""

    async def process(self, *args, **kwargs):
        return "processed_result"


class TestProcessor:
    """Processor abstract base class tests"""

    @pytest.mark.asyncio
    async def test_process(self):
        """Test process method"""
        processor = ConcreteProcessor()
        result = await processor.process("test", key="value")
        assert result == "processed_result"

    @staticmethod
    def test_cannot_instantiate_abstract_class():
        """Test cannot directly instantiate abstract class"""
        with pytest.raises(TypeError):
            Processor()

