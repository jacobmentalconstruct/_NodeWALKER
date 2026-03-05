#!/usr/bin/env python3
"""
Comprehensive Test Suite for Node Walker v3
Run with: python -m src.test_walker
"""

import sqlite3
import json
import sys
import uuid
import hashlib
import tempfile
from pathlib import Path

from src.walker import (
    # Database
    CartridgeDB, REQUIRED_TABLES,
    
    # Manifest
    ManifestReader, ReadinessLevel,
    
    # CAS
    CASResolver,
    
    # Operators
    StructureOperators, ChunkOperators, GraphOperators,
    
    # Scoring
    Scorer, TraversalPolicy, TraversalMode, Budgets, ScoreWeights,
    
    # Walker
    NodeWalker, WalkerConfig,
    
    # Policy
    PolicySelector,
    
    # Notes
    NotesDB,
    
    # Signature
    SignatureComputer,
    
    # Anti-data
    AntiDataEngine, AntiDataAction,
)


def sha256_cid(content: str) -> str:
    """Generate a sha256 CID"""
    h = hashlib.sha256(content.encode()).hexdigest()
    return f"sha256:{h[:16]}"


def create_test_cartridge(path: Path):
    """Create a complete Tripartite test cartridge"""
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    
    # Create all tables
    cur.executescript("""
        -- Verbatim Layer
        CREATE TABLE verbatim_lines (
            line_cid TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            byte_len INTEGER
        );
        
        CREATE TABLE source_files (
            file_cid TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            line_cids TEXT NOT NULL,
            line_count INTEGER,
            byte_size INTEGER,
            language TEXT
        );
        
        -- Structural Layer
        CREATE TABLE tree_nodes (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            name TEXT,
            parent_id TEXT,
            path TEXT,
            depth INTEGER DEFAULT 0,
            file_cid TEXT,
            line_start INTEGER,
            line_end INTEGER,
            chunk_id TEXT,
            graph_node_id TEXT
        );
        
        -- Semantic Layer
        CREATE TABLE chunk_manifest (
            chunk_id TEXT PRIMARY KEY,
            node_id TEXT,
            chunk_type TEXT,
            context_prefix TEXT,
            token_count INTEGER DEFAULT 0,
            spans TEXT,
            hierarchy TEXT,
            overlap TEXT,
            embed_status TEXT DEFAULT 'done',
            embed_model TEXT,
            embed_dims INTEGER,
            graph_status TEXT DEFAULT 'structural'
        );
        
        CREATE TABLE embeddings (
            chunk_id TEXT PRIMARY KEY,
            vector BLOB,
            model TEXT
        );
        
        -- Knowledge Graph
        CREATE TABLE graph_nodes (
            node_id TEXT PRIMARY KEY,
            label TEXT,
            node_type TEXT,
            tree_node_id TEXT,
            properties TEXT DEFAULT '{}'
        );
        
        CREATE TABLE graph_edges (
            edge_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}'
        );
        
        -- Temporal
        CREATE TABLE snapshots (snapshot_id TEXT PRIMARY KEY, created_at TEXT);
        CREATE TABLE diff_chain (diff_id TEXT PRIMARY KEY, parent_id TEXT);
        
        -- Manifests
        CREATE TABLE cartridge_manifest (
            cartridge_id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            schema_ver INTEGER DEFAULT 1,
            pipeline_ver TEXT,
            source_root TEXT,
            embed_model TEXT,
            embed_dims INTEGER,
            structural_complete INTEGER DEFAULT 1,
            semantic_complete INTEGER DEFAULT 1,
            graph_complete INTEGER DEFAULT 1,
            search_index_complete INTEGER DEFAULT 1,
            is_deployable INTEGER DEFAULT 1,
            file_count INTEGER,
            tree_node_count INTEGER,
            chunk_count INTEGER,
            embedding_count INTEGER,
            graph_node_count INTEGER,
            graph_edge_count INTEGER
        );
        
        CREATE TABLE ingest_runs (
            run_id INTEGER PRIMARY KEY,
            started_at TEXT,
            finished_at TEXT,
            status TEXT,
            source_root TEXT
        );
        
        -- FTS
        CREATE VIRTUAL TABLE fts_chunks USING fts5(content, chunk_id UNINDEXED);
        
        -- Indices
        CREATE INDEX idx_tree_parent ON tree_nodes(parent_id);
        CREATE INDEX idx_chunk_node ON chunk_manifest(node_id);
    """)
    
    # Insert test data
    lines = [
        "# Authentication Module",
        "import jwt",
        "from typing import Optional",
        "",
        "class AuthService:",
        "    '''Handles user authentication'''",
        "    ",
        "    def __init__(self, secret: str):",
        "        self.secret = secret",
        "    ",
        "    def create_token(self, user_id: str) -> str:",
        "        '''Create a JWT token'''",
        "        return jwt.encode({'user_id': user_id}, self.secret)",
        "    ",
        "    def verify_token(self, token: str) -> Optional[str]:",
        "        '''Verify and decode a JWT token'''",
        "        try:",
        "            payload = jwt.decode(token, self.secret)",
        "            return payload.get('user_id')",
        "        except jwt.InvalidTokenError:",
        "            return None",
        "",
        "# Utility functions",
        "def hash_password(password: str) -> str:",
        "    '''Hash a password securely'''",
        "    import hashlib",
        "    return hashlib.sha256(password.encode()).hexdigest()",
    ]
    
    # Verbatim lines
    line_cids = {}
    seen_cids = set()
    for i, line in enumerate(lines):
        cid = sha256_cid(f"{i}:{line}")
        line_cids[i] = cid
        if cid not in seen_cids:
            seen_cids.add(cid)
            cur.execute(
                "INSERT INTO verbatim_lines (line_cid, content, byte_len) VALUES (?, ?, ?)",
                (cid, line, len(line.encode()))
            )
    
    # Source file
    file_cid = sha256_cid("\n".join(lines))
    file_line_cids = [line_cids[i] for i in range(len(lines))]
    cur.execute("""
        INSERT INTO source_files (file_cid, path, line_cids, line_count, byte_size, language)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (file_cid, "src/auth.py", json.dumps(file_line_cids), len(lines), len("\n".join(lines)), "python"))
    
    # Tree nodes
    project_id = str(uuid.uuid4())
    file_node_id = str(uuid.uuid4())
    class_node_id = str(uuid.uuid4())
    init_node_id = str(uuid.uuid4())
    create_token_id = str(uuid.uuid4())
    verify_token_id = str(uuid.uuid4())
    hash_pass_id = str(uuid.uuid4())
    
    tree_nodes = [
        (project_id, "project", "test_project", None, "/", 0, None, 0, 0),
        (file_node_id, "file", "auth.py", project_id, "/src/auth.py", 1, file_cid, 0, len(lines)-1),
        (class_node_id, "class", "AuthService", file_node_id, "/src/auth.py::AuthService", 2, file_cid, 4, 20),
        (init_node_id, "method", "__init__", class_node_id, "/src/auth.py::AuthService::__init__", 3, file_cid, 7, 8),
        (create_token_id, "method", "create_token", class_node_id, "/src/auth.py::AuthService::create_token", 3, file_cid, 10, 12),
        (verify_token_id, "method", "verify_token", class_node_id, "/src/auth.py::AuthService::verify_token", 3, file_cid, 14, 20),
        (hash_pass_id, "function", "hash_password", file_node_id, "/src/auth.py::hash_password", 2, file_cid, 23, 26),
    ]
    
    for node in tree_nodes:
        cur.execute("""
            INSERT INTO tree_nodes 
            (node_id, node_type, name, parent_id, path, depth, file_cid, line_start, line_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, node)
    
    # Chunks
    chunks = [
        (sha256_cid("class_chunk"), class_node_id, "class", "src/auth.py > AuthService", 100,
         json.dumps([{"file_cid": file_cid, "line_start": 4, "line_end": 20}]),
         json.dumps({"depth": 2, "heading_path": ["auth.py", "AuthService"]}),
         json.dumps({})),
        
        (sha256_cid("init_chunk"), init_node_id, "method", "src/auth.py > AuthService > __init__", 30,
         json.dumps([{"file_cid": file_cid, "line_start": 7, "line_end": 8}]),
         json.dumps({"depth": 3}),
         json.dumps({"next_chunk_id": sha256_cid("create_chunk")})),
        
        (sha256_cid("create_chunk"), create_token_id, "method", "src/auth.py > AuthService > create_token", 40,
         json.dumps([{"file_cid": file_cid, "line_start": 10, "line_end": 12}]),
         json.dumps({"depth": 3}),
         json.dumps({"prev_chunk_id": sha256_cid("init_chunk"), "next_chunk_id": sha256_cid("verify_chunk")})),
        
        (sha256_cid("verify_chunk"), verify_token_id, "method", "src/auth.py > AuthService > verify_token", 60,
         json.dumps([{"file_cid": file_cid, "line_start": 14, "line_end": 20}]),
         json.dumps({"depth": 3}),
         json.dumps({"prev_chunk_id": sha256_cid("create_chunk")})),
        
        (sha256_cid("hash_chunk"), hash_pass_id, "function", "src/auth.py > hash_password", 35,
         json.dumps([{"file_cid": file_cid, "line_start": 23, "line_end": 26}]),
         json.dumps({"depth": 2}),
         json.dumps({})),
    ]
    
    for chunk in chunks:
        cur.execute("""
            INSERT INTO chunk_manifest 
            (chunk_id, node_id, chunk_type, context_prefix, token_count, spans, hierarchy, overlap)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, chunk)
        
        cur.execute("UPDATE tree_nodes SET chunk_id = ? WHERE node_id = ?", (chunk[0], chunk[1]))
        cur.execute("INSERT INTO fts_chunks VALUES (?, ?)", (chunk[3], chunk[0]))
        cur.execute("INSERT INTO embeddings (chunk_id, vector, model) VALUES (?, ?, ?)",
                   (chunk[0], b'\x00' * 768, "nomic"))
    
    # Graph nodes
    graph_nodes = [
        ("g_auth", "AuthService", "class", class_node_id),
        ("g_create", "create_token", "method", create_token_id),
        ("g_verify", "verify_token", "method", verify_token_id),
        ("g_hash", "hash_password", "function", hash_pass_id),
    ]
    
    for gn in graph_nodes:
        cur.execute("""
            INSERT INTO graph_nodes (node_id, label, node_type, tree_node_id)
            VALUES (?, ?, ?, ?)
        """, gn)
        cur.execute("UPDATE tree_nodes SET graph_node_id = ? WHERE node_id = ?", (gn[0], gn[3]))
    
    # Graph edges
    edges = [
        ("e1", "g_auth", "g_create", "CONTAINS", 1.0),
        ("e2", "g_auth", "g_verify", "CONTAINS", 1.0),
        ("e3", "g_create", "g_verify", "CALLS", 0.8),
        ("e4", "g_verify", "g_hash", "CALLS", 0.7),
    ]
    
    for edge in edges:
        cur.execute("""
            INSERT INTO graph_edges (edge_id, source_id, target_id, edge_type, weight)
            VALUES (?, ?, ?, ?, ?)
        """, edge)
    
    # Cartridge manifest
    cur.execute("""
        INSERT INTO cartridge_manifest 
        (cartridge_id, created_at, schema_ver, pipeline_ver, source_root, embed_model, embed_dims,
         structural_complete, semantic_complete, graph_complete, search_index_complete, is_deployable,
         file_count, tree_node_count, chunk_count, embedding_count, graph_node_count, graph_edge_count)
        VALUES (?, datetime('now'), 1, '1.0.0', '/test', 'nomic-embed-text', 768,
                1, 1, 1, 1, 1, 1, 7, 5, 5, 4, 4)
    """, (str(uuid.uuid4()),))
    
    # Ingest run
    cur.execute("""
        INSERT INTO ingest_runs (run_id, started_at, finished_at, status, source_root)
        VALUES (1, datetime('now', '-1 hour'), datetime('now'), 'success', '/test')
    """)
    
    conn.commit()
    conn.close()
    
    return path


def run_tests():
    """Run all tests"""
    print("\n" + "="*70)
    print("Node Walker v3 - Comprehensive Test Suite")
    print("="*70 + "\n")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        cart_path = tmpdir / "test_cartridge.db"
        create_test_cartridge(cart_path)
        print(f"✓ Created test cartridge: {cart_path}\n")
        
        db = CartridgeDB()
        db.connect(cart_path)
        
        # Test Database Layer
        print("Testing Database Layer...")
        assert db.is_connected
        tables = db.get_tables()
        assert "verbatim_lines" in tables
        missing = db.get_missing_tables()
        assert len(missing) == 0
        print("  ✓ Connection and schema validation")
        
        # Test Manifest Reader
        print("\nTesting Manifest Reader...")
        manifest_reader = ManifestReader(db)
        manifest = manifest_reader.manifest
        assert manifest is not None
        assert manifest.schema_ver == 1
        print(f"  ✓ Manifest: schema_ver={manifest.schema_ver}, chunks={manifest.chunk_count}")
        
        readiness = manifest_reader.assess_readiness()
        assert readiness.level == ReadinessLevel.READY
        print(f"  ✓ Readiness: {readiness.level.value}")
        
        # Test CAS Resolution
        print("\nTesting CAS Resolution...")
        cas = CASResolver(db)
        files = list(db.iter_source_files())
        file = files[0]
        reconstructed = cas.reconstruct_file(file.file_cid)
        assert "class AuthService" in reconstructed.content
        print(f"  ✓ File reconstruction: {reconstructed.path}")
        
        # Test Structure Operators
        print("\nTesting Structure Operators...")
        structure = StructureOperators(db)
        roots = structure.roots()
        assert len(roots) == 1
        print(f"  ✓ Roots: {len(roots)}")
        
        # Test Chunk Operators
        print("\nTesting Chunk Operators...")
        chunks_op = ChunkOperators(db, cas)
        class_nodes = structure.by_type("class")
        chunks = chunks_op.node_to_chunks(class_nodes[0].node_id)
        assert len(chunks) >= 1
        print(f"  ✓ Chunks for class: {len(chunks)}")
        
        # Test Graph Operators
        print("\nTesting Graph Operators...")
        graph = GraphOperators(db)
        edge_types = graph.get_edge_types()
        assert len(edge_types) >= 2
        print(f"  ✓ Edge types: {edge_types}")
        
        # Test Scoring
        print("\nTesting Scoring...")
        policy = TraversalPolicy()
        scorer = Scorer(policy)
        struct_score = scorer.compute_structural_score(1)
        assert 0 < struct_score < 1
        print(f"  ✓ Structural score: {struct_score:.3f}")
        
        # Test Full Walker
        print("\nTesting Full Walker...")
        config = WalkerConfig(
            policy=TraversalPolicy(
                mode=TraversalMode.SEMANTIC_SEEDED,
                budgets=Budgets(max_nodes=30, max_chunks=20),
            ),
            trace_enabled=True
        )
        
        walker = NodeWalker(db, config)
        readiness = walker.assess_readiness()
        assert readiness.level != ReadinessLevel.BLOCKED
        
        artifact = walker.walk(query="authentication token")
        assert artifact.total_chunks > 0
        
        print(f"  ✓ Walk completed:")
        print(f"    - Chunks: {artifact.total_chunks}")
        print(f"    - Nodes: {artifact.total_nodes}")
        print(f"    - Time: {artifact.elapsed_ms}ms")
        
        db.close()
        
        print("\n" + "="*70)
        print("All tests passed! ✓")
        print("="*70 + "\n")


if __name__ == "__main__":
    run_tests()
