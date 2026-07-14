# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LakeBase Memory Provider - External memory backed by LakeBase (DBay).

LakeBase provides:
- Semantic memory storage and retrieval (pgvector)
- Multiple memory types (fact, episode, procedural, decision, rejection, convention)
- Trait extraction via digest API
- Copy-on-write branching for experimentation
- Version snapshots for rollback
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional, List, Dict

import httpx

from openjiuwen.core.common.logging import memory_logger as logger
from openjiuwen.core.memory.external.provider import MemoryProvider


# Default configuration
DEFAULT_BASE_URL = "http://localhost:8080/api/v1"
DEFAULT_TIMEOUT = 60.0
DEFAULT_PREFETCH_TIMEOUT = 5.0

# Memory type enumeration
MEMORY_TYPES = ["fact", "episode", "procedural", "decision", "rejection", "convention"]

# Tool schemas for LakeBase memory operations
LKB_MEMORY_SEARCH_SCHEMA = {
    "name": "lkb_memory_search",
    "description": "Search memories by semantic similarity. Can filter by memory type.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query text"
            },
            "top_k": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of results"
            },
            "memory_types": {
                "type": "array",
                "items": {"type": "string"},
                "enum": MEMORY_TYPES,
                "description": "Filter by memory types (optional)"
            },
        },
        "required": ["query"],
    },
}

LKB_MEMORY_ADD_SCHEMA = {
    "name": "lkb_memory_add",
    "description": "Store a new memory. Choose appropriate memory_type for better organization.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Memory content to store"
            },
            "memory_type": {
                "type": "string",
                "default": "fact",
                "enum": MEMORY_TYPES,
                "description": (
                    "Type of memory: fact (knowledge), episode (events), procedural (how-to),"
                    " decision (choices), rejection (avoid), convention (rules)"
                )
            },
            "importance": {
                "type": "number",
                "default": 0.5,
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Importance score (0-1), higher = more significant"
            },
            "metadata": {
                "type": "object",
                "description": "Optional structured metadata"
            },
        },
        "required": ["content"],
    },
}

LKB_MEMORY_LIST_SCHEMA = {
    "name": "lkb_memory_list",
    "description": "List memories with pagination. Optionally filter by type.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_type": {
                "type": "string",
                "enum": MEMORY_TYPES,
                "description": "Filter by memory type (optional)"
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "description": "Pagination offset"
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "maximum": 100,
                "description": "Page size"
            },
        },
        "required": [],
    },
}

LKB_MEMORY_GET_SCHEMA = {
    "name": "lkb_memory_get",
    "description": "Get a single memory by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "Memory ID to retrieve"
            },
        },
        "required": ["memory_id"],
    },
}

LKB_MEMORY_DELETE_SCHEMA = {
    "name": "lkb_memory_delete",
    "description": "Delete a memory by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "integer",
                "description": "Memory ID to delete"
            },
        },
        "required": ["memory_id"],
    },
}

