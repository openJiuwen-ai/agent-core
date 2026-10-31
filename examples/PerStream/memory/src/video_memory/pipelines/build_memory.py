from __future__ import annotations

from video_memory.config import AppConfig
from video_memory.data.frame_index import build_frame_index
from video_memory.data.windowing import make_windows
from video_memory.llm.api_client import make_model_client
from video_memory.memory.entity_extractor import SpacyEntityExtractor
from video_memory.memory.entity_store import EntityNormalizer, entity_from_mention
from video_memory.memory.generator import MemoryGenerator
from video_memory.memory.ocr_generator import OCRLLMMemoryGenerator, OCRMemoryGenerator
from video_memory.memory.store import SQLiteMemoryStore
from video_memory.ocr.tesseract import TesseractOCR
from video_memory.schemas import NodeEntityEdge, RejectedNode
from video_memory.tracing.trace_logger import TraceLogger


class BuildMemoryPipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(self, clear_existing: bool = False, limit_windows: int | None = None) -> dict:
        frame_index = build_frame_index(self.config.paths.frames_dir)
        windows = make_windows(
            frame_index.frames,
            window_size=self.config.window.window_size,
            stride=self.config.window.stride,
        )
        if limit_windows is not None:
            windows = windows[:limit_windows]

        if self.config.memory.generation_mode == "ocr":
            generator = OCRMemoryGenerator(
                TesseractOCR(),
                min_chars=self.config.memory.min_ocr_chars,
            )
        elif self.config.memory.generation_mode == "ocr_llm":
            client = make_model_client(self.config.llm)
            generator = OCRLLMMemoryGenerator(
                TesseractOCR(),
                client,
            )
        elif self.config.memory.generation_mode == "vision":
            client = make_model_client(self.config.llm)
            generator = MemoryGenerator(client)
        else:
            raise ValueError(f"Unsupported memory.generation_mode: {self.config.memory.generation_mode}")
        extractor = SpacyEntityExtractor(
            self.config.entities.spacy_model,
            allowed_labels=self.config.entities.allowed_labels,
            conditional_labels=self.config.entities.conditional_labels,
            blocked_labels=self.config.entities.blocked_labels,
            blocklist=self.config.entities.blocklist,
        )
        normalizer = EntityNormalizer(self.config.entities.aliases)
        store = SQLiteMemoryStore(self.config.paths.memory_db)
        trace = TraceLogger(self.config.tracing.traces_dir, self.config.tracing.enabled)

        if clear_existing:
            store.clear()

        total_nodes = 0
        total_edges = 0
        rejected_nodes: list[RejectedNode] = []

        for window in windows:
            frames = frame_index.subset(window.frame_keys)
            nodes, node_frame_edges, ocr_observations, rejected = generator.generate(window, frames)
            rejected_nodes.extend(rejected)
            store.add_nodes(nodes)
            store.add_node_frame_edges(node_frame_edges)

            node_entity_edges: list[NodeEntityEdge] = []
            entity_trace = []
            for node in nodes:
                mentions = extractor.extract(node.description_text)
                for mention in mentions:
                    entity = entity_from_mention(mention, normalizer)
                    stored_entity = store.add_entity(entity)
                    node_entity_edges.append(NodeEntityEdge(node.node_id, stored_entity.entity_id))
                    entity_trace.append(
                        {
                            "node_id": node.node_id,
                            "mention": mention.to_dict(),
                            "entity": stored_entity.to_dict(),
                        }
                    )

            store.add_node_entity_edges(node_entity_edges)
            total_nodes += len(nodes)
            total_edges += len(node_frame_edges) + len(node_entity_edges)

            trace.write_json(
                f"memory/{window.window_id}.json",
                {
                    "window": window.to_dict(),
                    "nodes": [node.to_dict() for node in nodes],
                    "node_frame_edges": [edge.to_dict() for edge in node_frame_edges],
                    "ocr": [item.to_dict() for item in ocr_observations],
                    "entities": entity_trace,
                    "rejected_nodes": [item.to_dict() for item in rejected],
                },
            )

        summary = {
            "frames": len(frame_index),
            "windows": len(windows),
            "nodes": total_nodes,
            "entities": len(store.list_entities()),
            "edges": total_edges,
            "rejected_node_count": len(rejected_nodes),
            "rejected_nodes": [item.to_dict() for item in rejected_nodes],
            "memory_db": str(self.config.paths.memory_db),
        }
        trace.write_json("memory_summary.json", summary)
        store.close()
        return summary
