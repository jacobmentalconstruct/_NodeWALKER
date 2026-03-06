"""
Evidence Gravity Type Contracts.

All dataclasses and enums for the forensic pipeline:
- Facet decomposition
- Evidence mass scoring
- Sufficiency reporting
- KV budget packing
- Pipeline configuration
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any


# =============================================================================
# Facet Decomposition
# =============================================================================

class FacetKind(Enum):
    """Intentful sub-question categories."""
    BEHAVIOR = "behavior"          # What does it do?
    CONTRACT = "contract"          # Inputs/outputs/types
    FAILURE = "failure"            # Error/failure analysis
    CONTEXT = "context"            # Dependencies/related entities
    DEFINITION = "definition"      # What is it? Identity/location
    COMPARISON = "comparison"      # How does X compare to Y?
    CUSTOM = "custom"              # Free-form sub-question
    # Aligned with IntentLabel for forensic router
    EXPLANATION = "explanation"    # Maps from IntentLabel.EXPLAIN
    SUMMARY = "summary"           # Maps from IntentLabel.SUMMARIZE
    MUTATION = "mutation"          # Maps from IntentLabel.MUTATION


@dataclass
class Facet:
    """A single intentful sub-question decomposed from the user query."""
    facet_id: str
    kind: FacetKind
    question: str                                    # The sub-question
    priority: float = 1.0                            # 0.0-1.0 relative importance
    evidence_ids: List[str] = field(default_factory=list)  # Collected evidence
    sufficient: bool = False                         # Has enough evidence
    summary: str = ""                                # Per-facet evidence summary

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_ids)


# =============================================================================
# Evidence Gravity Scoring
# =============================================================================

@dataclass
class GravitySource:
    """
    A piece of collected evidence with its gravitational mass.

    Mass increases when multiple gradients (structural, semantic, graph,
    verbatim) converge on the same target. High mass = explanatory core.
    """
    evidence_id: str                     # chunk_id or node_id
    target_type: str                     # "chunk" or "node"
    mass: float = 0.0                    # Computed gravity mass
    structural_signal: float = 0.0       # 0-1: structural proximity to intent
    semantic_signal: float = 0.0         # 0-1: embedding similarity
    graph_signal: float = 0.0            # 0-1: knowledge graph proximity
    verbatim_signal: float = 0.0         # 0-1: exact identifier/literal overlap
    facet_id: str = ""                   # Which facet this evidence serves

    @property
    def gradient_count(self) -> int:
        """How many gradients contribute signal > 0."""
        return sum(1 for s in [
            self.structural_signal,
            self.semantic_signal,
            self.graph_signal,
            self.verbatim_signal,
        ] if s > 0.1)


@dataclass
class EvidenceMassScore:
    """
    Gravity-enhanced score for a candidate node.

    total = base_relevance + gravity_pull - distance_penalty - redundancy_penalty
    """
    candidate_id: str
    base_relevance: float = 0.0          # Original scorer output
    gravity_pull: float = 0.0            # Attraction from heavy evidence
    distance_penalty: float = 0.0        # Decay from walk depth
    redundancy_penalty: float = 0.0      # Overlap with already-collected evidence
    total: float = 0.0                   # Final gravity-adjusted score
    contributing_sources: List[str] = field(default_factory=list)

    def compute_total(self) -> float:
        self.total = (
            self.base_relevance
            + self.gravity_pull
            - self.distance_penalty
            - self.redundancy_penalty
        )
        return self.total


# =============================================================================
# Sufficiency Reporting
# =============================================================================

class SufficiencyLevel(Enum):
    """How sufficient the collected evidence is."""
    INSUFFICIENT = "insufficient"
    PARTIAL = "partial"
    SUFFICIENT = "sufficient"


@dataclass
class FacetSufficiency:
    """Sufficiency assessment for a single facet."""
    facet_id: str
    level: SufficiencyLevel = SufficiencyLevel.INSUFFICIENT
    evidence_count: int = 0
    heavy_evidence_count: int = 0        # Evidence with mass > threshold
    coverage: float = 0.0               # 0-1 estimated coverage

    @property
    def is_answerable(self) -> bool:
        return self.level != SufficiencyLevel.INSUFFICIENT


@dataclass
class SufficiencyReport:
    """Global sufficiency assessment across all facets."""
    facets: List[FacetSufficiency] = field(default_factory=list)
    global_level: SufficiencyLevel = SufficiencyLevel.INSUFFICIENT
    all_facets_answerable: bool = False
    kv_budget_remaining: int = 0
    should_stop: bool = False
    reason: str = ""


# =============================================================================
# KV Budget Packing
# =============================================================================

@dataclass
class PackedFacet:
    """A single facet's evidence compressed for synthesis."""
    facet_id: str
    question: str                        # The facet question
    summary: str = ""                    # Compressed evidence summary
    citations: List[str] = field(default_factory=list)
    token_estimate: int = 0


@dataclass
class VerbatimExpansion:
    """A critical verbatim chunk included in the pack."""
    evidence_id: str
    text: str
    mass: float = 0.0                    # Why it was selected
    token_estimate: int = 0


@dataclass
class KVPackPlan:
    """
    The final evidence package that fits within KV budget.

    This is what gets fed to the synthesis inference call.
    """
    total_budget: int = 8000             # Max tokens
    packed_facets: List[PackedFacet] = field(default_factory=list)
    verbatim_expansions: List[VerbatimExpansion] = field(default_factory=list)
    tokens_used: int = 0
    tokens_remaining: int = 0

    def compute_remaining(self):
        self.tokens_used = (
            sum(f.token_estimate for f in self.packed_facets)
            + sum(v.token_estimate for v in self.verbatim_expansions)
        )
        self.tokens_remaining = max(0, self.total_budget - self.tokens_used)


# =============================================================================
# Pipeline Configuration
# =============================================================================

@dataclass
class GravityConfig:
    """Configuration for the forensic evidence gravity pipeline."""

    # --- Mass computation ---
    heavy_mass_threshold: float = 2.0    # Mass above this = "heavy" evidence
    gradient_alignment_bonus: float = 0.5  # Bonus per aligned gradient (2+ = heavy)

    # --- Gravity pull weights (how each gradient contributes to pull) ---
    gravity_structural_weight: float = 0.30
    gravity_semantic_weight: float = 0.30
    gravity_graph_weight: float = 0.25
    gravity_verbatim_weight: float = 0.15

    # --- Sufficiency thresholds ---
    min_evidence_per_facet: int = 2
    min_heavy_evidence_per_facet: int = 1
    sufficiency_coverage_threshold: float = 0.7

    # --- KV budget ---
    kv_token_budget: int = 8000
    facet_summary_max_tokens: int = 200
    verbatim_expansion_max_tokens: int = 500
    max_verbatim_expansions: int = 5

    # --- Termination conditions ---
    relevance_decay_threshold: float = 0.35
    max_walk_depth: int = 4
    max_evidence_per_facet: int = 12
    max_nodes_per_facet: int = 30
    max_time_per_facet_ms: int = 2000

    # --- Pipeline ---
    max_facets: int = 6
    enable_integrity_critic: bool = True
