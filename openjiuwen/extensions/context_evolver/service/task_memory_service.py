# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Task memory service implementation."""

from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error, ToolchainError, ValidationError as _ValidationError
# Import openjiuwen core LLM
from openjiuwen.core.foundation.llm import OpenAIModelClient, ModelClientConfig, ModelRequestConfig
# Import openjiuwen core Embedding
from openjiuwen.core.retrieval import OpenAIEmbedding as CoreOpenAIEmbedding, EmbeddingConfig

from ..core import config
from ..core.context import RuntimeContext, ServiceContext
from ..core.vector_store import MemoryVectorStore
from ..core.persistence import MemoryPersistenceHelper
from ..core.schema import VectorNode



# Import ACE operations
from ..retrieve.task.ace import RecallMemoryOp as ACERecallMemoryOp
from ..summary.task.ace import (
    LoadPlaybookOp as ACELoadPlaybookOp,
    ReflectOp as ACEReflectOp,
    ParallelReflectOp as ACEParallelReflectOp,
    CurateOp as ACECurateOp,
    ParallelCurateOp as ACEParallelCurateOp,
    ApplyDeltaOp as ACEApplyDeltaOp,
    PersistMemoryOp as ACEPersistMemoryOp,
)

# Import ReasoningBank operations
from ..retrieve.task.reasoning_bank import RecallMemoryOp as RBRecallMemoryOp
from ..summary.task.reasoning_bank import (
    SummarizeMemoryOp as RBSummarizeMemoryOp,
    SummarizeMemoryParallelOp as RBSummarizeMemoryParallelOp,
    UpdateVectorStoreOp as RBUpdateVectorStoreOp,
    PersistMemoryOp as RBPersistMemoryOp,
)

# Import ReMe operations
from ..retrieve.task.reme import (
    RecallMemoryOp as ReMeRecallMemoryOp,
    RerankMemoryOp as ReMeRerankMemoryOp,
    RewriteMemoryOp as ReMeRewriteMemoryOp,
)
from ..summary.task.reme import (
    TrajectoryPreprocessOp as ReMeTrajectoryPreprocessOp,
    SuccessExtractionOp as ReMeSuccessExtractionOp,
    FailureExtractionOp as ReMeFailureExtractionOp,
    ComparativeExtractionOp as ReMeComparativeExtractionOp,
    ComparativeAllExtractionOp as ReMeComparativeAllExtractionOp,
    MemoryValidationOp as ReMeMemoryValidationOp,
    MemoryDeduplicationOp as ReMeMemoryDeduplicationOp,
    UpdateVectorStoreOp as ReMeUpdateVectorStoreOp,
    PersistMemoryOp as ReMePersistMemoryOp,
)

from ..schema import SummarizeResponse, RetrieveResponse
from ..core.op import SequentialOp


@dataclass
class AddMemoryRequest:
    """Request for adding a memory.

    Attributes:
        content: Memory content (required)
        query: Query for embedding (ReasoningBank only)
        when_to_use: When to use this memory (ReMe only)
        title: Memory title (ReasoningBank only)
        description: Memory description (ReasoningBank only)
        section: Memory section/category (ACE only), defaults to "general"
        label: Memory label (ReasoningBank only)
    """
    content: str
    query: Optional[str] = None
    when_to_use: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    section: str = "general"
    label: Optional[str] = None


