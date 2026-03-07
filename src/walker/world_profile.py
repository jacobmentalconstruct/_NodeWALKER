"""
World Profile — Identity Frame for NodeWALKER.

Inspects a loaded cartridge to determine what kind of world is loaded,
then renders a structured identity block for LLM prompt injection.

This is the "system prompt" equivalent: it tells the LLM who it is,
what it's looking at, and how to resolve vague references like
"the app", "the project", "this page", etc.

No LLM calls. Purely structural/heuristic classification.
"""

import os
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.walker.db import CartridgeDB
    from src.walker.structure import StructureOperators


# =========================================================================
# Extension families for world-kind classification
# =========================================================================

CODE_EXTS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".cpp",
    ".c", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".lua",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".h", ".hpp", ".hxx", ".m", ".mm",
    ".r", ".jl", ".pl", ".pm", ".ex", ".exs", ".erl", ".hs",
    ".v", ".sv", ".vhd", ".vhdl",
})

DOC_EXTS = frozenset({
    ".md", ".txt", ".rst", ".adoc", ".org", ".tex", ".latex",
    ".html", ".htm", ".xhtml", ".xml",
    ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
})

PDF_EXTS = frozenset({".pdf"})

IMAGE_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".bmp", ".ico", ".webp",
    ".tiff", ".tif", ".psd", ".ai", ".eps",
})

# Known entry-point filenames
_ENTRY_POINT_NAMES = frozenset({
    "main.py", "app.py", "__main__.py", "__init__.py",
    "index.js", "index.ts", "index.jsx", "index.tsx",
    "main.js", "main.ts", "app.js", "app.ts",
    "main.go", "main.rs", "main.java", "main.c", "main.cpp",
    "package.json", "setup.py", "pyproject.toml",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Makefile", "CMakeLists.txt", "Dockerfile",
    "manage.py", "wsgi.py", "asgi.py",
    "server.py", "server.js", "server.ts",
})

# Extension → language display name
_EXT_TO_LANGUAGE = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "JavaScript (JSX)", ".tsx": "TypeScript (TSX)",
    ".java": "Java", ".go": "Go", ".rs": "Rust",
    ".cpp": "C++", ".c": "C", ".cs": "C#",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".kt": "Kotlin", ".scala": "Scala", ".lua": "Lua",
    ".r": "R", ".jl": "Julia", ".pl": "Perl",
    ".ex": "Elixir", ".erl": "Erlang", ".hs": "Haskell",
    ".sh": "Shell", ".bash": "Bash",
    ".html": "HTML", ".css": "CSS",
    ".md": "Markdown", ".txt": "Plain Text", ".rst": "reStructuredText",
    ".pdf": "PDF",
}


# =========================================================================
# WorldKind enum
# =========================================================================

class WorldKind(Enum):
    APPLICATION_PROJECT = "application_project"
    DOCUMENT_CORPUS = "document_corpus"
    PDF_COLLECTION = "pdf_collection"
    IMAGE_ARCHIVE = "image_archive"
    MIXED_ARCHIVE = "mixed_archive"
    SINGLE_DOCUMENT = "single_document"
    SINGLE_SOURCE_FILE = "single_source_file"


# =========================================================================
# Scope vocabulary per world kind
# =========================================================================

_SCOPE_VOCAB = {
    WorldKind.APPLICATION_PROJECT: [
        "project", "app", "codebase", "file", "module",
        "class", "function", "method", "variable", "import",
    ],
    WorldKind.SINGLE_SOURCE_FILE: [
        "file", "module", "class", "function", "method", "variable",
    ],
    WorldKind.DOCUMENT_CORPUS: [
        "corpus", "document", "file", "section", "paragraph",
        "heading", "chapter",
    ],
    WorldKind.PDF_COLLECTION: [
        "collection", "document", "page", "paragraph", "section",
    ],
    WorldKind.SINGLE_DOCUMENT: [
        "document", "page", "section", "paragraph",
    ],
    WorldKind.IMAGE_ARCHIVE: [
        "archive", "image", "file", "collection",
    ],
    WorldKind.MIXED_ARCHIVE: [
        "archive", "folder", "file", "document", "item", "asset",
    ],
}