LKB_MEMORY_DIGEST_SCHEMA = {
    "name": "lkb_memory_digest",
    "description": "Run reflection to extract behavioral traits from accumulated memories.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

LKB_MEMORY_TRAITS_SCHEMA = {
    "name": "lkb_memory_traits",
    "description": "List all discovered behavioral traits.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

LKB_MEMORY_STATS_SCHEMA = {
    "name": "lkb_memory_stats",
    "description": "Get memory base statistics (count, types, etc.).",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

LKB_MEMORY_SWITCH_BASE_SCHEMA = {
    "name": "lkb_memory_switch_base",
    "description": "Switch to a different memory base (workspace).",
    "parameters": {
        "type": "object",
        "properties": {
            "base_id": {
                "type": "string",
                "description": "Target memory base ID to switch to"
            },
        },
        "required": ["base_id"],
    },
}

# === Branching Operations (Phase 3) ===

LKB_BRANCH_LIST_SCHEMA = {
    "name": "lkb_branch_list",
    "description": "List all branches in the current database. Branches are isolated memory snapshots.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

LKB_BRANCH_CREATE_SCHEMA = {
    "name": "lkb_branch_create",
    "description": "Create a new branch from the current state. Useful for experimenting with memory changes.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Branch name (e.g., 'experiment', 'backup')"
            },
            "parent_branch_id": {
                "type": "string",
                "description": "Optional parent branch ID (defaults to current branch)"
            },
        },
        "required": ["name"],
    },
}

LKB_BRANCH_DELETE_SCHEMA = {
    "name": "lkb_branch_delete",
    "description": "Delete a branch by ID. Cannot delete the default branch.",
    "parameters": {
        "type": "object",
        "properties": {
            "branch_id": {
                "type": "string",
                "description": "Branch ID to delete"
            },
        },
        "required": ["branch_id"],
    },
}

LKB_BRANCH_PROMOTE_SCHEMA = {
    "name": "lkb_branch_promote",
    "description": "Promote a branch to become the default. Merges its changes to main.",
    "parameters": {
        "type": "object",
        "properties": {
            "branch_id": {
                "type": "string",
                "description": "Branch ID to promote"
            },
        },
        "required": ["branch_id"],
    },
}

LKB_BRANCH_RESTORE_SCHEMA = {
    "name": "lkb_branch_restore",
    "description": "Restore a branch to a specific version or LSN point.",
    "parameters": {
        "type": "object",
        "properties": {
            "branch_id": {
                "type": "string",
                "description": "Branch ID to restore"
            },
            "version_id": {
                "type": "string",
                "description": "Version ID to restore to (optional)"
            },
            "lsn": {
                "type": "string",
                "description": "LSN (Log Sequence Number) to restore to (optional)"
            },
        },
        "required": ["branch_id"],
    },
}

LKB_VERSION_LIST_SCHEMA = {
    "name": "lkb_version_list",
    "description": "List all versions (snapshots) in a branch.",
    "parameters": {
        "type": "object",
        "properties": {
            "branch_id": {
                "type": "string",
                "description": "Branch ID (defaults to current branch)"
            },
        },
        "required": [],
    },
}

LKB_VERSION_CREATE_SCHEMA = {
    "name": "lkb_version_create",
    "description": "Create a named version (snapshot) for backup or restore points.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Version name (e.g., 'before_refactor')"
            },
            "description": {
                "type": "string",
                "description": "Optional description"
            },
            "branch_id": {
                "type": "string",
                "description": "Branch ID (defaults to current branch)"
            },
        },
        "required": ["name"],
    },
}

LKB_VERSION_DELETE_SCHEMA = {
    "name": "lkb_version_delete",
    "description": "Delete a version snapshot.",
    "parameters": {
        "type": "object",
        "properties": {
            "version_id": {
                "type": "string",
                "description": "Version ID to delete"
            },
            "branch_id": {
                "type": "string",
                "description": "Branch ID (defaults to current branch)"
            },
        },
        "required": ["version_id"],
    },
}


