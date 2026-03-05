"""
Database Layer
SQLite connection management and prepared statement execution.
All raw database access goes through this module.
"""

import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from src.walker.types import (
    CartridgeManifest, IngestRun, SourceFile, TreeNode,
    ChunkManifest, ChunkSpan, ChunkHierarchy, ChunkOverlap,
    GraphNode, GraphEdge
)


class DatabaseError(Exception):
    """Base database exception"""
    pass


class NotConnectedError(DatabaseError):
    """Operation attempted without connection"""
    pass


class IntegrityError(DatabaseError):
    """Data integrity error"""
    pass


# Required tables for a valid Tripartite cartridge
REQUIRED_TABLES = frozenset({
    "verbatim_lines",
    "source_files",
    "tree_nodes",
    "chunk_manifest",
    "embeddings",
    "graph_nodes",
    "graph_edges",
    "cartridge_manifest",
    "ingest_runs",
})

OPTIONAL_TABLES = frozenset({
    "diff_chain",
    "snapshots",
    "fts_chunks",
    "fts_lines",
})


class CartridgeDB:
    """
    Read-only connection to a Tripartite cartridge.
    Provides low-level SQL access with caching.
    """
    
    def __init__(self):
        self._conn: Optional[sqlite3.Connection] = None
        self._path: Optional[Path] = None
        
        # Schema caches
        self._tables: Optional[Set[str]] = None
        self._columns: Dict[str, List[str]] = {}
        
        # Content caches (keyed by canonical IDs)
        self._file_cache: Dict[str, SourceFile] = {}
        self._line_cache: Dict[str, str] = {}
        self._node_cache: Dict[str, TreeNode] = {}
        self._chunk_cache: Dict[str, ChunkManifest] = {}
        
        # Cache limits
        self._max_line_cache = 10000
        self._max_file_cache = 500
    
    @property
    def is_connected(self) -> bool:
        return self._conn is not None
    
    @property
    def path(self) -> Optional[Path]:
        return self._path
    
    # =========================================================================
    # Connection Management
    # =========================================================================
    
    def connect(self, path: Path) -> None:
        """Open read-only connection to cartridge"""
        if self._conn:
            self.close()
        
        path = Path(path)
        if not path.exists():
            raise DatabaseError(f"File not found: {path}")
        
        if path.suffix != ".db":
            raise DatabaseError(f"Not a .db file: {path}")
        
        try:
            uri = f"file:{path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._path = path
            self._clear_caches()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to open: {e}")
    
    def close(self) -> None:
        """Close connection"""
        if self._conn:
            self._conn.close()
            self._conn = None
            self._path = None
            self._clear_caches()
    
    def _clear_caches(self) -> None:
        """Clear all caches"""
        self._tables = None
        self._columns.clear()
        self._file_cache.clear()
        self._line_cache.clear()
        self._node_cache.clear()
        self._chunk_cache.clear()
    
    @contextmanager
    def cursor(self):
        """Get a cursor with error handling"""
        if not self._conn:
            raise NotConnectedError("No database connected")
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
    
    # =========================================================================
    # Schema Introspection
    # =========================================================================
    
    def get_tables(self) -> Set[str]:
        """Get all table names"""
        if self._tables is None:
            with self.cursor() as cur:
                cur.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type IN ('table', 'view')
                """)
                self._tables = {row[0] for row in cur.fetchall()}
        return self._tables
    
    def has_table(self, name: str) -> bool:
        """Check if table exists"""
        return name in self.get_tables()
    
    def get_columns(self, table: str) -> List[str]:
        """Get column names for a table"""
        if table not in self._columns:
            with self.cursor() as cur:
                cur.execute(f"PRAGMA table_info({table})")
                self._columns[table] = [row[1] for row in cur.fetchall()]
        return self._columns[table]
    
    def has_column(self, table: str, column: str) -> bool:
        """Check if column exists in table"""
        return column in self.get_columns(table)
    
    def get_missing_tables(self) -> Set[str]:
        """Get required tables that are missing"""
        return REQUIRED_TABLES - self.get_tables()
    
    def validate_schema(self) -> List[str]:
        """
        Validate schema and return list of issues.
        Empty list means schema is valid.
        """
        issues = []
        
        missing = self.get_missing_tables()
        if missing:
            issues.append(f"Missing tables: {', '.join(sorted(missing))}")
        
        # Check cartridge_manifest has exactly 1 row
        if self.has_table("cartridge_manifest"):
            with self.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM cartridge_manifest")
                count = cur.fetchone()[0]
                if count != 1:
                    issues.append(f"cartridge_manifest has {count} rows (expected 1)")
        
        return issues
    
    # =========================================================================
    # Counting
    # =========================================================================
    
    def count_table(self, table: str) -> int:
        """Get row count for a table"""
        if not self.has_table(table):
            return 0
        with self.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
    
    def get_all_counts(self) -> Dict[str, int]:
        """Get row counts for all tables"""
        counts = {}
        for table in self.get_tables():
            try:
                counts[table] = self.count_table(table)
            except:
                counts[table] = -1
        return counts
    
    # =========================================================================
    # Manifest Queries
    # =========================================================================
    
    def get_cartridge_manifest(self) -> Optional[CartridgeManifest]:
        """Read cartridge manifest (expect 1 row)"""
        if not self.has_table("cartridge_manifest"):
            return None
        
        with self.cursor() as cur:
            cur.execute("SELECT * FROM cartridge_manifest LIMIT 1")
            row = cur.fetchone()
            if not row:
                return None
            
            d = dict(row)
            return CartridgeManifest(
                cartridge_id=d.get("cartridge_id", ""),
                created_at=d.get("created_at", ""),
                updated_at=d.get("updated_at", ""),
                schema_ver=d.get("schema_ver", 1),
                pipeline_ver=d.get("pipeline_ver", ""),
                source_root=d.get("source_root", ""),
                source_roots_json=d.get("source_roots_json", "[]"),
                embed_model=d.get("embed_model", ""),
                embed_dims=d.get("embed_dims", 0),
                structural_complete=bool(d.get("structural_complete", 0)),
                semantic_complete=bool(d.get("semantic_complete", 0)),
                graph_complete=bool(d.get("graph_complete", 0)),
                search_index_complete=bool(d.get("search_index_complete", 0)),
                is_deployable=bool(d.get("is_deployable", 0)),
                deployment_notes=d.get("deployment_notes", ""),
                file_count=d.get("file_count", 0),
                line_count=d.get("line_count", 0),
                tree_node_count=d.get("tree_node_count", 0),
                chunk_count=d.get("chunk_count", 0),
                embedding_count=d.get("embedding_count", 0),
                graph_node_count=d.get("graph_node_count", 0),
                graph_edge_count=d.get("graph_edge_count", 0),
            )
    
    def get_latest_ingest_run(self) -> Optional[IngestRun]:
        """Get most recent ingest run"""
        if not self.has_table("ingest_runs"):
            return None
        
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM ingest_runs 
                ORDER BY run_id DESC LIMIT 1
            """)
            row = cur.fetchone()
            if not row:
                return None
            
            d = dict(row)
            
            stage_status = d.get("stage_status", "{}")
            if isinstance(stage_status, str):
                try:
                    stage_status = json.loads(stage_status)
                except:
                    stage_status = {}
            
            error_log = d.get("error_log", "[]")
            if isinstance(error_log, str):
                try:
                    error_log = json.loads(error_log)
                except:
                    error_log = []
            
            return IngestRun(
                run_id=d.get("run_id", 0),
                started_at=d.get("started_at", ""),
                finished_at=d.get("finished_at", ""),
                status=d.get("status", ""),
                source_root=d.get("source_root", ""),
                stage_status=stage_status,
                error_log=error_log,
            )
    
    # =========================================================================
    # Verbatim Layer (CAS)
    # =========================================================================
    
    def get_verbatim_line(self, line_cid: str) -> Optional[str]:
        """Get single line content by CID (cached)"""
        if line_cid in self._line_cache:
            return self._line_cache[line_cid]
        
        with self.cursor() as cur:
            cur.execute(
                "SELECT content FROM verbatim_lines WHERE line_cid = ?",
                (line_cid,)
            )
            row = cur.fetchone()
            if row:
                content = row[0]
                if len(self._line_cache) < self._max_line_cache:
                    self._line_cache[line_cid] = content
                return content
        return None
    
    def get_verbatim_lines_batch(self, line_cids: List[str]) -> Dict[str, str]:
        """Get multiple lines by CID (batch query)"""
        if not line_cids:
            return {}
        
        result = {}
        missing = []
        
        # Check cache first
        for cid in line_cids:
            if cid in self._line_cache:
                result[cid] = self._line_cache[cid]
            else:
                missing.append(cid)
        
        # Batch query for missing
        if missing:
            with self.cursor() as cur:
                placeholders = ",".join("?" * len(missing))
                cur.execute(f"""
                    SELECT line_cid, content FROM verbatim_lines
                    WHERE line_cid IN ({placeholders})
                """, missing)
                
                for row in cur.fetchall():
                    cid, content = row[0], row[1]
                    result[cid] = content
                    if len(self._line_cache) < self._max_line_cache:
                        self._line_cache[cid] = content
        
        return result
    
    def get_source_file(self, file_cid: str) -> Optional[SourceFile]:
        """Get source file by CID (cached)"""
        if file_cid in self._file_cache:
            return self._file_cache[file_cid]
        
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM source_files WHERE file_cid = ?",
                (file_cid,)
            )
            row = cur.fetchone()
            if not row:
                return None
            
            d = dict(row)
            sf = SourceFile(
                file_cid=d.get("file_cid", ""),
                path=d.get("path", ""),
                line_cids=SourceFile.parse_line_cids(d.get("line_cids", "[]")),
                line_count=d.get("line_count", 0),
                byte_size=d.get("byte_size", 0),
                mime_type=d.get("mime_type", ""),
                language=d.get("language", ""),
            )
            
            if len(self._file_cache) < self._max_file_cache:
                self._file_cache[file_cid] = sf
            
            return sf
    
    def iter_source_files(self) -> Iterator[SourceFile]:
        """Iterate all source files"""
        with self.cursor() as cur:
            cur.execute("SELECT * FROM source_files ORDER BY path")
            for row in cur.fetchall():
                d = dict(row)
                yield SourceFile(
                    file_cid=d.get("file_cid", ""),
                    path=d.get("path", ""),
                    line_cids=SourceFile.parse_line_cids(d.get("line_cids", "[]")),
                    line_count=d.get("line_count", 0),
                    byte_size=d.get("byte_size", 0),
                    mime_type=d.get("mime_type", ""),
                    language=d.get("language", ""),
                )
    
    # =========================================================================
    # Structural Layer (tree_nodes)
    # =========================================================================
    
    def _row_to_tree_node(self, row: sqlite3.Row) -> TreeNode:
        """Convert row to TreeNode"""
        d = dict(row)
        return TreeNode(
            node_id=d.get("node_id", ""),
            node_type=d.get("node_type", ""),
            name=d.get("name", ""),
            parent_id=d.get("parent_id"),
            path=d.get("path", ""),
            depth=d.get("depth", 0),
            file_cid=d.get("file_cid", ""),
            line_start=d.get("line_start", 0),
            line_end=d.get("line_end", 0),
            chunk_id=d.get("chunk_id"),
            graph_node_id=d.get("graph_node_id"),
        )
    
    def get_tree_node(self, node_id: str) -> Optional[TreeNode]:
        """Get tree node by ID"""
        if node_id in self._node_cache:
            return self._node_cache[node_id]
        
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM tree_nodes WHERE node_id = ?",
                (node_id,)
            )
            row = cur.fetchone()
            if row:
                node = self._row_to_tree_node(row)
                self._node_cache[node_id] = node
                return node
        return None
    
    def get_tree_children(self, parent_id: str) -> List[TreeNode]:
        """Get children of a tree node"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM tree_nodes 
                WHERE parent_id = ?
                ORDER BY line_start, name
            """, (parent_id,))
            return [self._row_to_tree_node(row) for row in cur.fetchall()]
    
    def get_tree_parent(self, node_id: str) -> Optional[TreeNode]:
        """Get parent of a tree node"""
        node = self.get_tree_node(node_id)
        if not node or not node.parent_id:
            return None
        return self.get_tree_node(node.parent_id)
    
    def get_tree_siblings(self, node_id: str) -> List[TreeNode]:
        """Get siblings (same parent, excluding self)"""
        node = self.get_tree_node(node_id)
        if not node or not node.parent_id:
            return []
        
        children = self.get_tree_children(node.parent_id)
        return [c for c in children if c.node_id != node_id]
    
    def get_tree_roots(self) -> List[TreeNode]:
        """Get root nodes (no parent)"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM tree_nodes 
                WHERE parent_id IS NULL
                ORDER BY path, name
            """)
            return [self._row_to_tree_node(row) for row in cur.fetchall()]
    
    def get_tree_nodes_by_type(self, node_type: str) -> List[TreeNode]:
        """Get all nodes of a type"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM tree_nodes 
                WHERE node_type = ?
                ORDER BY path
            """, (node_type,))
            return [self._row_to_tree_node(row) for row in cur.fetchall()]
    
    def get_tree_nodes_by_file(self, file_cid: str) -> List[TreeNode]:
        """Get all nodes for a file"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM tree_nodes 
                WHERE file_cid = ?
                ORDER BY line_start
            """, (file_cid,))
            return [self._row_to_tree_node(row) for row in cur.fetchall()]
    
    # =========================================================================
    # Semantic Layer (chunk_manifest)
    # =========================================================================
    
    def _row_to_chunk(self, row: sqlite3.Row) -> ChunkManifest:
        """Convert row to ChunkManifest"""
        d = dict(row)
        return ChunkManifest(
            chunk_id=d.get("chunk_id", ""),
            node_id=d.get("node_id", ""),
            chunk_type=d.get("chunk_type", ""),
            context_prefix=d.get("context_prefix", ""),
            token_count=d.get("token_count", 0),
            spans=ChunkSpan.parse_spans(d.get("spans", "[]")),
            hierarchy=ChunkHierarchy.from_json(d.get("hierarchy")),
            overlap=ChunkOverlap.from_json(d.get("overlap")),
            embed_status=d.get("embed_status", "pending"),
            embed_model=d.get("embed_model", ""),
            embed_dims=d.get("embed_dims", 0),
            graph_status=d.get("graph_status", "none"),
        )
    
    def get_chunk(self, chunk_id: str) -> Optional[ChunkManifest]:
        """Get chunk by ID (cached)"""
        if chunk_id in self._chunk_cache:
            return self._chunk_cache[chunk_id]
        
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM chunk_manifest WHERE chunk_id = ?",
                (chunk_id,)
            )
            row = cur.fetchone()
            if row:
                chunk = self._row_to_chunk(row)
                self._chunk_cache[chunk_id] = chunk
                return chunk
        return None
    
    def get_chunks_for_node(self, node_id: str) -> List[ChunkManifest]:
        """Get chunks associated with a tree node"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM chunk_manifest 
                WHERE node_id = ?
            """, (node_id,))
            return [self._row_to_chunk(row) for row in cur.fetchall()]
    
    def get_chunks_by_type(self, chunk_type: str) -> List[ChunkManifest]:
        """Get all chunks of a type"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM chunk_manifest 
                WHERE chunk_type = ?
            """, (chunk_type,))
            return [self._row_to_chunk(row) for row in cur.fetchall()]
    
    # =========================================================================
    # Graph Layer
    # =========================================================================
    
    def _row_to_graph_node(self, row: sqlite3.Row) -> GraphNode:
        """Convert row to GraphNode"""
        d = dict(row)
        props = d.get("properties", "{}")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except:
                props = {}
        
        return GraphNode(
            node_id=d.get("node_id", ""),
            label=d.get("label", ""),
            node_type=d.get("node_type", ""),
            tree_node_id=d.get("tree_node_id"),
            properties=props,
        )
    
    def _row_to_graph_edge(self, row: sqlite3.Row) -> GraphEdge:
        """Convert row to GraphEdge"""
        d = dict(row)
        props = d.get("properties", "{}")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except:
                props = {}
        
        return GraphEdge(
            edge_id=d.get("edge_id", ""),
            source_id=d.get("source_id", ""),
            target_id=d.get("target_id", ""),
            edge_type=d.get("edge_type", ""),
            weight=d.get("weight", 1.0),
            properties=props,
        )
    
    def get_graph_node(self, node_id: str) -> Optional[GraphNode]:
        """Get graph node by ID"""
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM graph_nodes WHERE node_id = ?",
                (node_id,)
            )
            row = cur.fetchone()
            return self._row_to_graph_node(row) if row else None
    
    def get_graph_edges_from(self, source_id: str) -> List[GraphEdge]:
        """Get edges from a graph node"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM graph_edges 
                WHERE source_id = ?
                ORDER BY weight DESC
            """, (source_id,))
            return [self._row_to_graph_edge(row) for row in cur.fetchall()]
    
    def get_graph_edges_to(self, target_id: str) -> List[GraphEdge]:
        """Get edges to a graph node"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM graph_edges 
                WHERE target_id = ?
                ORDER BY weight DESC
            """, (target_id,))
            return [self._row_to_graph_edge(row) for row in cur.fetchall()]
    
    def get_graph_edges_by_type(self, edge_type: str) -> List[GraphEdge]:
        """Get all edges of a type"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT * FROM graph_edges 
                WHERE edge_type = ?
                ORDER BY weight DESC
            """, (edge_type,))
            return [self._row_to_graph_edge(row) for row in cur.fetchall()]
    
    def get_all_edge_types(self) -> List[str]:
        """Get unique edge types"""
        with self.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT edge_type FROM graph_edges 
                ORDER BY edge_type
            """)
            return [row[0] for row in cur.fetchall()]
    
    # =========================================================================
    # FTS Search
    # =========================================================================
    
    def fts_search_chunks(self, query: str, limit: int = 20
                          ) -> List[tuple[str, float]]:
        """
        FTS search on chunks.
        Returns list of (chunk_id, rank) tuples.
        """
        if not self.has_table("fts_chunks"):
            return []
        
        try:
            with self.cursor() as cur:
                cur.execute("""
                    SELECT chunk_id, rank
                    FROM fts_chunks
                    WHERE fts_chunks MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, limit))
                return [(row[0], row[1]) for row in cur.fetchall()]
        except sqlite3.Error:
            return []
    
    def fts_search_chunks_with_snippet(self, query: str, limit: int = 20
                                        ) -> List[tuple[str, float, str]]:
        """
        FTS search with snippets.
        Returns list of (chunk_id, rank, snippet) tuples.
        """
        if not self.has_table("fts_chunks"):
            return []
        
        try:
            with self.cursor() as cur:
                cur.execute("""
                    SELECT chunk_id, rank,
                           snippet(fts_chunks, 0, '<mark>', '</mark>', '...', 32)
                    FROM fts_chunks
                    WHERE fts_chunks MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, limit))
                return [(row[0], row[1], row[2]) for row in cur.fetchall()]
        except sqlite3.Error:
            return []
    
    # =========================================================================
    # Embeddings
    # =========================================================================
    
    def get_embedding(self, chunk_id: str) -> Optional[List[float]]:
        """Get embedding vector for a chunk"""
        if not self.has_table("embeddings"):
            return None
        
        try:
            with self.cursor() as cur:
                cur.execute(
                    "SELECT vector FROM embeddings WHERE chunk_id = ?",
                    (chunk_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                
                vec = row[0]
                if isinstance(vec, bytes):
                    import struct
                    n = len(vec) // 4
                    return list(struct.unpack(f'{n}f', vec))
                elif isinstance(vec, str):
                    return json.loads(vec)
        except:
            pass
        return None
