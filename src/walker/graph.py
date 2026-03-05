"""
Graph Module
Knowledge graph traversal operators.

Operators:
- GRAPH_NEIGHBORS(graph_node_id, edge_types, k) → neighboring graph nodes
- GRAPH_PATH(graph_node_id, target, max_hops) → path finding

Graph traversal is opportunistic: only used when graph layer is available.
"""

from typing import Optional, List, Set, Tuple, Dict
from dataclasses import dataclass

from src.walker.types import GraphNode, GraphEdge, TreeNode, OperatorType
from src.walker.db import CartridgeDB


@dataclass
class GraphExpansion:
    """Result of a graph expansion operation"""
    source_id: str
    operator: OperatorType
    neighbors: List[Tuple[GraphNode, GraphEdge]]
    hops: int = 1


class GraphOperators:
    """
    Knowledge graph traversal operators.
    Maps between graph_nodes and tree_nodes via join keys.
    """
    
    # High-signal edge types (prioritized in traversal)
    HIGH_SIGNAL_EDGES = frozenset({
        "calls", "imports", "implements", "defines", "references",
        "CALLS", "IMPORTS", "IMPLEMENTS", "DEFINES", "REFERENCES",
        "contains", "CONTAINS", "uses", "USES",
    })
    
    def __init__(self, db: CartridgeDB):
        self.db = db
        
        self.stats = {
            "neighbor_ops": 0,
            "path_ops": 0,
            "tree_map_ops": 0,
        }
    
    def reset_stats(self):
        for key in self.stats:
            self.stats[key] = 0
    
    # =========================================================================
    # Basic Graph Operations
    # =========================================================================
    
    def get_node(self, graph_node_id: str) -> Optional[GraphNode]:
        """Get a graph node by ID"""
        return self.db.get_graph_node(graph_node_id)
    
    def get_edges_from(self, graph_node_id: str) -> List[GraphEdge]:
        """Get outgoing edges from a graph node"""
        return self.db.get_graph_edges_from(graph_node_id)
    
    def get_edges_to(self, graph_node_id: str) -> List[GraphEdge]:
        """Get incoming edges to a graph node"""
        return self.db.get_graph_edges_to(graph_node_id)
    
    def get_edge_types(self) -> List[str]:
        """Get all unique edge types in the graph"""
        return self.db.get_all_edge_types()
    
    # =========================================================================
    # Graph ↔ Tree Mapping
    # =========================================================================
    
    def graph_to_tree_node(self, graph_node_id: str) -> Optional[TreeNode]:
        """Map graph node to its associated tree node"""
        self.stats["tree_map_ops"] += 1
        
        gnode = self.db.get_graph_node(graph_node_id)
        if not gnode or not gnode.tree_node_id:
            return None
        return self.db.get_tree_node(gnode.tree_node_id)
    
    def tree_to_graph_node(self, tree_node_id: str) -> Optional[GraphNode]:
        """Map tree node to its associated graph node"""
        self.stats["tree_map_ops"] += 1
        
        tnode = self.db.get_tree_node(tree_node_id)
        if not tnode or not tnode.graph_node_id:
            return None
        return self.db.get_graph_node(tnode.graph_node_id)
    
    # =========================================================================
    # Neighbor Expansion
    # =========================================================================
    
    def neighbors(self, graph_node_id: str,
                   edge_types: Optional[List[str]] = None,
                   k: int = 10,
                   include_incoming: bool = False
                   ) -> List[Tuple[GraphNode, GraphEdge]]:
        """
        GRAPH_NEIGHBORS(graph_node_id, edge_types, k)
        
        Returns list of (neighbor_node, connecting_edge) tuples,
        sorted by edge weight descending.
        """
        self.stats["neighbor_ops"] += 1
        
        result = []
        
        # Outgoing edges
        edges = self.get_edges_from(graph_node_id)
        
        # Optionally include incoming
        if include_incoming:
            edges.extend(self.get_edges_to(graph_node_id))
        
        # Filter by edge types
        if edge_types:
            edge_types_lower = {t.lower() for t in edge_types}
            edges = [e for e in edges if e.edge_type.lower() in edge_types_lower]
        
        # Sort by weight
        edges.sort(key=lambda e: e.weight, reverse=True)
        
        # Resolve neighbor nodes
        seen = set()
        for edge in edges:
            target_id = edge.target_id
            if edge.source_id != graph_node_id:
                target_id = edge.source_id  # For incoming edges
            
            if target_id in seen:
                continue
            seen.add(target_id)
            
            target_node = self.db.get_graph_node(target_id)
            if target_node:
                result.append((target_node, edge))
            
            if len(result) >= k:
                break
        
        return result
    
    def neighbors_with_tree(self, graph_node_id: str,
                             edge_types: Optional[List[str]] = None,
                             k: int = 10
                             ) -> List[Tuple[GraphNode, GraphEdge, Optional[TreeNode]]]:
        """
        Get neighbors with their associated tree nodes.
        Returns (graph_node, edge, tree_node) tuples.
        """
        neighbors = self.neighbors(graph_node_id, edge_types, k)
        
        result = []
        for gnode, edge in neighbors:
            tnode = None
            if gnode.tree_node_id:
                tnode = self.db.get_tree_node(gnode.tree_node_id)
            result.append((gnode, edge, tnode))
        
        return result
    
    # =========================================================================
    # Expansion Operation (for walker)
    # =========================================================================
    
    def expand_graph(self, graph_node_id: str,
                      edge_types: Optional[List[str]] = None,
                      k: int = 5,
                      visited: Optional[Set[str]] = None
                      ) -> GraphExpansion:
        """
        Expand from a graph node to neighbors.
        Filters out already-visited nodes.
        """
        visited = visited or set()
        
        all_neighbors = self.neighbors(
            graph_node_id,
            edge_types=edge_types,
            k=k * 2,  # Fetch more to account for filtering
            include_incoming=False
        )
        
        # Filter visited
        filtered = [
            (gn, edge) for gn, edge in all_neighbors
            if gn.node_id not in visited
        ][:k]
        
        return GraphExpansion(
            source_id=graph_node_id,
            operator=OperatorType.GRAPH_NEIGHBORS,
            neighbors=filtered,
            hops=1
        )
    
    def expand_from_tree_node(self, tree_node_id: str,
                               edge_types: Optional[List[str]] = None,
                               k: int = 5,
                               visited_graph: Optional[Set[str]] = None
                               ) -> GraphExpansion:
        """
        Expand graph from a tree node.
        Maps tree → graph, expands, returns graph results.
        """
        gnode = self.tree_to_graph_node(tree_node_id)
        if not gnode:
            return GraphExpansion(
                source_id=tree_node_id,
                operator=OperatorType.GRAPH_NEIGHBORS,
                neighbors=[],
                hops=0
            )
        
        return self.expand_graph(gnode.node_id, edge_types, k, visited_graph)
    
    # =========================================================================
    # Path Finding
    # =========================================================================
    
    def find_path(self, source_id: str, target_id: str,
                   max_hops: int = 3,
                   edge_types: Optional[List[str]] = None
                   ) -> Optional[List[str]]:
        """
        GRAPH_PATH(source, target, max_hops)
        
        BFS path finding. Returns list of node IDs if path exists.
        """
        self.stats["path_ops"] += 1
        
        if source_id == target_id:
            return [source_id]
        
        # BFS
        queue = [(source_id, [source_id])]
        seen = {source_id}
        
        while queue:
            current_id, path = queue.pop(0)
            
            if len(path) > max_hops:
                continue
            
            neighbors = self.neighbors(
                current_id,
                edge_types=edge_types,
                k=20,
                include_incoming=True
            )
            
            for gnode, _ in neighbors:
                if gnode.node_id == target_id:
                    return path + [target_id]
                
                if gnode.node_id not in seen:
                    seen.add(gnode.node_id)
                    queue.append((gnode.node_id, path + [gnode.node_id]))
        
        return None
    
    # =========================================================================
    # Distance and Proximity
    # =========================================================================
    
    def graph_distance(self, source_id: str, target_id: str,
                        max_hops: int = 5) -> int:
        """
        Calculate shortest path distance in graph.
        Returns -1 if no path within max_hops.
        """
        path = self.find_path(source_id, target_id, max_hops)
        if path is None:
            return -1
        return len(path) - 1
    
    def graph_proximity(self, source_id: str, target_id: str,
                         max_hops: int = 3) -> float:
        """
        Calculate graph proximity score (0.0 to 1.0).
        1.0 = same node
        Decays with hop count.
        Boosts high-signal edge types.
        """
        dist = self.graph_distance(source_id, target_id, max_hops)
        if dist < 0:
            return 0.0
        if dist == 0:
            return 1.0
        
        # Decay: 1 / (1 + 0.5 * distance)
        base_score = 1.0 / (1.0 + 0.5 * dist)
        
        # Check if connected by high-signal edge
        if dist == 1:
            edges = self.get_edges_from(source_id)
            for edge in edges:
                if edge.target_id == target_id:
                    if edge.edge_type in self.HIGH_SIGNAL_EDGES:
                        base_score *= 1.3  # Boost
                    break
        
        return min(base_score, 1.0)
    
    # =========================================================================
    # Utility
    # =========================================================================
    
    def is_high_signal_edge(self, edge_type: str) -> bool:
        """Check if edge type is high-signal"""
        return edge_type.lower() in {e.lower() for e in self.HIGH_SIGNAL_EDGES}
    
    def filter_high_signal_edges(self, edges: List[GraphEdge]) -> List[GraphEdge]:
        """Filter to only high-signal edges"""
        return [e for e in edges if self.is_high_signal_edge(e.edge_type)]
