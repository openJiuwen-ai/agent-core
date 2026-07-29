#!/usr/bin/env python3
"""
Personalized Memory Graph (PMG) Implementation
Two-layer structure: Dictionary Forest (Tree Layer) + Graph Layer
Supports CRUD operations with triplet-based memory storage
"""

import numpy as np
import json
import uuid
from typing import Dict, List, Tuple, Optional, Set, Callable
from collections import defaultdict
import datetime
from src.core.memory_subcategories import MEMORY_SUBCATEGORIES
from src.utils.perstream_utils import cosine_similarity

class MemoryNode:
    """Node in the memory graph storing textual memory and embeddings"""

    def __init__(self, category: str, node_label: str, label_embedding: np.ndarray,
                 caption_text: str, caption_embedding: np.ndarray,
                 mean_visual_vector: Optional[np.ndarray] = None,
                 nearest_visual_vector: Optional[np.ndarray] = None):
        self.node_id = str(uuid.uuid4())
        self.node_label = node_label
        self.label_embedding = label_embedding
        self.caption_text = caption_text
        self.caption_embedding = caption_embedding
        self.mean_visual_vector = mean_visual_vector if mean_visual_vector is not None else np.array([])
        self.nearest_visual_vector = nearest_visual_vector if nearest_visual_vector is not None else np.array([])
        self.category = category
        self.created_at = datetime.datetime.now()
        self.updated_at = datetime.datetime.now()

    def to_dict(self):
        """Convert node to dictionary for serialization"""
        return {
            "node_id": self.node_id,
            "caption_text": self.caption_text,
            "embedding": self.embedding.tolist() if self.embedding.size > 0 else [],
            "mean_visual_vector": self.mean_visual_vector.tolist() if self.mean_visual_vector.size > 0 else [],
            "nearest_visual_vector": self.nearest_visual_vector.tolist() if self.nearest_visual_vector.size > 0 else [],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Create node from dictionary"""
        node = cls(
            data["node_id"],
            data["caption_text"],
            np.array(data["embedding"]) if data["embedding"] else np.array([]),
            np.array(data["mean_visual_vector"]) if data["mean_visual_vector"] else np.array([]),
            np.array(data["nearest_visual_vector"]) if data["nearest_visual_vector"] else np.array([])
        )
        node.created_at = datetime.datetime.fromisoformat(data["created_at"])
        node.updated_at = datetime.datetime.fromisoformat(data["updated_at"])
        return node

class MemoryEdge:
    """Edge in the memory graph representing relationships"""

    def __init__(self, source_id: str, target_id: str, predicate: str, sequence_number: int = 0):
        self.edge_id = str(uuid.uuid4())
        self.source_id = source_id
        self.target_id = target_id
        self.predicate = predicate
        self.sequence_number = sequence_number
        self.created_at = datetime.datetime.now()

    def to_dict(self):
        """Convert edge to dictionary for serialization"""
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "predicate": self.predicate,
            "sequence_number": self.sequence_number,
            "created_at": self.created_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Create edge from dictionary"""
        edge = cls(data["source_id"], data["target_id"], data["predicate"], data["sequence_number"])
        edge.edge_id = data["edge_id"]
        edge.created_at = datetime.datetime.fromisoformat(data["created_at"])
        return edge

class PersonalizedMemoryGraph:
    """
    Personalized Memory Graph with two-layer structure:
    - Tree Layer: Dictionary forest using memory categories as keys
    - Graph Layer: Dynamic triplet-based memory storage
    """

    def __init__(self, embedding_function, similarity_threshold: float = 0.9, delta_dfs_threshold: float = 0.0, verbose=False, allow_duplicates: bool = False):
        self.similarity_threshold = similarity_threshold
        self.delta_dfs_threshold = delta_dfs_threshold
        self.embedding_function = embedding_function
        self.verbose = verbose
        self.allow_duplicates = allow_duplicates
        self.sequence_counter = 0

        # Tree Layer: Dictionary forest with memory categories as keys
        self.memory_forest = {}
        for subcategory in MEMORY_SUBCATEGORIES.keys():
            self.memory_forest[subcategory] = []

        # Graph Layer: Nodes and edges
        self.nodes: Dict[str, MemoryNode] = {}
        self.edges: Dict[str, MemoryEdge] = {}

        # Adjacency lists for efficient traversal
        self.adjacency_list: Dict[str, List[str]] = defaultdict(list)
        self.reverse_adjacency_list: Dict[str, List[str]] = defaultdict(list)

    def __str__(self) -> str:
        """Return a formatted string representation of all graphs by category"""
        
        output = []
        output.append("=" * 80)
        output.append("PERSONALIZED MEMORY GRAPH")
        output.append("=" * 80)
        
        # Overall statistics
        stats = self.get_stats()
        output.append(f"Total Nodes: {stats['total_nodes']}")
        output.append(f"Total Edges: {stats['total_edges']}")
        output.append(f"Similarity Threshold: {self.similarity_threshold}")
        output.append("")
        
        # Process each category
        for category, node_ids in self.memory_forest.items():
            if not node_ids:  # Skip empty categories
                continue
                
            output.append("-" * 60)
            output.append(f"CATEGORY: {category.upper()}")
            output.append("-" * 60)
            output.append(f"Nodes in category: {len(node_ids)}")
            output.append("")
            
            # Show nodes in this category
            output.append("NODES:")
            for i, node_id in enumerate(node_ids, 1):
                if node_id in self.nodes:
                    node = self.nodes[node_id]
                    # Use node_label if available, otherwise fall back to caption_text
                    display_text = node.node_label if node.node_label else node.caption_text
                    
                    # Truncate long text for readability
                    if len(display_text) > 50:
                        display_text = display_text[:47] + "..."
                    output.append(f"  {i}. [{node_id[:8]}...] {display_text}")
            
            output.append("")
            
            # Show edges involving nodes in this category
            category_edges = []
            for edge in self.edges.values():
                if edge.source_id in node_ids or edge.target_id in node_ids:
                    category_edges.append(edge)
            
            if category_edges:
                output.append("EDGES:")
                for i, edge in enumerate(category_edges, 1):
                    source_node = self.nodes.get(edge.source_id)
                    target_node = self.nodes.get(edge.target_id)
                    
                    # Use node_label if available, otherwise fall back to caption_text
                    if source_node:
                        source_text = source_node.node_label if source_node.node_label else source_node.caption_text
                        if len(source_text) > 20:
                            source_text = source_text[:17] + "..."
                    else:
                        source_text = "Unknown"
                    
                    if target_node:
                        target_text = target_node.node_label if target_node.node_label else target_node.caption_text
                        if len(target_text) > 20:
                            target_text = target_text[:17] + "..."
                    else:
                        target_text = "Unknown"
                    
                    output.append(f"  {i}. [{source_text}] --[{edge.predicate}]--> [{target_text}]")
            else:
                output.append("EDGES: None")
            
            output.append("")
        
        # Show cross-category connections
        cross_category_edges = []
        for edge in self.edges.values():
            source_category = None
            target_category = None
            
            # Find which categories the source and target belong to
            for cat, node_list in self.memory_forest.items():
                if edge.source_id in node_list:
                    source_category = cat
                if edge.target_id in node_list:
                    target_category = cat
            
            if source_category and target_category and source_category != target_category:
                cross_category_edges.append((edge, source_category, target_category))
        
        if cross_category_edges:
            output.append("-" * 60)
            output.append("CROSS-CATEGORY CONNECTIONS")
            output.append("-" * 60)
            for i, (edge, src_cat, tgt_cat) in enumerate(cross_category_edges, 1):
                source_node = self.nodes.get(edge.source_id)
                target_node = self.nodes.get(edge.target_id)
                
                # Use node_label if available, otherwise fall back to caption_text
                if source_node:
                    source_text = source_node.node_label if source_node.node_label else source_node.caption_text
                    if len(source_text) > 15:
                        source_text = source_text[:12] + "..."
                else:
                    source_text = "Unknown"
                
                if target_node:
                    target_text = target_node.node_label if target_node.node_label else target_node.caption_text
                    if len(target_text) > 15:
                        target_text = target_text[:12] + "..."
                else:
                    target_text = "Unknown"
                
                output.append(f"  {i}. [{src_cat}] {source_text} --[{edge.predicate}]--> [{tgt_cat}] {target_text}")
        
        output.append("=" * 80)
        return "\n".join(output)

    def create(self, subject: str, predicate: str, obj: str, category: str, memory_set: dict) -> Tuple[str, str, str]:
        """
        Create operation: Add new memory triplet to PMG
        Returns: (subject_id, edge_id, object_id)
        """

        if self.verbose: print(f"Creating {subject} -- {predicate} --> {obj}")

        # Find existing or create new nodes for subject and object
        subject_id = self._find_or_create_node(category, subject, memory_set, is_object=False)
        obj_id = self._find_or_create_node(category, obj, memory_set, is_object=True)

        # Skip edge duplicate check if allow_duplicates is True
        if not self.allow_duplicates:
            existing_edge = self._find_edge(subject_id, obj_id, predicate)
            if existing_edge:
                if self.verbose: print("Existing edge exists!")
                return subject_id, existing_edge.edge_id, obj_id

        # Create new edge
        self.sequence_counter += 1
        edge = MemoryEdge(subject_id, obj_id, predicate, self.sequence_counter)

        self.edges[edge.edge_id] = edge
        self.adjacency_list[subject_id].append(obj_id)
        self.reverse_adjacency_list[obj_id].append(subject_id)

        return subject_id, edge.edge_id, obj_id

    def _find_or_create_node(self, category: str, node_label: str, memory_set: dict, is_object: bool) -> str:
        """Find existing node by text similarity or create new one"""
        
        if category not in self.memory_forest:
            return ValueError("category must be in subcategory predefined")

        # Skip duplicate checking if allow_duplicates is True
        if not self.allow_duplicates:
            # First check for exact text match (no duplicates allowed)
            for node_id in self.memory_forest[category]:
                if node_id in self.nodes:
                    if self.nodes[node_id] is not None and node_label is not None and self.nodes[node_id].node_label.lower().strip() == node_label.lower().strip():
                        return node_id
            
            # Generate embedding for the input text
            label_embedding = self.embedding_function(node_label)
            
            # Search for semantically similar nodes using embedding similarity
            best_similarity = 0.0
            best_node_id = None
            
            # Check nodes in the same category first for efficiency
            for node_id in self.memory_forest[category]:
                if node_id in self.nodes:
                    node = self.nodes[node_id]
                    similarity = cosine_similarity(label_embedding, node.label_embedding)
                    
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_node_id = node_id
            
            # Return existing node if similarity exceeds threshold
            if best_similarity >= self.similarity_threshold and best_node_id:
                if self.verbose: print(f"Found similar node: '{node_label}' -> '{self.nodes[best_node_id].caption_text}' (similarity: {best_similarity:.3f})")
                return best_node_id
        else:
            # When duplicates are allowed, skip duplicate checking and always create new node
            # Still need to generate embedding for new node creation
            label_embedding = self.embedding_function(node_label)
        
        # Create new node if no similar node found (or if duplicates allowed)
        if is_object:
            curr_storage_set = MemoryNode(
                category=category,
                node_label=node_label,
                label_embedding=label_embedding,
                caption_text=memory_set.get("caption_text", None),
                caption_embedding=memory_set.get("caption_embedding", None),
                mean_visual_vector=memory_set.get("mean_visual_vector", None),
                nearest_visual_vector=memory_set.get("nearest_visual_vector", None)
            )
        else:
            curr_storage_set = MemoryNode(
                category=category,
                node_label=node_label,
                label_embedding=label_embedding,
                caption_text=node_label, 
                caption_embedding=label_embedding, 
                mean_visual_vector=None, 
                nearest_visual_vector=None,
            )

        node_id = curr_storage_set.node_id
        self.nodes[node_id] = curr_storage_set  # Add to graph layer
        self.memory_forest[category].append(node_id)  # Add to tree layer (memory forest)
        
        if self.verbose: print(f"Created new node: '{node_label}' in category '{category}'")
        return node_id

    def _find_edge(self, source_id: str, target_id: str, predicate: str) -> Optional[MemoryEdge]:
        """Find existing edge between two nodes with given predicate"""
        for edge in self.edges.values():
            if (edge.source_id == source_id and edge.target_id == target_id and
                edge.predicate == predicate):
                return edge
        return None

    def retrieve(self, query: str, top_k: int = 5, delta_dfs_threshold: Optional[float] = None,
                category_filter: Optional[List[str]] = None) -> List[Dict]:
        """
        Retrieve operation: Get relevant memories using DFS traversal
        Returns list of memory storage sets {M, v_M, v̄, v̂}
        """
        if delta_dfs_threshold is None:
            delta_dfs_threshold = self.delta_dfs_threshold

        query_embedding = self.embedding_function(query)

        return self._retrieve_by_embedding_internal(query_embedding, top_k, delta_dfs_threshold, category_filter)

    def retrieve_by_embedding(self, query_embedding: np.ndarray, top_k: int = 5,
                             delta_dfs_threshold: Optional[float] = None,
                             category_filter: Optional[List[str]] = None) -> List[Dict]:
        """
        Retrieve operation using precomputed embedding (for proactive mode)
        Returns list of memory storage sets {M, v_M, v̄, v̂}

        Args:
            query_embedding: Precomputed embedding vector (e.g., from first frame)
            top_k: Number of top candidates to consider
            delta_dfs_threshold: Similarity threshold for DFS traversal
            category_filter: Optional list of categories to search in
        """
        if delta_dfs_threshold is None:
            delta_dfs_threshold = self.delta_dfs_threshold

        return self._retrieve_by_embedding_internal(query_embedding, top_k, delta_dfs_threshold, category_filter)

    def _retrieve_by_embedding_internal(self, query_embedding: np.ndarray, top_k: int,
                                       delta_dfs_threshold: float,
                                       category_filter: Optional[List[str]] = None) -> List[Dict]:
        """Internal method for retrieval using embedding"""

        # Step 1: Find activated nodes based on query similarity
        activated_nodes = []

        # Search in specific categories if filter provided
        search_categories = category_filter if category_filter else self.memory_forest.keys()

        for category in search_categories:
            for node_id in self.memory_forest[category]:
                if node_id in self.nodes:
                    node = self.nodes[node_id]
                    
                    # Skip nodes with empty caption_embedding (reduced nodes)
                    if node.caption_embedding.size == 0:
                        if self.verbose: 
                            print(f"Skipping node {node.node_label} - caption_embedding reduced")
                        continue
                    
                    similarity = cosine_similarity(query_embedding, node.caption_embedding)
                    if self.verbose: 
                        print(f"{node.node_label} similarity:", similarity)
                    activated_nodes.append((node_id, similarity))

        # Sort by similarity and take top candidates
        activated_nodes.sort(key=lambda x: x[1], reverse=True)
        top_nodes = activated_nodes[:top_k]  # Get more candidates for DFS

        # Step 2: Perform DFS traversal from activated nodes
        visited = set()
        storage_sets = []

        for node_id, similarity in top_nodes:
            if node_id in visited or similarity < delta_dfs_threshold:  # Lower threshold for DFS traversal
                continue

            # DFS from this node
            dfs_result = self._dfs_traverse(node_id, query_embedding, delta_dfs_threshold, visited)
            if dfs_result:
                storage_sets.extend(dfs_result)

        for storage_set in storage_sets:
            self.increment_visit_frequency(storage_set["node_id"])
        return storage_sets

    def _dfs_traverse(self, start_node_id: str, query_embedding: np.ndarray, delta_dfs_threshold: float,
                     visited: Set[str]) -> List[Dict]:
        """Perform DFS traversal and collect storage sets"""

        stack = [start_node_id]
        storage_sets = []

        while stack:
            node_id = stack.pop()
            if node_id in visited:
                continue

            visited.add(node_id)
            node = self.nodes[node_id]

            # Skip nodes with empty caption_embedding (reduced nodes)
            if node.caption_embedding.size == 0:
                if self.verbose:
                    print(f"Skipping reduced node {node.node_label} in DFS traversal")
                # Still traverse connected nodes even if this node is reduced
                for connected_node_id in self.adjacency_list[node_id]:
                    if connected_node_id not in visited:
                        stack.append(connected_node_id)
                continue

            # Calculate similarity with query
            similarity = cosine_similarity(query_embedding, node.caption_embedding)
            if self.verbose: 
                print(f"{node.node_label} similarity:", similarity)

            if similarity >= delta_dfs_threshold:  # Lower threshold for DFS collection
                # Create storage set {M, v_M, v̄, v̂}
                storage_set = {
                    'category': node.category,
                    'node_label': node.node_label,
                    'caption_text': node.caption_text,
                    'caption_embedding': node.caption_embedding,
                    'mean_visual_vector': node.mean_visual_vector,
                    'nearest_visual_vector': node.nearest_visual_vector,
                    'similarity': similarity,
                    'node_id': node_id,
                    'created_at': node.created_at  # Add temporal ordering
                }
                storage_sets.append(storage_set)

            # Always traverse connected nodes regardless of similarity
            for connected_node_id in self.adjacency_list[node_id]:
                if connected_node_id not in visited:
                    stack.append(connected_node_id)

        return storage_sets

    def update(self, node_id: str, 
               new_node_label: Optional[str] = None,
               new_text: Optional[str] = None,
               new_embedding: Optional[np.ndarray] = None,
               new_visual_mean: Optional[np.ndarray] = None,
               new_visual_nearest: Optional[np.ndarray] = None) -> bool:
        """Update operation: Modify existing memory"""

        if node_id not in self.nodes:
            return False

        node = self.nodes[node_id]

        if new_node_label is not None:
            node.node_label = new_node_label
        if new_text is not None:
            node.caption_text = new_text
        if new_embedding is not None:
            node.embedding = new_embedding
        if new_visual_mean is not None:
            node.mean_visual_vector = new_visual_mean
        if new_visual_nearest is not None:
            node.nearest_visual_vector = new_visual_nearest

        node.updated_at = datetime.datetime.now()
        return True

    def delete(self, node_id: str) -> bool:
        """Delete operation: Remove memory node and associated edges"""

        if node_id not in self.nodes:
            return False

        # Remove from tree layer
        for category_nodes in self.memory_forest.values():
            if node_id in category_nodes:
                category_nodes.remove(node_id)

        # Remove from graph layer - collect edges to remove
        edges_to_remove = []
        for edge_id, edge in self.edges.items():
            if edge.source_id == node_id or edge.target_id == node_id:
                edges_to_remove.append(edge_id)

        # Remove edges and update adjacency lists
        for edge_id in edges_to_remove:
            edge = self.edges[edge_id]

            # Update adjacency lists
            if edge.target_id in self.adjacency_list[edge.source_id]:
                self.adjacency_list[edge.source_id].remove(edge.target_id)
            if edge.source_id in self.reverse_adjacency_list[edge.target_id]:
                self.reverse_adjacency_list[edge.target_id].remove(edge.source_id)

            del self.edges[edge_id]

        # Remove node
        del self.nodes[node_id]

        # Clean up empty adjacency lists
        if node_id in self.adjacency_list:
            del self.adjacency_list[node_id]
        if node_id in self.reverse_adjacency_list:
            del self.reverse_adjacency_list[node_id]

        return True

    def reduce(self, target_memory_mb: float, reduction_mode: str = "NSBG") -> Dict:
        """
        Memory reduction operation to manage PMG storage within VRAM constraints.
        
        Args:
            target_memory_mb: Target memory reduction in MB
            reduction_mode: Either "NSBG" (Node Scan by Granularity) or "GSBN" (Granularity Scan by Node)
        
        Returns:
            Dictionary containing reduction statistics
        """
        if reduction_mode not in ["NSBG", "GSBN"]:
            raise ValueError("reduction_mode must be either 'NSBG' or 'GSBN'")
        
        # Track visit frequency for each node (simulated here - in practice would be maintained during CRUD operations)
        if not hasattr(self, 'node_visit_frequency'):
            self.node_visit_frequency = defaultdict(int)
            # Initialize with creation time as proxy for access frequency
            for node_id, node in self.nodes.items():
                # More recent nodes get higher frequency (inverse of days since creation)
                days_since_creation = (datetime.datetime.now() - node.created_at).days
                self.node_visit_frequency[node_id] = max(1, 30 - days_since_creation)
        
        # Calculate current memory usage estimate (in bytes)
        def estimate_node_memory(node: MemoryNode) -> Dict[str, float]:
            """Estimate memory usage for each storage granularity of a node"""
            memory_usage = {}
            
            # M: Raw textual sentence (coarsest granularity)
            memory_usage['M'] = len(node.caption_text.encode('utf-8'))
            
            # v_M: High-dimensional textual semantic embedding  
            memory_usage['v_M'] = node.caption_embedding.nbytes if node.caption_embedding.size > 0 else 0
            
            # v̄: Average visual embedding
            memory_usage['v_mean'] = node.mean_visual_vector.nbytes if node.mean_visual_vector.size > 0 else 0
            
            # v̂: Nearest visual embedding (finest granularity for available data)
            memory_usage['v_nearest'] = node.nearest_visual_vector.nbytes if node.nearest_visual_vector.size > 0 else 0
            
            return memory_usage
        
        # Sort nodes by visit frequency (cold to hot)
        nodes_by_frequency = sorted(
            self.nodes.items(),
            key=lambda x: self.node_visit_frequency[x[0]]
        )
        
        # Track reduction statistics
        reduction_stats = {
            'total_memory_freed_mb': 0.0,
            'nodes_modified': 0,
            'granularities_removed': defaultdict(int),
            'strategy_used': reduction_mode
        }
        
        target_memory_bytes = target_memory_mb * 1024 * 1024
        memory_freed = 0.0
        
        if reduction_mode == "NSBG":
            # Node Scan by Granularity: Traverse nodes from cold to hot,
            # remove storage from fine to coarse granularity
            
            for node_id, node in nodes_by_frequency:
                if memory_freed >= target_memory_bytes:
                    break
                    
                node_memory = estimate_node_memory(node)
                granularities = ['v_nearest', 'v_mean', 'v_M', 'M']  # Fine to coarse
                
                for granularity in granularities:
                    if memory_freed >= target_memory_bytes:
                        break
                        
                    if granularity == 'v_nearest' and node.nearest_visual_vector.size > 0:
                        memory_freed += node_memory['v_nearest']
                        node.nearest_visual_vector = np.array([])
                        reduction_stats['granularities_removed']['v_nearest'] += 1
                        
                    elif granularity == 'v_mean' and node.mean_visual_vector.size > 0:
                        memory_freed += node_memory['v_mean'] 
                        node.mean_visual_vector = np.array([])
                        reduction_stats['granularities_removed']['v_mean'] += 1
                        
                    elif granularity == 'v_M' and node.caption_embedding.size > 0:
                        memory_freed += node_memory['v_M']
                        node.caption_embedding = np.array([])
                        reduction_stats['granularities_removed']['v_M'] += 1
                        
                    elif granularity == 'M' and len(node.caption_text) > 0:
                        # Keep a minimal placeholder to maintain node structure
                        memory_freed += node_memory['M']
                        node.caption_text = "[REDUCED]"
                        reduction_stats['granularities_removed']['M'] += 1
                
                if memory_freed > 0:
                    reduction_stats['nodes_modified'] += 1
                    node.updated_at = datetime.datetime.now()
        
        elif reduction_mode == "GSBN":
            # Granularity Scan by Node: Traverse granularities from fine to coarse,
            # remove from cold to hot nodes
            
            granularities = ['v_nearest', 'v_mean', 'v_M', 'M']  # Fine to coarse
            
            for granularity in granularities:
                if memory_freed >= target_memory_bytes:
                    break
                    
                for node_id, node in nodes_by_frequency:  # Cold to hot
                    if memory_freed >= target_memory_bytes:
                        break
                        
                    node_memory = estimate_node_memory(node)
                    
                    if granularity == 'v_nearest' and node.nearest_visual_vector.size > 0:
                        memory_freed += node_memory['v_nearest']
                        node.nearest_visual_vector = np.array([])
                        reduction_stats['granularities_removed']['v_nearest'] += 1
                        reduction_stats['nodes_modified'] += 1
                        node.updated_at = datetime.datetime.now()
                        
                    elif granularity == 'v_mean' and node.mean_visual_vector.size > 0:
                        memory_freed += node_memory['v_mean']
                        node.mean_visual_vector = np.array([])
                        reduction_stats['granularities_removed']['v_mean'] += 1
                        reduction_stats['nodes_modified'] += 1
                        node.updated_at = datetime.datetime.now()
                        
                    elif granularity == 'v_M' and node.caption_embedding.size > 0:
                        memory_freed += node_memory['v_M']
                        node.caption_embedding = np.array([])
                        reduction_stats['granularities_removed']['v_M'] += 1
                        reduction_stats['nodes_modified'] += 1
                        node.updated_at = datetime.datetime.now()
                        
                    elif granularity == 'M' and len(node.caption_text) > 0:
                        memory_freed += node_memory['M']
                        node.caption_text = "[REDUCED]"
                        reduction_stats['granularities_removed']['M'] += 1
                        reduction_stats['nodes_modified'] += 1
                        node.updated_at = datetime.datetime.now()
        
        reduction_stats['total_memory_freed_mb'] = memory_freed / (1024 * 1024)
        
        if self.verbose:
            print(f"Memory reduction completed using {reduction_mode} strategy:")
            print(f"  - Memory freed: {reduction_stats['total_memory_freed_mb']:.2f} MB")
            print(f"  - Nodes modified: {reduction_stats['nodes_modified']}")
            print(f"  - Granularities removed: {dict(reduction_stats['granularities_removed'])}")
        
        return reduction_stats

    def increment_visit_frequency(self, node_id: str):
        """
        Increment visit frequency for a node (to be called during CRUD operations)
        """
        if not hasattr(self, 'node_visit_frequency'):
            self.node_visit_frequency = defaultdict(int)
        
        self.node_visit_frequency[node_id] += 1

    def get_memory_usage_estimate(self) -> Dict:
        """
        Get estimated memory usage of the PMG
        """
        total_memory = 0.0
        granularity_usage = defaultdict(float)
        
        for node in self.nodes.values():
            # Text memory
            text_memory = len(node.caption_text.encode('utf-8'))
            total_memory += text_memory
            granularity_usage['M'] += text_memory
            
            # Embedding memory
            if node.caption_embedding.size > 0:
                emb_memory = node.caption_embedding.nbytes
                total_memory += emb_memory
                granularity_usage['v_M'] += emb_memory
            
            # Visual vector memory
            if node.mean_visual_vector.size > 0:
                visual_memory = node.mean_visual_vector.nbytes
                total_memory += visual_memory
                granularity_usage['v_mean'] += visual_memory
                
            if node.nearest_visual_vector.size > 0:
                nearest_memory = node.nearest_visual_vector.nbytes
                total_memory += nearest_memory
                granularity_usage['v_nearest'] += nearest_memory
        
        return {
            'total_memory_mb': total_memory / (1024 * 1024),
            'granularity_breakdown_mb': {k: v / (1024 * 1024) for k, v in granularity_usage.items()},
            'total_nodes': len(self.nodes),
            'total_edges': len(self.edges)
        }

    def get_stats(self) -> Dict:
        """Get PMG statistics"""
        category_counts = {cat: len(nodes) for cat, nodes in self.memory_forest.items()}

        return {
            'total_nodes': len(self.nodes),
            'total_edges': len(self.edges),
            'total_categories': len(self.memory_forest),
            'category_distribution': category_counts,
            'sequence_counter': self.sequence_counter
        }

    def save_to_file(self, filepath: str):
        """Save PMG to JSON file"""
        data = {
            'similarity_threshold': self.similarity_threshold,
            'sequence_counter': self.sequence_counter,
            'memory_forest': self.memory_forest,
            'nodes': {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            'edges': {edge_id: edge.to_dict() for edge_id, edge in self.edges.items()},
            'save_timestamp': datetime.datetime.now().isoformat()
        }

        # Convert numpy arrays to lists for JSON serialization
        def convert_numpy(obj):
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {key: convert_numpy(value) for key, value in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy(item) for item in obj]
            else:
                return obj

        json_data = convert_numpy(data)

        with open(filepath, 'w') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

    def load_from_file(self, filepath: str):
        """Load PMG from JSON file"""
        with open(filepath, 'r') as f:
            data = json.load(f)

        self.similarity_threshold = data['similarity_threshold']
        self.sequence_counter = data['sequence_counter']
        self.memory_forest = data['memory_forest']

        # Load nodes
        self.nodes = {}
        for node_id, node_data in data['nodes'].items():
            self.nodes[node_id] = MemoryNode.from_dict(node_data)

        # Load edges and rebuild adjacency lists
        self.edges = {}
        self.adjacency_list = defaultdict(list)
        self.reverse_adjacency_list = defaultdict(list)

        for edge_id, edge_data in data['edges'].items():
            edge = MemoryEdge.from_dict(edge_data)
            self.edges[edge_id] = edge
            self.adjacency_list[edge.source_id].append(edge.target_id)
            self.reverse_adjacency_list[edge.target_id].append(edge.source_id)