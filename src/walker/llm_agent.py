"""
LLM Agent with 3-Tier Memory and Citation Extraction.

Integrates with local Ollama models and implements a sliding memory window
with automatic summarization when Tier 1 overflows.
"""

import re
import json
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

# Ollama integration (optional - graceful fallback if not installed)
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


@dataclass
class ChatMessage:
    """A single chat message."""
    role: str  # "user", "assistant", "system"
    content: str


class LLMAgent:
    """
    LLM Agent with 3-tier memory and citation extraction.

    Tier 1: Recent chat history (FIFO, ~6000 tokens)
    Tier 2/3: Rolled-up summaries from session_db
    """

    # System prompt enforcing citations
    SYSTEM_PROMPT = """You are a helpful assistant analyzing code and data.

IMPORTANT: When referencing specific chunks or nodes, you MUST include citations in this format:
- For chunks: [[chunk:CHUNK_ID]]
- For nodes: [[node:NODE_ID]]
- For files: [[file:FILE_PATH]]

Example: "The main function is defined in [[chunk:abc123]] and uses [[node:def456]]."

Always include citations to make your responses verifiable and traceable."""

    def __init__(self, model: str = "phi-3-mini-128k", helper_model: str = "",
                 session_db=None):
        """
        Initialize the LLM Agent.

        Args:
            model: Ollama model name for primary reasoning (big_brain slot)
            helper_model: Ollama model name for critics/classification (helper slot)
            session_db: SessionDB instance for storing summaries
        """
        self.model = model
        self.helper_model = helper_model  # Used by forensic critics
        self.session_db = session_db
        self.chat_history: List[ChatMessage] = []
        self.tier1_tokens = 0
        self.tier1_limit = 6000
        self.pinned_context: List[str] = []
        self.cartridge_context: str = ""  # Persistent context about the loaded cartridge

    def set_cartridge_context(self, summary: str) -> None:
        """Set persistent context about the loaded cartridge."""
        self.cartridge_context = summary

    def add_pinned_context(self, label: str, text: str, source_id: str) -> None:
        """Add text to the pinned context tray."""
        self.pinned_context.append(f"**{label}** ({source_id}):\n{text}")

    def clear_pinned_context(self) -> None:
        """Clear all pinned context."""
        self.pinned_context.clear()

    def get_pinned_context_str(self) -> str:
        """Get pinned context as a formatted string."""
        if not self.pinned_context:
            return ""
        return "### Pinned Context:\n" + "\n\n".join(self.pinned_context)

    def process_prompt(
        self,
        prompt: str,
        session_id: str,
        include_tier2_3: bool = True
    ) -> Tuple[str, List[str]]:
        """
        Process a user prompt with 3-tier memory and return response + citations.

        Builds the prompt in this exact order:
        1. System Prompt: Instructions for citations
        2. Tier 2/3: Rolled summaries from session_db
        3. Injected Context: Pinned text from UI
        4. Tier 1: Recent chat history (~6000 tokens)

        Args:
            prompt: User's input prompt
            session_id: Session ID for fetching summaries
            include_tier2_3: Whether to include Tier 2/3 summaries

        Returns:
            Tuple of (response_text, extracted_citations)
        """
        # STEP 1: System Prompt (enforces citations)
        messages = [ChatMessage("system", self.SYSTEM_PROMPT)]

        # STEP 1.5: Cartridge context (persistent data description)
        if self.cartridge_context:
            messages.append(ChatMessage("system", self.cartridge_context))

        # STEP 2: Tier 2/3 - Long-term memory (rolled summaries)
        if include_tier2_3 and self.session_db:
            ltm_parts = []
            for tier in [3, 2]:  # Load in reverse priority (3 is oldest)
                summaries = self.session_db.get_summaries(session_id, tier)
                if summaries:
                    ltm_parts.append(f"[Tier {tier}]\n" + "\n---\n".join(summaries))

            if ltm_parts:
                ltm_text = "### Long-Term Context (Prior Sessions):\n" + "\n\n".join(ltm_parts)
                messages.append(ChatMessage("system", ltm_text))

        # STEP 3: Injected Context - Pinned items from UI
        pinned = self.get_pinned_context_str()
        if pinned:
            messages.append(ChatMessage("system", pinned))

        # STEP 4: Tier 1 - Short-term memory (recent chat history)
        tier1_text = self._build_tier1_context()
        if tier1_text:
            messages.append(ChatMessage("system", tier1_text))

        # STEP 5: Current user prompt
        messages.append(ChatMessage("user", prompt))

        # STEP 6: Call LLM with assembled context
        try:
            response_text = self._call_ollama(messages)
        except Exception as e:
            response_text = f"[Error during inference: {str(e)}]"

        # STEP 7: Update chat history
        self.chat_history.append(ChatMessage("user", prompt))
        self.chat_history.append(ChatMessage("assistant", response_text))

        # STEP 8: Manage Tier 1 overflow (auto-summarization)
        self._manage_tier1_overflow(session_id)

        # STEP 9: Extract citations from response
        citations = self._extract_citations(response_text)

        return response_text, citations

    def _build_tier1_context(self) -> str:
        """Build Tier 1 context from recent chat history."""
        if not self.chat_history:
            return ""

        # Estimate tokens (roughly 4 chars = 1 token)
        recent_messages = []
        token_count = 0
        for msg in reversed(self.chat_history):
            msg_tokens = len(msg.content) // 4
            if token_count + msg_tokens > self.tier1_limit:
                break
            recent_messages.insert(0, msg)
            token_count += msg_tokens

        lines = []
        for msg in recent_messages:
            lines.append(f"[{msg.role.upper()}]: {msg.content}")
        return "### Recent Chat History:\n" + "\n".join(lines)

    def _manage_tier1_overflow(self, session_id: str) -> None:
        """
        Check if Tier 1 is full. If so, summarize oldest messages
        and move to Tier 2/3.
        """
        if not self.session_db:
            return

        # Estimate total tokens in chat history
        total_tokens = sum(len(msg.content) // 4 for msg in self.chat_history)

        if total_tokens > self.tier1_limit:
            # Summarize oldest messages
            oldest_messages = self.chat_history[:10]  # Summarize first 10 messages
            summary_text = self._summarize_messages(oldest_messages)

            # Store in Tier 2
            self.session_db.insert_summary(session_id, tier=2, content=summary_text)

            # Remove summarized messages from history
            self.chat_history = self.chat_history[10:]

    def _summarize_messages(self, messages: List[ChatMessage]) -> str:
        """
        Summarize a list of messages using the LLM.

        Returns a brief summary suitable for Tier 2/3 storage.
        """
        if not messages or not OLLAMA_AVAILABLE:
            return "[Summary unavailable]"

        # Build text to summarize
        text = "\n".join(f"{msg.role}: {msg.content}" for msg in messages)

        # Call LLM for summary
        try:
            response = ollama.generate(
                model=self.model,
                prompt=f"Summarize this conversation concisely:\n\n{text}",
                stream=False
            )
            return response.get("response", "[Summary failed]")
        except Exception as e:
            return f"[Summary error: {str(e)}]"

    def _call_ollama(self, messages: List[ChatMessage]) -> str:
        """
        Call the Ollama API with the given messages.

        Returns the assistant's response text.
        """
        if not OLLAMA_AVAILABLE:
            return "[Ollama not available - returning placeholder response]"

        try:
            # Convert messages to ollama format
            formatted_messages = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]

            response = ollama.chat(
                model=self.model,
                messages=formatted_messages,
                stream=False
            )
            return response.get("message", {}).get("content", "[Empty response]")
        except Exception as e:
            return f"[Error calling Ollama: {str(e)}]"

    def call_helper(self, system: str, prompt: str, max_tokens: int = 64) -> str:
        """
        Disposable inference call on the helper model.

        No conversation history is read or written. Used for critics,
        scope gate, intent classification — any side-channel inference
        that should not pollute the chat tier.

        Args:
            system: System prompt constraining the helper's response
            prompt: The user/task prompt
            max_tokens: Max tokens to generate (keep small for classification)

        Returns:
            Raw response text from the helper model
        """
        target_model = self.helper_model or self.model
        if not OLLAMA_AVAILABLE:
            return "[helper not available]"

        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
            response = ollama.chat(
                model=target_model,
                messages=messages,
                options={"num_predict": max_tokens, "num_ctx": 2048},
                stream=False,
            )
            return response.get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"[helper error: {e}]"

    def process_prompt_with_referent(
        self,
        prompt: str,
        session_id: str,
        referent_context: str = "",
    ) -> Tuple[str, List[str]]:
        """
        Process a prompt with optional referent context injected.

        The referent_context describes what "this/it/that" points to
        (e.g. a specific node, chunk, or selected text).  It is injected
        as a system message right before the user's prompt so the LLM
        knows the deictic target.

        Args:
            prompt: User's input prompt.
            session_id: Session ID for fetching summaries.
            referent_context: Plain-text description of the bound referent.

        Returns:
            Tuple of (response_text, extracted_citations).
        """
        if referent_context:
            # Temporarily inject referent as pinned context
            marker = f"**Active Referent**:\n{referent_context}\n\n" \
                     "IMPORTANT: Only describe what is actually present " \
                     "in the code above. Do not invent behaviour."
            self.pinned_context.append(marker)

        try:
            return self.process_prompt(prompt, session_id)
        finally:
            # Remove the injected marker so it doesn't persist
            if referent_context:
                try:
                    self.pinned_context.remove(marker)
                except ValueError:
                    pass

    @staticmethod
    def _extract_citations(text: str) -> List[str]:
        """
        Extract citation tokens from response text.

        Looks for patterns like [[chunk:ID]], [[node:ID]], [[file:PATH]].
        Returns list of (citation_type, id_or_path) tuples.
        """
        pattern = r"\[\[(\w+):([^\]]+)\]\]"
        matches = re.findall(pattern, text)
        return matches  # List of (type, id) tuples
