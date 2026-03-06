"""
Mutation Prompt Builder.

Constructs a constrained LLM prompt that produces a JSON PatchProposal
from gathered evidence.  The prompt enforces exact-match search/replace
semantics and forbids mutations to cartridge DB files.

Usage:
    prompt = build_patch_prompt(evidence_bundle, intent_label)
    # Feed `prompt` to the LLM; parse JSON response into PatchProposal.
"""

from typing import List, Dict, Any


# JSON schema the LLM must produce
_PATCH_SCHEMA = """\
{
  "target_file_path": "<absolute path to file>",
  "search_block": "<exact verbatim text to find>",
  "replace_block": "<replacement text>",
  "justification": "<1-2 sentence rationale>"
}\
"""

# Few-shot examples embedded in the system prompt
_FEW_SHOT = """\
### Example 1 — rename a variable
Evidence: The variable `tmp` in `utils.py` line 42 is poorly named.
```json
{
  "target_file_path": "/project/src/utils.py",
  "search_block": "tmp = compute_result(data)",
  "replace_block": "result = compute_result(data)",
  "justification": "Rename 'tmp' to 'result' for clarity."
}
```

### Example 2 — fix an off-by-one
Evidence: `range(len(items))` should be `range(len(items) - 1)` in loop.
```json
{
  "target_file_path": "/project/src/processor.py",
  "search_block": "for i in range(len(items)):",
  "replace_block": "for i in range(len(items) - 1):",
  "justification": "Fix off-by-one: last index is unused and causes IndexError."
}
```
"""


def build_patch_prompt(
    evidence_bundle: List[Dict[str, Any]],
    intent: str = "mutation",
) -> str:
    """
    Build a structured prompt that asks the LLM to produce a PatchProposal.

    Args:
        evidence_bundle: List of dicts, each with keys:
            - chunk_id: str
            - file_path: str (optional)
            - content: str (the evidence text)
            - score: float (optional)
        intent: The classified intent label (e.g. "mutation", "fix", "refactor").

    Returns:
        A prompt string ready for LLM inference.
    """
    # Format evidence
    evidence_parts = []
    for i, ev in enumerate(evidence_bundle, 1):
        path = ev.get("file_path", "unknown")
        chunk_id = ev.get("chunk_id", "?")
        content = ev.get("content", "")
        score = ev.get("score", 0.0)
        evidence_parts.append(
            f"--- Evidence {i} (chunk={chunk_id}, file={path}, "
            f"score={score:.2f}) ---\n{content}"
        )

    evidence_text = "\n\n".join(evidence_parts) if evidence_parts else "[No evidence]"

    prompt = f"""\
## Task
You are a precise code editor. Based on the evidence below, produce
exactly ONE patch in JSON format.

Intent: {intent}

## Rules
1. The `search_block` MUST be an EXACT verbatim substring of the target file.
   Do NOT paraphrase, re-indent, or approximate.
2. The `replace_block` is the replacement text.  Keep changes minimal.
3. NEVER target `.db`, `.sqlite`, or database files.
4. Output ONLY the JSON object — no markdown fences, no commentary.

## Output Schema
{_PATCH_SCHEMA}

## Few-Shot Examples
{_FEW_SHOT}

## Evidence
{evidence_text}

## Your Patch (JSON only)
"""
    return prompt
