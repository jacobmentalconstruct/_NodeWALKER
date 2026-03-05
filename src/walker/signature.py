"""
Signature Module
Computes deterministic signature hashes for cartridge matching.

The signature allows matching new cartridges to prior experience
by fingerprinting manifest fields and structural properties.
"""

import hashlib
import json
from typing import Optional, List, Dict, Any

from src.walker.types import CartridgeManifest
from src.walker.db import CartridgeDB


class SignatureComputer:
    """
    Computes signature hashes for cartridge fingerprinting.
    
    Signature inputs:
    - schema_ver
    - pipeline_ver
    - embed_model
    - embed_dims
    - counts (file_count, chunk_count, graph_node_count, graph_edge_count)
    - optional: stable digest of root-level tree_nodes.path prefixes
    """
    
    def __init__(self, db: CartridgeDB):
        self.db = db
    
    def compute_signature(self, manifest: CartridgeManifest,
                           include_structure: bool = True) -> str:
        """
        Compute signature hash for a cartridge.
        
        Args:
            manifest: The cartridge manifest
            include_structure: Whether to include structural fingerprint
        
        Returns:
            Hex signature hash
        """
        # Build signature components
        components = {
            "schema_ver": manifest.schema_ver,
            "pipeline_ver": manifest.pipeline_ver,
            "embed_model": manifest.embed_model,
            "embed_dims": manifest.embed_dims,
            "file_count": manifest.file_count,
            "chunk_count": manifest.chunk_count,
            "graph_node_count": manifest.graph_node_count,
            "graph_edge_count": manifest.graph_edge_count,
        }
        
        # Add structural fingerprint
        if include_structure:
            components["structure_fingerprint"] = self._compute_structure_fingerprint()
        
        # Compute hash
        canonical = json.dumps(components, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    
    def compute_partial_signature(self, manifest: CartridgeManifest) -> str:
        """
        Compute partial signature for fuzzy matching.
        Only uses pipeline_ver and embed_model.
        """
        components = {
            "pipeline_ver": manifest.pipeline_ver,
            "embed_model": manifest.embed_model,
        }
        
        canonical = json.dumps(components, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:8]
    
    def _compute_structure_fingerprint(self) -> str:
        """
        Compute a stable fingerprint of the structural tree.
        Uses root-level path prefixes.
        """
        roots = self.db.get_tree_roots()
        
        # Get path prefixes from roots
        prefixes = sorted(set(
            r.path.split("/")[1] if "/" in r.path else r.path
            for r in roots
            if r.path
        ))
        
        # Also include node type distribution
        type_counts = {}
        for root in roots:
            nt = root.node_type
            type_counts[nt] = type_counts.get(nt, 0) + 1
        
        fingerprint_data = {
            "prefixes": prefixes[:10],  # Limit to first 10
            "root_types": type_counts,
        }
        
        canonical = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:8]
    
    def compute_content_fingerprint(self, sample_size: int = 5) -> str:
        """
        Compute a content-based fingerprint from sample files.
        Useful for detecting duplicate or near-duplicate cartridges.
        """
        # Get first N source files
        files = []
        for sf in self.db.iter_source_files():
            files.append(sf)
            if len(files) >= sample_size:
                break
        
        # Hash file CIDs
        cids = sorted(f.file_cid for f in files)
        canonical = json.dumps(cids)
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]
    
    def signature_matches(self, sig_a: str, sig_b: str,
                           exact: bool = False) -> bool:
        """
        Check if two signatures match.
        
        Args:
            sig_a: First signature
            sig_b: Second signature
            exact: Require exact match (vs prefix match)
        """
        if exact:
            return sig_a == sig_b
        
        # Prefix matching (first 8 chars)
        min_len = min(len(sig_a), len(sig_b), 8)
        return sig_a[:min_len] == sig_b[:min_len]
    
    def get_signature_components(self, manifest: CartridgeManifest
                                   ) -> Dict[str, Any]:
        """Get the raw components used for signature (for debugging)"""
        return {
            "schema_ver": manifest.schema_ver,
            "pipeline_ver": manifest.pipeline_ver,
            "embed_model": manifest.embed_model,
            "embed_dims": manifest.embed_dims,
            "file_count": manifest.file_count,
            "chunk_count": manifest.chunk_count,
            "graph_node_count": manifest.graph_node_count,
            "graph_edge_count": manifest.graph_edge_count,
            "structure_fingerprint": self._compute_structure_fingerprint(),
        }