# =========================================================================
# WorldProfile dataclass
# =========================================================================

@dataclass
class WorldProfile:
    """Structured description of the loaded cartridge world."""
    world_kind: WorldKind
    world_label: str
    dominant_language: str
    file_count: int = 0
    tree_node_count: int = 0
    chunk_count: int = 0
    source_root: str = ""
    top_level_items: List[str] = field(default_factory=list)
    scope_vocab: List[str] = field(default_factory=list)
    entry_point_candidates: List[str] = field(default_factory=list)
    extension_counts: Dict[str, int] = field(default_factory=dict)
    node_type_counts: Dict[str, int] = field(default_factory=dict)


# =========================================================================
# build_world_profile()
# =========================================================================

def build_world_profile(
    db: "CartridgeDB",
    structure: Optional["StructureOperators"] = None,
) -> WorldProfile:
    """
    Inspect a loaded cartridge and build a WorldProfile.

    Deterministic, no LLM. Reads manifest, counts extensions and node
    types, classifies the world kind, and infers labels.

    Args:
        db: Connected CartridgeDB (read-only).
        structure: Optional StructureOperators for root node names.

    Returns:
        A populated WorldProfile.
    """
    # --- Read manifest ---
    manifest = db.get_cartridge_manifest()
    source_root = (manifest.source_root or "") if manifest else ""
    file_count = (manifest.file_count or 0) if manifest else 0
    tree_node_count = (manifest.tree_node_count or 0) if manifest else 0
    chunk_count = (manifest.chunk_count or 0) if manifest else 0

    # --- Count file extensions ---
    extension_counts = _count_extensions(db)
    # Use actual counted files if manifest count is missing
    counted_files = sum(extension_counts.values())
    if not file_count and counted_files:
        file_count = counted_files
    total_files = counted_files or file_count or 1

    # --- Count node types ---
    node_type_counts = _count_node_types(db)
    # Use actual counted nodes if manifest count is missing
    counted_nodes = sum(node_type_counts.values())
    if not tree_node_count and counted_nodes:
        tree_node_count = counted_nodes
    # Estimate chunk count if manifest doesn't have it
    if not chunk_count:
        chunk_count = db.count_table("chunk_manifest") if db.has_table("chunk_manifest") else 0

    # --- Determine dominant language (prefer DB language field, fall back to extensions) ---
    language_counts = _count_languages(db)
    dominant_language = ""
    if language_counts:
        # Use the language field from source_files if populated
        top_lang = language_counts.most_common(1)[0]
        if top_lang[0]:  # Skip empty strings
            dominant_language = top_lang[0]

    if not dominant_language:
        # Fall back to inferring from most common code extension
        dominant_language = _infer_language_from_extensions(extension_counts)

    # --- Classify world kind ---
    world_kind = _classify_world_kind(
        extension_counts, node_type_counts, total_files
    )

    # --- Infer world label ---
    world_label = _infer_label(source_root, db)

    # --- Get scope vocabulary ---
    scope_vocab = list(_SCOPE_VOCAB.get(world_kind, _SCOPE_VOCAB[WorldKind.MIXED_ARCHIVE]))

    # --- Find entry point candidates ---
    entry_points = []
    if structure:
        roots = structure.roots()
        for r in roots:
            name = r.name or ""
            basename = os.path.basename(name) if name else ""
            if basename.lower() in {n.lower() for n in _ENTRY_POINT_NAMES}:
                entry_points.append(name)

    # --- Get top-level items ---
    top_level_items = []
    if structure:
        roots = structure.roots()
        top_level_items = [
            r.name or r.node_type for r in roots[:20]
        ]

    return WorldProfile(
        world_kind=world_kind,
        world_label=world_label,
        dominant_language=dominant_language,
        file_count=file_count,
        tree_node_count=tree_node_count,
        chunk_count=chunk_count,
        source_root=source_root,
        top_level_items=top_level_items,
        scope_vocab=scope_vocab,
        entry_point_candidates=entry_points,
        extension_counts=dict(extension_counts),
        node_type_counts=dict(node_type_counts),
    )


