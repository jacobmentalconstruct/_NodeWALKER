"""
Content Addressed Storage (CAS) Resolution Module
Handles deterministic reconstruction of files, spans, and chunks from verbatim storage.

Resolution Rules:
1. File → source_files.line_cids → verbatim_lines.content → join with newline
2. Node Span → tree_nodes(file_cid, line_start, line_end) → slice line_cids → verbatim_lines
3. Chunk → chunk_manifest.spans → resolve each span via file/range
"""

from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

from src.walker.types import (
    SourceFile, TreeNode, ChunkManifest, ChunkSpan, TextSpan
)
from src.walker.db import CartridgeDB


class CASError(Exception):
    """CAS resolution error"""
    pass


class MissingContentError(CASError):
    """Content not found in CAS"""
    pass


@dataclass
class ReconstructedFile:
    """A fully reconstructed file"""
    file_cid: str
    path: str
    content: str
    lines: List[str]
    line_cids: List[str]
    missing_cids: List[str]  # CIDs that couldn't be resolved


@dataclass
class ReconstructedSpan:
    """A reconstructed text span with full provenance"""
    file_cid: str
    file_path: str
    line_start: int
    line_end: int
    content: str
    lines: List[str]
    line_cids: List[str]
    missing_cids: List[str]
    
    @property
    def line_count(self) -> int:
        return len(self.lines)
    
    @property
    def is_complete(self) -> bool:
        return len(self.missing_cids) == 0


