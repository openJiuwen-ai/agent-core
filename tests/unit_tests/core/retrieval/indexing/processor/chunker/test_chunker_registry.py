# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
Chunker registry (register_chunker / get_chunker) test cases
"""

import pytest

from openjiuwen.core.retrieval.indexing.processor.chunker import (
    CHUNKER_REGISTRY,
    CharChunker,
    Chunker,
    HybridChunker,
    get_chunker,
    register_chunker,
)


class TestGetChunkerBuiltin:
    """Tests for built-in chunker retrieval via get_chunker"""

    @staticmethod
    def test_get_char_chunker():
        chunker = get_chunker("char", chunk_size=256, chunk_overlap=30)
        assert isinstance(chunker, CharChunker)
        assert chunker.chunk_size == 256
        assert chunker.chunk_overlap == 30

    @staticmethod
    def test_get_char_chunker_defaults():
        chunker = get_chunker("char")
        assert isinstance(chunker, CharChunker)
        assert chunker.chunk_size == 512

    @staticmethod
    def test_get_hybrid_chunker():
        chunker = get_chunker("hybrid", chunk_size=128, chunk_overlap=20)
        assert isinstance(chunker, HybridChunker)
        assert chunker.chunk_size == 128
        assert chunker.chunk_overlap == 20

    @staticmethod
    def test_get_hybrid_chunker_defaults():
        chunker = get_chunker("hybrid")
        assert isinstance(chunker, HybridChunker)
        assert chunker.chunk_size == 512

    @staticmethod
    def test_get_hybrid_with_custom_inner():
        inner = CharChunker(chunk_size=64, chunk_overlap=10)
        chunker = get_chunker("hybrid", inner_chunker=inner)
        assert isinstance(chunker, HybridChunker)
        assert chunker.chunk_size == 64

    @staticmethod
    def test_get_hybrid_with_no_split_when():
        def predicate(doc):
            return doc.metadata.get("type") == "special"

        chunker = get_chunker("hybrid", no_split_when=predicate)
        assert isinstance(chunker, HybridChunker)

    @staticmethod
    def test_get_unknown_chunker():
        with pytest.raises(KeyError, match="Unknown chunker"):
            get_chunker("nonexistent")


class TestGetChunkerValidation:
    """Tests for get_chunker input/output validation"""

    @staticmethod
    def test_hybrid_unknown_kwargs_when_inner_provided():
        """Unknown kwargs raise when inner_chunker is explicitly provided."""
        inner = CharChunker(chunk_size=64)
        with pytest.raises(TypeError, match="Unknown kwargs"):
            get_chunker("hybrid", inner_chunker=inner, bad_param=True)

    @staticmethod
    def test_hybrid_extra_kwargs_passed_to_inner():
        """Extra kwargs are passed to default CharChunker when inner_chunker not provided."""
        chunker = get_chunker("hybrid", chunk_size=256, chunk_overlap=10)
        assert chunker.chunk_size == 256
        assert chunker.chunk_overlap == 10

    @staticmethod
    def test_hybrid_inner_chunker_not_chunker():
        with pytest.raises(TypeError, match="inner_chunker must be a Chunker instance"):
            get_chunker("hybrid", inner_chunker="not a chunker")

    @staticmethod
    def test_return_type_validation():
        """Registry entry that returns non-Chunker should raise TypeError"""
        def bad_factory(**kwargs):
            return "not a chunker"

        name = "_test_bad_return"
        register_chunker(name, bad_factory)
        try:
            with pytest.raises(TypeError, match="must return a Chunker instance"):
                get_chunker(name)
        finally:
            CHUNKER_REGISTRY.pop(name, None)


class TestRegisterChunker:
    """Tests for register_chunker"""

    @staticmethod
    def test_register_and_get():
        class MyChunker(Chunker):
            def chunk_text(self, text):
                return [text]

        name = "_test_my_chunker"
        register_chunker(name, MyChunker)
        try:
            chunker = get_chunker(name)
            assert isinstance(chunker, MyChunker)
        finally:
            CHUNKER_REGISTRY.pop(name, None)

    @staticmethod
    def test_register_duplicate_raises():
        name = "_test_dup"
        register_chunker(name, CharChunker)
        try:
            with pytest.raises(ValueError, match="already registered"):
                register_chunker(name, CharChunker)
        finally:
            CHUNKER_REGISTRY.pop(name, None)

    @staticmethod
    def test_register_duplicate_with_overwrite():
        class MyChunker(Chunker):
            def chunk_text(self, text):
                return [text]

        name = "_test_overwrite"
        register_chunker(name, CharChunker)
        try:
            register_chunker(name, MyChunker, overwrite=True)
            chunker = get_chunker(name)
            assert isinstance(chunker, MyChunker)
        finally:
            CHUNKER_REGISTRY.pop(name, None)

    @staticmethod
    def test_register_empty_name():
        with pytest.raises(ValueError, match="non-empty string"):
            register_chunker("", CharChunker)

    @staticmethod
    def test_register_whitespace_name():
        with pytest.raises(ValueError, match="non-empty string"):
            register_chunker("   ", CharChunker)

    @staticmethod
    def test_builtin_char_registered():
        assert "char" in CHUNKER_REGISTRY

    @staticmethod
    def test_builtin_hybrid_registered():
        assert "hybrid" in CHUNKER_REGISTRY

    @staticmethod
    def test_register_factory_callable():
        """Factory function should work as registry entry"""
        def my_factory(**kwargs):
            return CharChunker(chunk_size=kwargs.get("chunk_size", 100))

        name = "_test_factory"
        register_chunker(name, my_factory)
        try:
            chunker = get_chunker(name, chunk_size=200)
            assert isinstance(chunker, CharChunker)
            assert chunker.chunk_size == 200
        finally:
            CHUNKER_REGISTRY.pop(name, None)

    @staticmethod
    def test_overwrite_builtin_blocked():
        """Overwriting built-in 'char' without overwrite=True should raise"""
        with pytest.raises(ValueError, match="already registered"):
            register_chunker("char", CharChunker)
