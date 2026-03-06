"""
Walker Module
Main orchestration loop for Tripartite cartridge traversal.

Traversal Flow:
1. Read manifests, assess readiness
2. Select policy based on capabilities
3. Seed via FTS or structural roots
4. Expand using scoring + budgets
5. Collect content from CAS
6. Build output artifact with provenance
"""

import time
import uuid
from typing import Optional, List, Dict, Set, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime

from src.walker.types import (
    TraversalPolicy, TraversalMode, TraversalArtifact, TraversalTrace,
    TraversalStep, Provenance, OperatorType, Budgets, ScoreWeights,
    TreeNode, ChunkManifest, GraphNode
)
from src.walker.db import CartridgeDB
from src.walker.manifest import ManifestReader, ReadinessReport, ReadinessLevel
from src.walker.cas import CASResolver
from src.walker.structure import StructureOperators
from src.walker.chunks import ChunkOperators
from src.walker.graph import GraphOperators
from src.walker.scoring import Scorer, BudgetState
from src.walker.activation_types import ActivationEvent, ActivationKind, TargetType, WEIGHT_BY_KIND
from src.walker.session_db import SessionDB
from src.walker.activation_store import ActivationStore


@dataclass
class WalkerConfig:
    """Configuration for walker instance"""
    policy: TraversalPolicy = field(default_factory=TraversalPolicy)
    semantic_query_fn: Optional[callable] = None  # For vector search
    trace_enabled: bool = True
    verbose: bool = False
    session_db: Optional[SessionDB] = None  # For storing activations
    activation_store: Optional[ActivationStore] = None  # For in-memory aggregation


@dataclass
class ContentBlock:
    """A collected content block with metadata"""
    chunk_id: str
    node_id: str
    content: str
    context_prefix: str
    file_path: str
    line_start: int
    line_end: int
    line_count: int
    gradient: str  # How it was reached
    distance: int  # Hops from seed


