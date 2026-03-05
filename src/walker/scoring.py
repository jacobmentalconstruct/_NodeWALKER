"""
Scoring Module
Scoring formula and budget management for traversal.

Score Components:
- S_sem: semantic similarity (vector distance)
- S_struct: structural proximity (tree distance decay)
- S_adj: adjacency proximity (chunk chain distance decay)
- S_graph: graph proximity (hop count decay + edge type boost)
- S_source: provenance/trust boost

Total Score:
S_total = w1*S_sem + w2*S_struct + w3*S_adj + w4*S_graph + w5*S_source - w6*dup_penalty - anti_data_penalty
"""

from typing import List, Set, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
import heapq

from src.walker.types import (
    ScoreComponents, ScoreWeights, Budgets, 
    ExpansionCandidate, TraversalPolicy, OperatorType
)


@dataclass
class BudgetState:
    """Current budget consumption"""
    nodes_visited: int = 0
    chunks_collected: int = 0
    lines_collected: int = 0
    graph_hops: int = 0
    total_expansions: int = 0
    
    def within_budget(self, budgets: Budgets) -> bool:
        """Check if still within all budgets"""
        return (
            self.nodes_visited < budgets.max_nodes and
            self.chunks_collected < budgets.max_chunks and
            self.lines_collected < budgets.max_lines and
            self.graph_hops <= budgets.max_graph_hops and
            self.total_expansions < budgets.max_expansions
        )
    
    def to_dict(self) -> Dict[str, int]:
        return {
            "nodes_visited": self.nodes_visited,
            "chunks_collected": self.chunks_collected,
            "lines_collected": self.lines_collected,
            "graph_hops": self.graph_hops,
            "total_expansions": self.total_expansions,
        }