class CASResolver:
    """
    Resolves content from the verbatim CAS layer.
    All text reconstruction goes through this module.
    """
    
    def __init__(self, db: CartridgeDB):
        self.db = db
        
        # Track resolution stats
        self.stats = {
            "files_resolved": 0,
            "spans_resolved": 0,
            "lines_fetched": 0,
            "cache_hits": 0,
            "missing_lines": 0,
        }
    
    def reset_stats(self):
        """Reset resolution statistics"""
        for key in self.stats:
            self.stats[key] = 0
    
    # =========================================================================
    # File Reconstruction
    # =========================================================================
    
    def reconstruct_file(self, file_cid: str) -> Optional[ReconstructedFile]:
        """
        Fully reconstruct a file from CAS.
        
        Process:
        1. Lookup source_files by file_cid
        2. Parse line_cids JSON array
        3. Fetch each verbatim_lines.content by line_cid
        4. Join with newline
        """
        source = self.db.get_source_file(file_cid)
        if not source:
            return None
        
        return self._reconstruct_from_source(source)
    
    def reconstruct_file_by_path(self, path: str) -> Optional[ReconstructedFile]:
        """Reconstruct file by path (slower - requires scan)"""
        for source in self.db.iter_source_files():
            if source.path == path:
                return self._reconstruct_from_source(source)
        return None
    
    def _reconstruct_from_source(self, source: SourceFile) -> ReconstructedFile:
        """Reconstruct from a SourceFile object"""
        if not source.line_cids:
            return ReconstructedFile(
                file_cid=source.file_cid,
                path=source.path,
                content="",
                lines=[],
                line_cids=[],
                missing_cids=[]
            )
        
        # Batch fetch all lines
        line_map = self.db.get_verbatim_lines_batch(source.line_cids)
        self.stats["lines_fetched"] += len(line_map)
        
        # Reconstruct in order
        lines = []
        missing = []
        
        for cid in source.line_cids:
            if cid in line_map:
                lines.append(line_map[cid])
            else:
                lines.append("")  # Placeholder for missing
                missing.append(cid)
                self.stats["missing_lines"] += 1
        
        self.stats["files_resolved"] += 1
        
        return ReconstructedFile(
            file_cid=source.file_cid,
            path=source.path,
            content="\n".join(lines),
            lines=lines,
            line_cids=source.line_cids,
            missing_cids=missing
        )
    
    # =========================================================================
    # Span Reconstruction
    # =========================================================================
    
    def reconstruct_span(self, file_cid: str, line_start: int, line_end: int
                         ) -> Optional[ReconstructedSpan]:
        """
        Reconstruct a specific line range from a file.
        
        Process:
        1. Get source_files.line_cids
        2. Slice to [line_start:line_end+1]
        3. Fetch verbatim_lines for slice
        """
        source = self.db.get_source_file(file_cid)
        if not source:
            return None
        
        if not source.line_cids:
            return ReconstructedSpan(
                file_cid=file_cid,
                file_path=source.path,
                line_start=line_start,
                line_end=line_end,
                content="",
                lines=[],
                line_cids=[],
                missing_cids=[]
            )
        
        # Clamp range to valid indices
        start = max(0, line_start)
        end = min(len(source.line_cids), line_end + 1)
        
        if start >= end:
            return ReconstructedSpan(
                file_cid=file_cid,
                file_path=source.path,
                line_start=line_start,
                line_end=line_end,
                content="",
                lines=[],
                line_cids=[],
                missing_cids=[]
            )
        
        # Slice and fetch
        slice_cids = source.line_cids[start:end]
        line_map = self.db.get_verbatim_lines_batch(slice_cids)
        self.stats["lines_fetched"] += len(line_map)
        
        lines = []
        missing = []
        
        for cid in slice_cids:
            if cid in line_map:
                lines.append(line_map[cid])
            else:
                lines.append("")
                missing.append(cid)
                self.stats["missing_lines"] += 1
        
        self.stats["spans_resolved"] += 1
        
        return ReconstructedSpan(
            file_cid=file_cid,
            file_path=source.path,
            line_start=line_start,
            line_end=line_end,
            content="\n".join(lines),
            lines=lines,
            line_cids=slice_cids,
            missing_cids=missing
        )
    
    # =========================================================================
    # Node Span Resolution
    # =========================================================================
    
    def resolve_node_span(self, node: TreeNode) -> Optional[ReconstructedSpan]:
        """
        Resolve the text span for a tree node.
        Uses node.file_cid + line_start/line_end.
        """
        if not node.file_cid:
            return None
        
        return self.reconstruct_span(
            node.file_cid,
            node.line_start,
            node.line_end
        )
    
    def resolve_node_span_by_id(self, node_id: str) -> Optional[ReconstructedSpan]:
        """Resolve span by node_id (fetches node first)"""
        node = self.db.get_tree_node(node_id)
        if not node:
            return None
        return self.resolve_node_span(node)
    
    # =========================================================================
    # Chunk Span Resolution
    # =========================================================================
    
    def resolve_chunk_spans(self, chunk: ChunkManifest
                            ) -> List[ReconstructedSpan]:
        """
        Resolve all spans for a chunk.
        Uses chunk.spans JSON array.
        """
        if not chunk.spans:
            # Fallback: try to get span from associated tree node
            if chunk.node_id:
                node_span = self.resolve_node_span_by_id(chunk.node_id)
                if node_span:
                    return [node_span]
            return []
        
        spans = []
        for cs in chunk.spans:
            span = self.reconstruct_span(cs.file_cid, cs.line_start, cs.line_end)
            if span:
                spans.append(span)
        
        return spans
    
    def resolve_chunk_content(self, chunk: ChunkManifest) -> str:
        """
        Get combined content for a chunk.
        Joins multiple spans if present.
        """
        spans = self.resolve_chunk_spans(chunk)
        if not spans:
            return ""
        
        return "\n".join(s.content for s in spans)
    
    def resolve_chunk_by_id(self, chunk_id: str) -> Optional[str]:
        """Resolve chunk content by chunk_id"""
        chunk = self.db.get_chunk(chunk_id)
        if not chunk:
            return None
        return self.resolve_chunk_content(chunk)
    
    # =========================================================================
    # Batch Resolution
    # =========================================================================
    
    def resolve_chunks_batch(self, chunk_ids: List[str]
                              ) -> Dict[str, str]:
        """
        Resolve content for multiple chunks.
        Returns {chunk_id: content}.
        More efficient than individual calls.
        """
        result = {}
        
        # Collect all file_cids and line ranges needed
        file_ranges: Dict[str, List[Tuple[int, int, str]]] = {}
        
        for chunk_id in chunk_ids:
            chunk = self.db.get_chunk(chunk_id)
            if not chunk:
                result[chunk_id] = ""
                continue
            
            if chunk.spans:
                for cs in chunk.spans:
                    if cs.file_cid not in file_ranges:
                        file_ranges[cs.file_cid] = []
                    file_ranges[cs.file_cid].append(
                        (cs.line_start, cs.line_end, chunk_id)
                    )
            elif chunk.node_id:
                node = self.db.get_tree_node(chunk.node_id)
                if node and node.file_cid:
                    if node.file_cid not in file_ranges:
                        file_ranges[node.file_cid] = []
                    file_ranges[node.file_cid].append(
                        (node.line_start, node.line_end, chunk_id)
                    )
        
        # Process each file
        chunk_contents: Dict[str, List[str]] = {cid: [] for cid in chunk_ids}
        
        for file_cid, ranges in file_ranges.items():
            source = self.db.get_source_file(file_cid)
            if not source or not source.line_cids:
                continue
            
            # Find all unique lines needed from this file
            all_indices = set()
            for start, end, _ in ranges:
                for i in range(max(0, start), min(len(source.line_cids), end + 1)):
                    all_indices.add(i)
            
            # Fetch needed line_cids
            needed_cids = [source.line_cids[i] for i in sorted(all_indices)]
            line_map = self.db.get_verbatim_lines_batch(needed_cids)
            self.stats["lines_fetched"] += len(line_map)
            
            # Build content for each range
            for start, end, chunk_id in ranges:
                start = max(0, start)
                end = min(len(source.line_cids), end + 1)
                
                lines = []
                for i in range(start, end):
                    cid = source.line_cids[i]
                    lines.append(line_map.get(cid, ""))
                
                chunk_contents[chunk_id].append("\n".join(lines))
        
        # Combine
        for chunk_id in chunk_ids:
            parts = chunk_contents.get(chunk_id, [])
            result[chunk_id] = "\n".join(parts) if parts else ""
        
        return result
    
    # =========================================================================
    # Provenance Building
    # =========================================================================
    
    def build_text_span(self, file_cid: str, line_start: int, line_end: int
                        ) -> TextSpan:
        """Build a TextSpan with full provenance"""
        source = self.db.get_source_file(file_cid)
        span = self.reconstruct_span(file_cid, line_start, line_end)
        
        return TextSpan(
            file_cid=file_cid,
            file_path=source.path if source else "",
            line_start=line_start,
            line_end=line_end,
            content=span.content if span else "",
            line_cids=span.line_cids if span else [],
        )
    
    def get_file_path(self, file_cid: str) -> str:
        """Get file path for a file_cid"""
        source = self.db.get_source_file(file_cid)
        return source.path if source else ""
