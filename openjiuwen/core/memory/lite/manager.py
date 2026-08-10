# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Memory Index Manager - Core memory management for JiuWenClaw."""

import os
import json
import sqlite3
import struct
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Set, TYPE_CHECKING
from dataclasses import dataclass
from openjiuwen.core.common.logging import memory_logger as logger


@contextlib.contextmanager
def _cross_process_lock(lock_path: str, timeout: float = 30.0):
    """Cross-process exclusive lock via a lock file (blocks until acquired).

    Serializes the config-change rebuild across concurrently starting memory
    manager instances, so only one process drops & rebuilds the index tables
    while the others wait — then the waiters re-check and find the rebuild
    already done. Without this, concurrent rebuilds race on the same DB and
    fail with "database is locked", leaving a half-built (empty) vector table.

    Windows: msvcrt.locking (LK_LOCK, blocks with retry). POSIX: fcntl.flock.

    Raises ``TimeoutError`` if the lock is not acquired within ``timeout`` —
    the caller must NOT run the rebuild without the lock, so we fail loudly
    rather than silently proceeding and racing with another rebuild.
    """
    import time

    ensure_dir(os.path.dirname(lock_path) or ".")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        if os.name == "nt":
            import msvcrt

            while time.monotonic() < deadline:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    acquired = True
                    break
                except OSError:
                    time.sleep(0.1)
        else:
            import fcntl

            while time.monotonic() < deadline:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    time.sleep(0.1)
        if not acquired:
            raise TimeoutError(f"Could not acquire rebuild lock within {timeout}s: {lock_path}")
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    # Unlock failures are non-fatal (the fd is closed below and
                    # byte-range locks release on close); let the outer except
                    # log them at debug rather than silently swallowing here.
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception as e:
                logger.debug(f"Failed to release rebuild lock: {e}")
        os.close(fd)

if TYPE_CHECKING:
    from openjiuwen.core.sys_operation.sys_operation import SysOperation
    from openjiuwen.harness.workspace.workspace import Workspace

from .types import MemoryChunk
from .internal import (
    ensure_dir, list_memory_files, build_file_entry, chunk_markdown,
    hash_text, build_fts_query, bm25_rank_to_score, is_memory_path,
    split_query_tokens
)
from .embeddings import EmbeddingProvider, create_embedding_provider
from .config import MemorySettings

META_KEY = "memory_index_meta_v1"
SNIPPET_MAX_CHARS = 700
VECTOR_TABLE = "chunks_vec"
FTS_TABLE = "chunks_fts"
EMBEDDING_CACHE_TABLE = "embedding_cache"
SESSION_DIRTY_DEBOUNCE_MS = 5000

INDEX_CACHE: Dict[str, 'MemoryIndexManager'] = {}

# Module-level lock shared across all initialize() calls in this process so the
# rebuild (clear tables + reindex) is serialized per-process — distinct from the
# cross-process file lock, which serializes across processes. A per-call
# asyncio.Lock() would be a fresh instance each time and serialize nothing.
# Python 3.10+ defers loop binding to first await, so defining at import is safe.
_REBUILD_INTRAPROCESS_LOCK = asyncio.Lock()


@dataclass
class SessionDeltaState:
    """Tracks incremental changes to a session file."""
    last_size: int = 0
    pending_bytes: int = 0
    pending_messages: int = 0


@dataclass
class MemoryManagerParams:
    """Parameters for creating or retrieving a MemoryIndexManager instance."""
    agent_id: str
    workspace: "Workspace"
    settings: Optional[MemorySettings] = None
    embedding_config: Optional[Any] = None
    sys_operation: Optional["SysOperation"] = None
    node_name: str = "memory"


def vector_to_blob(embedding: List[float]) -> bytes:
    """Convert vector to binary blob."""
    return struct.pack(f'{len(embedding)}f', *embedding)


def blob_to_vector(blob: bytes) -> List[float]:
    """Convert binary blob to vector."""
    count = len(blob) // 4
    return list(struct.unpack(f'{count}f', blob))


def _open_database(db_path: str) -> sqlite3.Connection:
    """Open SQLite database."""
    ensure_dir(os.path.dirname(db_path) or ".")

    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # busy_timeout lets SQLite wait (up to ms) for a lock instead of failing
    # immediately with "database is locked" when another process is writing.
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def _is_recent_session_file(filename: str) -> bool:
    """Check if the file is today's or yesterday's session record.

    Session files are named as YYYY-MM-DD.md in the memory/ directory.

    Args:
        filename: The filename to check

    Returns:
        True if the file is from today or yesterday
    """
    import re

    match = re.match(r'^(\d{4}-\d{2}-\d{2})\.md$', filename)
    if not match:
        return False

    date_str = match.group(1)

    try:
        file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False

    beijing_tz = timezone(timedelta(hours=8))
    today = datetime.now(tz=beijing_tz).date()
    yesterday = today - timedelta(days=1)

    return file_date in (today, yesterday)


def _merge_hybrid_results(
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        vector_weight: float,
        text_weight: float
) -> List[Dict[str, Any]]:
    """Merge and rerank hybrid search results."""
    by_id: Dict[str, Dict[str, Any]] = {}

    for r in vector_results:
        r["_vector_score"] = r["score"]
        r["_text_score"] = 0.0
        by_id[r["id"]] = r

    for r in keyword_results:
        if r["id"] in by_id:
            by_id[r["id"]]["_text_score"] = r["score"]
        else:
            r["_vector_score"] = 0.0
            r["_text_score"] = r["score"]
            by_id[r["id"]] = r

    for r in by_id.values():
        r["score"] = vector_weight * r["_vector_score"] + text_weight * r["_text_score"]
        del r["_vector_score"]
        del r["_text_score"]

    results = list(by_id.values())
    results.sort(key=lambda x: x["score"], reverse=True)

    return results