class LakeBaseMemoryProvider(MemoryProvider):
    """External memory provider backed by LakeBase (DBay).

    Features:
    - Semantic memory storage and retrieval via pgvector
    - Multiple memory types for organization
    - Trait extraction via digest
    - Memory base switching for multi-workspace support
    - Async HTTP client with configurable timeout

    Configuration:
        api_key: LakeBase API key for authentication
        base_url: LakeBase API endpoint (default: localhost:8080)
        base_id: Memory base ID (workspace identifier)
        database_id: Database ID for branching operations
        timeout: HTTP request timeout

    Usage:
        provider = LakeBaseMemoryProvider(
            api_key="lk_xxx",
            base_url="http://localhost:8080/api/v1",
            base_id="mem_default",
        )
        await provider.initialize()
        results = await provider.handle_tool_call("memory_search", {"query": "preferences"})
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        base_id: str = "mem_default",
        database_id: str = "db_agent_memory",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Initialize LakeBase memory provider.

        Args:
            api_key: API key for LakeBase authentication
            base_url: LakeBase API endpoint URL
            base_id: Memory base ID (workspace)
            database_id: Database ID for branching
            timeout: HTTP request timeout in seconds
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._base_id = base_id
        self._database_id = database_id
        self._timeout = timeout

        self._http: Optional[httpx.AsyncClient] = None
        self._is_initialized = False

        # Track available bases for switching
        self._available_bases: List[str] = [base_id]

        # Circuit breaker state for resilience
        self._consecutive_failures: int = 0
        self._breaker_threshold: int = 5
        self._breaker_cooldown: float = 120.0
        self._breaker_until: float = 0.0

    @property
    def name(self) -> str:
        """Provider identifier."""
        return "lakebase"

    def is_available(self) -> bool:
        """Check if provider is configured and ready (no network calls)."""
        return bool(self._api_key and self._base_url and self._base_id)

    @property
    def is_initialized(self) -> bool:
        """Check if provider has been initialized."""
        return self._is_initialized

    @property
    def current_base_id(self) -> str:
        """Current memory base ID."""
        return self._base_id

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "LakeBaseMemoryProvider":
        """Create provider from config dict.

        Args:
            config: Configuration dictionary with lakebase settings

        Returns:
            LakeBaseMemoryProvider instance
        """
        lakebase_cfg = config.get("lakebase", {})
        return cls(
            api_key=lakebase_cfg.get("api_key", ""),
            base_url=lakebase_cfg.get("base_url", DEFAULT_BASE_URL),
            base_id=lakebase_cfg.get("base_id", "mem_default"),
            database_id=lakebase_cfg.get("database_id", "db_agent_memory"),
            timeout=lakebase_cfg.get("timeout", DEFAULT_TIMEOUT),
        )

    async def initialize(self, **kwargs) -> None:
        """Initialize provider: create HTTP client and verify connection.

        Args:
            **kwargs: Optional overrides (user_id, scope_id, session_id ignored for LakeBase)
        """
        if self._is_initialized:
            return

        # Create async HTTP client
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )

        # Verify connection (non-blocking, allow offline)
        try:
            resp = await self._http.get(f"/memory/bases/{self._base_id}/stats")
            if resp.status_code == 200:
                logger.info(
                    f"[LakeBaseMemoryProvider] Connected to base '{self._base_id}': "
                    f"{resp.json()}"
                )
            else:
                logger.warning(
                    f"[LakeBaseMemoryProvider] Base '{self._base_id}' not found "
                    f"(status {resp.status_code}), will create on first ingest"
                )
        except httpx.ConnectError as e:
            logger.warning(
                f"[LakeBaseMemoryProvider] Connection failed (LakeBase not running?): {e}"
            )
        except Exception as e:
            logger.warning(f"[LakeBaseMemoryProvider] Connection check failed: {e}")

        self._is_initialized = True
        logger.info(f"[LakeBaseMemoryProvider] Initialized with base_id={self._base_id}")

    async def shutdown(self) -> None:
        """Close HTTP client and cleanup."""
        if self._http:
            await self._http.aclose()
            self._http = None
        self._is_initialized = False
        logger.info("[LakeBaseMemoryProvider] Shutdown complete")

    def system_prompt_block(self) -> str:
        """Return system prompt section for agent."""
        return """# LakeBase Memory System

You have access to LakeBase memory with semantic search and structured storage.

## Memory Operations
- `lkb_memory_search`: Search memories by semantic similarity
- `lkb_memory_add`: Store new memories with appropriate type
- `lkb_memory_list`: List memories with pagination
- `lkb_memory_digest`: Extract behavioral traits from accumulated memories

## Memory Types
- `fact`: Static knowledge (preferences, system info)
- `episode`: Events and interactions (session history)
- `procedural`: How-to knowledge (workflows, procedures)
- `decision`: Choices made (architecture decisions)
- `rejection`: What to avoid (failed approaches)
- `convention`: Project rules (coding standards)

## Multi-Workspace
- `lkb_memory_switch_base`: Switch to different memory workspace

## Branching Operations (Phase 3)
- `lkb_branch_list`: List all branches (isolated memory snapshots)
- `lkb_branch_create`: Create a new branch for experimentation
- `lkb_branch_promote`: Promote branch to default (merge changes)
- `lkb_branch_restore`: Restore branch to a specific version/point
- `lkb_version_list`: List version snapshots in a branch
- `lkb_version_create`: Create named snapshot for backup