# =========================================================================
# Classification helpers
# =========================================================================

def _count_extensions(db: "CartridgeDB") -> Counter:
    """Count file extensions from source_files table via SQL."""
    counts: Counter = Counter()
    if not db.has_table("source_files"):
        return counts
    try:
        with db.cursor() as cur:
            cur.execute("SELECT path FROM source_files")
            for row in cur.fetchall():
                path = row[0] if isinstance(row, (tuple, list)) else row["path"]
                _, ext = os.path.splitext(path)
                if ext:
                    counts[ext.lower()] += 1
    except Exception:
        pass
    return counts


def _count_node_types(db: "CartridgeDB") -> Counter:
    """Count node types from tree_nodes table via SQL."""
    counts: Counter = Counter()
    if not db.has_table("tree_nodes"):
        return counts
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT node_type, COUNT(*) as cnt "
                "FROM tree_nodes GROUP BY node_type"
            )
            for row in cur.fetchall():
                if isinstance(row, (tuple, list)):
                    ntype, cnt = row[0], row[1]
                else:
                    ntype, cnt = row["node_type"], row["cnt"]
                if ntype:
                    counts[ntype] = cnt
    except Exception:
        pass
    return counts


def _count_languages(db: "CartridgeDB") -> Counter:
    """Count language field from source_files if available."""
    counts: Counter = Counter()
    if not db.has_table("source_files"):
        return counts
    try:
        columns = db.get_columns("source_files")
        if "language" not in columns:
            return counts
        with db.cursor() as cur:
            cur.execute(
                "SELECT language, COUNT(*) as cnt "
                "FROM source_files WHERE language != '' "
                "GROUP BY language"
            )
            for row in cur.fetchall():
                if isinstance(row, (tuple, list)):
                    lang, cnt = row[0], row[1]
                else:
                    lang, cnt = row["language"], row["cnt"]
                if lang:
                    counts[lang] = cnt
    except Exception:
        pass
    return counts


def _infer_language_from_extensions(ext_counts: Counter) -> str:
    """Infer dominant language from the most common code extension."""
    code_exts = {
        ext: count for ext, count in ext_counts.items()
        if ext in CODE_EXTS
    }
    if not code_exts:
        # Try document extensions
        doc_exts = {
            ext: count for ext, count in ext_counts.items()
            if ext in DOC_EXTS | PDF_EXTS
        }
        if doc_exts:
            top_ext = max(doc_exts, key=doc_exts.get)
            return _EXT_TO_LANGUAGE.get(top_ext, "")
        return ""

    top_ext = max(code_exts, key=code_exts.get)
    return _EXT_TO_LANGUAGE.get(top_ext, "")


def _classify_world_kind(
    ext_counts: Counter,
    node_type_counts: Counter,
    total_files: int,
) -> WorldKind:
    """Classify the world kind from extension and node type distributions."""
    if total_files <= 0:
        return WorldKind.MIXED_ARCHIVE

    # Count files per family
    code_count = sum(ext_counts.get(e, 0) for e in CODE_EXTS)
    doc_count = sum(ext_counts.get(e, 0) for e in DOC_EXTS)
    pdf_count = sum(ext_counts.get(e, 0) for e in PDF_EXTS)
    image_count = sum(ext_counts.get(e, 0) for e in IMAGE_EXTS)

    # Single file cases
    if total_files == 1:
        if code_count == 1:
            return WorldKind.SINGLE_SOURCE_FILE
        return WorldKind.SINGLE_DOCUMENT

    # Dominance thresholds (> 50% of files)
    half = total_files / 2

    if code_count > half:
        # Confirm with structural evidence: code projects have functions/classes
        structural_types = {"function", "class", "method", "module"}
        has_structure = any(
            node_type_counts.get(t, 0) > 0 for t in structural_types
        )
        if has_structure or code_count > total_files * 0.7:
            return WorldKind.APPLICATION_PROJECT

    if pdf_count > half:
        return WorldKind.PDF_COLLECTION

    if doc_count > half:
        return WorldKind.DOCUMENT_CORPUS

    if image_count > half:
        return WorldKind.IMAGE_ARCHIVE

    # No clear dominant family
    return WorldKind.MIXED_ARCHIVE


