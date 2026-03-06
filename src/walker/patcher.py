"""
Stateless Exact-Match Patcher.

Provides verify → dry-run → diff → apply workflow for PatchProposal objects.
All operations use exact substring matching (no fuzzy, no regex).

Safety:
- NEVER modifies .db / .sqlite files (cartridge databases).
- verify_exact_match() ensures the search_block appears exactly once.
- apply_patch() writes to disk only after verification passes.
"""

import os
import difflib
from typing import Optional

from src.walker.types import PatchProposal, PatchResult, PatchVerificationResult


# File extensions that must never be modified
_BLOCKED_EXTENSIONS = frozenset({".db", ".sqlite", ".sqlite3"})

# Context lines shown around the match in verification previews
_CONTEXT_LINES = 5


def _is_blocked_path(file_path: str) -> Optional[str]:
    """Return an error string if the path targets a blocked file type."""
    _, ext = os.path.splitext(file_path)
    if ext.lower() in _BLOCKED_EXTENSIONS:
        return f"Blocked: cannot modify database file ({ext})"
    return None


def verify_exact_match(proposal: PatchProposal) -> PatchVerificationResult:
    """
    Verify that proposal.search_block appears exactly once in the target file.

    Returns a PatchVerificationResult with match location and context preview.
    """
    blocked = _is_blocked_path(proposal.target_file_path)
    if blocked:
        return PatchVerificationResult(found=False, error=blocked)

    if not os.path.isfile(proposal.target_file_path):
        return PatchVerificationResult(
            found=False,
            error=f"File not found: {proposal.target_file_path}",
        )

    try:
        with open(proposal.target_file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return PatchVerificationResult(found=False, error=f"Read error: {e}")

    count = content.count(proposal.search_block)

    if count == 0:
        return PatchVerificationResult(
            found=False,
            file_path=proposal.target_file_path,
            error="search_block not found in file",
        )

    if count > 1:
        return PatchVerificationResult(
            found=False,
            file_path=proposal.target_file_path,
            error=f"search_block found {count} times (must be exactly 1)",
        )

    # Exactly one occurrence — locate it
    idx = content.index(proposal.search_block)
    line_number = content[:idx].count("\n") + 1

    # Build context previews
    lines = content.split("\n")
    match_end_line = line_number + proposal.search_block.count("\n")

    ctx_start = max(0, line_number - 1 - _CONTEXT_LINES)
    ctx_end = min(len(lines), match_end_line + _CONTEXT_LINES)

    context_before = "\n".join(lines[ctx_start:ctx_end])

    # Preview with replacement applied
    replaced = content[:idx] + proposal.replace_block + content[idx + len(proposal.search_block):]
    replaced_lines = replaced.split("\n")
    new_match_end = line_number + proposal.replace_block.count("\n")
    ctx_end_after = min(len(replaced_lines), new_match_end + _CONTEXT_LINES)
    context_after = "\n".join(replaced_lines[ctx_start:ctx_end_after])

    return PatchVerificationResult(
        found=True,
        file_path=proposal.target_file_path,
        line_number=line_number,
        context_before=context_before,
        context_after=context_after,
    )


def apply_dry_run_patch(proposal: PatchProposal) -> str:
    """
    Apply the patch in-memory only and return the full resulting file content.

    Does NOT write to disk.  Returns empty string on failure.
    """
    blocked = _is_blocked_path(proposal.target_file_path)
    if blocked:
        return ""

    if not os.path.isfile(proposal.target_file_path):
        return ""

    try:
        with open(proposal.target_file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return ""

    if content.count(proposal.search_block) != 1:
        return ""

    return content.replace(proposal.search_block, proposal.replace_block, 1)


def build_unified_diff(proposal: PatchProposal) -> str:
    """
    Build a unified diff string for display in the approval modal.

    Returns a human-readable diff or an error message.
    """
    blocked = _is_blocked_path(proposal.target_file_path)
    if blocked:
        return f"ERROR: {blocked}"

    if not os.path.isfile(proposal.target_file_path):
        return f"ERROR: File not found: {proposal.target_file_path}"

    try:
        with open(proposal.target_file_path, "r", encoding="utf-8") as f:
            original = f.read()
    except Exception as e:
        return f"ERROR: {e}"

    patched = original.replace(proposal.search_block, proposal.replace_block, 1)
    if patched == original:
        return "ERROR: search_block not found — no diff produced"

    original_lines = original.splitlines(keepends=True)
    patched_lines = patched.splitlines(keepends=True)

    diff = difflib.unified_diff(
        original_lines,
        patched_lines,
        fromfile=f"a/{os.path.basename(proposal.target_file_path)}",
        tofile=f"b/{os.path.basename(proposal.target_file_path)}",
        lineterm="",
    )
    return "".join(diff)


def apply_patch(proposal: PatchProposal) -> PatchResult:
    """
    Apply the patch to disk after full verification.

    SAFETY:
    - NEVER modifies .db / .sqlite files.
    - Verifies exactly one occurrence before writing.
    """
    blocked = _is_blocked_path(proposal.target_file_path)
    if blocked:
        return PatchResult(success=False, file_path=proposal.target_file_path, error=blocked)

    verification = verify_exact_match(proposal)
    if not verification.found:
        return PatchResult(
            success=False,
            file_path=proposal.target_file_path,
            error=verification.error or "Verification failed",
        )

    try:
        with open(proposal.target_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        patched = content.replace(proposal.search_block, proposal.replace_block, 1)

        with open(proposal.target_file_path, "w", encoding="utf-8") as f:
            f.write(patched)

        lines_changed = abs(
            proposal.replace_block.count("\n") - proposal.search_block.count("\n")
        ) + 1

        return PatchResult(
            success=True,
            file_path=proposal.target_file_path,
            lines_changed=lines_changed,
        )
    except Exception as e:
        return PatchResult(
            success=False,
            file_path=proposal.target_file_path,
            error=f"Write error: {e}",
        )