class Scorer:
    """
    Scores expansion candidates and manages budgets.
    """
    
    def __init__(self, policy: TraversalPolicy):
        self.policy = policy
        self.weights = policy.weights
        self.budgets = policy.budgets
        
        # State
        self.budget_state = BudgetState()
        self.visited_nodes: Set[str] = set()
        self.visited_chunks: Set[str] = set()
        
        # Priority queue for candidates
        # Items: (-score, candidate_id, candidate)
        self._candidate_heap: List[Tuple[float, int, ExpansionCandidate]] = []
        self._candidate_counter = 0
        
        # Score history for marginal gain calculation
        self._recent_scores: List[float] = []
        self._marginal_threshold = policy.budgets.marginal_gain_threshold
    
    def reset(self):
        """Reset scorer state"""
        self.budget_state = BudgetState()
        self.visited_nodes.clear()
        self.visited_chunks.clear()
        self._candidate_heap.clear()
        self._candidate_counter = 0
        self._recent_scores.clear()
    
    # =========================================================================
    # Score Calculation
    # =========================================================================
    
    def compute_score(self, candidate: ExpansionCandidate) -> float:
        """
        Compute total score for a candidate.
        
        S_total = w1*S_sem + w2*S_struct + w3*S_adj + w4*S_graph + w5*S_source
                  - w6*dup_penalty - anti_data_penalty
        """
        components = candidate.score
        
        total = (
            self.weights.semantic * components.semantic +
            self.weights.structural * components.structural +
            self.weights.adjacency * components.adjacency +
            self.weights.graph * components.graph +
            self.weights.source * components.source -
            self.weights.duplicate * components.duplicate_penalty -
            components.anti_data_penalty
        )
        
        return total
    
    def compute_structural_score(self, tree_distance: int) -> float:
        """
        S_struct: decays by tree distance.
        1.0 at same node, 0.7 parent/child, etc.
        """
        if tree_distance < 0:
            return 0.0
        if tree_distance == 0:
            return 1.0
        
        # Decay: 1 / (1 + 0.3 * distance)
        return 1.0 / (1.0 + 0.3 * tree_distance)
    
    def compute_adjacency_score(self, chunk_distance: int) -> float:
        """
        S_adj: decays by chunk chain distance.
        """
        if chunk_distance < 0:
            return 0.0
        if chunk_distance == 0:
            return 1.0
        
        # Decay: 1 / (1 + 0.4 * distance)
        return 1.0 / (1.0 + 0.4 * chunk_distance)
    
    def compute_graph_score(self, hop_count: int, 
                             edge_type: Optional[str] = None) -> float:
        """
        S_graph: decays by hop count.
        Boosted for high-signal edge types.
        """
        if hop_count < 0:
            return 0.0
        if hop_count == 0:
            return 1.0
        
        # Base decay
        base = 1.0 / (1.0 + 0.5 * hop_count)
        
        # Boost for high-signal edges
        if edge_type and hop_count == 1:
            high_signal = {
                "calls", "imports", "implements", "defines", "references",
                "CALLS", "IMPORTS", "IMPLEMENTS", "DEFINES", "REFERENCES",
            }
            if edge_type in high_signal:
                base *= 1.3
        
        return min(base, 1.0)
    
    def compute_source_score(self, has_spans: bool, 
                              has_provenance: bool) -> float:
        """
        S_source: trust/provenance boost.
        """
        score = 0.5  # Base
        if has_spans:
            score += 0.3
        if has_provenance:
            score += 0.2
        return min(score, 1.0)
    
    def compute_duplicate_penalty(self, target_id: str) -> float:
        """
        Penalty for revisiting already-visited nodes/chunks.
        """
        if target_id in self.visited_nodes or target_id in self.visited_chunks:
            return 1.0
        return 0.0
    
    # =========================================================================
    # Candidate Management
    # =========================================================================
    
    def add_candidate(self, candidate: ExpansionCandidate):
        """Add a candidate to the priority queue"""
        score = self.compute_score(candidate)
        
        # Skip if below threshold
        if score < self.budgets.min_score_threshold:
            return
        
        # Use negative score for min-heap (we want max)
        self._candidate_counter += 1
        heapq.heappush(
            self._candidate_heap,
            (-score, self._candidate_counter, candidate)
        )
    
    def add_candidates(self, candidates: List[ExpansionCandidate]):
        """Add multiple candidates"""
        for c in candidates:
            self.add_candidate(c)
    
    def pop_best_candidate(self) -> Optional[ExpansionCandidate]:
        """
        Pop the highest-scoring candidate.
        Returns None if queue empty or all below threshold.
        """
        while self._candidate_heap:
            neg_score, _, candidate = heapq.heappop(self._candidate_heap)
            score = -neg_score
            
            # Re-check against current state (may have been visited since added)
            dup_penalty = self.compute_duplicate_penalty(candidate.target_id)
            if dup_penalty > 0:
                continue  # Skip duplicates
            
            # Check score threshold
            if score < self.budgets.min_score_threshold:
                continue
            
            # Track for marginal gain
            self._recent_scores.append(score)
            if len(self._recent_scores) > 10:
                self._recent_scores.pop(0)
            
            return candidate
        
        return None
    
    def has_candidates(self) -> bool:
        """Check if there are candidates in queue"""
        return len(self._candidate_heap) > 0
    
    def candidate_count(self) -> int:
        """Number of candidates in queue"""
        return len(self._candidate_heap)
    
    # =========================================================================
    # Budget Management
    # =========================================================================
    
    def mark_node_visited(self, node_id: str):
        """Mark a node as visited"""
        if node_id not in self.visited_nodes:
            self.visited_nodes.add(node_id)
            self.budget_state.nodes_visited += 1
    
    def mark_chunk_collected(self, chunk_id: str, line_count: int = 0):
        """Mark a chunk as collected"""
        if chunk_id not in self.visited_chunks:
            self.visited_chunks.add(chunk_id)
            self.budget_state.chunks_collected += 1
            self.budget_state.lines_collected += line_count
    
    def mark_expansion(self):
        """Mark an expansion operation"""
        self.budget_state.total_expansions += 1
    
    def mark_graph_hop(self):
        """Mark a graph hop"""
        self.budget_state.graph_hops += 1
    
    def within_budget(self) -> bool:
        """Check if still within all budgets"""
        return self.budget_state.within_budget(self.budgets)
    
    def should_stop(self) -> bool:
        """
        Check if traversal should stop.
        Based on: budgets, marginal gain, empty queue.
        """
        # Budget exhausted
        if not self.within_budget():
            return True
        
        # No more candidates
        if not self.has_candidates():
            return True
        
        # Marginal gain check
        if self._should_stop_marginal():
            return True
        
        return False
    
    def _should_stop_marginal(self) -> bool:
        """Check if marginal gain has dropped below threshold"""
        if len(self._recent_scores) < 5:
            return False
        
        # Check if recent scores are all low
        recent_avg = sum(self._recent_scores[-5:]) / 5
        if recent_avg < self._marginal_threshold:
            return True
        
        # Check if scores are declining
        if len(self._recent_scores) >= 10:
            first_half = sum(self._recent_scores[:5]) / 5
            second_half = sum(self._recent_scores[5:]) / 5
            if second_half < first_half * 0.5:
                return True
        
        return False
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def create_candidate(self,
                          target_id: str,
                          target_type: str,
                          operator: OperatorType,
                          source_id: str,
                          semantic: float = 0.0,
                          structural: float = 0.0,
                          adjacency: float = 0.0,
                          graph: float = 0.0,
                          source: float = 0.5,
                          edge_type: Optional[str] = None,
                          distance: int = 0
                          ) -> ExpansionCandidate:
        """Create a candidate with score components"""
        dup_penalty = self.compute_duplicate_penalty(target_id)
        
        return ExpansionCandidate(
            target_id=target_id,
            target_type=target_type,
            operator=operator,
            source_id=source_id,
            score=ScoreComponents(
                semantic=semantic,
                structural=structural,
                adjacency=adjacency,
                graph=graph,
                source=source,
                duplicate_penalty=dup_penalty,
                anti_data_penalty=0.0,  # To be filled by anti-data engine
            ),
            edge_type=edge_type,
            distance=distance,
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scorer statistics"""
        return {
            "budget": self.budget_state.to_dict(),
            "visited_nodes": len(self.visited_nodes),
            "visited_chunks": len(self.visited_chunks),
            "candidates_remaining": self.candidate_count(),
            "recent_scores": self._recent_scores[-5:] if self._recent_scores else [],
        }