def _infer_label(source_root: str, db: "CartridgeDB") -> str:
    """Infer a human-readable label for the loaded world."""
    if source_root:
        # Use last meaningful path component
        parts = source_root.replace("\\", "/").rstrip("/").split("/")
        for part in reversed(parts):
            if part and part not in (".", "..", ""):
                return part

    # Fall back to DB filename stem (e.g. "MyProject" from "MyProject.db")
    db_path = getattr(db, '_path', None) or getattr(db, 'path', None)
    if db_path:
        stem = os.path.splitext(os.path.basename(str(db_path)))[0]
        if stem and not _looks_like_uuid(stem):
            return stem

    # Fall back to cartridge_id (only if not a UUID)
    manifest = db.get_cartridge_manifest()
    if manifest and manifest.cartridge_id:
        if not _looks_like_uuid(manifest.cartridge_id):
            return manifest.cartridge_id

    return "Loaded World"


def _looks_like_uuid(text: str) -> bool:
    """Check if a string looks like a UUID (to avoid using it as a label)."""
    import re
    return bool(re.match(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        text.lower(),
    ))


# =========================================================================
# render_identity_block()
# =========================================================================

def render_identity_block(
    profile: WorldProfile,
    active_scope: Optional[Dict[str, str]] = None,
) -> str:
    """
    Render a compact identity block for LLM prompt injection.

    This is the structured frame that tells the LLM who it is,
    what it's looking at, and how to resolve vague references.

    Args:
        profile: The WorldProfile for the loaded cartridge.
        active_scope: Optional dict with keys like 'focus', 'node_name',
                       'node_type' describing the currently examined object.

    Returns:
        A formatted text block (~150-200 tokens) for system prompt injection.
    """
    # Try to load identity header from prompt library
    try:
        from src.walker.prompt_library import PromptLibrary
        _lib = PromptLibrary.load()
        _identity_header = _lib.active_text("identity_header")
    except Exception:
        _identity_header = ""

    lines = []

    # --- Identity ---
    if _identity_header:
        lines.append(_identity_header)
    else:
        lines.append("## Identity")
        lines.append("- Agent: NodeWALKER Synthesizer")
        lines.append("- Role: Grounded datastore examiner for code and document analysis")
        lines.append("- User: Operator examining the loaded world")
    lines.append("")

    # --- Loaded World ---
    lines.append("## Loaded World")
    lines.append(f"- Kind: {profile.world_kind.value}")
    lines.append(f"- Label: {profile.world_label}")
    if profile.dominant_language:
        lines.append(f"- Language: {profile.dominant_language}")
    lines.append(
        f"- Scale: {profile.file_count} files, "
        f"{profile.tree_node_count} tree nodes, "
        f"{profile.chunk_count} chunks"
    )
    if profile.top_level_items:
        items_str = ", ".join(profile.top_level_items[:10])
        lines.append(f"- Top-level items: {items_str}")
    if profile.entry_point_candidates:
        ep_str = ", ".join(profile.entry_point_candidates[:5])
        lines.append(f"- Entry points: {ep_str}")
    lines.append("")

    # --- Discourse Rules (adapted to world kind) ---
    lines.append("## Discourse Rules")
    lines.extend(_discourse_rules(profile))
    lines.append(
        "- Prefer evidence from the loaded datastore over generic prior knowledge"
    )
    if profile.scope_vocab:
        lines.append(f"- Valid scope terms: {', '.join(profile.scope_vocab)}")
    lines.append("")

    # --- Active Scope (only if provided) ---
    if active_scope:
        focus = active_scope.get("focus", "")
        node_name = active_scope.get("node_name", "")
        node_type = active_scope.get("node_type", "")
        if focus or node_name:
            lines.append("## Active Scope")
            if focus:
                lines.append(f"- Focus: {focus}")
            if node_name:
                type_hint = f" ({node_type})" if node_type else ""
                lines.append(f"- Node: {node_name}{type_hint}")
            lines.append("")

    return "\n".join(lines)