Usage tips:
- Use specific memory types for better organization
- Search before storing to avoid duplicates
- Run digest periodically to discover patterns
- Create branches before risky memory changes
- Create versions at stable points for easy rollback
"""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas exposed by this provider."""
        return [
            LKB_MEMORY_SEARCH_SCHEMA,
            LKB_MEMORY_ADD_SCHEMA,
            LKB_MEMORY_LIST_SCHEMA,
            LKB_MEMORY_GET_SCHEMA,
            LKB_MEMORY_DELETE_SCHEMA,
            LKB_MEMORY_DIGEST_SCHEMA,
            LKB_MEMORY_TRAITS_SCHEMA,
            LKB_MEMORY_STATS_SCHEMA,
            LKB_MEMORY_SWITCH_BASE_SCHEMA,
            # Phase 3: Branching operations
            LKB_BRANCH_LIST_SCHEMA,
            LKB_BRANCH_CREATE_SCHEMA,
            LKB_BRANCH_DELETE_SCHEMA,
            LKB_BRANCH_PROMOTE_SCHEMA,
            LKB_BRANCH_RESTORE_SCHEMA,
            LKB_VERSION_LIST_SCHEMA,
            LKB_VERSION_CREATE_SCHEMA,
            LKB_VERSION_DELETE_SCHEMA,
        ]

    async def handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Dispatch tool call to appropriate handler.

        Args:
            tool_name: Tool name from schema
            args: Tool arguments

        Returns:
            JSON string result
        """
        if not self._is_initialized:
            return json.dumps({"error": "Provider not initialized", "results": []})

        # Check circuit breaker
        if self._is_breaker_open():
            return json.dumps({
                "error": "Circuit breaker open (too many failures)",
                "results": [],
            })

        handlers = {
            "lkb_memory_search": self._handle_search,
            "lkb_memory_add": self._handle_add,
            "lkb_memory_list": self._handle_list,
            "lkb_memory_get": self._handle_get,
            "lkb_memory_delete": self._handle_delete,
            "lkb_memory_digest": self._handle_digest,
            "lkb_memory_traits": self._handle_traits,
            "lkb_memory_stats": self._handle_stats,
            "lkb_memory_switch_base": self._handle_switch_base,
            # Phase 3: Branching operations
            "lkb_branch_list": self._handle_branch_list,
            "lkb_branch_create": self._handle_branch_create,
            "lkb_branch_delete": self._handle_branch_delete,
            "lkb_branch_promote": self._handle_branch_promote,
            "lkb_branch_restore": self._handle_branch_restore,
            "lkb_version_list": self._handle_version_list,
            "lkb_version_create": self._handle_version_create,
            "lkb_version_delete": self._handle_version_delete,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}", "results": []})

        try:
            result = await handler(args)
            self._reset_breaker()
            return json.dumps(result, ensure_ascii=False)
        except httpx.HTTPStatusError as e:
            self._record_failure()
            return json.dumps({
                "error": f"API error [{e.response.status_code}]: {e.response.text}",
                "results": [],
            })
        except httpx.ConnectError as e:
            self._record_failure()
            return json.dumps({
                "error": f"Connection failed: {e}",
                "results": [],
            })
        except Exception as e:
            self._record_failure()
            return json.dumps({"error": str(e), "results": []})

    async def prefetch(self, query: str, **kwargs) -> str:
        """Background recall before model call.

        Args:
            query: User query for context retrieval
            **kwargs: Optional filters

        Returns:
            Formatted context string or empty on failure
        """
        if not self._is_initialized or not query:
            return ""

        try:
            memories = await self._recall(
                query=query,
                top_k=kwargs.get("top_k", 5),
                memory_types=kwargs.get("memory_types"),
            )

            if not memories:
                return ""

            parts = ["## Related Memories"]
            for mem in memories:
                type_label = mem.get("memory_type", "unknown")
                score = mem.get("score", 0)
                content = mem.get("content", "")
                parts.append(f"- [{type_label}] {content} (score: {score:.2f})")

            return "\n".join(parts)

        except Exception as e:
            logger.warning(f"[LakeBaseMemoryProvider] prefetch failed: {e}")
            return ""

    async def sync_turn(self, user_msg: str, assistant_msg: str, **kwargs) -> None:
        """Persist conversation turn as episode memory.

        Args:
            user_msg: User message content
            assistant_msg: Assistant response content
            **kwargs: Optional metadata
        """
        if not self._is_initialized or not user_msg:
            return

        # Skip if circuit breaker is open
        if self._is_breaker_open():
            logger.warning("[LakeBaseMemoryProvider] sync_turn skipped (breaker open)")
            return

        try:
            # Combine as episode
            content = f"User: {user_msg}"
            if assistant_msg:
                content += f"\nAssistant: {assistant_msg}"

            await self._ingest(
                content=content,
                memory_type="episode",
                importance=kwargs.get("importance", 0.4),
                metadata=kwargs.get("metadata"),
            )
            self._reset_breaker()

        except Exception as e:
            self._record_failure()
            logger.warning(f"[LakeBaseMemoryProvider] sync_turn failed: {e}")

    async def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Process session end - could trigger digest."""
        # Optional: run digest at session end
        pass

    # ---- Internal API Methods ----

    async def _ingest(
        self,
        content: str,
        memory_type: str = "fact",
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store memory via LakeBase ingest API.

        Args:
            content: Memory content
            memory_type: Type classification
            importance: Importance score (0-1)
            metadata: Optional structured metadata

        Returns:
            API response with memory ID
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.post(
            f"/memory/bases/{self._base_id}/ingest",
            json={
                "content": content,
                "role": "user",
                "memory_type": memory_type,
                "importance": importance,
                "metadata": metadata or {},
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _recall(
        self,
        query: str,
        top_k: int = 10,
        memory_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Semantic search via LakeBase recall API.

        Args:
            query: Search query
            top_k: Max results
            memory_types: Optional type filter

        Returns:
            List of memory results
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.post(
            f"/memory/bases/{self._base_id}/recall",
            json={
                "query": query,
                "top_k": top_k,
                "memory_types": memory_types,
            },
        )
        resp.raise_for_status()
        return resp.json().get("memories", [])

    async def _list_memories(
        self,
        memory_type: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List memories with pagination.

        Args:
            memory_type: Optional type filter
            offset: Pagination offset
            limit: Page size

        Returns:
            Paginated memory list
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        params = {"offset": offset, "limit": limit}
        if memory_type:
            params["memory_type"] = memory_type

        resp = await self._http.get(
            f"/memory/bases/{self._base_id}/memories",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def _get_memory(self, memory_id: int) -> Dict[str, Any]:
        """Get single memory by ID.

        Args:
            memory_id: Memory ID

        Returns:
            Memory object
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.get(
            f"/memory/bases/{self._base_id}/memories/{memory_id}",
        )
        resp.raise_for_status()
        return resp.json()

    async def _delete_memory(self, memory_id: int) -> Dict[str, Any]:
        """Delete memory by ID.

        Args:
            memory_id: Memory ID

        Returns:
            Deletion result
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.delete(
            f"/memory/bases/{self._base_id}/memories/{memory_id}",
        )
        resp.raise_for_status()
        return resp.json()

    async def _digest(self) -> Dict[str, Any]:
        """Run trait extraction digest.

        Returns:
            Digest result with discovered traits
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.post(
            f"/memory/bases/{self._base_id}/digest",
        )
        resp.raise_for_status()
        return resp.json()

    async def _list_traits(self) -> List[Dict[str, Any]]:
        """List discovered traits.

        Returns:
            List of trait objects
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.get(
            f"/memory/bases/{self._base_id}/traits",
        )
        resp.raise_for_status()
        return resp.json()

    async def _get_stats(self) -> Dict[str, Any]:
        """Get memory base statistics.

        Returns:
            Stats object
        """
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.get(
            f"/memory/bases/{self._base_id}/stats",
        )
        resp.raise_for_status()
        return resp.json()

    # ---- Tool Handlers ----

    async def _handle_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_search tool call."""
        memories = await self._recall(
            query=args.get("query", ""),
            top_k=args.get("top_k", 10),
            memory_types=args.get("memory_types"),
        )
        return {
            "memories": memories,
            "count": len(memories),
            "base_id": self._base_id,
        }

    async def _handle_add(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_add tool call."""
        result = await self._ingest(
            content=args.get("content", ""),
            memory_type=args.get("memory_type", "fact"),
            importance=args.get("importance", 0.5),
            metadata=args.get("metadata"),
        )
        return {
            "success": True,
            "memory_id": result.get("memory_id"),
            "memory_type": result.get("memory_type"),
            "base_id": self._base_id,
        }

    async def _handle_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_list tool call."""
        result = await self._list_memories(
            memory_type=args.get("memory_type"),
            offset=args.get("offset", 0),
            limit=args.get("limit", 20),
        )
        return {
            "memories": result.get("memories", []),
            "total": result.get("total", 0),
            "base_id": self._base_id,
        }

    async def _handle_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_get tool call."""
        memory_id = args.get("memory_id")
        if not memory_id:
            return {"error": "memory_id required"}

        memory = await self._get_memory(memory_id)
        return {"memory": memory, "base_id": self._base_id}

    async def _handle_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_delete tool call."""
        memory_id = args.get("memory_id")
        if not memory_id:
            return {"error": "memory_id required"}

        await self._delete_memory(memory_id)
        return {"success": True, "deleted_id": memory_id, "base_id": self._base_id}

    async def _handle_digest(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_digest tool call."""
        result = await self._digest()
        return {
            "success": True,
            "traits": result.get("traits", []),
            "base_id": self._base_id,
        }

    async def _handle_traits(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_traits tool call."""
        traits = await self._list_traits()
        return {"traits": traits, "base_id": self._base_id}

    async def _handle_stats(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_stats tool call."""
        stats = await self._get_stats()
        return {"stats": stats, "base_id": self._base_id}

    async def _handle_switch_base(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle memory_switch_base tool call.

        Allows switching between different memory bases (workspaces).
        """
        new_base_id = args.get("base_id")
        if not new_base_id:
            return {"error": "base_id required"}

        # Verify new base exists
        if self._http:
            try:
                stats = await self._http.get(f"/memory/bases/{new_base_id}/stats")
                if stats.status_code == 404:
                    return {"error": f"Base {new_base_id} not found"}
                if stats.status_code != 200:
                    stats.raise_for_status()
            except Exception as e:
                logger.warning("[LakeBaseMemoryProvider] Base check failed: %s", e)
                return {"error": f"Base check failed: {e}"}

        # Switch to new base
        old_base_id = self._base_id
        self._base_id = new_base_id

        # Track available bases
        if new_base_id not in self._available_bases:
            self._available_bases.append(new_base_id)

        logger.info(
            f"[LakeBaseMemoryProvider] Switched base: {old_base_id} -> {new_base_id}"
        )

        return {
            "success": True,
            "old_base_id": old_base_id,
            "new_base_id": new_base_id,
            "available_bases": self._available_bases,
        }

    # ---- Branching Operations (Phase 3) ----

    async def _handle_branch_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle branch_list tool call."""
        branches = await self._list_branches()
        return {
            "branches": branches,
            "count": len(branches),
            "database_id": self._database_id,
        }

    async def _handle_branch_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle branch_create tool call."""
        name = args.get("name")
        if not name:
            return {"error": "name required"}

        result = await self._create_branch(
            name=name,
            parent_branch_id=args.get("parent_branch_id"),
        )
        return {
            "success": True,
            "branch": result,
            "database_id": self._database_id,
        }

    async def _handle_branch_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle branch_delete tool call."""
        branch_id = args.get("branch_id")
        if not branch_id:
            return {"error": "branch_id required"}

        await self._delete_branch(branch_id)
        return {
            "success": True,
            "deleted_branch_id": branch_id,
            "database_id": self._database_id,
        }

    async def _handle_branch_promote(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle branch_promote tool call."""
        branch_id = args.get("branch_id")
        if not branch_id:
            return {"error": "branch_id required"}

        await self._promote_branch(branch_id)
        return {
            "success": True,
            "promoted_branch_id": branch_id,
            "database_id": self._database_id,
        }

    async def _handle_branch_restore(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle branch_restore tool call."""
        branch_id = args.get("branch_id")
        if not branch_id:
            return {"error": "branch_id required"}

        await self._restore_branch(
            branch_id=branch_id,
            target_version_id=args.get("version_id"),
            target_lsn=args.get("lsn"),
        )
        return {
            "success": True,
            "restored_branch_id": branch_id,
            "database_id": self._database_id,
        }

    async def _handle_version_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle version_list tool call."""
        branch_id = args.get("branch_id")
        versions = await self._list_versions(branch_id)
        return {
            "versions": versions,
            "count": len(versions),
            "database_id": self._database_id,
        }

    async def _handle_version_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle version_create tool call."""
        name = args.get("name")
        if not name:
            return {"error": "name required"}

        result = await self._create_version(
            name=name,
            description=args.get("description"),
            branch_id=args.get("branch_id"),
        )
        return {
            "success": True,
            "version": result,
            "database_id": self._database_id,
        }

    async def _handle_version_delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle version_delete tool call."""
        version_id = args.get("version_id")
        if not version_id:
            return {"error": "version_id required"}

        await self._delete_version(
            version_id=version_id,
            branch_id=args.get("branch_id"),
        )
        return {
            "success": True,
            "deleted_version_id": version_id,
            "database_id": self._database_id,
        }

    # ---- Branch API Methods ----

    async def _list_branches(self) -> List[Dict[str, Any]]:
        """List all branches in the database."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.get(f"/databases/{self._database_id}/branches")
        resp.raise_for_status()
        return resp.json()

    async def _create_branch(
        self, name: str, parent_branch_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new branch."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        body: Dict[str, Any] = {"name": name}
        if parent_branch_id:
            body["parent_branch_id"] = parent_branch_id

        resp = await self._http.post(f"/databases/{self._database_id}/branches", json=body)
        resp.raise_for_status()
        return resp.json()

    async def _delete_branch(self, branch_id: str) -> None:
        """Delete a branch."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.delete(f"/databases/{self._database_id}/branches/{branch_id}")
        resp.raise_for_status()

    async def _promote_branch(self, branch_id: str) -> None:
        """Promote a branch to default."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        resp = await self._http.post(
            f"/databases/{self._database_id}/branches/{branch_id}/promote"
        )
        resp.raise_for_status()

    async def _restore_branch(
        self,
        branch_id: str,
        target_version_id: Optional[str] = None,
        target_lsn: Optional[str] = None,
    ) -> None:
        """Restore a branch to a version or LSN."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        body: Dict[str, Any] = {}
        if target_version_id:
            body["target_version_id"] = target_version_id
        if target_lsn:
            body["target_lsn"] = target_lsn

        resp = await self._http.post(
            f"/databases/{self._database_id}/branches/{branch_id}/restore",
            json=body,
        )
        resp.raise_for_status()

    async def _list_versions(
        self, branch_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List versions in a branch."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        # Default to first branch if not specified
        if branch_id is None:
            branch_id = await self._get_default_branch_id()
            if branch_id is None:
                return []

        resp = await self._http.get(
            f"/databases/{self._database_id}/branches/{branch_id}/versions"
        )
        resp.raise_for_status()
        return resp.json()

    async def _create_version(
        self,
        name: str,
        description: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a version snapshot."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        # Default to first branch if not specified
        if branch_id is None:
            branch_id = await self._get_default_branch_id()
            if branch_id is None:
                raise ValueError("No branch found to create version")

        body: Dict[str, Any] = {"name": name}
        if description:
            body["description"] = description

        resp = await self._http.post(
            f"/databases/{self._database_id}/branches/{branch_id}/versions",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    async def _delete_version(
        self, version_id: str, branch_id: Optional[str] = None
    ) -> None:
        """Delete a version snapshot."""
        if not self._http:
            raise RuntimeError("HTTP client not initialized")

        # Default to first branch if not specified
        if branch_id is None:
            branch_id = await self._get_default_branch_id()
            if branch_id is None:
                raise ValueError("No branch found to delete version")

        resp = await self._http.delete(
            f"/databases/{self._database_id}/branches/{branch_id}/versions/{version_id}"
        )
        resp.raise_for_status()

    async def _get_default_branch_id(self) -> Optional[str]:
        """Resolve the default branch ID from the available branches.

        Returns the branch marked ``is_default``; falls back to the first
        branch if none is marked; returns ``None`` when no branches exist.
        """
        branches = await self._list_branches()
        default_branch = next(
            (b for b in branches if b.get("is_default")), branches[0] if branches else None
        )
        return default_branch["id"] if default_branch else None

    # ---- Circuit Breaker ----

    def _is_breaker_open(self) -> bool:
        """Check if circuit breaker is open."""
        if self._consecutive_failures < self._breaker_threshold:
            return False

        if time.monotonic() < self._breaker_until:
            return True

        # Cooldown expired, reset
        self._consecutive_failures = 0
        return False

    def _record_failure(self) -> None:
        """Record a failure for circuit breaker."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._breaker_until = time.monotonic() + self._breaker_cooldown
            logger.warning(
                f"[LakeBaseMemoryProvider] Circuit breaker opened for "
                f"{self._breaker_cooldown}s after {self._consecutive_failures} failures"
            )

    def _reset_breaker(self) -> None:
        """Reset circuit breaker on success."""
        self._consecutive_failures = 0


__all__ = [
    "LakeBaseMemoryProvider",
    "MEMORY_TYPES",
    "LKB_BRANCH_LIST_SCHEMA",
    "LKB_BRANCH_CREATE_SCHEMA",
    "LKB_VERSION_CREATE_SCHEMA",
]