class MemoryIndexManager:
    """Manages memory indexing and search."""

    def __init__(
            self,
            agent_id: str,
            workspace: "Workspace",
            settings: MemorySettings,
            node_name: str = "memory",
    ):
        self.agent_id = agent_id
        self.workspace = workspace
        self.node_name = node_name
        self.memory_dir = str(workspace.get_node_path(node_name)) if workspace.get_node_path(node_name) else ""
        self.settings = settings

        self.db: Optional[sqlite3.Connection] = None
        self.db_path: str = ""

        self.provider: Optional[EmbeddingProvider] = None
        self.provider_key: str = ""

        self.dirty = True
        self.sessions_dirty = False
        self.sessions_dirty_files: Set[str] = set()
        self.session_warm: Set[str] = set()
        self.closed = False

        self.fts_enabled = settings.store.get("fts", {}).get("enabled", True)
        self.vector_enabled = settings.store.get("vector", {}).get("enabled", True)
        self.cache_enabled = settings.cache.get("enabled", True)

        self.fts_available = False
        self.fts_error: Optional[str] = None
        self.vector_available = False
        self.vector_error: Optional[str] = None
        self.vector_dims: Optional[int] = None

        self._interval_timer: Optional[asyncio.Task] = None
        self._watch_timer: Optional[asyncio.Task] = None
        self._session_timer: Optional[asyncio.Task] = None
        self._session_pending_files: Set[str] = set()
        self._session_deltas: Dict[str, SessionDeltaState] = {}

        self._file_observer: Optional[Any] = None
        self._watcher_paths: Set[str] = set()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.watcher_initialized: bool = False
        self._file_stability_tracker: Dict[str, float] = {}
        self.embedding_config = None
        self.sys_operation: Optional["SysOperation"] = None
        self.llm: Optional[Any] = None

        # Set by _ensure_schema when it drops a legacy (non-trigram) chunks_fts;
        # initialize() uses it to force a full reindex (incremental sync skips
        # unchanged files and would leave the new trigram table empty).
        self._fts_migrated: bool = False

    @classmethod
    async def get(
            cls,
            params: MemoryManagerParams,
    ) -> Optional['MemoryIndexManager']:
        """Get or create memory index manager.

        Args:
            params: MemoryManagerParams containing all initialization parameters.

        Returns:
            MemoryIndexManager instance, or None if memory is disabled.
        """
        node_path = params.workspace.get_node_path(params.node_name)
        memory_dir = str(node_path) if node_path else ""
        cache_key = f"{params.agent_id}:{params.node_name}:{memory_dir}"

        if cache_key in INDEX_CACHE:
            manager = INDEX_CACHE[cache_key]
            if not manager.closed:
                return manager

        settings = params.settings or MemorySettings()
        manager = cls(params.agent_id, params.workspace, settings, params.node_name)
        manager.embedding_config = params.embedding_config
        manager.sys_operation = params.sys_operation

        try:
            await manager.initialize()
            INDEX_CACHE[cache_key] = manager
            return manager
        except Exception as e:
            logger.error(f"Failed to initialize memory manager: {e}")
            return None

    async def initialize(self) -> None:
        """Initialize the memory manager."""
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None

        self.db_path = self._resolve_db_path()
        self.db = _open_database(self.db_path)
        self._ensure_schema()
        await self._initialize_provider()
        await self._load_vector_extension()

        # Embedding 配置变化时，清空索引表数据后重新索引，而不是在脏库上做增量 reindex。
        # 原因：旧库的向量表里残留着旧模型/旧维度的向量行，且其 rowid 与重新写入的 chunks
        # rowid 错位（更新路径只 DELETE chunks 不清向量表），导致搜索时 query 向量与库里
        # 向量对不上 rowid、返回空。清空各表并重置 rowid 序列，让重新索引在干净的表上
        # 从 rowid 1 连续递增、维度统一，彻底消除脏状态。源记忆文件(.md)仍在，可完整重建。
        # 注意：必须在 _load_vector_extension 之后执行，清向量虚拟表需要扩展已加载。
        #
        # 多个 manager 实例可能在不同进程里同时初始化（agent_server / gateway 各起一个，
        # 或重启时新旧进程短暂并存）。若它们同时检测到配置变化并清表重建，会抢 memory.db
        # 的写锁互相覆盖、写一半失败，留下空的 1024 维表。用跨进程文件锁把"检测→清表→
        # 全量重建"串行化：持锁者重建，其他进程等锁后重新检测，发现已重建好就直接跳过。
        #
        # 同一套锁也串行化 FTS trigram 迁移的 force reindex：迁移 DROP+重建了 chunks_fts
        # 但 files/chunks 行未动，增量 sync 会因 hash 未变而跳过重建、留下空 FTS 表，
        # 必须 force=True 全量重写。
        config_rebuild = await self._needs_rebuild_on_config_change()
        fts_migration = self._needs_fts_migration_reindex()
        if config_rebuild or fts_migration:
            lock_path = self._rebuild_lock_path()
            async with _REBUILD_INTRAPROCESS_LOCK:  # 本进程内串行（多个 manager 实例同进程时）
                with _cross_process_lock(lock_path, timeout=120.0):
                    # 持锁后再检测一次：可能在等锁期间，别的进程已完成重建、meta 已更新。
                    if await self._needs_rebuild_on_config_change():
                        self._clear_index_tables()
                        # 在锁内一次跑完整个重建，释放锁时表已是完整的新索引，避免别的
                        # 进程看到半成品（chunks 有数据但向量表空）。
                        await self.sync(reason="initial")
                    elif self._needs_fts_migration_reindex():
                        # config 没变，必须 force=True 才会重写未变文件，填满 trigram 表。
                        await self.sync(reason="fts_migration", force=True)
        else:
            await self.sync(reason="initial")

        if self.settings.sync.get("watch", True):
            self._setup_file_watcher()

        self._ensure_interval_sync()

        logger.info(f"Memory manager initialized for agent: {self.agent_id}")

    def _rebuild_lock_path(self) -> str:
        """Path to the cross-process rebuild lock file (next to the DB)."""
        return self.db_path + ".rebuild.lock"

    async def _needs_rebuild_on_config_change(self) -> bool:
        """Whether the embedding config changed since the last index (=> must rebuild).

        Mirrors ``_should_full_reindex``'s config-comparison logic but is evaluated
        at init, so a rebuild can clear the index tables rather than mutate a dirty,
        mismatched index in place. Returns False when there is no prior index — in
        that case ``sync`` builds the index normally, nothing to rebuild.
        """
        try:
            cursor = self.db.execute(
                "SELECT value FROM meta WHERE key = ?", (META_KEY,)
            )
            row = cursor.fetchone()
            if not row:
                return False
            meta = json.loads(row["value"])
            if meta.get("provider") != self.provider.id:
                return True
            if meta.get("model") != self.provider.model:
                return True
            if meta.get("providerKey") != self.provider_key:
                return True
            if meta.get("chunkTokens") != self.settings.chunking.get("tokens"):
                return True
            return False
        except Exception as e:
            logger.warning(f"Failed to check meta for rebuild: {e}")
            return False

    def _needs_fts_migration_reindex(self) -> bool:
        """True if this instance just migrated chunks_fts to trigram and the reindex hasn't run yet.

        ``_fts_migrated`` is set by _ensure_schema when it dropped a legacy table;
        the meta ``ftsTrigram`` check makes this converge across restarts — once a
        process force-reindexes and writes ``ftsTrigram``, later processes skip.
        """
        if not self._fts_migrated:
            return False
        try:
            cursor = self.db.execute(
                "SELECT value FROM meta WHERE key = ?", (META_KEY,)
            )
            row = cursor.fetchone()
            if not row:
                return True
            meta = json.loads(row["value"])
            return not meta.get("ftsTrigram", False)
        except Exception as e:
            logger.warning(f"Failed to check meta for FTS migration reindex: {e}")
            return True

    def _clear_index_tables(self) -> None:
        """Clear index tables for rebuild, keeping the DB file.

        Called when embedding config changed. The vector virtual table is DROPPED
        and the base tables cleared, so the subsequent ``sync`` rebuilds the vector
        index under the new model/dims from scratch.

        Drop (not DELETE) is essential for the vector table: a stale ``chunks_vec``
        created under the old model's dims (e.g. float[1024]) would survive a
        DELETE, and ``_ensure_vector_table``'s ``CREATE ... IF NOT EXISTS`` would
        then no-op — leaving a 1024-dim table that rejects 2560-dim Qwen vectors
        with a "Dimension mismatch" error that is swallowed at debug level,
        silently producing an empty vector table.

        The FTS table, by contrast, is DELETEd (not DROPPed): it has no dimension
        constraint, so a plain DELETE clears it for re-indexing. And unlike the
        vector table it is never recreated after init — ``_ensure_schema`` (the
        only place that creates it) runs once *before* this method; the later
        ``sync``/``_index_chunk`` path only INSERTs into it. Dropping it here
        would leave ``fts_available`` True but the table gone, so every FTS
        insert (debug-swallowed) and keyword search (returns [] silently) fails
        for the rest of this instance's life. DELETE keeps the structure so
        ``_index_chunk`` can repopulate it during the rebuild.
        """
        if not self.db:
            return
        logger.info(
            f"Embedding config changed, clearing index tables for rebuild: {self.db_path}"
        )
        drops = [
            (f"DROP TABLE IF EXISTS {VECTOR_TABLE}", "vector"),
        ]
        for sql, label in drops:
            try:
                self.db.execute(sql)
            except Exception as e:
                logger.debug(f"Skipping {label} drop: {e}")

        # Clear base tables (DELETE keeps schema; rowids reset below). The FTS
        # virtual table is cleared here too (DELETE, not DROP) — see docstring.
        deletions = [
            ("DELETE FROM chunks", "chunks"),
            ("DELETE FROM files", "files"),
            (f"DELETE FROM {FTS_TABLE}", "fts"),
            (f"DELETE FROM {EMBEDDING_CACHE_TABLE}", "embedding_cache"),
            ("DELETE FROM meta", "meta"),
        ]
        for sql, label in deletions:
            try:
                self.db.execute(sql)
            except Exception as e:
                logger.debug(f"Skipping {label} clear: {e}")

        # Reset autoincrement so re-indexed chunks get rowids from 1, matching the
        # freshly written vector rows 1:1 (search joins chunks.rowid <-> vec.rowid).
        for table in ("chunks", VECTOR_TABLE):
            try:
                self.db.execute(
                    "DELETE FROM sqlite_sequence WHERE name = ?", (table,)
                )
            except Exception as e:
                logger.debug(f"Skipping {table} sqlite_sequence reset: {e}")

        # Forget the old dims so _ensure_vector_table recreates the vec table under
        # the new model's dims on first write. Combined with the DROP above, the first
        # chunk write builds a correctly-dimensioned vector table from scratch.
        self.vector_dims = None
        self.db.commit()

    def _resolve_db_path(self) -> str:
        """Resolve database path.

        确保向量数据库索引文件存放在与 MEMORY.md 同目录 (workspace/agent/memory/)
        """
        store_path = self.settings.store.get("path", "memory.db")
        if os.path.isabs(store_path):
            return store_path

        workspace_name = os.path.basename(self.memory_dir)
        if store_path.startswith(f"{workspace_name}/") or store_path.startswith(f"{workspace_name}\\"):
            store_path = store_path[len(workspace_name) + 1:]

        return os.path.join(self.memory_dir, store_path)

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
        if not self.db:
            raise RuntimeError("Database not initialized")

        self.db.execute("""
                        CREATE TABLE IF NOT EXISTS meta
                        (
                            key
                            TEXT
                            PRIMARY
                            KEY,
                            value
                            TEXT
                        )
                        """)

        self.db.execute("""
                        CREATE TABLE IF NOT EXISTS files
                        (
                            path
                            TEXT
                            PRIMARY
                            KEY,
                            source
                            TEXT,
                            hash
                            TEXT,
                            mtime
                            INTEGER,
                            size
                            INTEGER
                        )
                        """)

        self.db.execute("""
                        CREATE TABLE IF NOT EXISTS chunks
                        (
                            id
                            TEXT
                            PRIMARY
                            KEY,
                            path
                            TEXT,
                            source
                            TEXT,
                            start_line
                            INTEGER,
                            end_line
                            INTEGER,
                            hash
                            TEXT,
                            model
                            TEXT,
                            text
                            TEXT,
                            embedding
                            BLOB,
                            updated_at
                            INTEGER
                        )
                        """)

        self.db.execute(f"""
            CREATE TABLE IF NOT EXISTS {EMBEDDING_CACHE_TABLE} (
                provider TEXT,
                model TEXT,
                provider_key TEXT,
                hash TEXT PRIMARY KEY,
                embedding BLOB,
                dims INTEGER,
                updated_at INTEGER
            )
        """)

        if self.fts_enabled:
            try:
                # Migrate legacy chunks_fts (default unicode61, which doesn't
                # segment CJK and so never matched Chinese queries) to trigram.
                # DROP first so the CREATE below rebuilds an empty trigram table;
                # initialize() then forces a full reindex to repopulate it
                # (see _needs_fts_migration_reindex).
                if self._fts_table_is_legacy():
                    logger.info(
                        f"Migrating {FTS_TABLE} from unicode61 to trigram tokenizer: "
                        f"{self.db_path}"
                    )
                    try:
                        self.db.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
                    except sqlite3.Error as e:
                        logger.debug(f"Skipping legacy {FTS_TABLE} drop: {e}")
                    self._fts_migrated = True

                self.db.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
                        id UNINDEXED,
                        path UNINDEXED,
                        source UNINDEXED,
                        text,
                        content='',
                        contentless_delete=1,
                        tokenize='trigram'
                    )
                """)
                self.fts_available = True
            except Exception as e:
                self.fts_available = False
                self.fts_error = str(e)
                logger.warning(f"Failed to create FTS5 table: {e}")

        self.db.commit()

    def _fts_table_is_legacy(self) -> bool:
        """True if chunks_fts exists but was created without tokenize=trigram.

        Such a table (the old unicode61 default) must be dropped so _ensure_schema
        rebuilds it under trigram. A missing table is not legacy.
        """
        if not self.db:
            return False
        try:
            cursor = self.db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
                (FTS_TABLE,)
            )
            row = cursor.fetchone()
        except sqlite3.Error as e:
            logger.debug(f"Failed to introspect {FTS_TABLE} schema: {e}")
            return False
        if not row or not row["sql"]:
            return False
        return "tokenize=trigram" not in row["sql"].lower()

    async def _initialize_provider(self) -> None:
        """Initialize embedding provider."""
        if self.embedding_config is None or not getattr(self.embedding_config, "api_key", None):
            self.provider = None
            self.provider_key = "none:no-embedding"
            logger.info(
                "Embedding provider not configured (no embedding_config / api_key); "
                "memory will use FTS5 keyword search only."
            )
            return
        try:
            self.provider = await create_embedding_provider(
                model=self.settings.model,
                embedding_config=self.embedding_config,
            )
            # provider_key now incorporates base_url + api_key hash (via
            # config_fingerprint), so changing endpoint/key produces a different
            # key: this both drives _should_full_reindex detection and isolates
            # embedding_cache entries per config.
            self.provider_key = self.provider.config_fingerprint
            logger.info(f"Embedding provider: {self.provider.id} / {self.provider.model}")
        except Exception as e:
            logger.error(f"Failed to initialize embedding provider: {e}")
            raise

    async def _load_vector_extension(self) -> None:
        """Load sqlite-vec extension."""
        if not self.vector_enabled or not self.db:
            return

        try:
            import sqlite_vec
            self.db.enable_load_extension(True)
            sqlite_vec.load(self.db)
            self.db.enable_load_extension(False)
            self.vector_available = True
            logger.info("sqlite-vec extension loaded successfully")
        except Exception as e:
            self.vector_available = False
            self.vector_error = str(e)
            logger.warning(f"Failed to load sqlite-vec extension: {e}")

    def _ensure_vector_table(self, dims: int) -> bool:
        """Ensure vector virtual table exists with correct dimensions."""
        if not self.db or not self.vector_available:
            return False

        try:
            if self.vector_dims == dims:
                return True

            if self.vector_dims is not None and self.vector_dims != dims:
                try:
                    self.db.execute(f"DROP TABLE IF EXISTS {VECTOR_TABLE}")
                except sqlite3.Error as e:
                    logger.debug(f"Skipping {VECTOR_TABLE} drop before recreate: {e}")

            self.db.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {VECTOR_TABLE} USING vec0(
                    embedding float[{dims}]
                )
            """)
            self.vector_dims = dims
            logger.info(f"Vector table created with dims={dims}")
            return True

        except Exception as e:
            logger.warning(f"Failed to create vector table: {e}")
            self.vector_available = False
            self.vector_error = str(e)
            return False

    def _setup_file_watcher(self) -> None:
        """Setup file system watcher for memory files.
        
        Watches:
        - workspace_dir (memory directory for *.md files)
        - workspace_dir/daily_memory (for daily logs)
        - workspace root (for USER.md at root level)
        """
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class MemoryFileHandler(FileSystemEventHandler):
                def __init__(self, manager: 'MemoryIndexManager'):
                    self.manager = manager

                def on_modified(self, event):
                    if not event.is_directory and self.manager.watcher_initialized:
                        self._handle_change(event.src_path, "modified")

                def on_created(self, event):
                    if not event.is_directory and self.manager.watcher_initialized:
                        self._handle_change(event.src_path, "created")

                def on_deleted(self, event):
                    if not event.is_directory and self.manager.watcher_initialized:
                        self._handle_change(event.src_path, "deleted")

                def _handle_change(self, path: str, event_type: str):
                    if path.endswith(".md"):
                        self.manager.schedule_watch_sync(path, event_type)

            watch_paths = set()

            if os.path.isdir(self.memory_dir):
                watch_paths.add(self.memory_dir)

            daily_rel = self.workspace.get_directory("daily_memory")
            if daily_rel:
                daily_memory_dir = os.path.join(self.memory_dir, daily_rel)
                os.makedirs(daily_memory_dir, exist_ok=True)
                if os.path.isdir(daily_memory_dir):
                    watch_paths.add(daily_memory_dir)

            workspace_root = str(self.workspace.root_path) if self.workspace.root_path else None
            if workspace_root and os.path.isdir(workspace_root):
                watch_paths.add(workspace_root)

            for extra_path in self.settings.extra_paths:
                full_path = os.path.join(self.memory_dir, extra_path)
                if os.path.exists(full_path) and not os.path.islink(full_path):
                    watch_paths.add(full_path)

            if not watch_paths:
                logger.debug("No memory paths to watch")
                return

            self._file_observer = Observer()
            handler = MemoryFileHandler(self)

            for watch_path in watch_paths:
                if os.path.isdir(watch_path):
                    self._file_observer.schedule(handler, watch_path, recursive=False)
                self._watcher_paths.add(watch_path)

            self._file_observer.start()

            self.watcher_initialized = False
            if self._event_loop:
                self._event_loop.call_later(1.0, self._set_watcher_initialized)

            logger.info(f"File watcher started for {len(watch_paths)} path(s)")

        except ImportError:
            logger.warning("watchdog not installed, file watching disabled")
        except Exception as e:
            logger.error(f"Failed to setup file watcher: {e}")

    def _set_watcher_initialized(self) -> None:
        """Mark watcher as initialized after initial scan period."""
        self.watcher_initialized = True
        logger.debug("File watcher initialized")

    def schedule_watch_sync(self, path: Optional[str] = None, event_type: Optional[str] = None) -> None:
        """Schedule a sync after file change (debounced)."""
        self.dirty = True

        if not self._event_loop:
            return

        debounce_ms = self.settings.sync.get("watchDebounceMs", 2000)

        def schedule_sync():
            if self.closed:
                return

            if self._watch_timer:
                self._watch_timer.cancel()

            async def do_watch_sync():
                await asyncio.sleep(debounce_ms / 1000)
                self._watch_timer = None

                if not self.closed:
                    try:
                        await self.sync(reason="watch")
                    except Exception as e:
                        logger.warning(f"Memory sync failed (watch): {e}")

            self._watch_timer = asyncio.create_task(do_watch_sync())

        try:
            self._event_loop.call_soon_threadsafe(schedule_sync)
        except Exception as e:
            logger.debug(f"Failed to schedule sync: {e}")

    def _ensure_interval_sync(self) -> None:
        """Setup interval-based sync if configured."""
        minutes = self.settings.sync.get("intervalMinutes", 0)
        if not minutes or minutes <= 0:
            return

        if self._interval_timer:
            return

        async def interval_sync():
            while not self.closed:
                await asyncio.sleep(minutes * 60)
                if not self.closed:
                    try:
                        await self.sync(reason="interval")
                    except Exception as e:
                        logger.warning(f"Memory sync failed (interval): {e}")

        self._interval_timer = asyncio.create_task(interval_sync())
        logger.info(f"Interval sync enabled: every {minutes} minutes")

    async def sync(
            self,
            reason: Optional[str] = None,
            force: bool = False
    ) -> None:
        """Synchronize memory index."""
        if self.closed:
            return

        needs_full_reindex = force or await self._should_full_reindex()

        if needs_full_reindex:
            logger.info(f"Running full reindex (reason: {reason or 'unknown'})...")
            await self._run_reindex()
            return

        logger.debug(f"Memory sync (reason: {reason or 'unknown'})...")

        if "memory" in self.settings.sources and self.dirty:
            await self._sync_memory_files()
            self.dirty = False

        if "sessions" in self.settings.sources:
            await self._sync_session_files()

    async def _should_full_reindex(self) -> bool:
        """Check if full reindex is needed."""
        try:
            cursor = self.db.execute(
                "SELECT value FROM meta WHERE key = ?",
                (META_KEY,)
            )
            row = cursor.fetchone()

            if not row:
                return True

            meta = json.loads(row["value"])

            provider_id = self.provider.id if self.provider else None
            provider_model = self.provider.model if self.provider else None

            if meta.get("provider") != provider_id:
                return True

            if meta.get("model") != provider_model:
                return True

            if meta.get("chunkTokens") != self.settings.chunking.get("tokens"):
                return True

            # providerKey embeds base_url + api_key hash (via config_fingerprint).
            # A change in endpoint or key flips it -> reindex. Old meta written
            # before this field existed (or with the old id:model form) will not
            # match the new fingerprint -> reindex once, then a fresh meta is
            # written. This is the backward-compatible migration path.
            if meta.get("providerKey") != self.provider_key:
                return True

            return False

        except Exception as e:
            logger.warning(f"Failed to check meta: {e}")
            return True

    async def _run_reindex(self) -> None:
        """Run full reindex.

        Forces re-indexing of every file regardless of hash so that chunks are
        re-embedded under the current model/dims — this is what "re-index on
        vector config switch" must mean, otherwise unchanged files keep stale
        embeddings from the old model and search returns empty results.
        """
        if "memory" in self.settings.sources:
            await self._sync_memory_files(force=True)
            self.dirty = False

        if "sessions" in self.settings.sources:
            await self._sync_session_files(force=True)

        meta = {
            "provider": self.provider.id if self.provider else None,
            "model": self.provider.model if self.provider else None,
            "providerKey": self.provider_key,
            "chunkTokens": self.settings.chunking.get("tokens"),
            "chunkOverlap": self.settings.chunking.get("overlap"),
            # Marks chunks_fts as migrated to trigram; initialize() skips the
            # migration force-reindex once this is recorded.
            "ftsTrigram": self.fts_available,
        }
        if self.vector_available and self.vector_dims:
            meta["vectorDims"] = self.vector_dims

        self._write_meta(meta)

    async def _sync_memory_files(self, force: bool = False) -> None:
        """Sync memory files.

        All session files (YYYY-MM-DD.md) are indexed for search.
        Recent session files (today + yesterday) are also loaded for context.

        When ``force`` is True (e.g. embedding config changed -> full reindex),
        every file is re-indexed even if its hash is unchanged, so that chunks
        get re-embedded under the new model/dims. This is the intended meaning
        of "re-index already-indexed files on vector config switch" — not skip.
        """
        files = list_memory_files(self.workspace, node_name=self.node_name)

        logger.debug(f"Syncing {len(files)} memory files (force={force})")

        active_paths = set()

        for filepath in files:
            base_dir = self._get_base_dir_for_file(filepath)
            entry = await build_file_entry(filepath, base_dir)
            active_paths.add(entry["path"])

            if not force:
                cursor = self.db.execute(
                    "SELECT hash FROM files WHERE path = ? AND source = ?",
                    (entry["path"], "memory")
                )
                row = cursor.fetchone()

                if row and row["hash"] == entry["hash"]:
                    continue

            await self._index_file(entry, "memory")

        cursor = self.db.execute("SELECT path FROM files WHERE source = ?", ("memory",))
        for row in cursor.fetchall():
            if row["path"] not in active_paths:
                self._remove_file_from_index(row["path"])

    def _get_base_dir_for_file(self, filepath: str) -> str:
        """Get the base directory for calculating relative path of a file."""
        user_md_path = self.workspace.get_node_path("USER.md")
        if user_md_path and os.path.normpath(filepath) == os.path.normpath(str(user_md_path)):
            return str(self.workspace.root_path)
        return self.memory_dir

    async def _sync_session_files(self, force: bool = False) -> None:
        """Sync session transcript files.

        See ``_sync_memory_files`` for the meaning of ``force``.
        """
        sessions_dir = os.path.join(self.memory_dir, "sessions")
        if not os.path.exists(sessions_dir):
            return

        session_files = []
        for root, _, files in os.walk(sessions_dir):
            for f in files:
                if f.endswith(".jsonl"):
                    session_files.append(os.path.join(root, f))

        logger.debug(f"Syncing {len(session_files)} session files (force={force})")

        active_paths = set()
        for session_file in session_files:
            entry = await build_file_entry(session_file, self.memory_dir)
            active_paths.add(entry["path"])

            if not force:
                cursor = self.db.execute(
                    "SELECT hash FROM files WHERE path = ? AND source = ?",
                    (entry["path"], "sessions")
                )
                row = cursor.fetchone()

                if row and row["hash"] == entry["hash"]:
                    continue

            await self._index_file(entry, "sessions")

        cursor = self.db.execute("SELECT path FROM files WHERE source = ?", ("sessions",))
        for row in cursor.fetchall():
            if row["path"] not in active_paths:
                self._remove_file_from_index(row["path"])

    async def _index_file(self, entry: Dict[str, Any], source: str) -> None:
        """Index a single file."""
        try:
            if self.sys_operation:
                read_result = await self.sys_operation.fs().read_file(entry["absPath"])
                content = read_result.data.content
            else:
                logger.error("no available sys_operation when _index_file")
            chunks = chunk_markdown(content, self.settings.chunking)

            # Before deleting the old chunks, drop their vector rows too —
            # otherwise re-indexing a file (force reindex / hash change) leaves
            # orphan vectors in chunks_vec whose rowids no longer match any chunk
            # (the new chunks get fresh rowids), polluting the index and breaking
            # the rowid join used by search.
            old_rowids = []
            for r in self.db.execute(
                "SELECT rowid FROM chunks WHERE path = ?", (entry["path"],)
            ).fetchall():
                old_rowids.append(r["rowid"])
            if self.vector_available:
                for rid in old_rowids:
                    try:
                        self.db.execute(
                            f"DELETE FROM {VECTOR_TABLE} WHERE rowid = ?", (rid,)
                        )
                    except sqlite3.Error as e:
                        logger.debug(f"Skipping orphan vector row delete (rowid={rid}): {e}")

            self.db.execute("DELETE FROM chunks WHERE path = ?", (entry["path"],))
            if self.fts_available:
                try:
                    self.db.execute(f"DELETE FROM {FTS_TABLE} WHERE path = ?", (entry["path"],))
                except sqlite3.Error as e:
                    logger.debug(f"Skipping FTS delete for {entry['path']}: {e}")

            for chunk in chunks:
                await self._index_chunk(entry["path"], source, chunk)

            self.db.execute("""
                INSERT OR REPLACE INTO files (path, source, hash, mtime, size)
                VALUES (?, ?, ?, ?, ?)
            """, (entry["path"], source, entry["hash"], entry["mtimeMs"], entry["size"]))

            self.db.commit()

        except Exception as e:
            logger.error(f"Failed to index file {entry['path']}: {e}")
            self.db.rollback()

    async def _index_chunk(
            self,
            file_path: str,
            source: str,
            chunk: MemoryChunk
    ) -> None:
        """Index a single chunk."""
        chunk_id = f"{file_path}:{chunk.start_line}:{chunk.end_line}"
        chunk_hash = hash_text(chunk.text)

        embedding = await self._get_embedding(chunk.text)

        model_name = self.provider.model if self.provider else None

        cursor = self.db.execute("""
            INSERT OR REPLACE INTO chunks
            (id, path, source, start_line, end_line, hash, model, text, embedding, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING rowid
        """, (
            chunk_id, file_path, source, chunk.start_line, chunk.end_line,
            chunk_hash, model_name, chunk.text,
            vector_to_blob(embedding) if embedding else None,
            int(asyncio.get_event_loop().time()) if self._event_loop else 0
        ))
        row = cursor.fetchone()
        chunk_rowid = row["rowid"] if row else None

        if self.fts_available and chunk_rowid:
            try:
                self.db.execute(f"""
                    INSERT OR REPLACE INTO {FTS_TABLE} (rowid, id, path, source, text)
                    VALUES (?, ?, ?, ?, ?)
                """, (chunk_rowid, chunk_id, file_path, source, chunk.text))
            except Exception as e:
                logger.debug(f"Failed to insert into FTS: {e}")

        if self.vector_available and embedding and chunk_rowid:
            try:
                if self._ensure_vector_table(len(embedding)):
                    self.db.execute(f"""
                        INSERT OR REPLACE INTO {VECTOR_TABLE} (rowid, embedding)
                        VALUES (?, vec_f32(?))
                    """, (chunk_rowid, vector_to_blob(embedding)))
            except Exception as e:
                logger.debug(f"Failed to insert into vector table: {e}")

    def _remove_file_from_index(self, file_path: str) -> None:
        """Remove file from index."""
        try:
            if self.vector_available:
                cursor = self.db.execute(
                    "SELECT rowid FROM chunks WHERE path = ?", (file_path,)
                )
                for row in cursor.fetchall():
                    try:
                        self.db.execute(f"DELETE FROM {VECTOR_TABLE} WHERE rowid = ?", (row["rowid"],))
                    except sqlite3.Error as e:
                        logger.debug(f"Skipping vector row delete for {file_path} (rowid={row['rowid']}): {e}")

            if self.fts_available:
                cursor = self.db.execute("SELECT rowid FROM chunks WHERE path = ?", (file_path,))
                for row in cursor.fetchall():
                    try:
                        self.db.execute(f"DELETE FROM {FTS_TABLE} WHERE rowid = ?", (row["rowid"],))
                    except sqlite3.Error as e:
                        logger.debug(f"Skipping FTS row delete for {file_path} (rowid={row['rowid']}): {e}")

            self.db.execute("DELETE FROM chunks WHERE path = ?", (file_path,))
            self.db.execute("DELETE FROM files WHERE path = ?", (file_path,))
            self.db.commit()

        except Exception as e:
            logger.error(f"Failed to remove file from index: {e}")
            self.db.rollback()

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding for text (with caching)."""
        if not self.provider:
            return None

        text_hash = hash_text(text)

        if self.cache_enabled:
            cursor = self.db.execute(f"""
                SELECT embedding FROM {EMBEDDING_CACHE_TABLE}
                WHERE provider = ? AND model = ? AND provider_key = ? AND hash = ?
            """, (self.provider.id, self.provider.model, self.provider_key, text_hash))
            row = cursor.fetchone()
            if row:
                return blob_to_vector(row["embedding"])

        try:
            embedding = await self.provider.embed_query(text)

            if self.cache_enabled:
                self.db.execute(f"""
                    INSERT OR REPLACE INTO {EMBEDDING_CACHE_TABLE}
                    (provider, model, provider_key, hash, embedding, dims, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.provider.id, self.provider.model, self.provider_key,
                    text_hash, vector_to_blob(embedding), len(embedding),
                    int(asyncio.get_event_loop().time()) if self._event_loop else 0
                ))
                self.db.commit()

            return embedding

        except Exception as e:
            logger.error(f"Failed to get embedding: {e}")
            return None

    async def search(
            self,
            query: str,
            opts: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Search memory for relevant content.

        Note: Excludes MEMORY.md and recent (today/yesterday) memory files
        as they are already loaded in the system prompt.
        """
        opts = opts or {}

        if self.settings.sync.get("onSearch", True) and self.dirty:
            try:
                await self.sync(reason="search")
            except Exception as e:
                logger.warning(f"Memory sync failed (search): {e}")

        cleaned = query.strip()
        if not cleaned:
            return []

        min_score = opts.get("min_score") if opts and "min_score" in opts else\
            self.settings.query.get("min_score", 0.7)
        max_results = opts.get("max_results") if opts and "max_results" in opts else\
            self.settings.query.get("max_results", 10)
        hybrid = self.settings.query.get("hybrid") or {}

        candidates = min(200, max(1, int(max_results * (hybrid.get("candidateMultiplier") or 2.0))))

        keyword_results = []
        if hybrid.get("enabled", True) and self.fts_available:
            try:
                keyword_results = await self._search_keyword(cleaned, candidates)
            except Exception as e:
                logger.debug(f"Keyword search failed: {e}")

        query_vec = await self._embed_query_with_timeout(cleaned)
        has_vector = any(v != 0 for v in query_vec)

        vector_results = []
        if has_vector:
            try:
                vector_results = await self._search_vector(query_vec, candidates)
            except Exception as e:
                logger.debug(f"Vector search failed: {e}")

        if not hybrid.get("enabled", True):
            return [
                r for r in vector_results
                if r["score"] >= min_score
            ][:max_results]

        # if not embedding, Skip the rerank and use the raw keyword scores directly instead.
        # Pure-keyword BM25 scores run low (trigram multi-token queries commonly land
        # 0.1-0.3 after the rank->score transform), so apply a lower floor here than
        # the hybrid path — otherwise real hits get filtered out and search returns [].
        if not has_vector:
            keyword_min_score = self.settings.query.get("keywordMinScore", 0.1)
            return [
                r for r in keyword_results
                if r["score"] >= keyword_min_score
            ][:max_results]

        merged = _merge_hybrid_results(
            vector_results,
            keyword_results,
            hybrid.get("vectorWeight", 0.7),
            hybrid.get("textWeight", 0.3)
        )

        return [r for r in merged if r["score"] >= min_score][:max_results]

    async def _search_vector(
            self,
            query_vec: List[float],
            limit: int
    ) -> List[Dict[str, Any]]:
        """Search using vector similarity."""
        if not self.vector_enabled:
            return await self._search_vector_fallback(query_vec, limit)

        if not self.vector_dims:
            if not self.provider:
                return []
            sample = await self.provider.embed_query("sample")
            self._ensure_vector_table(len(sample))
        else:
            self._ensure_vector_table(self.vector_dims)

        if not self.vector_available:
            return await self._search_vector_fallback(query_vec, limit)

        try:
            query_blob = vector_to_blob(query_vec)

            source_filter, source_params = self._build_source_filter()

            cursor = self.db.execute(f"""
                SELECT rowid, id, path, source, start_line, end_line, text
                FROM chunks
                WHERE {source_filter}
            """, source_params)

            chunk_map = {}
            for row in cursor.fetchall():
                chunk_map[row["rowid"]] = {
                    "id": str(row["id"]),
                    "path": str(row["path"]),
                    "source": str(row["source"]),
                    "start_line": int(row["start_line"]),
                    "end_line": int(row["end_line"]),
                    "snippet": str(row["text"][:SNIPPET_MAX_CHARS])
                }

            if not chunk_map:
                return []

            rows = self.db.execute(f"""
                SELECT 
                    rowid,
                    vec_distance_cosine(embedding, vec_f32(?)) as distance
                FROM {VECTOR_TABLE}
                WHERE rowid IN ({','.join('?' * len(chunk_map))})
                ORDER BY distance
                LIMIT ?
            """, (query_blob, *chunk_map.keys(), limit))

            results = []
            for row in rows:
                rowid = row["rowid"]
                if rowid in chunk_map:
                    distance = row["distance"]
                    score = max(0, 1 - distance / 2)

                    result = chunk_map[rowid].copy()
                    result["score"] = score
                    results.append(result)

            return results

        except Exception as e:
            logger.debug(f"Vector search with sqlite-vec failed: {e}")
            return await self._search_vector_fallback(query_vec, limit)

    async def _search_vector_fallback(
            self,
            query_vec: List[float],
            limit: int
    ) -> List[Dict[str, Any]]:
        """Fallback vector search using in-memory cosine similarity."""
        import math

        query_norm = math.sqrt(sum(x * x for x in query_vec))
        if query_norm < 1e-10:
            return []
        query_vec = [x / query_norm for x in query_vec]

        source_filter, source_params = self._build_source_filter()

        cursor = self.db.execute(f"""
            SELECT id, path, source, start_line, end_line, text, embedding
            FROM chunks
            WHERE {source_filter} AND embedding IS NOT NULL
        """, source_params)

        results = []
        for row in cursor.fetchall():
            if not row["embedding"]:
                continue

            vec = blob_to_vector(row["embedding"])
            if len(vec) != len(query_vec):
                continue

            dot = sum(a * b for a, b in zip(vec, query_vec))
            vec_norm = math.sqrt(sum(x * x for x in vec))
            if vec_norm < 1e-10:
                continue

            similarity = dot / vec_norm

            results.append({
                "id": str(row["id"]),
                "path": str(row["path"]),
                "source": str(row["source"]),
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "snippet": str(row["text"][:SNIPPET_MAX_CHARS]),
                "score": float(max(0, similarity))
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    async def _search_keyword(
            self,
            query: str,
            limit: int
    ) -> List[Dict[str, Any]]:
        """Search via trigram FTS (long tokens) with a LIKE fallback for short tokens.

        Long tokens (>=3 chars) hit the BM25-ranked FTS index; short tokens
        (<3 chars, e.g. 2-char Chinese) can't match trigram and fall back to
        ``LIKE '%tok%'`` on ``chunks.text``. Results are merged by chunk id —
        FTS scores win on conflict, LIKE hits get a fixed score.
        """
        long_tokens, short_tokens = split_query_tokens(query)
        if not long_tokens and not short_tokens:
            return []

        source_filter, source_params = self._build_source_filter()

        cursor = self.db.execute(f"""
            SELECT rowid, id, path, source, start_line, end_line, text
            FROM chunks
            WHERE {source_filter}
        """, source_params)

        chunk_map = {}
        for row in cursor.fetchall():
            chunk_map[row["rowid"]] = {
                "id": str(row["id"]),
                "path": str(row["path"]),
                "source": str(row["source"]),
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "snippet": str(row["text"][:SNIPPET_MAX_CHARS])
            }

        if not chunk_map:
            return []

        results: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        # FTS path for long tokens. Restrict to rowids that still exist in chunks
        # (via chunk_map): chunks_fts is contentless and may carry orphan rowids
        # left by prior reindexes where chunks was cleared but the FTS postings
        # weren't fully purged. Without this filter, high-scoring orphans fill
        # the LIMIT before the real hits surface — returning [].
        fts_query = build_fts_query(query) if long_tokens else ""
        if self.fts_available and fts_query:
            try:
                rowid_placeholders = ",".join("?" * len(chunk_map))
                rows = self.db.execute(f"""
                    SELECT
                        rowid,
                        rank
                    FROM {FTS_TABLE}
                    WHERE {FTS_TABLE} MATCH ? AND rowid IN ({rowid_placeholders})
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query, *list(chunk_map.keys()), limit))
                for row in rows:
                    rowid = row["rowid"]
                    if rowid in chunk_map:
                        score = bm25_rank_to_score(float(row["rank"]))
                        result = chunk_map[rowid].copy()
                        result["score"] = float(score)
                        if result["id"] not in seen_ids:
                            seen_ids.add(result["id"])
                            results.append(result)
            except Exception as e:
                logger.debug(f"Keyword FTS search failed: {e}")

        # LIKE fallback for short tokens: trigram can't match <3 chars, so scan
        # chunks.text. No index, but memory chunk volume is small enough to scan.
        if short_tokens:
            like_clauses = " OR ".join("text LIKE ? ESCAPE '\\'" for _ in short_tokens)
            like_params = [self._escape_like(t) for t in short_tokens]
            try:
                like_rows = self.db.execute(f"""
                    SELECT rowid
                    FROM chunks
                    WHERE {source_filter} AND ({like_clauses})
                    LIMIT ?
                """, (*source_params, *like_params, limit))
                # Fixed score clears min_score (0.3) but stays below FTS/BM25 hits.
                like_score = 0.5
                for row in like_rows:
                    rowid = row["rowid"]
                    if rowid in chunk_map:
                        result = chunk_map[rowid].copy()
                        result["score"] = float(like_score)
                        if result["id"] not in seen_ids:
                            seen_ids.add(result["id"])
                            results.append(result)
            except Exception as e:
                logger.debug(f"Keyword LIKE fallback failed: {e}")

        return results

    @staticmethod
    def _escape_like(token: str) -> str:
        """Escape a token into a LIKE ``%token%`` pattern (literal %, _, \\)."""
        escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def _build_source_filter(self) -> tuple:
        """Build SQL filter for enabled sources.
        
        Returns:
            Tuple of (filter_string, params_list)
        """
        sources = self.settings.sources
        if not sources:
            return ("1=0", [])

        if len(sources) == 1:
            return ("source = ?", sources)

        return (f"source IN ({', '.join(['?'] * len(sources))})", sources)

    async def _embed_query_with_timeout(self, query: str) -> List[float]:
        """Embed query with timeout."""
        if not self.provider:
            return []
        try:
            timeout = 60.0
            return await asyncio.wait_for(
                self.provider.embed_query(query),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning("Embedding query timed out")
            return []
        except Exception as e:
            logger.error(f"Embedding query failed: {e}")
            return []

    async def read_file(
            self,
            rel_path: str,
            from_line: Optional[int] = None,
            lines: Optional[int] = None
    ) -> Dict[str, Any]:
        """Read file content.
        
        Args:
            rel_path: Can be a relative path (from memory_dir/workspace_root) 
                      or an absolute path.
            from_line: Starting line number (1-based).
            lines: Number of lines to read.
        """
        if os.path.isabs(rel_path):
            full_path = rel_path
        elif rel_path == "USER.md":
            full_path = str(self.workspace.get_node_path("USER.md"))
        else:
            full_path = os.path.join(self.memory_dir, rel_path)

        if self.sys_operation:
            line_range = None
            if from_line is not None and lines is not None:
                line_range = (from_line, from_line + lines - 1)
            elif from_line is not None:
                line_range = (from_line, -1)

            read_result = await self.sys_operation.fs().read_file(
                full_path,
                line_range=line_range,
            )
            content = read_result.data.content if read_result.data else ""
            all_lines = content.split("\n") if content else []
            total_lines = len(all_lines)

            if from_line is not None:
                start = max(0, from_line - 1)
                end = total_lines
                if lines is not None:
                    end = min(total_lines, start + lines)
                content_lines = all_lines[start:end]
            else:
                content_lines = all_lines

            return {
                "path": rel_path,
                "text": "\n".join(content_lines),
                "totalLines": total_lines,
                "fromLine": from_line or 1,
                "toLine": (from_line or 1) + len(content_lines) - 1
            }
        else:
            logger.error("no available sys_operation when reading memory")
            return {}

    def _write_meta(self, meta: Dict[str, Any]) -> None:
        """Write metadata to database."""
        self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (META_KEY, json.dumps(meta))
        )
        self.db.commit()

    def status(self) -> Dict[str, Any]:
        """Get memory system status."""
        if not self.db:
            return {"available": False}

        cursor = self.db.execute("SELECT COUNT(*) as count FROM files")
        file_count = cursor.fetchone()["count"]

        cursor = self.db.execute("SELECT COUNT(*) as count FROM chunks")
        chunk_count = cursor.fetchone()["count"]

        cursor = self.db.execute("""
                                 SELECT source, COUNT(*) as files
                                 FROM files
                                 GROUP BY source
                                 """)
        source_counts = [
            {"source": str(row["source"]), "files": int(row["files"])}
            for row in cursor.fetchall()
        ]

        return {
            "available": True,
            "provider": self.provider.id if self.provider else None,
            "model": self.provider.model if self.provider else None,
            "files": int(file_count),
            "chunks": int(chunk_count),
            "sourceCounts": source_counts,
            "dirty": self.dirty,
            "fts": {
                "enabled": self.fts_enabled,
                "available": self.fts_available,
                "error": self.fts_error
            },
            "vector": {
                "enabled": self.vector_enabled,
                "available": self.vector_available,
                "error": self.vector_error,
                "dims": self.vector_dims
            },
            "cache": {
                "enabled": self.cache_enabled,
                "entries": int(self._get_cache_entry_count())
            }
        }

    def _get_cache_entry_count(self) -> int:
        """Get number of cache entries."""
        try:
            cursor = self.db.execute(f"SELECT COUNT(*) as count FROM {EMBEDDING_CACHE_TABLE}")
            return cursor.fetchone()["count"]
        except sqlite3.Error:
            return 0

    async def close(self) -> None:
        """Close the memory manager."""
        if self.closed:
            return

        self.closed = True

        if self._interval_timer:
            self._interval_timer.cancel()
        if self._watch_timer:
            self._watch_timer.cancel()
        if self._session_timer:
            self._session_timer.cancel()

        if self._file_observer:
            try:
                self._file_observer.stop()
                self._file_observer.join()
            except Exception:
                logger.warning("File observer stopped")

        if self.db:
            self.db.close()

        cache_key = f"{self.agent_id}:{self.node_name}:{self.memory_dir}"
        if cache_key in INDEX_CACHE:
            del INDEX_CACHE[cache_key]

        logger.info("Memory manager closed")


