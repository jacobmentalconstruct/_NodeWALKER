"""
Evidence Gravity System.

Forensic pipeline for graph-walk reasoning with evidence accumulation,
gravitational scoring, and KV-budgeted synthesis.

Usage:
    from src.walker.gravity import ForensicPipeline, GravityConfig

    pipeline = ForensicPipeline(walker, llm_agent, GravityConfig())
    result = pipeline.run("Explain function process_data")
"""

from src.walker.gravity.types import (
    # Facet decomposition
    FacetKind,
    Facet,

    # Evidence gravity
    GravitySource,
    EvidenceMassScore,

    # Sufficiency
    SufficiencyLevel,
    FacetSufficiency,
    SufficiencyReport,

    # KV packing
    PackedFacet,
    VerbatimExpansion,
    KVPackPlan,

    # Configuration
    GravityConfig,
)

from src.walker.gravity.decomposer import FacetDecomposer
from src.walker.gravity.engine import EvidenceGravityEngine
from src.walker.gravity.sufficiency import SufficiencyCritic
from src.walker.gravity.packer import EvidencePacker
from src.walker.gravity.pipeline import ForensicPipeline, ForensicResult

__all__ = [
    # Types
    "FacetKind", "Facet",
    "GravitySource", "EvidenceMassScore",
    "SufficiencyLevel", "FacetSufficiency", "SufficiencyReport",
    "PackedFacet", "VerbatimExpansion", "KVPackPlan",
    "GravityConfig",

    # Components
    "FacetDecomposer",
    "EvidenceGravityEngine",
    "SufficiencyCritic",
    "EvidencePacker",

    # Pipeline
    "ForensicPipeline",
    "ForensicResult",
]
