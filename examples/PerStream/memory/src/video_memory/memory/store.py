from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from video_memory.schemas import Entity, MemoryNode, NodeEntityEdge, NodeFrameEdge


class SQLiteMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.create_schema()

    def close(self) -> None:
        self.conn.close()

    def create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                description_text TEXT NOT NULL,
                time_ids TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL UNIQUE,
                entity_type TEXT,
                aliases TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS node_frame_edges (
                node_id TEXT NOT NULL,
                frame_key TEXT NOT NULL,
                confidence REAL NOT NULL,
                PRIMARY KEY (node_id, frame_key)
            );

            CREATE TABLE IF NOT EXISTS node_entity_edges (
                node_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                confidence REAL NOT NULL,
                PRIMARY KEY (node_id, entity_id)
            );
            """
        )
        self.conn.commit()

    def clear(self) -> None:
        self.conn.executescript(
            """
            DELETE FROM node_entity_edges;
            DELETE FROM node_frame_edges;
            DELETE FROM entities;
            DELETE FROM memory_nodes;
            """
        )
        self.conn.commit()

    def add_node(self, node: MemoryNode) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO memory_nodes (node_id, node_type, description_text, time_ids)
            VALUES (?, ?, ?, ?)
            """,
            (node.node_id, node.node_type, node.description_text, json.dumps(node.time_ids)),
        )
        self.conn.commit()

    def add_nodes(self, nodes: Iterable[MemoryNode]) -> None:
        for node in nodes:
            self.add_node(node)

    def add_entity(self, entity: Entity) -> Entity:
        existing = self.get_entity_by_name(entity.canonical_name)
        if existing is not None:
            return existing
        self.conn.execute(
            """
            INSERT INTO entities (entity_id, canonical_name, entity_type, aliases)
            VALUES (?, ?, ?, ?)
            """,
            (entity.entity_id, entity.canonical_name, entity.entity_type, json.dumps(entity.aliases)),
        )
        self.conn.commit()
        return entity

    def get_entity_by_name(self, canonical_name: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE canonical_name = ?",
            (canonical_name,),
        ).fetchone()
        return _entity_from_row(row) if row else None

    def add_node_frame_edges(self, edges: Iterable[NodeFrameEdge]) -> None:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO node_frame_edges (node_id, frame_key, confidence)
            VALUES (?, ?, ?)
            """,
            [(edge.node_id, edge.frame_key, edge.confidence) for edge in edges],
        )
        self.conn.commit()

    def add_node_entity_edges(self, edges: Iterable[NodeEntityEdge]) -> None:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO node_entity_edges (node_id, entity_id, confidence)
            VALUES (?, ?, ?)
            """,
            [(edge.node_id, edge.entity_id, edge.confidence) for edge in edges],
        )
        self.conn.commit()

    def list_nodes(self) -> list[MemoryNode]:
        rows = self.conn.execute("SELECT * FROM memory_nodes ORDER BY node_id").fetchall()
        return [_node_from_row(row) for row in rows]

    def list_entities(self) -> list[Entity]:
        rows = self.conn.execute("SELECT * FROM entities ORDER BY canonical_name").fetchall()
        return [_entity_from_row(row) for row in rows]

    def get_node(self, node_id: str) -> MemoryNode | None:
        row = self.conn.execute("SELECT * FROM memory_nodes WHERE node_id = ?", (node_id,)).fetchone()
        return _node_from_row(row) if row else None

    def get_nodes(self, node_ids: Iterable[str]) -> list[MemoryNode]:
        return [node for node_id in node_ids if (node := self.get_node(node_id)) is not None]

    def node_frame_edges(self, node_ids: set[str] | None = None) -> list[NodeFrameEdge]:
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            rows = self.conn.execute(
                f"SELECT * FROM node_frame_edges WHERE node_id IN ({placeholders})",
                tuple(node_ids),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM node_frame_edges").fetchall()
        return [NodeFrameEdge(row["node_id"], row["frame_key"], row["confidence"]) for row in rows]

    def node_entity_edges(self, node_ids: set[str] | None = None) -> list[NodeEntityEdge]:
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            rows = self.conn.execute(
                f"SELECT * FROM node_entity_edges WHERE node_id IN ({placeholders})",
                tuple(node_ids),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM node_entity_edges").fetchall()
        return [NodeEntityEdge(row["node_id"], row["entity_id"], row["confidence"]) for row in rows]

    def frame_keys_for_nodes(self, node_ids: Iterable[str]) -> list[str]:
        node_id_set = set(node_ids)
        if not node_id_set:
            return []
        edges = self.node_frame_edges(node_id_set)
        return sorted({edge.frame_key for edge in edges})


def _node_from_row(row: sqlite3.Row) -> MemoryNode:
    return MemoryNode(
        node_id=row["node_id"],
        node_type=row["node_type"],
        description_text=row["description_text"],
        time_ids=list(json.loads(row["time_ids"])),
    )


def _entity_from_row(row: sqlite3.Row) -> Entity:
    return Entity(
        entity_id=row["entity_id"],
        canonical_name=row["canonical_name"],
        entity_type=row["entity_type"],
        aliases=list(json.loads(row["aliases"])),
    )