def clear_memory_manager_cache() -> None:
    """清除 memory manager 缓存，使下次 get_memory_manager 使用最新配置（如 embed_api_base 等）创建新实例。

    同步版本：仅清空 INDEX_CACHE，不关闭旧实例的 db 连接 / 文件监听器。
    旧实例随后会被 GC，但 watchdog observer 线程与 sqlite 连接可能延迟释放。
    如需彻底释放，改用 aclose_memory_manager_cache()。
    """
    INDEX_CACHE.clear()


async def aclose_memory_manager_cache() -> None:
    """异步清除 memory manager 缓存并关闭旧实例（db 连接 / watchdog observer / 定时任务）。

    用于 embedding 配置热变更场景：close 旧 manager 后，下次
    init_memory_manager_async 会用新 embedding_config 创建新实例与新 provider，
    从而让新 base_url / api_key / model 真正生效。
    """
    managers = list(INDEX_CACHE.values())
    INDEX_CACHE.clear()
    for mgr in managers:
        try:
            await mgr.close()
        except Exception as e:
            logger.warning(f"close memory manager on cache clear failed: {e}")


async def get_memory_manager(
        agent_id: str = "default",
        workspace: "Workspace" = None,
        settings: Optional[MemorySettings] = None
) -> Optional[MemoryIndexManager]:
    """Get or create memory manager.

    Args:
        agent_id: Agent identifier.
        workspace: Workspace instance.
        settings: Memory settings instance.
    """
    settings = settings or MemorySettings()
    params = MemoryManagerParams(
        agent_id=agent_id,
        workspace=workspace,
        settings=settings
    )
    return await MemoryIndexManager.get(params)
