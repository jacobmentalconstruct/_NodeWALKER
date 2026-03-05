"""
Chunks Module
Chunk operators for the semantic layer.

Operators:
- NODE_TO_CHUNKS(node_id) → chunk ids for a tree node
- CHUNK_TO_NODE(chunk_id) → node id for a chunk
- CHUNK_PREV(chunk_id) / CHUNK_NEXT(chunk_id) → adjacency via overlap
- CHUNK_WINDOW(chunk_id, radius) → chunks in continuity window
"""

from typing import Optional, List, Set, Tuple, Dict
from dataclasses import dataclass

from src.walker.types import ChunkManifest, TreeNode, OperatorType, ChunkOverlap
from src.walker.db import CartridgeDB
from src.walker.cas import CASResolver


@dataclass
class ChunkExpansion:
    """Result of a chunk expansion operation"""
    source_id: str
    operator: OperatorType
    chunks: List[ChunkManifest]
    direction: str = ""  # "prev", "next", "both"


class ChunkOperators:
    """
    Chunk traversal operators.
    Handles node↔chunk mapping and overlap-based continuity.
    """
    
    def __init__(self, db: CartridgeDB, cas: CASResolver):
        self.db = db
        self.cas = cas
        
        self.stats = {
            "node_to_chunk_ops": 0,
            "chunk_to_node_ops": 0,
            "chunk_prev_ops": 0,
            "chunk_next_ops": 0,
            "chunk_window_ops": 0,
        }
    
    def reset_stats(self):
        for key in self.stats:
            self.stats[key] = 0
    
    # =========================================================================
    # Node ↔ Chunk Mapping
    # =========================================================================
    
    def node_to_chunks(self, node_id: str) -> List[ChunkManifest]:
        """
        NODE_TO_CHUNKS(node_id) → chunks associated with this tree node
        """
        self.stats["node_to_chunk_ops"] += 1
        return self.db.get_chunks_for_node(node_id)
    
    def node_to_chunk_ids(self, node_id: str) -> List[str]:
        """Get chunk IDs for a node"""
        chunks = self.node_to_chunks(node_id)
        return [c.chunk_id for c in chunks]
    
    def chunk_to_node(self, chunk_id: str) -> Optional[TreeNode]:
        """
        CHUNK_TO_NODE(chunk_id) → tree node for this chunk
        """
        self.stats["chunk_to_node_ops"] += 1
        chunk = self.db.get_chunk(chunk_id)
        if not chunk or not chunk.node_id:
            return None
        return self.db.get_tree_node(chunk.node_id)
    
    def chunk_to_node_id(self, chunk_id: str) -> Optional[str]:
        """Get node_id for a chunk"""
        chunk = self.db.get_chunk(chunk_id)
        return chunk.node_id if chunk else None
    
    # =========================================================================
    # Overlap-Based Adjacency
    # =========================================================================
    
    def chunk_prev(self, chunk_id: str) -> Optional[ChunkManifest]:
        """
        CHUNK_PREV(chunk_id) → previous chunk via overlap.prev_chunk_id
        """
        self.stats["chunk_prev_ops"] += 1
        chunk = self.db.get_chunk(chunk_id)
        if not chunk or not chunk.overlap.prev_chunk_id:
            return None
        return self.db.get_chunk(chunk.overlap.prev_chunk_id)
    
    def chunk_next(self, chunk_id: str) -> Optional[ChunkManifest]:
        """
        CHUNK_NEXT(chunk_id) → next chunk via overlap.next_chunk_id
        """
        self.stats["chunk_next_ops"] += 1
        chunk = self.db.get_chunk(chunk_id)
        if not chunk or not chunk.overlap.next_chunk_id:
            return None
        return self.db.get_chunk(chunk.overlap.next_chunk_id)
    
    def chunk_adjacent(self, chunk_id: str
                        ) -> Tuple[Optional[ChunkManifest], Optional[ChunkManifest]]:
        """Get both prev and next chunks"""
        return (self.chunk_prev(chunk_id), self.chunk_next(chunk_id))
    
    # =========================================================================
    # Chunk Window (Continuity Context)
    # =========================================================================
    
    def chunk_window(self, chunk_id: str, radius: int = 2,
                      visited: Optional[Set[str]] = None
                      ) -> List[ChunkManifest]:
        """
        CHUNK_WINDOW(chunk_id, radius) → chunks in continuity window
        
        Returns up to `radius` chunks before and after in the overlap chain.
        Ordered: [...prev_chunks, center, ...next_chunks]
        """
        self.stats["chunk_window_ops"] += 1
        
        visited = visited or set()
        center = self.db.get_chunk(chunk_id)
        if not center:
            return []
        
        prev_chunks = []
        next_chunks = []
        
        # Walk backwards
        current = center
        for _ in range(radius):
            if not current.overlap.prev_chunk_id:
                break
            if current.overlap.prev_chunk_id in visited:
                break
            prev = self.db.get_chunk(current.overlap.prev_chunk_id)
            if not prev:
                break
            prev_chunks.insert(0, prev)
            current = prev
        
        # Walk forwards
        current = center
        for _ in range(radius):
            if not current.overlap.next_chunk_id:
                break
            if current.overlap.next_chunk_id in visited:
                break
            nxt = self.db.get_chunk(current.overlap.next_chunk_id)
            if not nxt:
                break
            next_chunks.append(nxt)
            current = nxt
        
        return prev_chunks + [center] + next_chunks
    
    def chunk_window_ids(self, chunk_id: str, radius: int = 2,
                          visited: Optional[Set[str]] = None) -> List[str]:
        """Get chunk IDs in window"""
        chunks = self.chunk_window(chunk_id, radius, visited)
        return [c.chunk_id for c in chunks]
    
    # =========================================================================
    # Expansion Operations (for walker)
    # =========================================================================
    
    def expand_adjacency(self, chunk_id: str,
                          radius: int = 1,
                          include_prev: bool = True,
                          include_next: bool = True,
                          visited: Optional[Set[str]] = None
                          ) -> ChunkExpansion:
        """
        Expand along chunk adjacency chain.
        """
        visited = visited or set()
        chunks = []
        
        center = self.db.get_chunk(chunk_id)
        if not center:
            return ChunkExpansion(
                source_id=chunk_id,
                operator=OperatorType.CHUNK_WINDOW,
                chunks=[],
                direction="both"
            )
        
        direction = "both"
        if include_prev and not include_next:
            direction = "prev"
        elif include_next and not include_prev:
            direction = "next"
        
        # Walk prev
        if include_prev:
            current = center
            for _ in range(radius):
                if not current.overlap.prev_chunk_id:
                    break
                if current.overlap.prev_chunk_id in visited:
                    break
                prev = self.db.get_chunk(current.overlap.prev_chunk_id)
                if prev:
                    chunks.append(prev)
                    current = prev
                else:
                    break
        
        # Walk next
        if include_next:
            current = center
            for _ in range(radius):
                if not current.overlap.next_chunk_id:
                    break
                if current.overlap.next_chunk_id in visited:
                    break
                nxt = self.db.get_chunk(current.overlap.next_chunk_id)
                if nxt:
                    chunks.append(nxt)
                    current = nxt
                else:
                    break
        
        return ChunkExpansion(
            source_id=chunk_id,
            operator=OperatorType.CHUNK_WINDOW,
            chunks=chunks,
            direction=direction
        )
    
    # =========================================================================
    # Query Operators
    # =========================================================================
    
    def get_chunk(self, chunk_id: str) -> Optional[ChunkManifest]:
        """Get a single chunk"""
        return self.db.get_chunk(chunk_id)
    
    def by_type(self, chunk_type: str) -> List[ChunkManifest]:
        """Get all chunks of a type"""
        return self.db.get_chunks_by_type(chunk_type)
    
    # =========================================================================
    # Content Resolution
    # =========================================================================
    
    def get_content(self, chunk_id: str) -> str:
        """Get resolved content for a chunk"""
        return self.cas.resolve_chunk_by_id(chunk_id) or ""
    
    def get_content_batch(self, chunk_ids: List[str]) -> Dict[str, str]:
        """Get content for multiple chunks"""
        return self.cas.resolve_chunks_batch(chunk_ids)
    
    # =========================================================================
    # Distance Calculation
    # =========================================================================
    
    def adjacency_distance(self, chunk_id_a: str, chunk_id_b: str,
                           max_search: int = 20) -> int:
        """
        Calculate distance in the overlap chain.
        Returns -1 if not connected within max_search.
        """
        if chunk_id_a == chunk_id_b:
            return 0
        
        # BFS in both directions
        seen = {chunk_id_a}
        queue = [(chunk_id_a, 0)]
        
        while queue and len(seen) < max_search:
            current_id, dist = queue.pop(0)
            chunk = self.db.get_chunk(current_id)
            if not chunk:
                continue
            
            # Check prev
            if chunk.overlap.prev_chunk_id:
                if chunk.overlap.prev_chunk_id == chunk_id_b:
                    return dist + 1
                if chunk.overlap.prev_chunk_id not in seen:
                    seen.add(chunk.overlap.prev_chunk_id)
                    queue.append((chunk.overlap.prev_chunk_id, dist + 1))
            
            # Check next
            if chunk.overlap.next_chunk_id:
                if chunk.overlap.next_chunk_id == chunk_id_b:
                    return dist + 1
                if chunk.overlap.next_chunk_id not in seen:
                    seen.add(chunk.overlap.next_chunk_id)
                    queue.append((chunk.overlap.next_chunk_id, dist + 1))
        
        return -1
    
    def adjacency_proximity(self, chunk_id_a: str, chunk_id_b: str) -> float:
        """
        Calculate adjacency proximity score (0.0 to 1.0).
        1.0 = same chunk or adjacent
        Decays with chain distance.
        """
        dist = self.adjacency_distance(chunk_id_a, chunk_id_b)
        if dist < 0:
            return 0.0
        if dist == 0:
            return 1.0
        
        # Decay: 1 / (1 + 0.4 * distance)
        return 1.0 / (1.0 + 0.4 * dist)