class OpenAILLMWrapper:
    """Wrapper for OpenAIModelClient that provides the same interface as the old OpenAILLM."""

    def __init__(
        self,
        model_name: str = "gpt-5.2",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ):
        """Initialize OpenAI LLM wrapper.

        Args:
            model_name: Model name (e.g., 'gpt-5.2', 'gpt-3.5-turbo')
            api_key: OpenAI API key
            base_url: Base URL for API
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
        """
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Check if it's a newer model that requires max_completion_tokens instead of max_tokens
        model_lower = model_name.lower()
        self._is_newer_model = (
            "gpt-4" in model_lower or
            "gpt-5" in model_lower or
            "o1" in model_lower or
            "o3" in model_lower
        )

        api_key = api_key or config.get("API_KEY")
        if not api_key:
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_CONFIG_INVALID,
                error_msg="API key not provided and API_KEY not set in config.yaml",
            )

        base_url = base_url or "https://api.openai.com/v1"

        # Create model client config
        self.model_client_config = ModelClientConfig(
            client_provider="OpenAI",
            api_key=api_key,
            api_base=base_url,
            verify_ssl=False,
        )

        # Create model request config
        self.model_request_config = ModelRequestConfig(
            model_name=model_name,
            temperature=temperature,
        )

        # Create the model client
        self.client = OpenAIModelClient(
            model_config=self.model_request_config,
            model_client_config=self.model_client_config,
        )

        logger.info("Initialized OpenAI LLM with model: %s", model_name)

    async def async_generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a response from the LLM.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Override default temperature
            max_tokens: Override default max_tokens

        Returns:
            Generated text response
        """
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        try:
            # Build invoke kwargs - don't pass max_tokens for newer models
            # as they require max_completion_tokens which the client may not support
            invoke_kwargs = {
                "messages": messages,
                "model": self.model_name,
                "temperature": temperature or self.temperature,
            }
            if not self._is_newer_model:
                invoke_kwargs["max_tokens"] = max_tokens or self.max_tokens

            response = await self.client.invoke(**invoke_kwargs)

            content = response.content or ""
            logger.debug("LLM generated %s characters", len(content))
            return content

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("LLM generation failed: %s", e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_LLM_GENERATION_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    async def async_generate_with_messages(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a response from the LLM using message list.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Override default temperature
            max_tokens: Override default max_tokens

        Returns:
            Generated text response
        """
        try:
            # Build invoke kwargs - don't pass max_tokens for newer models
            # as they require max_completion_tokens which the client may not support
            invoke_kwargs = {
                "messages": messages,
                "model": self.model_name,
                "temperature": temperature or self.temperature,
            }
            if not self._is_newer_model:
                invoke_kwargs["max_tokens"] = max_tokens or self.max_tokens

            response = await self.client.invoke(**invoke_kwargs)

            content = response.content or ""
            logger.debug("LLM generated %s characters", len(content))
            return content

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("LLM generation failed: %s", e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_LLM_GENERATION_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    def __repr__(self) -> str:
        """String representation."""
        return f"OpenAILLMWrapper(model={self.model_name})"


class OpenAIEmbeddingWrapper:
    """Wrapper for CoreOpenAIEmbedding that provides the same interface as the old OpenAIEmbedding."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """Initialize OpenAI embedding model wrapper.

        Args:
            model_name: Model name (e.g., 'text-embedding-3-small', 'text-embedding-ada-002')
            api_key: API key
            base_url: Base URL for API
        """
        self.model_name = model_name

        api_key = api_key or config.get("API_KEY")
        if not api_key:
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_CONFIG_INVALID,
                error_msg="API key not provided and API_KEY not set in config.yaml",
            )

        base_url = base_url or "https://api.openai.com/v1"

        # Create embedding config
        embedding_config = EmbeddingConfig(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
        )

        # Create the embedding client
        self.client = CoreOpenAIEmbedding(
            config=embedding_config,
            verify=False,
        )

        logger.info("Initialized OpenAI Embedding with model: %s", model_name)

    async def async_embed(self, text: str) -> List[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        try:
            embedding = await self.client.embed_query(text)
            logger.debug("Generated embedding of dimension %s", len(embedding))
            return embedding

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("Embedding generation failed: %s", e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_EMBEDDING_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    async def async_embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        try:
            embeddings = await self.client.embed_documents(texts)
            logger.debug("Generated %s embeddings", len(embeddings))
            return embeddings

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("Batch embedding generation failed: %s", e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_EMBEDDING_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    def __repr__(self) -> str:
        """String representation."""
        return f"OpenAIEmbeddingWrapper(model={self.model_name})"


class TaskMemoryService:
    """Service for task memory retrieval and summarization.

    This service provides a high-level interface for:
    - Retrieving memories to answer queries
    - Summarizing trajectories into memories
    - Managing memory storage

    Configuration is loaded from config.yaml file or a custom configuration file.
    Supports separate algorithms for retrieval and summary via RETRIEVAL_ALGO and SUMMARY_ALGO.
    """

    def __init__(
        self,
        llm_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
        api_key: Optional[str] = None,
        retrieval_algo: Optional[str] = None,
        summary_algo: Optional[str] = None,
        config_path: Optional[str] = None,
        persist_type: str = "json",
        persist_path: str = "./memories/{algo_name}/{user_id}.json",
        milvus_host: Optional[str] = "localhost",
        milvus_port: Optional[int] = 19530,
        milvus_collection: Optional[str] = "vector_nodes",
    ):
        """Initialize task memory service.

        Args:
            llm_model: LLM model name (defaults to MODEL_NAME or LLM_MODEL from config file)
            embedding_model: Embedding model name (defaults to EMBEDDING_MODEL from config file)
            api_key: API key (defaults to API_KEY from config file)
            retrieval_algo: Algorithm for retrieval (defaults to RETRIEVAL_ALGO from config file, or ACE)
            summary_algo: Algorithm for summary (defaults to SUMMARY_ALGO from config file, or ACE)
            config_path: Path to custom configuration file (defaults to config.yaml in context_evolver root)
            persist_type: Persistence backend for the summary workflow.
                ``None`` (default) disables persistence entirely.
                ``"auto"`` probes Milvus first; falls back to JSON if
                Milvus is unreachable.  ``"json"`` or ``"milvus"`` force
                a specific backend.  Can also be set via ``PERSIST_TYPE``
                in config.yaml.
            persist_path: File-path template for the JSON backend.  ``{user_id}`` and
                ``{algo_name}`` are substituted at runtime.
                Default: ``"./memories/{algo_name}/{user_id}.json"``.
                Can also be set via ``PERSIST_PATH`` in config.yaml.
            milvus_host: Milvus server hostname (default: ``"localhost"``).
            milvus_port: Milvus gRPC port (default: ``19530``).
            milvus_collection: Milvus collection name (default: ``"vector_nodes"``).
        """
        try:
            # Load configuration from custom path if provided
            if config_path is not None:
                config.load(config_path)

            self.service_context = ServiceContext()

            # Load configuration from config file
            llm_model = llm_model or config.get("MODEL_NAME") or config.get("LLM_MODEL", "gpt-5.2")
            embedding_model = embedding_model or config.get("EMBEDDING_MODEL", "text-embedding-3-small")
            api_key = api_key or config.get("API_KEY")
            api_base = config.get("API_BASE")

            # Initialize services using wrappers
            logger.info("Initializing TaskMemoryService...")
            logger.info("Configuration: llm_model=%s, embedding_model=%s", llm_model, embedding_model)

            self.llm = OpenAILLMWrapper(model_name=llm_model, api_key=api_key, base_url=api_base)
            self.embedding = OpenAIEmbeddingWrapper(model_name=embedding_model, api_key=api_key, base_url=api_base)
            self.vector_store = MemoryVectorStore()

            # Register services in context
            self.service_context.register_service("llm", self.llm)
            self.service_context.register_service("embedding_model", self.embedding)
            self.service_context.register_service("vector_store", self.vector_store)

            # Get algorithm selection from config.yaml
            retrieval_algo = (retrieval_algo or config.get("RETRIEVAL_ALGO", "ACE")).upper()
            summary_algo = (summary_algo or config.get("SUMMARY_ALGO", "ACE")).upper()

            logger.info("Selected algorithms - Retrieval: %s, Summary: %s", retrieval_algo, summary_algo)

            # Store algorithm selections
            self.retrieval_algorithm = self.normalize_algo_name(retrieval_algo)
            self.summary_algorithm = self.normalize_algo_name(summary_algo)

            # Persistence configuration (passed through to PersistMemoryOp in summary flows)
            self._persist_type: Optional[str] = persist_type or config.get("PERSIST_TYPE")
            self._persist_path: str = persist_path or config.get(
                "PERSIST_PATH", "./memories/{algo_name}/{user_id}.json"
            )
            self._milvus_host: str = milvus_host
            self._milvus_port: int = milvus_port
            self._milvus_collection: str = milvus_collection

            if self._persist_type:
                logger.info(
                    "Memory persistence enabled: type=%s, path=%s",
                    self._persist_type, self._persist_path,
                )
                self._persistence_helper: Optional[MemoryPersistenceHelper] = MemoryPersistenceHelper(
                    persist_type=self._persist_type,
                    persist_path=self._persist_path,
                    milvus_host=self._milvus_host,
                    milvus_port=self._milvus_port,
                    milvus_collection=self._milvus_collection,
                )
            else:
                self._persistence_helper = None

            # Create retrieve flow based on retrieval algorithm
            self.retrieve_flow = self._create_retrieve_flow()

            # Create summary flow based on summary algorithm
            self.summary_flow = self._create_summary_flow()

            logger.info(
                "TaskMemoryService initialized successfully with retrieval=%s, summary=%s",
                self.retrieval_algorithm, self.summary_algorithm
            )
        except ToolchainError:
            raise
        except Exception as e:
            logger.error("TaskMemoryService initialization failed: %s", e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_SERVICE_INIT_FAILED,
                error_msg=str(e),
                cause=e,
            )

    # ------------------------------------------------------------------
    # Public read-only accessors for persistence configuration
    # ------------------------------------------------------------------

    @property
    def persist_type(self) -> Optional[str]:
        """The configured persistence backend type, or ``None`` if disabled."""
        return self._persist_type

    @property
    def persist_path(self) -> str:
        """File-path template for the JSON persistence backend."""
        return self._persist_path

    @property
    def persistence_helper(self) -> Optional["MemoryPersistenceHelper"]:
        """The active ``MemoryPersistenceHelper``, or ``None`` if persistence is disabled."""
        return self._persistence_helper

    @property
    def milvus_host(self) -> str:
        """Milvus server hostname."""
        return self._milvus_host

    @property
    def milvus_port(self) -> int:
        """Milvus gRPC port."""
        return self._milvus_port

    @property
    def milvus_collection(self) -> str:
        """Milvus collection name."""
        return self._milvus_collection

    def reconfigure(self, algorithm: str) -> None:
        """Switch both retrieval and summary algorithms at runtime.

        Rebuilds the retrieve and summary flows so subsequent calls to
        :meth:`retrieve` and :meth:`summarize` use the new algorithm
        immediately.  The underlying LLM, embedding model, and vector store
        are not re-created.

        Args:
            algorithm: Target algorithm.  Accepted values (case-insensitive):
                ``ACE``, ``ReasoningBank`` (alias ``RB``), ``ReMe``,
                ``RefCon``, ``DivCon``.

        Raises:
            ValueError: If *algorithm* is not one of the accepted values.
        """
        normalized = self.normalize_algo_name(algorithm.upper())
        self.retrieval_algorithm = normalized
        self.summary_algorithm = normalized
        self.retrieve_flow = self._create_retrieve_flow()
        self.summary_flow = self._create_summary_flow()  # re-creates PersistMemoryOp with stored params
        logger.info(
            "TaskMemoryService reconfigured: retrieval=%s, summary=%s",
            self.retrieval_algorithm, self.summary_algorithm,
        )

    @staticmethod
    def normalize_algo_name(algo: str) -> str:
        """Normalize algorithm name to standard format.

        Args:
            algo: Raw algorithm name (ACE, RB, REASONINGBANK, REME, etc.)

        Returns:
            Normalized algorithm name (ACE, ReasoningBank, or ReMe)
        """
        algo_map = {
            "RB": "ReasoningBank",
            "REASONINGBANK": "ReasoningBank",
            "REME": "ReMe",
            "ACE": "ACE",
            "REFCON": "RefCon",
            "DIVCON": "DivCon",
        }
        normalized = algo_map.get(algo.upper())
        if normalized is None:
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_CONFIG_INVALID,
                error_msg=f"Invalid algorithm '{algo}'. Must be one of: ACE, ReasoningBank, ReMe, RefCon, DivCon.",
            )
        return normalized

    def _create_retrieve_flow(self):
        """Create retrieve flow based on configured algorithm.

        Returns:
            Configured retrieve flow
        """
        if self.retrieval_algorithm == "ReasoningBank":
            logger.info("Using ReasoningBank algorithm for retrieval")
            topk_query = int(config.get("TOPK_QUERY", 1))
            return RBRecallMemoryOp(top_k=topk_query)
        elif self.retrieval_algorithm == "ACE":
            logger.info("Using ACE algorithm for retrieval")
            return ACERecallMemoryOp()
        elif self.retrieval_algorithm == "ReMe":
            logger.info("Using ReMe algorithm for retrieval")
            topk_retrieval = int(config.get("TOPK_RETRIEVAL", 10))
            topk_rerank = int(config.get("TOPK_RERANK", 5))
            llm_rerank = config.get("LLM_RERANK", True)
            llm_rewrite = config.get("LLM_REWRITE", True)
            return (
                ReMeRecallMemoryOp(topk_retrieval=topk_retrieval) >>
                ReMeRerankMemoryOp(llm_rerank=llm_rerank, topk_rerank=topk_rerank) >>
                ReMeRewriteMemoryOp(llm_rewrite=llm_rewrite)
            )
        elif self.retrieval_algorithm == "RefCon" or self.retrieval_algorithm == "DivCon":
            logger.info("Using %s algorithm for retrieval", self.retrieval_algorithm)
            return (
                # The values (topk_retrieval, llm_rerank, topk_rerank, llm_rewrite) 
                # are hardcodes because thay are the best parameters found through experiments.
                ReMeRecallMemoryOp(topk_retrieval=10) >>
                ReMeRerankMemoryOp(llm_rerank=True, topk_rerank=5) >>
                ReMeRewriteMemoryOp(llm_rewrite=True)
            )
        else:
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_CONFIG_INVALID,
                error_msg=(
                    f"Invalid retrieval algorithm '{self.retrieval_algorithm}'. "
                    "Must be one of: ACE, ReasoningBank, ReMe, RefCon, DivCon."
                ),
            )
            return None

    def _persist_op(self, algo_cls):
        """Return a configured PersistMemoryOp instance, or None if persistence is off."""
        if not self._persist_type:
            return None
        return algo_cls(
            persist_type=self._persist_type,
            persist_path=self._persist_path,
            milvus_host=self._milvus_host,
            milvus_port=self._milvus_port,
            milvus_collection=self._milvus_collection,
        )

    def _create_summary_flow(self) -> SequentialOp:
        """Create summary flow based on configured algorithm.

        When ``persist_type`` is set a ``PersistMemoryOp`` is automatically
        appended at the end of the pipeline so memories are saved to the
        configured backend after every :meth:`summarize` call.

        Returns:
            SequentialOp configured for the algorithm
        """
        if self.summary_algorithm == "ACE":
            logger.info("Using ACE algorithm for summary")
            use_ground_truth = config.get("USE_GROUNDTRUTH", False)
            max_playbook_size = int(config.get("MAX_PLAYBOOK_SIZE", 50))
            flow = (
                ACELoadPlaybookOp() >>
                (ACEReflectOp(use_ground_truth=use_ground_truth) |
                 ACEParallelReflectOp(use_ground_truth=use_ground_truth)) >>
                (ACECurateOp() | ACEParallelCurateOp()) >>
                ACEApplyDeltaOp(max_bullets=max_playbook_size)
            )
            persist = self._persist_op(ACEPersistMemoryOp)
            return flow >> persist if persist else flow

        elif self.summary_algorithm == "ReasoningBank":
            logger.info("Using ReasoningBank algorithm for summary")
            # USE_GOLDLABEL implemented automatically by checking whether label is available or not in the context
            # Since ReasoningBank without MaTTS setting must have label, so if label not provided it use LLM-as-judge
            flow = (
                (RBSummarizeMemoryOp() | RBSummarizeMemoryParallelOp()) >>
                RBUpdateVectorStoreOp()
            )
            persist = self._persist_op(RBPersistMemoryOp)
            return flow >> persist if persist else flow

        elif self.summary_algorithm == "ReMe":
            logger.info("Using ReMe algorithm for summary")
            extract_best_traj = config.get("EXTRACT_BEST_TRAJ", True)
            extract_worst_traj = config.get("EXTRACT_WORST_TRAJ", True)
            extract_comparative_traj = config.get("EXTRACT_COMPARATIVE_TRAJ", True)
            memory_validation = config.get("MEMORY_VALIDATION", True)
            memory_deduplication = config.get("MEMORY_DEDUPLICATION", True)
            flow = (
                ReMeTrajectoryPreprocessOp() >>
                (ReMeSuccessExtractionOp(use_extraction=extract_best_traj) |
                 ReMeFailureExtractionOp(use_extraction=extract_worst_traj) |
                 ReMeComparativeExtractionOp(use_extraction=extract_comparative_traj)) >>
                ReMeMemoryValidationOp(use_validation=memory_validation) >>
                ReMeMemoryDeduplicationOp(use_deduplication=memory_deduplication) >>
                ReMeUpdateVectorStoreOp()
            )
            persist = self._persist_op(ReMePersistMemoryOp)
            return flow >> persist if persist else flow

        elif self.summary_algorithm == "RefCon" or self.summary_algorithm == "DivCon":
            logger.info("Using %s algorithm for summary", self.summary_algorithm)
            flow = (
                # The values are hardcoded because they are the best parameters found through experiments.
                ReMeTrajectoryPreprocessOp() >>
                ReMeComparativeAllExtractionOp(use_extraction=True) >>
                ReMeMemoryValidationOp(use_validation=False) >>
                ReMeMemoryDeduplicationOp(use_deduplication=True) >>
                ReMeUpdateVectorStoreOp()
            )
            persist = self._persist_op(ReMePersistMemoryOp)
            return flow >> persist if persist else flow

        else:
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_CONFIG_INVALID,
                error_msg=(
                    f"Invalid summary algorithm '{self.summary_algorithm}'. "
                    "Must be one of: ACE, ReasoningBank, ReMe, RefCon, DivCon."
                ),
            )
            return None

    def load_memories(self, user_id: str) -> None:
        """Load persisted memories into the in-memory vector store.

        Reads nodes from the persistence backend (JSON or Milvus) and inserts
        them into ``self.vector_store`` so that subsequent :meth:`retrieve` and
        :meth:`get_playbook` calls work without requiring a summarize run first.

        This is a no-op when persistence is disabled.

        Args:
            user_id: User identifier whose memories should be loaded.
        """
        if self._persistence_helper is None:
            return
        try:
            algo_to_name = {
                "ACE": "ace",
                "ReasoningBank": "rb",
                "ReMe": "reme",
                "RefCon": "reme",
                "DivCon": "reme",
            }
            algo_name = algo_to_name.get(self.summary_algorithm, "ace")
            data = self._persistence_helper.load(user_id, algo_name)
            if not data:
                return
            count = 0
            for node_id, node_data in data.items():
                try:
                    node = VectorNode.from_dict(node_data)
                    if hasattr(self.vector_store, "load_node"):
                        self.vector_store.load_node(node_id, node)
                        count += 1
                except Exception as err:
                    logger.warning("Failed to load node %s: %s", node_id, err)
            logger.info(
                "Loaded %d memories into vector store (algo=%s)",
                count, algo_name,
            )
        except Exception as exc:
            logger.error("Failed to load existing memories for user=%s: %s", user_id, exc)

    async def retrieve(
        self,
        user_id: str,
        query: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Retrieve task memory to answer a query.

        Args:
            user_id: User/workspace identifier
            query: Query to answer
            **kwargs: Additional parameters

        Returns:
            Dictionary with:
                - answer: Generated answer
                - query: Original query
                - user_id: User identifier
                - memories_used: Number of memories used
        """
        logger.info("Retrieving task memory for user=%s, query='%s...'", user_id, query[:50])

        try:
            # Create runtime context
            context = RuntimeContext()
            context.user_id = user_id
            context.query = query
            for key, value in kwargs.items():
                setattr(context, key, value)

            # Execute retrieve flow
            await self.retrieve_flow(context)

            # Get retrieved memories from context (algorithm-specific format)
            retrieved_memories = context.get("retrieved_memories", [])

            # Format memory string (basic implementation, can be customized per algorithm)
            memory_string = ""
            if retrieved_memories:
                if self.retrieval_algorithm == "ReasoningBank":
                    # ReasoningBankRetrievedMemory has title, description, content directly
                    memory_items = []
                    for mem in retrieved_memories:
                        if hasattr(mem, 'title') and hasattr(mem, 'description') and hasattr(mem, 'content'):
                            memory_items.append(
                                f"Title: {mem.title}\nDescription: {mem.description}\n"
                                f"Content: {mem.content}"
                            )
                    memory_string = "\n\n".join(memory_items)
                elif self.retrieval_algorithm == "ACE":
                    memory_items = [
                        f"[{mem.id}] helpful={mem.helpful} harmful={mem.harmful} neutral={mem.neutral}\n"
                        f"Section: {mem.section}\n"
                        f"Content: {mem.content}"
                        for mem in retrieved_memories
                    ]
                    memory_string = "\n\n".join(memory_items)
                else:  # ReMe or RefCon or DivCon
                    memory_string = context.get("memory_string", "")

            result = RetrieveResponse(
                status="success",
                memory_string=memory_string,
                retrieved_memory=retrieved_memories
            )

            logger.info("Retrieved memories:\n%s\nUsing %s memories", memory_string, len(retrieved_memories))
            try:
                return result.model_dump()
            except AttributeError:
                return result()

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("Memory retrieval failed for user=%s: %s", user_id, e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_RETRIEVE_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    async def summarize(
        self,
        user_id: str,
        matts: str,
        query: str,
        trajectories: list,
        **kwargs
    ) -> Dict[str, Any]:
        """Summarize trajectories into task memories.

        Args:
            user_id: User/workspace identifier
            trajectories: List of trajectory dicts with query, response, feedback
            **kwargs: Additional parameters

        Returns:
            Dictionary with:
                - status: Success status
                - user_id: User identifier
                - trajectories_processed: Number of trajectories processed
                - memories_added: Number of memories added
                - playbook_size: Current playbook size
        """

        logger.info("Summarizing %s trajectories for user=%s", len(trajectories), user_id)

        try:
            # Create runtime context
            context = RuntimeContext()
            context.user_id = user_id
            context.matts = matts # or use config.get("MATTS_DEFAULT_MODE")
            context.query = query
            context.trajectories = trajectories
            for key, value in kwargs.items():
                setattr(context, key, value)

            # Execute summary flow
            await self.summary_flow(context)

            memories = context.get("memories", [])
            result = SummarizeResponse(
                status="success",
                memory=memories
            )

            # Log based on algorithm type
            if self.summary_algorithm == "ReasoningBank":
                logger.info(
                    "Summarized %s trajectories into %s memories",
                    len(trajectories), len(memories[0].memory) if memories else 0
                )
            else:
                logger.info(
                    "Summarized %s trajectories into %s memories",
                    len(trajectories), len(memories) if memories else 0
                )
            try:
                return result.model_dump()
            except AttributeError:
                return result()

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("Memory summarization failed for user=%s: %s", user_id, e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_SUMMARIZE_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    async def add_memory(
        self,
        user_id: str,
        request: AddMemoryRequest,
    ) -> Dict[str, Any]:
        """Manually add a memory.

        Uses summary_algorithm to determine memory format since adding memory
        is a form of summarization/storage.

        Args:
            user_id: User/workspace identifier
            request: AddMemoryRequest containing memory parameters

        Returns:
            Dictionary with status and memory info
        """
        from ..schema import (
             ACEMemory,
             ReasoningBankMemory,
             ReasoningBankMemoryItem,
        )
        from datetime import datetime, timezone
        import hashlib

        logger.info("Adding manual %s memory for user=%s", self.summary_algorithm, user_id)

        try:
            # Create memory based on summary algorithm
            if self.summary_algorithm == "ReasoningBank":
                if not request.title or not request.description or not request.content:
                    raise_error(
                        StatusCode.TOOLCHAIN_EVOLVING_MEMORY_INPUT_INVALID,
                        error_msg="ReasoningBank requires 'title', 'description' and 'content' parameters",
                    )

                # Use description as default query if not provided (used as embedding index)
                effective_query = request.query if request.query else request.description

                # Create memory item
                memory_item = ReasoningBankMemoryItem(
                    title=request.title,
                    description=request.description,
                    content=request.content
                )

                # Create ReasoningBankMemory with query and memory list
                memory = ReasoningBankMemory(
                    workspace_id=user_id,
                    query=effective_query,
                    memory=[memory_item],
                    label=request.label  # Manual memories don't have labels
                )
            elif self.summary_algorithm in ("ReMe", "RefCon", "DivCon"):
                if not request.when_to_use:
                    raise_error(
                        StatusCode.TOOLCHAIN_EVOLVING_MEMORY_INPUT_INVALID,
                        error_msg="ReMe/RefCon/DivCon algorithm requires 'when_to_use' parameter",
                    )

                from ..schema import ReMeMemory, ReMeMemoryMetadata

                memory = ReMeMemory(
                    when_to_use=request.when_to_use,
                    content=request.content,
                    score=1.0,  # Manual memories get default score
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    metadata=ReMeMemoryMetadata(
                        tags=[],
                        step_type="manual",
                        tools_used=[],
                        confidence=1.0,
                        freq=1,
                        utility=1,
                    ),
                    workspace_id=user_id,
                )
            else:  # ACE
                if not request.section or not request.content:
                    raise_error(
                        StatusCode.TOOLCHAIN_EVOLVING_MEMORY_INPUT_INVALID,
                        error_msg="ACE algorithm requires 'content' and 'section' parameters",
                    )

                # Generate unique ID for ACE memory
                content_hash = hashlib.md5(request.content.encode()).hexdigest()[:8]
                memory_id = f"{request.section}-{content_hash}"

                memory = ACEMemory(
                    id=memory_id,
                    workspace_id=user_id,
                    section=request.section,
                    content=request.content,
                    helpful=0,
                    harmful=0,
                    neutral=0,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )

            # Convert to vector node
            vector_node = memory.to_vector_node()

            # Get embedding
            vector_node.embedding = await self.embedding.async_embed(vector_node.content)

            # Store in vector store
            await self.vector_store.async_upsert(vector_node)

            # Persist to external storage (Milvus or JSON fallback) if configured
            if self._persistence_helper is not None:
                algo_to_name = {
                    "ACE": "ace",
                    "ReasoningBank": "rb",
                    "ReMe": "reme",
                    "RefCon": "reme",
                    "DivCon": "reme",
                }
                algo_name = algo_to_name.get(self.summary_algorithm, "ace")
                self._persistence_helper.save(
                    user_id, algo_name, {vector_node.id: vector_node.to_dict()}
                )
                logger.info(
                    "Persisted %s memory %s (backend=%s)",
                    self.summary_algorithm, vector_node.id, self._persistence_helper.resolved_type,
                )

            logger.info("Added %s memory: %s", self.summary_algorithm, vector_node.id)

            return {
                "status": "success",
                "memory_id": vector_node.id,
                "user_id": user_id,
                "algorithm": self.summary_algorithm,
            }

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("Add memory failed for user=%s: %s", user_id, e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_ADD_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    async def get_playbook(self, user_id: str) -> Dict[str, Any]:
        """Get user's playbook (all memories).

        Args:
            user_id: User identifier

        Returns:
            Dictionary with user_id, memory_count, and all memories
        """
        try:
            # Get all vectors for this user
            all_vectors = self.vector_store.get_all(metadata_filter={"workspace_id": user_id})

            # Extract memory data from vector nodes
            memories = []
            for node in all_vectors:
                memory_data = {
                    "id": node.id,
                    "content": node.content,
                    **node.metadata
                }
                memories.append(memory_data)

            return {
                "user_id": user_id,
                "memory_count": len(memories),
                "memories": memories,
            }

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("Get playbook failed for user=%s: %s", user_id, e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_FETCH_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )

    async def clear_playbook(self, user_id: str) -> Dict[str, Any]:
        """Clear user's playbook.

        Args:
            user_id: User identifier

        Returns:
            Status message
        """
        logger.warning("Clearing playbook for user=%s", user_id)

        try:
            # In full implementation, would filter by user_id
            # For now, clear all
            self.vector_store.clear()

            return {
                "status": "success",
                "message": f"Cleared playbook for user {user_id}",
            }

        except ToolchainError:
            raise
        except Exception as e:
            logger.error("Clear playbook failed for user=%s: %s", user_id, e)
            raise_error(
                StatusCode.TOOLCHAIN_EVOLVING_MEMORY_CLEAR_EXECUTION_ERROR,
                error_msg=str(e),
                cause=e,
            )
