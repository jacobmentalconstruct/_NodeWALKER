"""
Forensic Query Pipeline -- Type Contracts.

Enums and dataclasses for the manifold-native agent:
- Scope classification (cartridge-bound vs light chat)
- Referent binding (what 'this/it/that' resolves to)
- Intent classification (explain, summarize, find, compare, mutate)
- ManifoldResult (wraps gravity's ForensicResult with routing metadata)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Any

from src.walker.gravity.types import SufficiencyReport


# =============================================================================
# Scope Classification
# =============================================================================

class ScopeLabel(Enum):
    """Whether the query is cartridge-bound or light chat."""
    CARTRIDGE = "cartridge"          # About the loaded codebase as a whole
    NODE = "node"                    # About a specific node (function, class)
    CHUNK = "chunk"                  # About a specific code chunk
    PINNED = "pinned"                # About pinned context items
    SOCIAL_LIGHT = "social_light"    # Generic chat, not cartridge-bound


# =============================================================================
# Referent Binding
# =============================================================================

class ReferentType(Enum):
    """What the user's deictic pronouns resolve to."""
    FOCUS_TARGET = "focus_target"        # Selected node/chunk in UI
    PINNED_CONTEXT = "pinned_context"    # Pinned context items
    CARTRIDGE = "cartridge"              # Cartridge as a whole
    UNBOUND = "unbound"                  # No cartridge, no selection


@dataclass
class ReferentBinding:
    """What 'this/it/that' resolves to based on UI state."""
    referent_type: ReferentType
    node_id: Optional[str] = None
    chunk_id: Optional[str] = None
    file_path: Optional[str] = None
    selected_text: Optional[str] = None
    display_label: str = ""


# =============================================================================
# Intent Classification
# =============================================================================

class IntentLabel(Enum):
    """High-level user intent categories."""
    EXPLAIN = "explain"                  # How does X work?
    SUMMARIZE = "summarize"              # Give me an overview of X
    FIND_DEFINITION = "find_definition"  # Where is X defined?
    COMPARE = "compare"                  # How do X and Y differ?
    MUTATION = "mutation"                # Fix/change/refactor X


# =============================================================================
# Per-Facet Outcome
# =============================================================================

@dataclass
class FacetResult:
    """Per-facet outcome within a ManifoldResult."""
    facet_id: str
    question: str
    evidence_count: int = 0
    heavy_evidence_count: int = 0
    sufficient: bool = False
    summary: str = ""


# =============================================================================
# Sufficiency Summary (wraps gravity's SufficiencyReport)
# =============================================================================

@dataclass
class SufficiencySummary:
    """Extended sufficiency wrapper around gravity's SufficiencyReport."""
    report: Optional[SufficiencyReport] = None
    tokens_used: int = 0
    search_party_needed: bool = False
    facet_summaries: List[FacetResult] = field(default_factory=list)


# =============================================================================
# Manifold Result
# =============================================================================

@dataclass
class ManifoldResult:
    """
    Output of the forensic query pipeline.

    Named ManifoldResult to avoid collision with gravity's ForensicResult.
    Contains the gravity result plus forensic routing metadata.
    """
    query: str
    scope: ScopeLabel = ScopeLabel.CARTRIDGE
    intent: IntentLabel = IntentLabel.EXPLAIN
    referent: Optional[ReferentBinding] = None
    synthesis: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    drift_warnings: List[str] = field(default_factory=list)
    sufficiency: Optional[SufficiencySummary] = None
    gravity_result: Optional[Any] = None      # gravity.pipeline.ForensicResult
    elapsed_ms: int = 0
    facet_results: List[FacetResult] = field(default_factory=list)
