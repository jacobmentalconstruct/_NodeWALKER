"""
Preview Pane (Center Stage).

Displays chunk/node content with highlighting.
Handles FOCUS_TARGET events and allows pinning to chat context.
Interaction behavior changes based on the selected tool (Read, Pin, Export).
"""

from typing import Optional, Dict, Any, Callable

from ..event_bus import get_event_bus, FOCUS_TARGET, PIN_CONTEXT, TOOL_CHANGED
from .tool_palette import Tool


class PreviewPane:
    """
    Preview Pane - displays selected chunk/node content with highlighting.

    Interaction behavior changes based on the active tool from ToolPalette.
    """

    def __init__(self, resolver: Optional[Callable] = None):
        """
        Initialize the preview pane.

        Args:
            resolver: CASResolver or similar for loading chunk content
        """
        self.resolver = resolver
        self.current_target: Optional[Dict[str, Any]] = None
        self.current_content: str = ""
        self.current_tool: Tool = Tool.READ
        self.bus = get_event_bus()
        self.unsubscribe_focus = None
        self.unsubscribe_tool = None

    def start(self) -> None:
        """Start listening to focus and tool change events."""
        self.unsubscribe_focus = self.bus.subscribe(FOCUS_TARGET, self._on_focus_target)
        self.unsubscribe_tool = self.bus.subscribe(TOOL_CHANGED, self._on_tool_changed)

    def stop(self) -> None:
        """Stop listening to events."""
        if self.unsubscribe_focus:
            self.unsubscribe_focus()
        if self.unsubscribe_tool:
            self.unsubscribe_tool()

    def _on_tool_changed(self, payload: dict) -> None:
        """Handle TOOL_CHANGED event."""
        tool_str = payload.get("tool", "read")
        try:
            self.current_tool = Tool(tool_str)
        except ValueError:
            self.current_tool = Tool.READ  # Default to read

    def _on_focus_target(self, payload: dict) -> None:
        """Handle FOCUS_TARGET event."""
        # payload = {
        #     "target_type": str,
        #     "target_id": str,
        #     "meta": dict (optional)
        # }
        target_type = payload.get("target_type")
        target_id = payload.get("target_id")

        self.current_target = {
            "target_type": target_type,
            "target_id": target_id,
            "meta": payload.get("meta", {})
        }

        # Load content
        self._load_content(target_type, target_id)

    def _load_content(self, target_type: str, target_id: str) -> None:
        """Load content for the given target using CASResolver."""
        if not self.resolver:
            self.current_content = f"[Preview for {target_type}:{target_id}]"
            return

        try:
            if target_type == "chunk":
                content = self.resolver.resolve_chunk_by_id(target_id)
                self.current_content = content if content else f"[No content for chunk {target_id}]"
            elif target_type == "tree_node":
                # Try chunk_id from meta, or resolve via the resolver's DB
                chunk_id = self.current_target.get("meta", {}).get("chunk_id") if self.current_target else None
                if chunk_id:
                    content = self.resolver.resolve_chunk_by_id(chunk_id)
                    self.current_content = content if content else f"[No content for node {target_id}]"
                else:
                    self.current_content = f"[Preview for {target_type}:{target_id}]"
            else:
                self.current_content = f"[Preview for {target_type}:{target_id}]"
        except Exception as e:
            self.current_content = f"[Error loading content: {str(e)}]"

    def handle_text_selection(self, selected_text: str, label: str = "Selection") -> None:
        """
        Handle text selection based on current tool.

        The behavior depends on which tool is active:
        - READ: Do nothing (just view)
        - PIN: Emit PIN_CONTEXT to add to chat context
        - EXPORT: Emit EXPORTED activation and copy to clipboard

        Args:
            selected_text: The selected text
            label: Display label for the selection
        """
        if self.current_tool == Tool.READ:
            # Just viewing, do nothing
            pass
        elif self.current_tool == Tool.PIN:
            # Pin to context
            self.pin_to_context(label, selected_text)
        elif self.current_tool == Tool.EXPORT:
            # Export as markdown
            self.export_text(label, selected_text)

    def pin_to_context(self, label: str, text: str = None) -> None:
        """
        Pin current selection to chat context.

        Emits PIN_CONTEXT event with the current target data.
        """
        if text is None:
            text = self.current_content

        if not self.current_target:
            return

        payload = {
            "label": label,
            "text": text,
            "chunk_id": self.current_target.get("target_id"),
            "path": self.current_target.get("meta", {}).get("path", "unknown"),
            "lines": self.current_target.get("meta", {}).get("lines", ""),
        }

        self.bus.emit(PIN_CONTEXT, payload)

    def export_text(self, label: str, text: str) -> None:
        """
        Export text as formatted markdown.

        Emits EXPORTED activation event and returns formatted text
        (in a real implementation, would copy to clipboard).

        Args:
            label: Display label for the exported content
            text: Content to export
        """
        # Format as markdown code block
        formatted = f"```\n{text}\n```"

        # Emit activation event for EXPORTED
        # (This would be done by the walker in a real implementation)
        # For now, just return the formatted content
        return formatted

    def highlight_content(self, start_line: int = None, end_line: int = None) -> str:
        """
        Return content with line range highlighted.

        For now, just returns the content. UI implementation would handle
        actual highlighting.
        """
        if start_line is None or end_line is None:
            return self.current_content

        lines = self.current_content.split("\n")
        highlighted_lines = []
        for i, line in enumerate(lines, 1):
            prefix = ">>> " if start_line <= i <= end_line else "    "
            highlighted_lines.append(f"{prefix}{line}")

        return "\n".join(highlighted_lines)

    def get_current_content(self) -> str:
        """Get the currently displayed content."""
        return self.current_content
