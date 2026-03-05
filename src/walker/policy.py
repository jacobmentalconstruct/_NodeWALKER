"""
Policy Module
Pre-walk policy selection based on manifest, notes, and anti-data.

Policy selection flow:
1. Read cartridge_manifest + latest ingest_runs
2. Compute signature_hash
3. Query notes DB for matching profiles
4. Apply anti_data_rules
5. Produce a TraversalPolicy
"""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from src.walker.types import (
    TraversalPolicy, TraversalMode, Budgets, ScoreWeights,
    CartridgeManifest
)
from src.walker.db import CartridgeDB
from src.walker.manifest import ManifestReader, ReadinessReport
from src.walker.notes import NotesDB
from src.walker.signature import SignatureComputer
from src.walker.antidata import AntiDataEngine


@dataclass
class PolicyDecision:
    """Result of policy selection"""
    policy: TraversalPolicy
    signature_hash: str
    matched_profile: bool
    profile_cartridge_id: Optional[str] = None
    adjustments: List[str] = None
    
    def __post_init__(self):
        if self.adjustments is None:
            self.adjustments = []


class PolicySelector:
    """
    Selects traversal policy based on cartridge characteristics
    and prior experience.
    
    Usage:
        selector = PolicySelector(db, notes_db)
        decision = selector.select_policy()
        walker.config.policy = decision.policy
    """
    
    # Default policies by mode
    DEFAULT_POLICIES = {
        TraversalMode.STRUCTURE_FIRST: TraversalPolicy(
            mode=TraversalMode.STRUCTURE_FIRST,
            use_semantic=False,
            use_structure=True,
            use_adjacency=True,
            use_graph=False,
            budgets=Budgets(max_nodes=60, max_chunks=50),
            weights=ScoreWeights(structural=0.5, adjacency=0.3, semantic=0.0, graph=0.0),
        ),
        TraversalMode.SEMANTIC_SEEDED: TraversalPolicy(
            mode=TraversalMode.SEMANTIC_SEEDED,
            use_semantic=True,
            use_structure=True,
            use_adjacency=True,
            use_graph=True,
            budgets=Budgets(max_nodes=50, max_chunks=40),
            weights=ScoreWeights(semantic=0.35, structural=0.25, adjacency=0.15, graph=0.15),
        ),
        TraversalMode.ADJACENCY_HEAVY: TraversalPolicy(
            mode=TraversalMode.ADJACENCY_HEAVY,
            use_semantic=True,
            use_structure=True,
            use_adjacency=True,
            use_graph=False,
            adjacency_radius=4,
            budgets=Budgets(max_chunks=60),
            weights=ScoreWeights(adjacency=0.4, structural=0.3, semantic=0.2, graph=0.0),
        ),
        TraversalMode.GRAPH_ASSISTED: TraversalPolicy(
            mode=TraversalMode.GRAPH_ASSISTED,
            use_semantic=True,
            use_structure=True,
            use_adjacency=True,
            use_graph=True,
            graph_max_hops=3,
            budgets=Budgets(max_nodes=70, max_graph_hops=4),
            weights=ScoreWeights(graph=0.35, structural=0.25, semantic=0.25, adjacency=0.1),
        ),
    }
    
    def __init__(self, db: CartridgeDB, 
                  notes_db: Optional[NotesDB] = None):
        self.db = db
        self.notes_db = notes_db
        
        self.manifest_reader = ManifestReader(db)
        self.signature_computer = SignatureComputer(db)
        self.antidata_engine = AntiDataEngine(notes_db) if notes_db else None
    
    def select_policy(self) -> PolicyDecision:
        """
        Select the best policy for the current cartridge.
        
        Flow:
        1. Assess cartridge readiness
        2. Compute signature
        3. Look for matching profile
        4. Apply anti-data adjustments
        5. Return policy decision
        """
        # Step 1: Assess readiness
        readiness = self.manifest_reader.assess_readiness()
        manifest = self.manifest_reader.manifest
        
        # Step 2: Compute signature
        signature_hash = ""
        if manifest:
            signature_hash = self.signature_computer.compute_signature(manifest)
        
        # Step 3: Start with recommended mode
        base_mode = readiness.recommended_mode
        policy = self._get_base_policy(base_mode)
        
        # Step 4: Apply capability constraints
        adjustments = self._apply_capability_constraints(policy, readiness)
        
        # Step 5: Look for matching profile
        matched_profile = False
        profile_cartridge_id = None
        
        if self.notes_db and self.notes_db.is_connected and signature_hash:
            profile = self.notes_db.get_profile_by_signature(signature_hash)
            if profile:
                matched_profile = True
                profile_cartridge_id = profile.cartridge_id
                profile_adjustments = self._apply_profile_learnings(policy, profile)
                adjustments.extend(profile_adjustments)
        
        # Step 6: Apply anti-data constraints
        if self.antidata_engine:
            antidata_adjustments = self._apply_antidata_constraints(policy, manifest)
            adjustments.extend(antidata_adjustments)
        
        return PolicyDecision(
            policy=policy,
            signature_hash=signature_hash,
            matched_profile=matched_profile,
            profile_cartridge_id=profile_cartridge_id,
            adjustments=adjustments,
        )
    
    def _get_base_policy(self, mode: TraversalMode) -> TraversalPolicy:
        """Get base policy for a mode (copy to avoid mutation)"""
        base = self.DEFAULT_POLICIES.get(mode, self.DEFAULT_POLICIES[TraversalMode.SEMANTIC_SEEDED])
        
        # Create a fresh copy
        return TraversalPolicy(
            mode=base.mode,
            budgets=Budgets(**vars(base.budgets)),
            weights=ScoreWeights(**vars(base.weights)),
            use_semantic=base.use_semantic,
            use_structure=base.use_structure,
            use_adjacency=base.use_adjacency,
            use_graph=base.use_graph,
            graph_max_hops=base.graph_max_hops,
            adjacency_radius=base.adjacency_radius,
            semantic_top_k=base.semantic_top_k,
            allowed_edge_types=list(base.allowed_edge_types),
            blocked_edge_types=list(base.blocked_edge_types),
            blocked_node_types=list(base.blocked_node_types),
        )
    
    def _apply_capability_constraints(self, policy: TraversalPolicy,
                                        readiness: ReadinessReport
                                        ) -> List[str]:
        """Apply constraints based on cartridge capabilities"""
        adjustments = []
        
        if not readiness.can_use_semantic:
            policy.use_semantic = False
            policy.weights.semantic = 0.0
            adjustments.append("Disabled semantic: not available")
        
        if not readiness.can_use_graph:
            policy.use_graph = False
            policy.weights.graph = 0.0
            adjustments.append("Disabled graph: not available")
        
        if not readiness.can_use_structure:
            policy.use_structure = False
            policy.weights.structural = 0.0
            adjustments.append("Disabled structure: not available")
        
        if not readiness.can_use_fts:
            policy.semantic_top_k = 0
            adjustments.append("FTS unavailable: using structural roots")
        
        # Normalize weights
        self._normalize_weights(policy.weights)
        
        return adjustments
    
    def _apply_profile_learnings(self, policy: TraversalPolicy,
                                   profile) -> List[str]:
        """Apply learnings from a matching profile"""
        adjustments = []
        
        # Apply success modes
        for mode in profile.success_modes:
            if mode == "adjacency_helps":
                policy.adjacency_radius = max(policy.adjacency_radius, 3)
                policy.weights.adjacency *= 1.2
                adjustments.append("Boosted adjacency: prior success")
            
            elif mode == "graph_helps":
                policy.graph_max_hops = max(policy.graph_max_hops, 2)
                policy.weights.graph *= 1.2
                adjustments.append("Boosted graph: prior success")
        
        # Apply failure modes
        for mode in profile.failure_modes:
            if mode == "graph_noisy":
                policy.graph_max_hops = min(policy.graph_max_hops, 1)
                policy.weights.graph *= 0.5
                adjustments.append("Reduced graph: prior noise")
            
            elif mode == "adjacency_duplicates":
                policy.adjacency_radius = min(policy.adjacency_radius, 1)
                adjustments.append("Reduced adjacency radius: prior duplicates")
            
            elif mode == "budget_exhausted":
                policy.budgets.max_nodes = int(policy.budgets.max_nodes * 0.8)
                policy.budgets.max_chunks = int(policy.budgets.max_chunks * 0.8)
                adjustments.append("Reduced budgets: prior exhaustion")
        
        # Normalize weights
        self._normalize_weights(policy.weights)
        
        return adjustments
    
    def _apply_antidata_constraints(self, policy: TraversalPolicy,
                                      manifest: Optional[CartridgeManifest]
                                      ) -> List[str]:
        """Apply anti-data rule constraints"""
        adjustments = []
        
        if not self.antidata_engine or not manifest:
            return adjustments
        
        # Check pipeline version
        if manifest.pipeline_ver:
            result = self.antidata_engine.evaluate(f"pipeline_ver:{manifest.pipeline_ver}")
            if result.blocked:
                # Can't really block the whole cartridge here, but we can note it
                adjustments.append(f"Warning: pipeline {manifest.pipeline_ver} flagged")
        
        # Check embed model
        if manifest.embed_model:
            result = self.antidata_engine.evaluate(f"embed_model:{manifest.embed_model}")
            if result.total_penalty > 0:
                policy.weights.semantic *= (1.0 - result.total_penalty)
                adjustments.append(f"Reduced semantic weight: embed model penalized")
        
        return adjustments
    
    def _normalize_weights(self, weights: ScoreWeights):
        """Normalize weights to sum to ~1.0"""
        total = (
            weights.semantic + 
            weights.structural + 
            weights.adjacency + 
            weights.graph + 
            weights.source
        )
        
        if total > 0:
            weights.semantic /= total
            weights.structural /= total
            weights.adjacency /= total
            weights.graph /= total
            weights.source /= total
    
    # =========================================================================
    # Manual Policy Creation
    # =========================================================================
    
    def create_structure_only_policy(self) -> TraversalPolicy:
        """Create a structure-only policy (no semantic/graph)"""
        return self._get_base_policy(TraversalMode.STRUCTURE_FIRST)
    
    def create_full_policy(self) -> TraversalPolicy:
        """Create a policy using all gradients"""
        return self._get_base_policy(TraversalMode.SEMANTIC_SEEDED)
    
    def create_custom_policy(self,
                              mode: TraversalMode = TraversalMode.SEMANTIC_SEEDED,
                              max_nodes: int = 50,
                              max_chunks: int = 40,
                              use_graph: bool = True,
                              graph_max_hops: int = 2,
                              adjacency_radius: int = 2
                              ) -> TraversalPolicy:
        """Create a custom policy"""
        policy = self._get_base_policy(mode)
        policy.budgets.max_nodes = max_nodes
        policy.budgets.max_chunks = max_chunks
        policy.use_graph = use_graph
        policy.graph_max_hops = graph_max_hops
        policy.adjacency_radius = adjacency_radius
        return policy