def _discourse_rules(profile: WorldProfile) -> List[str]:
    """Generate discourse rules adapted to the world kind."""
    kind = profile.world_kind
    label = profile.world_label

    # Try prompt library for custom discourse rules
    try:
        from src.walker.prompt_library import PromptLibrary
        _lib = PromptLibrary.load()
        if kind in (WorldKind.APPLICATION_PROJECT, WorldKind.SINGLE_SOURCE_FILE):
            custom = _lib.active_text("discourse_rules_code")
        elif kind in (WorldKind.DOCUMENT_CORPUS, WorldKind.SINGLE_DOCUMENT,
                      WorldKind.PDF_COLLECTION):
            custom = _lib.active_text("discourse_rules_document")
        else:
            custom = ""
        if custom:
            try:
                formatted = custom.format(world_label=label)
            except (KeyError, ValueError):
                formatted = custom
            return [line for line in formatted.split("\n") if line.strip()]
    except Exception:
        pass

    # Fallback: hardcoded discourse rules
    rules = []

    if kind in (WorldKind.APPLICATION_PROJECT, WorldKind.SINGLE_SOURCE_FILE):
        rules.append(
            f'- "the app", "the project", "the codebase" '
            f'-> the loaded world ({label})'
        )
        rules.append('- "this file" -> the currently focused file')
        rules.append(
            '- "this function/class/method" '
            '-> the currently selected node'
        )

    elif kind in (WorldKind.DOCUMENT_CORPUS, WorldKind.SINGLE_DOCUMENT):
        rules.append(
            f'- "the document", "the text", "the corpus" '
            f'-> the loaded world ({label})'
        )
        rules.append('- "this section", "this page" -> the currently focused section')
        rules.append('- "this paragraph" -> the currently selected text span')

    elif kind == WorldKind.PDF_COLLECTION:
        rules.append(
            f'- "the document", "the collection" '
            f'-> the loaded world ({label})'
        )
        rules.append('- "this page" -> the currently focused page')
        rules.append('- "this paragraph" -> the currently selected text span')

    elif kind == WorldKind.IMAGE_ARCHIVE:
        rules.append(
            f'- "the archive", "the collection" '
            f'-> the loaded world ({label})'
        )
        rules.append('- "this image", "this file" -> the currently focused item')

    else:  # MIXED_ARCHIVE
        rules.append(
            f'- "the project", "the archive", "the collection" '
            f'-> the loaded world ({label})'
        )
        rules.append('- "this file", "this item" -> the currently focused item')

    return rules


# =========================================================================
# Compact world hint (for helper model calls)
# =========================================================================

def make_world_hint(profile: Optional[WorldProfile]) -> str:
    """
    Build a one-line world hint for injection into helper model prompts.

    Example: "World: Python application_project 'NodeWALKER' (47 files)"
    """
    if not profile:
        return ""
    lang = f"{profile.dominant_language} " if profile.dominant_language else ""
    return (
        f"World: {lang}{profile.world_kind.value} "
        f"'{profile.world_label}' ({profile.file_count} files)"
    )
