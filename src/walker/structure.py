"""
Structure Module
Tree traversal operators for the structural layer.

Operators:
- PARENT(node_id) → parent node
- CHILDREN(node_id) → child nodes
- SIBLINGS(node_id) → nodes sharing parent
- ANCESTORS(node_id) → root to parent chain
- DESCENDANTS(node_id, depth_limit) → all descendants
"""

from typing import Optional, List, Set, Iterator, Dict, Any
from dataclasses import dataclass

from src.walker.types import TreeNode, OperatorType
from src.walker.db import CartridgeDB


@dataclass
class StructuralExpansion:
    """Result of a structural expansion operation"""
    source_id: str
    operator: OperatorType
    targets: List[TreeNode]
    depth: int = 0


class StructureOperators:
    """
    Structural traversal operators.
    All operations work on tree_nodes as the primary anchor.
    """
    
    def __init__(self, db: CartridgeDB):
        self.db = db
        
        # Expansion stats
        self.stats = {
            "parent_ops": 0,
            "children_ops": 0,
            "siblings_ops": 0,
            "ancestors_ops": 0,
            "descendants_ops": 0,
        }
    
    def reset_stats(self):
        """Reset operation statistics"""
        for key in self.stats:
            self.stats[key] = 0
    
    # =========================================================================
    # Basic Operators
    # =========================================================================
    
    def parent(self, node_id: str) -> Optional[TreeNode]:
        """
        PARENT(node_id) → parent node or None
        """
        self.stats["parent_ops"] += 1
        return self.db.get_tree_parent(node_id)
    
    def children(self, node_id: str) -> List[TreeNode]:
        """
        CHILDREN(node_id) → child nodes (ordered by line_start, name)
        """
        self.stats["children_ops"] += 1
        return self.db.get_tree_children(node_id)
    
    def siblings(self, node_id: str) -> List[TreeNode]:
        """
        SIBLINGS(node_id) → nodes sharing same parent (excluding self)
        """
        self.stats["siblings_ops"] += 1
        return self.db.get_tree_siblings(node_id)
    
    # =========================================================================
    # Chain Operators
    # =========================================================================
    
    def ancestors(self, node_id: str, max_depth: int = 100) -> List[TreeNode]:
        """
        ANCESTORS(node_id) → chain from root to parent
        Returns [root, ..., grandparent, parent]
        """
        self.stats["ancestors_ops"] += 1
        
        ancestors = []
        current = self.db.get_tree_node(node_id)
        depth = 0
        
        while current and current.parent_id and depth < max_depth:
            parent = self.db.get_tree_node(current.parent_id)
            if parent:
                ancestors.insert(0, parent)
                current = parent
                depth += 1
            else:
                break
        
        return ancestors
    
    def descendants(self, node_id: str, max_depth: int = 5) -> List[TreeNode]:
        """
        DESCENDANTS(node_id, depth_limit) → all descendants via BFS
        Returns nodes ordered by discovery (level by level)
        """
        self.stats["descendants_ops"] += 1
        
        result = []
        queue = [(node_id, 0)]
        seen = {node_id}
        
        while queue:
            current_id, depth = queue.pop(0)
            
            if depth >= max_depth:
                continue
            
            children = self.db.get_tree_children(current_id)
            for child in children:
                if child.node_id not in seen:
                    seen.add(child.node_id)
                    result.append(child)
                    queue.append((child.node_id, depth + 1))
        
        return result
    
    # =========================================================================
    # Expansion Operations (for walker)
    # =========================================================================
    
    def expand_structural(self, node_id: str, 
                          include_parent: bool = True,
                          include_children: bool = True,
                          include_siblings: bool = True,
                          max_siblings: int = 5,
                          visited: Optional[Set[str]] = None
                          ) -> StructuralExpansion:
        """
        Expand from a node in all structural directions.
        Returns unified expansion result with all reachable nodes.
        """
        visited = visited or set()
        targets = []
        
        if include_parent:
            parent = self.parent(node_id)
            if parent and parent.node_id not in visited:
                targets.append(parent)
        
        if include_children:
            for child in self.children(node_id):
                if child.node_id not in visited:
                    targets.append(child)
        
        if include_siblings:
            sibs = self.siblings(node_id)[:max_siblings]
            for sib in sibs:
                if sib.node_id not in visited:
                    targets.append(sib)
        
        # Dedupe while preserving order
        seen = set()
        unique = []
        for t in targets:
            if t.node_id not in seen:
                seen.add(t.node_id)
                unique.append(t)
        
        return StructuralExpansion(
            source_id=node_id,
            operator=OperatorType.CHILDREN,  # Primary operator
            targets=unique,
            depth=0
        )
    
    # =========================================================================
    # Query Operators
    # =========================================================================
    
    def roots(self) -> List[TreeNode]:
        """Get all root nodes (no parent)"""
        return self.db.get_tree_roots()
    
    def by_type(self, node_type: str) -> List[TreeNode]:
        """Get all nodes of a specific type"""
        return self.db.get_tree_nodes_by_type(node_type)
    
    def by_file(self, file_cid: str) -> List[TreeNode]:
        """Get all nodes for a file"""
        return self.db.get_tree_nodes_by_file(file_cid)
    
    def get_node(self, node_id: str) -> Optional[TreeNode]:
        """Get a single node by ID"""
        return self.db.get_tree_node(node_id)
    
    # =========================================================================
    # Path Operations
    # =========================================================================
    
    def get_path_to_root(self, node_id: str) -> List[str]:
        """Get node_id path from root to this node"""
        ancestors = self.ancestors(node_id)
        path = [a.node_id for a in ancestors]
        path.append(node_id)
        return path
    
    def get_path_string(self, node_id: str) -> str:
        """Get human-readable path string"""
        node = self.db.get_tree_node(node_id)
        if node:
            return node.path
        return ""
    
    def find_common_ancestor(self, node_id_a: str, node_id_b: str
                              ) -> Optional[TreeNode]:
        """Find lowest common ancestor of two nodes"""
        path_a = set(self.get_path_to_root(node_id_a))
        
        # Walk up from B until we hit something in A's path
        current = self.db.get_tree_node(node_id_b)
        while current:
            if current.node_id in path_a:
                return current
            if current.parent_id:
                current = self.db.get_tree_node(current.parent_id)
            else:
                break
        
        return None
    
    # =========================================================================
    # Distance Calculation
    # =========================================================================
    
    def tree_distance(self, node_id_a: str, node_id_b: str) -> int:
        """
        Calculate tree distance between two nodes.
        Distance = steps to common ancestor + steps back down.
        Returns -1 if no path exists.
        """
        if node_id_a == node_id_b:
            return 0
        
        # Get paths to root
        path_a = self.get_path_to_root(node_id_a)
        path_b = self.get_path_to_root(node_id_b)
        
        # Find divergence point
        common_len = 0
        for i in range(min(len(path_a), len(path_b))):
            if path_a[i] == path_b[i]:
                common_len = i + 1
            else:
                break
        
        if common_len == 0:
            return -1  # No common ancestor (shouldn't happen in valid tree)
        
        # Distance = steps up from A + steps down to B
        steps_up = len(path_a) - common_len
        steps_down = len(path_b) - common_len
        
        return steps_up + steps_down
    
    def structural_proximity(self, node_id_a: str, node_id_b: str) -> float:
        """
        Calculate structural proximity score (0.0 to 1.0).
        1.0 = same node
        Decays with tree distance.
        """
        dist = self.tree_distance(node_id_a, node_id_b)
        if dist < 0:
            return 0.0
        if dist == 0:
            return 1.0
        
        # Decay function: 1 / (1 + 0.3 * distance)
        return 1.0 / (1.0 + 0.3 * dist)
