# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved.
"""
API embedding model implementation test cases
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.retrieval import APIEmbedding, EmbeddingConfig
from openjiuwen.core.session.tracer.handler import TraceExtAgentHandler


@pytest.fixture
def embedding_config():
    """Create embedding model configuration"""
    return EmbeddingConfig(
        model_name="test-model",
        api_key="test-api-key",
        base_url="https://api.example.com/v1/embeddings",
    )


@pytest.fixture
def embedding_config_no_key():
    """Create embedding model configuration without API key"""
    return EmbeddingConfig(
        model_name="test-model",
        base_url="https://api.example.com/v1/embeddings",
    )


class TestAPIEmbedding:
    """API embedding model tests"""

    @classmethod
    def test_init_with_api_key(cls, embedding_config):
        """Test initialization with API key"""
        model = APIEmbedding(config=embedding_config)
        assert model.model_name == "test-model"
        assert model.api_key == "test-api-key"
        assert model.api_url == "https://api.example.com/v1/embeddings"

    @classmethod
    def test_init_without_api_key(cls, embedding_config_no_key):
        """Test initialization without API key"""
        model = APIEmbedding(config=embedding_config_no_key)
        assert model.api_key is None

    @classmethod
    def test_init_with_extra_headers(cls, embedding_config):
        """Test initialization with extra headers"""
        extra_headers = {"X-Custom-Header": "custom-value"}
        model = APIEmbedding(config=embedding_config, extra_headers=extra_headers)
        actual_header = getattr(model, "_headers", {})
        assert "X-Custom-Header" in actual_header
        assert actual_header.get("X-Custom-Header") == "custom-value"

    @classmethod
    def test_init_with_custom_params(cls, embedding_config):
        """Test initialization with custom parameters"""
        model = APIEmbedding(
            config=embedding_config,
            timeout=120,
            max_retries=5,
            max_batch_size=16,
        )
        assert model.timeout == 120
        assert model.max_retries == 5
        assert model.max_batch_size == 16

    @classmethod
    def test_init_semaphore(cls, embedding_config):
        """Test Semaphore initialization with max_concurrent"""
        model = APIEmbedding(config=embedding_config, max_concurrent=25)
        assert model.limiter is not None
        assert getattr(model.limiter, "_value") == 25

    @classmethod
    def test_init_semaphore_default(cls, embedding_config):
        """Test Semaphore initialization with default max_concurrent"""
        model = APIEmbedding(config=embedding_config)
        assert model.limiter is not None
        assert getattr(model.limiter, "_value") == 50

    @classmethod
    def test_executor_resource_management(cls, embedding_config):
        """Test ThreadPoolExecutor lazy initialization & cleanup"""
        model = APIEmbedding(config=embedding_config, max_concurrent=10)
        # Executor should be None initially
        assert getattr(model, "_executor") is None

        # Accessing executor property initialize it
        executor = model.executor
        assert executor is not None
        assert isinstance(executor, ThreadPoolExecutor)
        assert getattr(executor, "_max_workers") == 10
        assert getattr(executor, "_thread_name_prefix") == "openjiuwen_embed"

        # Subsequent accesses returns same executor
        assert model.executor is executor

    @pytest.mark.asyncio
    async def test_semaphore_concurrency_control(self, embedding_config):
        """Test that Semaphore properly controls concurrency"""
        import asyncio

        model = APIEmbedding(config=embedding_config, max_concurrent=2)

        # Create a counter to track concurrent operations
        concurrent_count = 0
        max_concurrent_seen = 0

        async def test_task():
            nonlocal concurrent_count, max_concurrent_seen
            async with model.limiter:
                concurrent_count += 1
                max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
                # Simulate some work
                await asyncio.sleep(0.01)
                concurrent_count -= 1

        # Launch more tasks than max_concurrent
        tasks = [test_task() for _ in range(5)]
        await asyncio.gather(*tasks)

        # Should never exceed max_concurrent
        assert max_concurrent_seen <= 2

    @pytest.mark.asyncio
    async def test_embed_query_success_embedding_format(self, embedding_config):
        """Test embedding query text successfully (embedding format)"""
        mock_response = Mock()
        mock_response.json.return_value = {"embedding": [0.1] * 384}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config)
            embedding = await model.embed_query("test query")
            assert len(embedding) == 384
            assert all(isinstance(x, float) for x in embedding)

    @pytest.mark.asyncio
    async def test_embed_query_success_embeddings_format(self, embedding_config):
        """Test embedding query text successfully (embeddings format)"""
        mock_response = Mock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 384]}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config)
            embedding = await model.embed_query("test query")
            assert len(embedding) == 384

    @pytest.mark.asyncio
    async def test_embed_query_success_data_format(self, embedding_config):
        """Test embedding query text successfully (data format)"""
        mock_response = Mock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1] * 384}]}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config)
            embedding = await model.embed_query("test query")
            assert len(embedding) == 384

    @pytest.mark.asyncio
    async def test_embed_query_empty_text(self, embedding_config):
        """Test embedding empty text"""
        model = APIEmbedding(config=embedding_config)
        with pytest.raises(BaseError, match="Empty text provided"):
            await model.embed_query("   ")

    @pytest.mark.asyncio
    async def test_embed_query_retry_on_failure(self, embedding_config):
        """Test retry on request failure"""
        import requests

        mock_response_success = Mock()
        mock_response_success.json.return_value = {"embedding": [0.1] * 384}
        mock_response_success.raise_for_status = Mock()

        mock_response_failure = Mock()
        mock_response_failure.raise_for_status = Mock(
            side_effect=requests.exceptions.RequestException("Connection error")
        )

        with patch("asyncio.to_thread") as mock_to_thread:
            # First attempt fails, second succeeds
            mock_to_thread.side_effect = [
                mock_response_failure,
                mock_response_success,
            ]

            model = APIEmbedding(config=embedding_config, max_retries=3)
            embedding = await model.embed_query("test query")
            assert len(embedding) == 384
            assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_query_max_retries_exceeded(self, embedding_config):
        """Test exceeding maximum retry count"""
        import requests

        mock_response = Mock()
        mock_response.raise_for_status = Mock(side_effect=requests.exceptions.RequestException("Connection error"))

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config, max_retries=2)
            with pytest.raises(BaseError, match="Failed to get embedding"):
                await model.embed_query("test query")
            assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_query_invalid_response_format(self, embedding_config):
        """Test invalid response format"""
        mock_response = Mock()
        mock_response.json.return_value = {"invalid": "format"}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config)
            with pytest.raises(BaseError, match="No embeddings in response"):
                await model.embed_query("test query")

    @pytest.mark.asyncio
    async def test_embed_documents_success(self, embedding_config):
        """Test embedding document list successfully"""
        mock_response = Mock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 384, [0.2] * 384, [0.3] * 384]}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config)
            texts = ["text 1", "text 2", "text 3"]
            embeddings = await model.embed_documents(texts)
            assert len(embeddings) == 3
            assert all(len(emb) == 384 for emb in embeddings)

    @pytest.mark.asyncio
    async def test_embed_documents_with_batching(self, embedding_config):
        """Test batch embedding documents"""
        mock_response = Mock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 384]}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config, max_batch_size=1)
            texts = ["text 1", "text 2", "text 3", "text 4"]
            embeddings = await model.embed_documents(texts, batch_size=1)
            # Should process in 4 batches
            assert mock_to_thread.call_count == 4
            assert len(embeddings) == 4

    @pytest.mark.asyncio
    async def test_embed_documents_respects_max_batch_size(self, embedding_config):
        """Test respecting maximum batch size"""
        mock_response = Mock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 384]}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config, max_batch_size=2)
            texts = ["text 1", "text 2", "text 3"]
            # Even if batch_size=5 is specified, it should be limited to max_batch_size=2
            await model.embed_documents(texts, batch_size=5)
            # Should process in 2 batches (2 + 1)
            assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_embed_documents_empty_list(self, embedding_config):
        """Test embedding empty list"""
        model = APIEmbedding(config=embedding_config)
        with pytest.raises(BaseError, match="Empty texts list provided"):
            await model.embed_documents([])

    @pytest.mark.asyncio
    async def test_embed_documents_with_empty_texts(self, embedding_config):
        """Test list containing empty texts"""
        model = APIEmbedding(config=embedding_config)
        with pytest.raises(BaseError, match="chunks are empty"):
            await model.embed_documents(["text 1", "   ", "text 2"])

    @pytest.mark.asyncio
    async def test_embed_documents_all_empty(self, embedding_config):
        """Test all texts are empty"""
        model = APIEmbedding(config=embedding_config)
        with pytest.raises(BaseError):
            await model.embed_documents(["   ", "  ", ""])

    @pytest.mark.asyncio
    async def test_dimension_from_response(self, embedding_config):
        """Test getting dimension from response"""
        mock_response = Mock()
        mock_response.json.return_value = {"embedding": [0.1] * 768}
        mock_response.raise_for_status = Mock()

        with patch("asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = mock_response

            model = APIEmbedding(config=embedding_config)
            # First call embed_query to set dimension
            await model.embed_query("test")
            # Dimension should have been retrieved from response
            assert model.dimension == 768


class _RecordingAgentHandler(TraceExtAgentHandler):
    """Minimal tracer agent handler recording on_embedding_* events.

    The remaining abstract event methods are no-op stubs — these tests only
    observe the embedding trio fired by APIEmbedding.
    """

    def __init__(self):
        super().__init__()
        self.events = []

    async def on_embedding_start(self, span, inputs, instance_info, **kwargs):
        self.events.append(("start", dict(inputs), dict(instance_info)))

    async def on_embedding_end(self, span, outputs, **kwargs):
        self.events.append(("end", dict(outputs)))

    async def on_embedding_error(self, span, error, **kwargs):
        self.events.append(("error", type(error).__name__))

    async def on_llm_start(self, **kw):
        pass

    async def on_llm_request(self, **kw):
        pass

    async def on_llm_end(self, **kw):
        pass

    async def on_llm_error(self, **kw):
        pass

    async def on_plugin_start(self, **kw):
        pass

    async def on_plugin_end(self, **kw):
        pass

    async def on_plugin_error(self, **kw):
        pass

    async def on_prompt_start(self, **kw):
        pass

    async def on_prompt_end(self, **kw):
        pass

    async def on_prompt_error(self, **kw):
        pass

    async def on_chain_start(self, **kw):
        pass

    async def on_chain_end(self, **kw):
        pass

    async def on_chain_error(self, **kw):
        pass

    async def on_retriever_start(self, **kw):
        pass

    async def on_retriever_end(self, **kw):
        pass

    async def on_retriever_error(self, **kw):
        pass

    async def on_evaluator_start(self, **kw):
        pass

    async def on_evaluator_end(self, **kw):
        pass

    async def on_evaluator_error(self, **kw):
        pass

    async def on_workflow_start(self, **kw):
        pass

    async def on_workflow_end(self, **kw):
        pass

    async def on_workflow_error(self, **kw):
        pass


class TestAPIEmbeddingTraceEvents:
    """Embedding calls fire on_embedding_* tracer events under a traced session."""

    @pytest.fixture
    def traced_env(self, monkeypatch):
        """Wire a real Tracer + recording handler into the session contextvar."""
        from types import SimpleNamespace

        from openjiuwen.core.session.tracer.tracer import Tracer, TracerHandlerRegistry

        recorder = _RecordingAgentHandler()
        saved_agent = TracerHandlerRegistry.get_agent_handlers()
        saved_workflow = TracerHandlerRegistry.get_workflow_handlers()
        TracerHandlerRegistry.register_handler("embed_recorder", recorder)
        tracer = Tracer(session_id="embed-trace-test")
        tracer.init()
        stub_session = SimpleNamespace(tracer=lambda: tracer)
        monkeypatch.setattr(
            "openjiuwen.core.retrieval.embedding.api_embedding.get_current_session",
            lambda: stub_session,
        )
        try:
            yield recorder
        finally:
            # Restore the pre-test global registry instead of leaving it empty:
            # clearing in teardown would drop handlers registered by other
            # test modules and couple this suite to execution order.
            TracerHandlerRegistry.clear()
            for name, saved in saved_agent.items():
                TracerHandlerRegistry.register_handler(name, saved)
            for name, saved in saved_workflow.items():
                TracerHandlerRegistry.register_handler(name, saved)

    @pytest.mark.asyncio
    async def test_embed_query_fires_start_and_end(self, embedding_config, traced_env):
        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2, 0.3]}],
            "model": "test-model-20260101",
            "usage": {"prompt_tokens": 5},
        }
        mock_response.raise_for_status = Mock()

        with patch(
            "openjiuwen.core.retrieval.embedding.api_embedding.requests.post",
            return_value=mock_response,
        ):
            model = APIEmbedding(config=embedding_config)
            vector = await model.embed_query("hello")

        assert vector == [0.1, 0.2, 0.3]
        events = traced_env.events
        assert [e[0] for e in events] == ["start", "end"]
        assert events[0][1]["text"] == "hello"
        assert events[0][2]["model_name"] == "test-model"
        assert events[0][2]["class_name"] == "APIEmbedding"
        end_outputs = events[1][1]
        assert end_outputs["vector_count"] == 1
        assert end_outputs["dimension"] == 3
        assert end_outputs["response_model"] == "test-model-20260101"
        assert end_outputs["usage_input_tokens"] == 5

    @pytest.mark.asyncio
    async def test_no_session_skips_events(self, embedding_config, monkeypatch):
        from openjiuwen.core.session.tracer.tracer import Tracer

        fired = []

        async def _spy_trigger(*args, **kwargs):
            fired.append((args, kwargs))

        monkeypatch.setattr(Tracer, "trigger", _spy_trigger)
        monkeypatch.setattr(
            "openjiuwen.core.retrieval.embedding.api_embedding.get_current_session",
            lambda: None,
        )
        mock_response = Mock()
        mock_response.json.return_value = {"embedding": [0.1, 0.2]}
        mock_response.raise_for_status = Mock()

        with patch(
            "openjiuwen.core.retrieval.embedding.api_embedding.requests.post",
            return_value=mock_response,
        ):
            model = APIEmbedding(config=embedding_config)
            assert await model.embed_query("hello") == [0.1, 0.2]
        # "Skips events" is observable, not just "did not crash": no tracer
        # event may fire anywhere along the call path.
        assert fired == []

    @pytest.mark.asyncio
    async def test_embed_failure_fires_error(self, embedding_config, traced_env):
        import requests as requests_mod

        def _raise(*args, **kwargs):
            raise requests_mod.exceptions.ConnectionError("down")

        with patch(
            "openjiuwen.core.retrieval.embedding.api_embedding.requests.post",
            side_effect=_raise,
        ):
            model = APIEmbedding(config=embedding_config, max_retries=1)
            with pytest.raises(BaseError):
                await model.embed_query("hello")

        assert [e[0] for e in traced_env.events] == ["start", "error"]
        # build_error(RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED) maps to FrameworkError
        assert traced_env.events[-1][1] == "FrameworkError"
