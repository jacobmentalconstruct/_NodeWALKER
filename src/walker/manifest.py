"""
Manifest Module
Manifest readers, integrity checks, and deployment readiness validation.
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from src.walker.types import CartridgeManifest, IngestRun, TraversalMode
from src.walker.db import CartridgeDB


class ReadinessLevel(Enum):
    """Deployment readiness levels"""
    BLOCKED = "blocked"
    DEGRADED = "degraded"
    READY = "ready"


@dataclass
class IntegrityCheck:
    """Result of a single integrity check"""
    name: str
    passed: bool
    message: str
    severity: int = 0  # 0=info, 1=warn, 2=error, 3=fatal
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadinessReport:
    """Full deployment readiness report"""
    level: ReadinessLevel = ReadinessLevel.BLOCKED
    checks: List[IntegrityCheck] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    # Capabilities based on manifest flags
    can_use_structure: bool = False
    can_use_semantic: bool = False
    can_use_graph: bool = False
    can_use_fts: bool = False
    
    # Recommended traversal mode
    recommended_mode: TraversalMode = TraversalMode.STRUCTURE_FIRST
    
    def add_check(self, check: IntegrityCheck):
        """Add a check result"""
        self.checks.append(check)
        if not check.passed:
            if check.severity >= 3:
                self.blockers.append(f"{check.name}: {check.message}")
            elif check.severity >= 2:
                self.blockers.append(f"{check.name}: {check.message}")
            elif check.severity >= 1:
                self.warnings.append(f"{check.name}: {check.message}")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "can_use_structure": self.can_use_structure,
            "can_use_semantic": self.can_use_semantic,
            "can_use_graph": self.can_use_graph,
            "can_use_fts": self.can_use_fts,
            "recommended_mode": self.recommended_mode.value,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "severity": c.severity,
                }
                for c in self.checks
            ],
        }


class ManifestReader:
    """
    Reads and validates cartridge manifests.
    Entry point for cartridge introspection.
    """
    
    def __init__(self, db: CartridgeDB):
        self.db = db
        self._manifest: Optional[CartridgeManifest] = None
        self._ingest_run: Optional[IngestRun] = None
    
    @property
    def manifest(self) -> Optional[CartridgeManifest]:
        """Cached manifest (read once)"""
        if self._manifest is None:
            self._manifest = self.db.get_cartridge_manifest()
        return self._manifest
    
    @property
    def latest_ingest(self) -> Optional[IngestRun]:
        """Cached latest ingest run"""
        if self._ingest_run is None:
            self._ingest_run = self.db.get_latest_ingest_run()
        return self._ingest_run
    
    def refresh(self):
        """Clear cached values"""
        self._manifest = None
        self._ingest_run = None
    
    # =========================================================================
    # Fast Integrity Checks (pre-traversal)
    # =========================================================================
    
    def check_required_tables(self) -> IntegrityCheck:
        """Check all required tables exist"""
        from src.walker.db import REQUIRED_TABLES
        missing = self.db.get_missing_tables()
        if missing:
            return IntegrityCheck(
                name="required_tables",
                passed=False,
                message=f"Missing: {', '.join(sorted(missing))}",
                severity=3,
                details={"missing": list(missing)}
            )
        return IntegrityCheck(
            name="required_tables",
            passed=True,
            message=f"All {len(REQUIRED_TABLES)} required tables present",
            severity=0
        )
    
    def check_manifest_exists(self) -> IntegrityCheck:
        """Check cartridge_manifest has exactly 1 row"""
        if not self.db.has_table("cartridge_manifest"):
            return IntegrityCheck(
                name="manifest_exists",
                passed=False,
                message="cartridge_manifest table missing",
                severity=3
            )
        
        count = self.db.count_table("cartridge_manifest")
        if count != 1:
            return IntegrityCheck(
                name="manifest_exists",
                passed=False,
                message=f"Expected 1 row, found {count}",
                severity=3,
                details={"count": count}
            )
        
        return IntegrityCheck(
            name="manifest_exists",
            passed=True,
            message="Manifest present",
            severity=0
        )
    
    def check_ingest_success(self) -> IntegrityCheck:
        """Check latest ingest run was successful"""
        run = self.latest_ingest
        if not run:
            return IntegrityCheck(
                name="ingest_success",
                passed=False,
                message="No ingest runs found",
                severity=2
            )
        
        if not run.is_success:
            return IntegrityCheck(
                name="ingest_success",
                passed=False,
                message=f"Latest run status: {run.status}",
                severity=2,
                details={"status": run.status, "run_id": run.run_id}
            )
        
        return IntegrityCheck(
            name="ingest_success",
            passed=True,
            message=f"Run #{run.run_id}: {run.status}",
            severity=0
        )
    
    def check_count_matches(self, table: str, manifest_field: str) -> IntegrityCheck:
        """Check table count matches manifest value"""
        manifest = self.manifest
        if not manifest:
            return IntegrityCheck(
                name=f"count_{table}",
                passed=True,
                message="No manifest to compare",
                severity=0
            )
        
        expected = getattr(manifest, manifest_field, None)
        if expected is None or expected == 0:
            return IntegrityCheck(
                name=f"count_{table}",
                passed=True,
                message="No expected count in manifest",
                severity=0
            )
        
        actual = self.db.count_table(table)
        if actual != expected:
            return IntegrityCheck(
                name=f"count_{table}",
                passed=False,
                message=f"Expected {expected}, found {actual}",
                severity=1,
                details={"expected": expected, "actual": actual}
            )
        
        return IntegrityCheck(
            name=f"count_{table}",
            passed=True,
            message=f"{actual} rows (matches manifest)",
            severity=0
        )
    
    def check_source_files_populated(self) -> IntegrityCheck:
        """Check source_files has data"""
        count = self.db.count_table("source_files")
        if count == 0:
            return IntegrityCheck(
                name="source_files",
                passed=False,
                message="No source files",
                severity=3
            )
        return IntegrityCheck(
            name="source_files",
            passed=True,
            message=f"{count} files",
            severity=0
        )
    
    def check_tree_nodes_populated(self) -> IntegrityCheck:
        """Check tree_nodes has data"""
        count = self.db.count_table("tree_nodes")
        if count == 0:
            return IntegrityCheck(
                name="tree_nodes",
                passed=False,
                message="No tree nodes",
                severity=2
            )
        return IntegrityCheck(
            name="tree_nodes",
            passed=True,
            message=f"{count} nodes",
            severity=0
        )
    
    def check_chunks_populated(self) -> IntegrityCheck:
        """Check chunk_manifest has data"""
        count = self.db.count_table("chunk_manifest")
        if count == 0:
            return IntegrityCheck(
                name="chunks",
                passed=False,
                message="No chunks",
                severity=2
            )
        return IntegrityCheck(
            name="chunks",
            passed=True,
            message=f"{count} chunks",
            severity=0
        )
    
    def check_embeddings_status(self) -> IntegrityCheck:
        """Check embedding completion status"""
        manifest = self.manifest
        if not manifest:
            return IntegrityCheck(
                name="embeddings",
                passed=True,
                message="No manifest",
                severity=0
            )
        
        if not manifest.semantic_complete:
            embed_count = self.db.count_table("embeddings")
            chunk_count = self.db.count_table("chunk_manifest")
            
            if embed_count == 0:
                return IntegrityCheck(
                    name="embeddings",
                    passed=False,
                    message="No embeddings generated",
                    severity=1
                )
            
            ratio = embed_count / max(chunk_count, 1)
            if ratio < 0.5:
                return IntegrityCheck(
                    name="embeddings",
                    passed=False,
                    message=f"Only {embed_count}/{chunk_count} ({ratio:.0%}) embedded",
                    severity=1,
                    details={"ratio": ratio}
                )
        
        return IntegrityCheck(
            name="embeddings",
            passed=True,
            message=f"semantic_complete={manifest.semantic_complete}",
            severity=0
        )
    
    def check_graph_status(self) -> IntegrityCheck:
        """Check graph layer status"""
        manifest = self.manifest
        
        node_count = self.db.count_table("graph_nodes")
        edge_count = self.db.count_table("graph_edges")
        
        if node_count == 0:
            return IntegrityCheck(
                name="graph",
                passed=True,
                message="Graph not built (optional)",
                severity=0,
                details={"status": "none"}
            )
        
        status = "structural"
        if manifest and manifest.graph_complete:
            status = "done"
        
        return IntegrityCheck(
            name="graph",
            passed=True,
            message=f"{node_count} nodes, {edge_count} edges (status: {status})",
            severity=0,
            details={"status": status, "nodes": node_count, "edges": edge_count}
        )
    
    def check_fts_status(self) -> IntegrityCheck:
        """Check FTS index status"""
        if not self.db.has_table("fts_chunks"):
            return IntegrityCheck(
                name="fts",
                passed=True,
                message="FTS not built",
                severity=0,
                details={"available": False}
            )
        
        count = self.db.count_table("fts_chunks")
        if count == 0:
            return IntegrityCheck(
                name="fts",
                passed=False,
                message="FTS table empty",
                severity=1,
                details={"available": False}
            )
        
        return IntegrityCheck(
            name="fts",
            passed=True,
            message=f"FTS indexed: {count} entries",
            severity=0,
            details={"available": True, "count": count}
        )
    
    # =========================================================================
    # Full Readiness Assessment
    # =========================================================================
    
    def assess_readiness(self) -> ReadinessReport:
        """
        Run full readiness assessment.
        Returns report with capabilities and recommended traversal mode.
        """
        report = ReadinessReport()
        
        # Run all checks
        report.add_check(self.check_required_tables())
        report.add_check(self.check_manifest_exists())
        report.add_check(self.check_ingest_success())
        report.add_check(self.check_source_files_populated())
        report.add_check(self.check_tree_nodes_populated())
        report.add_check(self.check_chunks_populated())
        report.add_check(self.check_embeddings_status())
        
        graph_check = self.check_graph_status()
        report.add_check(graph_check)
        
        fts_check = self.check_fts_status()
        report.add_check(fts_check)
        
        # Count validation
        report.add_check(self.check_count_matches("source_files", "file_count"))
        report.add_check(self.check_count_matches("tree_nodes", "tree_node_count"))
        report.add_check(self.check_count_matches("chunk_manifest", "chunk_count"))
        
        # Determine capabilities
        manifest = self.manifest
        
        # Structure capability
        if manifest and manifest.structural_complete:
            report.can_use_structure = True
        elif self.db.count_table("tree_nodes") > 0:
            report.can_use_structure = True
        
        # Semantic capability
        if manifest and manifest.semantic_complete:
            report.can_use_semantic = True
        elif self.db.count_table("embeddings") > 0:
            report.can_use_semantic = True
        
        # Graph capability
        if graph_check.details.get("status") in ("structural", "done"):
            report.can_use_graph = True
        
        # FTS capability
        if fts_check.details.get("available"):
            report.can_use_fts = True
        
        # Check manifest deployability
        if manifest and not manifest.is_deployable:
            report.blockers.append("cartridge_manifest.is_deployable = false")
        
        # Determine overall level
        if report.blockers:
            report.level = ReadinessLevel.BLOCKED
        elif report.warnings:
            report.level = ReadinessLevel.DEGRADED
        else:
            report.level = ReadinessLevel.READY
        
        # Recommend traversal mode
        if report.can_use_semantic and report.can_use_fts:
            report.recommended_mode = TraversalMode.SEMANTIC_SEEDED
        elif report.can_use_graph:
            report.recommended_mode = TraversalMode.GRAPH_ASSISTED
        elif report.can_use_structure:
            report.recommended_mode = TraversalMode.STRUCTURE_FIRST
        else:
            report.recommended_mode = TraversalMode.STRUCTURE_FIRST
        
        return report
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def get_telemetry(self) -> Dict[str, int]:
        """Get table counts for telemetry display"""
        return self.db.get_all_counts()
    
    def is_deployable(self) -> Tuple[bool, List[str]]:
        """Quick deployability check. Returns (deployable, blockers)"""
        report = self.assess_readiness()
        return (report.level != ReadinessLevel.BLOCKED, report.blockers)
    
    def get_capabilities(self) -> Dict[str, bool]:
        """Get available capabilities"""
        report = self.assess_readiness()
        return {
            "structure": report.can_use_structure,
            "semantic": report.can_use_semantic,
            "graph": report.can_use_graph,
            "fts": report.can_use_fts,
        }
