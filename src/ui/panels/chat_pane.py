"""
Chat Pane (Right Dock).

Displays chat history, context tray (pinned items), and sources used.
Integrates with LLMAgent for processing queries.
"""

from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

from ..event_bus import get_event_bus, PIN_CONTEXT, ACTIVATION_TOP, FOCUS_TARGET


@dataclass
class PinnedItem:
    """A pinned context item."""
    label: str
    text: str
    chunk_id: str
    path: str
    lines: str


@dataclass
class SourceItem:
    """A source used in the response."""
    target_type: str
    target_id: str
    score: float
    kind: str  # CITED, PIN, COLLECT, SYNTHESIS_USED


class ChatPane:
    """
    Chat Pane - displays chat history, pinned context, and sources.
    """

    def __init__(self, llm_agent: Optional[Any] = None):
        """
        Initialize the chat pane.

        Args:
            llm_agent: LLMAgent instance for processing queries
        """
        self.llm_agent = llm_agent
        self.chat_history: List[Dict[str, str]] = []
        self.pinned_items: List[PinnedItem] = []
        self.sources_used: List[SourceItem] = []
        self.bus = get_event_bus()
        self.unsubscribe_pin = None
        self.unsubscribe_activation = None

    def start(self) -> None:
        """Start listening to events."""
        self.unsubscribe_pin = self.bus.subscribe(PIN_CONTEXT, self._on_pin_context)
        self.unsubscribe_activation = self.bus.subscribe(ACTIVATION_TOP, self._on_activation_top)

    def stop(self) -> None:
        """Stop listening to events."""
        if self.unsubscribe_pin:
            self.unsubscribe_pin()
        if self.unsubscribe_activation:
            self.unsubscribe_activation()

    def _on_pin_context(self, payload: dict) -> None:
        """Handle PIN_CONTEXT event."""
        item = PinnedItem(
            label=payload.get("label", "Untitled"),
            text=payload.get("text", ""),
            chunk_id=payload.get("chunk_id", ""),
            path=payload.get("path", ""),
            lines=payload.get("lines", ""),
        )
        self.pinned_items.append(item)

        # Add to LLM agent's pinned context
        if self.llm_agent:
            self.llm_agent.add_pinned_context(item.label, item.text, item.chunk_id)

    def _on_activation_top(self, payload: dict) -> None:
        """Handle ACTIVATION_TOP event to populate sources used."""
        top_targets = payload.get("top_targets", [])
        kinds = payload.get("kinds", {})  # dict of (target_type, target_id) -> kind

        self.sources_used.clear()
        for target_type, target_id, score in top_targets:
            kind = kinds.get((target_type, target_id), "ACTIVATION")
            source = SourceItem(
                target_type=target_type,
                target_id=target_id,
                score=score,
                kind=kind
            )
            self.sources_used.append(source)

    def send_prompt(self, prompt: str, session_id: str) -> None:
        """
        Send a prompt and get response from LLM agent.

        Args:
            prompt: User's input prompt
            session_id: Current session ID
        """
        if not self.llm_agent:
            self.add_message("assistant", "[LLM Agent not configured]")
            return

        # Add user message to history
        self.add_message("user", prompt)

        # Process with LLM
        response, citations = self.llm_agent.process_prompt(prompt, session_id)

        # Add assistant response to history
        self.add_message("assistant", response)

        # Emit citations as activations (handled by walker module)
        # This is a hook for the engine to process citations

    def add_message(self, role: str, content: str) -> None:
        """Add a message to chat history."""
        self.chat_history.append({
            "role": role,
            "content": content
        })

    def clear_pinned_context(self) -> None:
        """Clear all pinned items."""
        self.pinned_items.clear()
        if self.llm_agent:
            self.llm_agent.clear_pinned_context()

    def get_chat_history(self) -> List[Dict[str, str]]:
        """Get chat history."""
        return self.chat_history

    def get_pinned_items(self) -> List[PinnedItem]:
        """Get pinned context items."""
        return self.pinned_items

    def get_sources_used(self) -> List[SourceItem]:
        """Get sources used in current response."""
        return self.sources_used

    def on_source_clicked(self, target_type: str, target_id: str) -> None:
        """Handle click on a source - focus the preview pane."""
        self.bus.emit(FOCUS_TARGET, {
            "target_type": target_type,
            "target_id": target_id
        })
