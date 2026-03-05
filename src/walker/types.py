"""
Node Walker Type Definitions
Dataclasses for all traversal inputs, outputs, and intermediate structures.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set, Tuple
from enum import Enum, auto
import json


# =============================================================================
# Enums
# =============================================================================

class IngestStatus(Enum):
    """Ingest run status values"""
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class EmbedStatus(Enum):
    """Embedding status values"""
    PENDING = "pending"
    DONE = "done"
    STALE = "stale"
    ERROR = "error"


class GraphStage(Enum):
    """Graph completion stages"""
    NONE = "none"
    STRUCTURAL = "structural"
    DONE = "done"


class TraversalMode(Enum):
    """Traversal strategy modes"""
    STRUCTURE_FIRST = "structure-first"
    SEMANTIC_SEEDED = "semantic-seeded"
    ADJACENCY_HEAVY = "adjacency-heavy"
    GRAPH_ASSISTED = "graph-assisted"


class OperatorType(Enum):
    """Expansion operator types"""
    # Structural
    PARENT = "parent"
    CHILDREN = "children"
    SIBLINGS = "siblings"
    DESCENDANTS = "descendants"
    ANCESTORS = "ancestors"
    
    # Chunk
    NODE_TO_CHUNKS = "node_to_chunks"
    CHUNK_TO_NODE = "chunk_to_node"
    CHUNK_PREV = "chunk_prev"
    CHUNK_NEXT = "chunk_next"
    CHUNK_WINDOW = "chunk_window"
    
    # CAS
    NODE_SPAN = "node_span"
    CHUNK_SPANS = "chunk_spans"
    FILE_CONTENT = "file_content"
    
    # Graph
    GRAPH_NEIGHBORS = "graph_neighbors"
    GRAPH_PATH = "graph_path"
    
    # Semantic
    SEMANTIC_NEIGHBORS = "semantic_neighbors"
    QUERY_SEMANTIC = "query_semantic"


class AntiDataAction(Enum):
    """Anti-data rule actions"""
    BLOCK = "block"
    PENALIZE = "penalize"
    WARN = "warn"


# =============================================================================
# Manifest Types
# =============================================================================

@dataclass
class CartridgeManifest:
    """Cartridge-level manifest from cartridge_manifest table"""
    cartridge_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    schema_ver: int = 1
    pipeline_ver: str = ""
    
    # Source configuration
    source_root: str = ""
    source_roots_json: str = "[]"
    
    # Embedding configuration
    embed_model: str = ""
    embed_dims: int = 0
    
    # Layer completion flags
    structural_complete: bool = False
    semantic_complete: bool = False
    graph_complete: bool = False
    search_index_complete: bool = False
    
    # Deployment
    is_deployable: bool = False
    deployment_notes: str = ""
    
    # Integrity counters
    file_count: int = 0
    line_count: int = 0
    tree_node_count: int = 0
    chunk_count: int = 0
    embedding_count: int = 0
    graph_node_count: int = 0
    graph_edge_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cartridge_id": self.cartridge_id,
            "schema_ver": self.schema_ver,
            "pipeline_ver": self.pipeline_ver,
            "source_root": self.source_root,
            "embed_model": self.embed_model,
            "embed_dims": self.embed_dims,
            "structural_complete": self.structural_complete,
            "semantic_complete": self.semantic_complete,
            "graph_complete": self.graph_complete,
            "is_deployable": self.is_deployable,
            "file_count": self.file_count,
            "tree_node_count": self.tree_node_count,
            "chunk_count": self.chunk_count,
        }


@dataclass
class IngestRun:
    """Ingest run record from ingest_runs table"""
    run_id: int = 0
    started_at: str = ""
    finished_at: str = ""
    status: str = ""
    source_root: str = ""
    stage_status: Dict[str, str] = field(default_factory=dict)
    error_log: List[str] = field(default_factory=list)
    
    @property
    def is_success(self) -> bool:
        return self.status in ("success", "complete", "done")


# =============================================================================
# CAS Types (Verbatim Layer)
# =============================================================================

@dataclass
class VerbatimLine:
    """Single line from verbatim_lines"""
    line_cid: str = ""
    content: str = ""
    byte_len: int = 0


@dataclass
class SourceFile:
    """Source file from source_files"""
    file_cid: str = ""
    path: str = ""
    line_cids: List[str] = field(default_factory=list)
    line_count: int = 0
    byte_size: int = 0
    mime_type: str = ""
    language: str = ""
    
    @classmethod
    def parse_line_cids(cls, raw: Any) -> List[str]:
        """Parse line_cids from JSON string or list"""
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except:
                return []
        return []


@dataclass
class TextSpan:
    """A resolved text span with provenance"""
    file_cid: str = ""
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    content: str = ""
    line_cids: List[str] = field(default_factory=list)
    
    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start + 1 if self.line_end >= self.line_start else 0


# =============================================================================
# Structural Types (tree_nodes)
# =============================================================================

@dataclass
class TreeNode:
    """Structural node from tree_nodes table"""
    node_id: str = ""
    node_type: str = ""
    name: str = ""
    parent_id: Optional[str] = None
    path: str = ""
    depth: int = 0
    
    # Source mapping
    file_cid: str = ""
    line_start: int = 0
    line_end: int = 0
    
    # Cross-layer join keys
    chunk_id: Optional[str] = None
    graph_node_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "name": self.name,
            "path": self.path,
            "depth": self.depth,
            "file_cid": self.file_cid,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


# =============================================================================
# Semantic Types (chunk_manifest)
# =============================================================================

@dataclass
class ChunkSpan:
    """A span reference within a chunk"""
    file_cid: str = ""
    line_start: int = 0
    line_end: int = 0
    
    @classmethod
    def from_dict(cls, d: Dict) -> "ChunkSpan":
        return cls(
            file_cid=d.get("file_cid", ""),
            line_start=d.get("line_start", 0),
            line_end=d.get("line_end", 0)
        )
    
    @classmethod
    def parse_spans(cls, raw: Any) -> List["ChunkSpan"]:
        """Parse spans from JSON string or list"""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except:
                return []
        if isinstance(raw, list):
            return [cls.from_dict(s) for s in raw if isinstance(s, dict)]
        return []


@dataclass
class ChunkHierarchy:
    """Hierarchy metadata from chunk"""
    heading_path: List[str] = field(default_factory=list)
    depth: int = 0
    parent_chunk_id: Optional[str] = None
    
    @classmethod
    def from_json(cls, raw: Any) -> "ChunkHierarchy":
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except:
                return cls()
        if isinstance(raw, dict):
            return cls(
                heading_path=raw.get("heading_path", []),
                depth=raw.get("depth", 0),
                parent_chunk_id=raw.get("parent_chunk_id")
            )
        return cls()


@dataclass
class ChunkOverlap:
    """Overlap/adjacency metadata from chunk"""
    prev_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None
    prefix_lines: int = 0
    suffix_lines: int = 0
    
    @classmethod
    def from_json(cls, raw: Any) -> "ChunkOverlap":
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except:
                return cls()
        if isinstance(raw, dict):
            return cls(
                prev_chunk_id=raw.get("prev_chunk_id"),
                next_chunk_id=raw.get("next_chunk_id"),
                prefix_lines=raw.get("prefix_lines", 0),
                suffix_lines=raw.get("suffix_lines", 0)
            )
        return cls()


@dataclass
class ChunkManifest:
    """Chunk from chunk_manifest table"""
    chunk_id: str = ""
    node_id: str = ""
    chunk_type: str = ""
    context_prefix: str = ""
    token_count: int = 0
    
    # Parsed JSON fields
    spans: List[ChunkSpan] = field(default_factory=list)
    hierarchy: ChunkHierarchy = field(default_factory=ChunkHierarchy)
    overlap: ChunkOverlap = field(default_factory=ChunkOverlap)
    
    # Status tracking
    embed_status: str = "pending"
    embed_model: str = ""
    embed_dims: int = 0
    graph_status: str = "none"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "node_id": self.node_id,
            "chunk_type": self.chunk_type,
            "context_prefix": self.context_prefix,
            "token_count": self.token_count,
        }


# =============================================================================
# Graph Types
# =============================================================================

@dataclass
class GraphNode:
    """Knowledge graph node"""
    node_id: str = ""
    label: str = ""
    node_type: str = ""
    tree_node_id: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """Knowledge graph edge"""
    edge_id: str = ""
    source_id: str = ""
    target_id: str = ""
    edge_type: str = ""
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Scoring Types
# =============================================================================

@dataclass
class ScoreComponents:
    """Individual score components for a candidate"""
    semantic: float = 0.0      # S_sem: vector similarity
    structural: float = 0.0    # S_struct: tree distance decay
    adjacency: float = 0.0     # S_adj: chunk distance decay
    graph: float = 0.0         # S_graph: hop count decay
    source: float = 0.0        # S_source: trust/provenance boost
    duplicate_penalty: float = 0.0
    anti_data_penalty: float = 0.0
    
    def total(self, weights: "ScoreWeights") -> float:
        """Compute weighted total score"""
        return (
            weights.semantic * self.semantic +
            weights.structural * self.structural +
            weights.adjacency * self.adjacency +
            weights.graph * self.graph +
            weights.source * self.source -
            weights.duplicate * self.duplicate_penalty -
            self.anti_data_penalty
        )


@dataclass
class ScoreWeights:
    """Weights for score components"""
    semantic: float = 0.35
    structural: float = 0.25
    adjacency: float = 0.15
    graph: float = 0.15
    source: float = 0.10
    duplicate: float = 0.20


@dataclass
class Budgets:
    """Traversal budgets"""
    max_nodes: int = 50
    max_chunks: int = 40
    max_lines: int = 2000
    max_graph_hops: int = 3
    max_expansions: int = 200
    min_score_threshold: float = 0.1
    marginal_gain_threshold: float = 0.05


# =============================================================================
# Traversal Types
# =============================================================================

@dataclass
class ExpansionCandidate:
    """A candidate for expansion"""
    target_id: str = ""                     # node_id or chunk_id
    target_type: str = ""                   # "node" or "chunk"
    operator: OperatorType = OperatorType.CHILDREN
    source_id: str = ""                     # where expansion originated
    score: ScoreComponents = field(default_factory=ScoreComponents)
    edge_type: Optional[str] = None         # for graph expansions
    distance: int = 0                        # hops from seed


@dataclass
class TraversalStep:
    """A single step in the traversal trace"""
    step_id: int = 0
    operator: OperatorType = OperatorType.CHILDREN
    source_id: str = ""
    target_id: str = ""
    score: float = 0.0
    gradient: str = ""                      # which gradient (struct/adj/graph/sem)
    collected: bool = False                 # was content collected?


@dataclass
class TraversalTrace:
    """Complete trace of a traversal"""
    trace_id: str = ""
    query: str = ""
    cartridge_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    
    # Seeds
    seed_nodes: List[str] = field(default_factory=list)
    seed_chunks: List[str] = field(default_factory=list)
    
    # Steps taken
    steps: List[TraversalStep] = field(default_factory=list)
    
    # Final collections
    visited_nodes: List[str] = field(default_factory=list)
    visited_chunks: List[str] = field(default_factory=list)
    
    # Stats
    total_expansions: int = 0
    total_lines_collected: int = 0


@dataclass
class Provenance:
    """Provenance for a collected span"""
    chunk_id: str = ""
    node_id: str = ""
    file_cid: str = ""
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    context_prefix: str = ""
    gradient: str = ""                      # how it was reached
    distance: int = 0                        # hops from seed


@dataclass 
class TraversalArtifact:
    """
    Output contract: portable traversal artifact.
    This is what the walker produces.
    """
    cartridge_id: str = ""
    query: str = ""
    mode: TraversalMode = TraversalMode.SEMANTIC_SEEDED
    
    # Seeds
    seeds: List[str] = field(default_factory=list)
    
    # Collections with provenance
    collected_spans: List[Provenance] = field(default_factory=list)
    
    # Content
    content_blocks: List[Dict[str, Any]] = field(default_factory=list)
    
    # Graph slice
    graph_nodes: List[str] = field(default_factory=list)
    graph_edges: List[Tuple[str, str, str]] = field(default_factory=list)
    
    # Paths taken (for determinism)
    paths: List[List[str]] = field(default_factory=list)
    
    # Trace
    trace: Optional[TraversalTrace] = None
    
    # Stats
    total_chunks: int = 0
    total_nodes: int = 0
    total_lines: int = 0
    elapsed_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cartridge_id": self.cartridge_id,
            "query": self.query,
            "mode": self.mode.value,
            "seeds": self.seeds,
            "total_chunks": self.total_chunks,
            "total_nodes": self.total_nodes,
            "total_lines": self.total_lines,
            "elapsed_ms": self.elapsed_ms,
            "collected_spans": [
                {
                    "chunk_id": p.chunk_id,
                    "file_path": p.file_path,
                    "line_start": p.line_start,
                    "line_end": p.line_end,
                    "context_prefix": p.context_prefix,
                    "gradient": p.gradient,
                }
                for p in self.collected_spans
            ],
            "paths": self.paths,
        }


# =============================================================================
# Policy Types
# =============================================================================

@dataclass
class TraversalPolicy:
    """Policy settings for a traversal run"""
    mode: TraversalMode = TraversalMode.SEMANTIC_SEEDED
    
    # Budgets
    budgets: Budgets = field(default_factory=Budgets)
    
    # Weights
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    
    # Feature toggles
    use_semantic: bool = True
    use_structure: bool = True
    use_adjacency: bool = True
    use_graph: bool = True
    
    # Graph settings
    graph_max_hops: int = 2
    allowed_edge_types: List[str] = field(default_factory=lambda: [
        "calls", "imports", "implements", "references", "contains"
    ])
    blocked_edge_types: List[str] = field(default_factory=list)
    
    # Semantic settings
    semantic_top_k: int = 15
    
    # Adjacency settings
    adjacency_radius: int = 2
    
    # Trust settings
    require_spans: bool = False
    blocked_node_types: List[str] = field(default_factory=list)


# =============================================================================
# Notes / Anti-Data Types
# =============================================================================

@dataclass
class NotesEvent:
    """Event record for notes_events table"""
    event_id: str = ""
    ts: str = ""
    scope_type: str = ""      # cartridge | file | chunk | pipeline | tool | global
    scope_id: str = ""
    event_type: str = ""      # ingest | walk | error | heuristic | warning | ban | allow
    severity: int = 0         # 0-5
    summary: str = ""
    details_json: str = "{}"


@dataclass
class AntiDataRule:
    """Rule from anti_data_rules table"""
    rule_id: str = ""
    match_type: str = ""      # exact | prefix | regex | jsonpath | cid_set
    match_value: str = ""
    action: AntiDataAction = AntiDataAction.WARN
    penalty: float = 0.0
    reason: str = ""
    created_at: str = ""
    expires_at: Optional[str] = None


@dataclass
class CartridgeProfile:
    """Learned profile from cartridge_profiles table"""
    cartridge_id: str = ""
    signature_hash: str = ""
    success_modes: List[str] = field(default_factory=list)
    failure_modes: List[str] = field(default_factory=list)
    last_seen: str = ""
    recommended_policy: Optional[TraversalPolicy] = None