class NodeWalker:
    """
    Main walker class for traversing Tripartite cartridges.
    
    Usage:
        walker = NodeWalker(db)
        walker.assess_readiness()
        artifact = walker.walk(query="find user authentication")
    """
    
    def __init__(self, db: CartridgeDB, config: Optional[WalkerConfig] = None):
        self.db = db
        self.config = config or WalkerConfig()
        
        # Initialize modules
        self.manifest_reader = ManifestReader(db)
        self.cas = CASResolver(db)
        self.structure = StructureOperators(db)
        self.chunks = ChunkOperators(db, self.cas)
        self.graph = GraphOperators(db)
        
        # State (reset per walk)
        self.scorer: Optional[Scorer] = None
        self.trace: Optional[TraversalTrace] = None
        self._step_counter = 0

        # Activation state
        self.session_id: Optional[str] = None
        self.query_id: Optional[str] = None
        self._last_activation_emit_time = 0
        self._activation_emit_debounce = 0.1  # seconds, max 10/sec

        # Escape detection state
        self._recent_expansions: List[str] = []     # Last N expanded node IDs
        self._recent_cluster_ids: List[str] = []    # Parent IDs of recent expansions
        self._escape_mode: bool = False
        self._current_query: str = ""

        # Capabilities (set by assess_readiness)
        self._readiness: Optional[ReadinessReport] = None
    
    # =========================================================================
    # Readiness Assessment
    # =========================================================================
    
    def assess_readiness(self) -> ReadinessReport:
        """Assess cartridge readiness and set capabilities"""
        self._readiness = self.manifest_reader.assess_readiness()
        
        # Update policy based on capabilities
        self._update_policy_from_readiness()
        
        return self._readiness
    
    def _update_policy_from_readiness(self):
        """Update policy toggles based on readiness"""
        if not self._readiness:
            return
        
        policy = self.config.policy
        policy.use_semantic = self._readiness.can_use_semantic
        policy.use_graph = self._readiness.can_use_graph
        policy.use_structure = self._readiness.can_use_structure
        
        # Set mode based on recommendation
        policy.mode = self._readiness.recommended_mode
    
    def is_ready(self) -> Tuple[bool, List[str]]:
        """Check if cartridge is ready for traversal"""
        if not self._readiness:
            self.assess_readiness()
        
        return (
            self._readiness.level != ReadinessLevel.BLOCKED,
            self._readiness.blockers
        )
    
    # =========================================================================
    # Main Walk Entry Point
    # =========================================================================
    
    def walk(self, query: str = "",
             seed_node_ids: Optional[List[str]] = None,
             seed_chunk_ids: Optional[List[str]] = None
             ) -> TraversalArtifact:
        """
        Execute a traversal walk.

        Args:
            query: Natural language query for FTS seeding
            seed_node_ids: Explicit tree node seeds
            seed_chunk_ids: Explicit chunk seeds

        Returns:
            TraversalArtifact with collected content and provenance
        """
        start_time = time.time()

        # Initialize state
        self._init_walk(query)

        # Emit query started event
        try:
            from src.ui.event_bus import get_event_bus
            bus = get_event_bus()
            bus.emit("QUERY_STARTED", {"query_id": self.query_id, "query": query})
        except ImportError:
            bus = None

        # Get seeds
        seeds = self._get_seeds(query, seed_node_ids, seed_chunk_ids)
        if not seeds:
            result = self._build_empty_artifact(query, "No seeds found")
            # Emit query finished event
            if self.query_id and self.config.session_db:
                self.config.session_db.end_query(self.query_id)
            if bus:
                bus.emit("QUERY_FINISHED", {"query_id": self.query_id})
            return result

        # Seed the scorer
        self._seed_candidates(seeds)

        # Main traversal loop
        self._traverse_loop()

        # Collect content
        content_blocks = self._collect_content()

        # Build artifact
        elapsed_ms = int((time.time() - start_time) * 1000)

        result = self._build_artifact(query, seeds, content_blocks, elapsed_ms)

        # Emit query finished event
        if self.query_id and self.config.session_db:
            self.config.session_db.end_query(self.query_id)
        if bus:
            bus.emit("QUERY_FINISHED", {"query_id": self.query_id})

        return result
    
    # =========================================================================
    # Walk Initialization
    # =========================================================================
    
    def _init_walk(self, query: str):
        """Initialize state for a new walk"""
        # Store query for escape re-seeding
        self._current_query = query
        self._recent_expansions.clear()
        self._recent_cluster_ids.clear()
        self._escape_mode = False

        # Create scorer with policy
        self.scorer = Scorer(self.config.policy)

        # Initialize trace
        if self.config.trace_enabled:
            manifest = self.manifest_reader.manifest
            self.trace = TraversalTrace(
                trace_id=str(uuid.uuid4()),
                query=query,
                cartridge_id=manifest.cartridge_id if manifest else "",
                started_at=datetime.utcnow().isoformat(),
            )

        self._step_counter = 0

        # Initialize activation state
        if self.config.session_db and self.config.activation_store:
            if not self.session_id:
                self.session_id = self.config.session_db.create_session()
            self.query_id = self.config.session_db.start_query(
                self.session_id, query, model="walker"
            )
            self.config.activation_store.reset_for_query(self.session_id, self.query_id)

        # Reset module stats
        self.cas.reset_stats()
        self.structure.reset_stats()
        self.chunks.reset_stats()
        self.graph.reset_stats()
    
    # =========================================================================
    # Seeding
    # =========================================================================
    
    def _get_seeds(self, query: str,
                    seed_node_ids: Optional[List[str]],
                    seed_chunk_ids: Optional[List[str]]
                    ) -> List[Tuple[str, str]]:
        """
        Get seed (id, type) pairs.
        Priority: explicit seeds > FTS > structural roots
        """
        seeds = []
        
        # Explicit node seeds
        if seed_node_ids:
            for nid in seed_node_ids:
                seeds.append((nid, "node"))
        
        # Explicit chunk seeds
        if seed_chunk_ids:
            for cid in seed_chunk_ids:
                seeds.append((cid, "chunk"))
        
        # FTS seeding
        if not seeds and query and self._readiness and self._readiness.can_use_fts:
            fts_seeds = self._seed_from_fts(query)
            seeds.extend(fts_seeds)
        
        # Fallback: structural roots
        if not seeds:
            roots = self.structure.roots()
            for root in roots[:10]:
                seeds.append((root.node_id, "node"))
        
        return seeds
    
    def _seed_from_fts(self, query: str) -> List[Tuple[str, str]]:
        """Seed from FTS search"""
        results = self.db.fts_search_chunks(
            query,
            limit=self.config.policy.semantic_top_k
        )
        
        seeds = []
        for chunk_id, rank in results:
            seeds.append((chunk_id, "chunk"))
            
            # Also add associated tree node
            chunk = self.db.get_chunk(chunk_id)
            if chunk and chunk.node_id:
                seeds.append((chunk.node_id, "node"))
        
        # Dedupe while preserving order
        seen = set()
        unique = []
        for s in seeds:
            if s[0] not in seen:
                seen.add(s[0])
                unique.append(s)
        
        return unique
    
    def _seed_candidates(self, seeds: List[Tuple[str, str]]):
        """Add seeds to candidate queue with high scores"""
        for seed_id, seed_type in seeds:
            candidate = self.scorer.create_candidate(
                target_id=seed_id,
                target_type=seed_type,
                operator=OperatorType.CHILDREN,  # Placeholder
                source_id="seed",
                semantic=1.0,  # Seeds get max semantic score
                structural=1.0,
                source=1.0,
                distance=0,
            )
            self.scorer.add_candidate(candidate)

            # Emit ENTRY_HIT activation for circuit highlighting
            target = TargetType.TREE_NODE if seed_type == "node" else TargetType.CHUNK
            self.emit_activation(ActivationKind.ENTRY_HIT, target, seed_id)

        # Track seeds in trace
        if self.trace:
            for seed_id, seed_type in seeds:
                if seed_type == "node":
                    self.trace.seed_nodes.append(seed_id)
                else:
                    self.trace.seed_chunks.append(seed_id)
    
    # =========================================================================
    # Main Traversal Loop
    # =========================================================================
    
    def _traverse_loop(self):
        """Main traversal loop: expand highest-scoring candidates with escape detection."""
        while not self.scorer.should_stop():
            # Check escape trigger
            if self.scorer.should_trigger_escape(
                recent_clusters=self._recent_cluster_ids,
            ):
                self._handle_escape()

            candidate = self.scorer.pop_best_candidate()
            if not candidate:
                break

            # Track for escape detection
            self._recent_expansions.append(candidate.target_id)
            if len(self._recent_expansions) > 20:
                self._recent_expansions.pop(0)

            # Track cluster (parent of expanded node)
            if candidate.target_type == "node":
                node = self.structure.get_node(candidate.target_id)
                if node and node.parent_id:
                    self._recent_cluster_ids.append(node.parent_id)
                    if len(self._recent_cluster_ids) > 10:
                        self._recent_cluster_ids.pop(0)

            # Expand this candidate
            self._expand_candidate(candidate)
            self.scorer.mark_expansion()
    
    def _handle_escape(self):
        """
        Escape a stuck cluster by switching traversal strategy.

        Re-seeds from FTS using the original query, filtering to unvisited
        nodes only. Resets cluster tracking so escape doesn't re-trigger
        immediately.
        """
        self._escape_mode = True

        # Re-seed from FTS using the original query
        if self._current_query:
            results = self.db.fts_search_chunks(
                self._current_query,
                limit=self.config.policy.semantic_top_k,
            )
            for chunk_id, rank in results:
                if chunk_id not in self.scorer.visited_chunks:
                    candidate = self.scorer.create_candidate(
                        target_id=chunk_id,
                        target_type="chunk",
                        operator=OperatorType.QUERY_SEMANTIC,
                        source_id="escape",
                        semantic=0.8,
                        distance=0,
                    )
                    self.scorer.add_candidate(candidate)

        # Reset cluster tracking
        self._recent_cluster_ids.clear()

    def _expand_candidate(self, candidate):
        """Expand a single candidate"""
        target_id = candidate.target_id
        target_type = candidate.target_type
        
        if target_type == "node":
            self._expand_node(target_id, candidate)
        elif target_type == "chunk":
            self._expand_chunk(target_id, candidate)
    
    def _expand_node(self, node_id: str, source_candidate):
        """Expand from a tree node"""
        self.scorer.mark_node_visited(node_id)

        # Emit TRAVERSAL_HOP activation for circuit highlighting
        self.emit_activation(ActivationKind.TRAVERSAL_HOP, TargetType.TREE_NODE, node_id)

        node = self.structure.get_node(node_id)
        if not node:
            return
        
        # Log step
        self._log_step(
            operator=OperatorType.CHILDREN,
            source_id=source_candidate.source_id,
            target_id=node_id,
            score=self.scorer.compute_score(source_candidate),
            gradient="structural"
        )
        
        policy = self.config.policy
        
        # Structural expansion
        if policy.use_structure:
            self._add_structural_candidates(node, source_candidate)
        
        # Get associated chunks
        chunks = self.chunks.node_to_chunks(node_id)
        for chunk in chunks:
            if chunk.chunk_id not in self.scorer.visited_chunks:
                self._expand_chunk(chunk.chunk_id, source_candidate)
        
        # Graph expansion
        if policy.use_graph and node.graph_node_id:
            self._add_graph_candidates(node, source_candidate)
    
    def _expand_chunk(self, chunk_id: str, source_candidate):
        """Expand from a chunk"""
        chunk = self.chunks.get_chunk(chunk_id)
        if not chunk:
            return

        # Emit COLLECT activation for circuit highlighting
        self.emit_activation(ActivationKind.COLLECT, TargetType.CHUNK, chunk_id)

        # Mark collected
        content = self.chunks.get_content(chunk_id)
        line_count = content.count('\n') + 1 if content else 0
        self.scorer.mark_chunk_collected(chunk_id, line_count)
        
        # Log step
        self._log_step(
            operator=OperatorType.CHUNK_WINDOW,
            source_id=source_candidate.source_id,
            target_id=chunk_id,
            score=self.scorer.compute_score(source_candidate),
            gradient="chunk",
            collected=True
        )
        
        # Mark associated node visited
        if chunk.node_id:
            self.scorer.mark_node_visited(chunk.node_id)
        
        policy = self.config.policy
        
        # Adjacency expansion
        if policy.use_adjacency:
            self._add_adjacency_candidates(chunk, source_candidate)
    
    # =========================================================================
    # Candidate Generation
    # =========================================================================
    
    def _add_structural_candidates(self, node: TreeNode, source_candidate):
        """Add structural expansion candidates"""
        distance = source_candidate.distance + 1
        
        # Children
        children = self.structure.children(node.node_id)
        for child in children:
            if child.node_id not in self.scorer.visited_nodes:
                struct_score = self.scorer.compute_structural_score(1)
                candidate = self.scorer.create_candidate(
                    target_id=child.node_id,
                    target_type="node",
                    operator=OperatorType.CHILDREN,
                    source_id=node.node_id,
                    structural=struct_score,
                    distance=distance,
                )
                self.scorer.add_candidate(candidate)
        
        # Parent (lower priority)
        parent = self.structure.parent(node.node_id)
        if parent and parent.node_id not in self.scorer.visited_nodes:
            struct_score = self.scorer.compute_structural_score(1) * 0.8
            candidate = self.scorer.create_candidate(
                target_id=parent.node_id,
                target_type="node",
                operator=OperatorType.PARENT,
                source_id=node.node_id,
                structural=struct_score,
                distance=distance,
            )
            self.scorer.add_candidate(candidate)
        
        # Siblings (limited)
        siblings = self.structure.siblings(node.node_id)[:3]
        for sib in siblings:
            if sib.node_id not in self.scorer.visited_nodes:
                struct_score = self.scorer.compute_structural_score(2) * 0.7
                candidate = self.scorer.create_candidate(
                    target_id=sib.node_id,
                    target_type="node",
                    operator=OperatorType.SIBLINGS,
                    source_id=node.node_id,
                    structural=struct_score,
                    distance=distance,
                )
                self.scorer.add_candidate(candidate)
    
    def _add_adjacency_candidates(self, chunk: ChunkManifest, source_candidate):
        """Add adjacency expansion candidates"""
        distance = source_candidate.distance + 1
        radius = self.config.policy.adjacency_radius
        
        # Previous chunks
        prev_chunk = self.chunks.chunk_prev(chunk.chunk_id)
        if prev_chunk and prev_chunk.chunk_id not in self.scorer.visited_chunks:
            adj_score = self.scorer.compute_adjacency_score(1)
            candidate = self.scorer.create_candidate(
                target_id=prev_chunk.chunk_id,
                target_type="chunk",
                operator=OperatorType.CHUNK_PREV,
                source_id=chunk.chunk_id,
                adjacency=adj_score,
                distance=distance,
            )
            self.scorer.add_candidate(candidate)
        
        # Next chunks
        next_chunk = self.chunks.chunk_next(chunk.chunk_id)
        if next_chunk and next_chunk.chunk_id not in self.scorer.visited_chunks:
            adj_score = self.scorer.compute_adjacency_score(1)
            candidate = self.scorer.create_candidate(
                target_id=next_chunk.chunk_id,
                target_type="chunk",
                operator=OperatorType.CHUNK_NEXT,
                source_id=chunk.chunk_id,
                adjacency=adj_score,
                distance=distance,
            )
            self.scorer.add_candidate(candidate)
    
    def _add_graph_candidates(self, node: TreeNode, source_candidate):
        """Add graph expansion candidates"""
        distance = source_candidate.distance + 1
        
        # Check hop budget
        if self.scorer.budget_state.graph_hops >= self.config.policy.graph_max_hops:
            return
        
        expansion = self.graph.expand_from_tree_node(
            node.node_id,
            edge_types=self.config.policy.allowed_edge_types,
            k=5,
            visited_graph=self.scorer.visited_nodes
        )
        
        for gnode, edge in expansion.neighbors:
            # Map back to tree node
            tree_node = self.graph.graph_to_tree_node(gnode.node_id)
            if not tree_node:
                continue
            
            if tree_node.node_id not in self.scorer.visited_nodes:
                graph_score = self.scorer.compute_graph_score(1, edge.edge_type)
                candidate = self.scorer.create_candidate(
                    target_id=tree_node.node_id,
                    target_type="node",
                    operator=OperatorType.GRAPH_NEIGHBORS,
                    source_id=node.node_id,
                    graph=graph_score,
                    edge_type=edge.edge_type,
                    distance=distance,
                )
                self.scorer.add_candidate(candidate)
        
        self.scorer.mark_graph_hop()
    
    # =========================================================================
    # Content Collection
    # =========================================================================
    
    def _collect_content(self) -> List[ContentBlock]:
        """Collect content for all visited chunks"""
        blocks = []
        
        chunk_ids = list(self.scorer.visited_chunks)
        contents = self.chunks.get_content_batch(chunk_ids)
        
        for chunk_id in chunk_ids:
            chunk = self.db.get_chunk(chunk_id)
            if not chunk:
                continue
            
            content = contents.get(chunk_id, "")
            line_count = content.count('\n') + 1 if content else 0
            
            # Get file path
            file_path = ""
            line_start = 0
            line_end = 0
            
            if chunk.node_id:
                node = self.db.get_tree_node(chunk.node_id)
                if node:
                    file_path = self.cas.get_file_path(node.file_cid)
                    line_start = node.line_start
                    line_end = node.line_end
            
            blocks.append(ContentBlock(
                chunk_id=chunk_id,
                node_id=chunk.node_id or "",
                content=content,
                context_prefix=chunk.context_prefix,
                file_path=file_path,
                line_start=line_start,
                line_end=line_end,
                line_count=line_count,
                gradient="collected",
                distance=0,
            ))
        
        return blocks
    
    # =========================================================================
    # Artifact Building
    # =========================================================================
    
    def _build_artifact(self, query: str,
                         seeds: List[Tuple[str, str]],
                         content_blocks: List[ContentBlock],
                         elapsed_ms: int
                         ) -> TraversalArtifact:
        """Build the output artifact"""
        manifest = self.manifest_reader.manifest
        
        # Build provenance
        collected_spans = []
        content_dicts = []
        
        for block in content_blocks:
            collected_spans.append(Provenance(
                chunk_id=block.chunk_id,
                node_id=block.node_id,
                file_cid="",  # Not tracking this level of detail
                file_path=block.file_path,
                line_start=block.line_start,
                line_end=block.line_end,
                context_prefix=block.context_prefix,
                gradient=block.gradient,
                distance=block.distance,
            ))
            
            content_dicts.append({
                "chunk_id": block.chunk_id,
                "context_prefix": block.context_prefix,
                "content": block.content,
                "file_path": block.file_path,
                "lines": f"{block.line_start}-{block.line_end}",
            })
        
        # Finalize trace
        if self.trace:
            self.trace.finished_at = datetime.utcnow().isoformat()
            self.trace.visited_nodes = list(self.scorer.visited_nodes)
            self.trace.visited_chunks = list(self.scorer.visited_chunks)
            self.trace.total_expansions = self.scorer.budget_state.total_expansions
            self.trace.total_lines_collected = self.scorer.budget_state.lines_collected
        
        return TraversalArtifact(
            cartridge_id=manifest.cartridge_id if manifest else "",
            query=query,
            mode=self.config.policy.mode,
            seeds=[s[0] for s in seeds],
            collected_spans=collected_spans,
            content_blocks=content_dicts,
            total_chunks=len(content_blocks),
            total_nodes=len(self.scorer.visited_nodes),
            total_lines=self.scorer.budget_state.lines_collected,
            elapsed_ms=elapsed_ms,
            trace=self.trace,
        )
    
    def _build_empty_artifact(self, query: str, reason: str) -> TraversalArtifact:
        """Build an empty artifact when traversal fails"""
        manifest = self.manifest_reader.manifest
        
        return TraversalArtifact(
            cartridge_id=manifest.cartridge_id if manifest else "",
            query=query,
            mode=self.config.policy.mode,
            seeds=[],
            collected_spans=[],
            content_blocks=[{"error": reason}],
            total_chunks=0,
            total_nodes=0,
            total_lines=0,
            elapsed_ms=0,
        )
    
    # =========================================================================
    # Trace Logging
    # =========================================================================
    
    def _log_step(self, operator: OperatorType,
                   source_id: str, target_id: str,
                   score: float, gradient: str,
                   collected: bool = False):
        """Log a traversal step"""
        if not self.trace:
            return
        
        self._step_counter += 1
        
        step = TraversalStep(
            step_id=self._step_counter,
            operator=operator,
            source_id=source_id,
            target_id=target_id,
            score=score,
            gradient=gradient,
            collected=collected,
        )
        
        self.trace.steps.append(step)

    # =========================================================================
    # Activation Emission (Circuit Highlighting)
    # =========================================================================

    def emit_activation(
        self,
        kind: ActivationKind,
        target_type: TargetType,
        target_id: str,
        weight: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Emit an activation event for circuit highlighting.

        This method creates an ActivationEvent, stores it in the session DB
        and in-memory store, and broadcasts it via the event bus.

        Args:
            kind: Type of activation (ENTRY_HIT, TRAVERSAL_HOP, etc.)
            target_type: Type of target (CHUNK, TREE_NODE, etc.)
            target_id: ID of the activated target
            weight: Custom weight (defaults to weight for the kind)
            meta: Optional metadata dict
        """
        if not self.session_id or not self.query_id:
            return  # Silently ignore if not in an active query

        # Default weight from kind
        if weight is None:
            weight = WEIGHT_BY_KIND.get(kind, 1.0)

        # Create event
        event = ActivationEvent(
            session_id=self.session_id,
            query_id=self.query_id,
            kind=kind,
            target_type=target_type,
            target_id=target_id,
            weight=weight,
            meta=meta or {}
        )

        # Store in-memory
        if self.config.activation_store:
            self.config.activation_store.add(event)

        # Store in database
        if self.config.session_db:
            self.config.session_db.insert_activation(event)

        # Emit event bus event (single activation) - lazy import
        try:
            from src.ui.event_bus import get_event_bus
            bus = get_event_bus()
            bus.emit("ACTIVATION_EVENT", {
                "event_id": event.event_id,
                "kind": event.kind.value,
                "target_type": event.target_type.value,
                "target_id": event.target_id,
                "weight": event.weight,
            })
        except ImportError:
            pass  # Event bus not available

        # Debounced top-activation emission (max 10/sec)
        current_time = time.time()
        if (current_time - self._last_activation_emit_time) >= self._activation_emit_debounce:
            self._emit_top_activations()
            self._last_activation_emit_time = current_time

    def _emit_top_activations(self) -> None:
        """Emit top activations to the event bus."""
        if not self.config.activation_store or not self.query_id:
            return

        try:
            from src.ui.event_bus import get_event_bus
            bus = get_event_bus()
            top_targets = self.config.activation_store.top_targets(limit=25)

            # Create explain function
            def explain_fn(target_type: str, target_id: str):
                return self.config.activation_store.explain(target_type, target_id)

            bus.emit("ACTIVATION_TOP", {
                "query_id": self.query_id,
                "top_targets": top_targets,
                "explain_fn": explain_fn,
            })
        except ImportError:
            pass  # Event bus not available

    # =========================================================================
    # Stats
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get combined stats from all modules"""
        return {
            "cas": self.cas.stats.copy(),
            "structure": self.structure.stats.copy(),
            "chunks": self.chunks.stats.copy(),
            "graph": self.graph.stats.copy(),
            "scorer": self.scorer.get_stats() if self.scorer else {},
        }
