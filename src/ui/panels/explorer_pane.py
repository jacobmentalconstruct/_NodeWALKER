"""
Explorer Pane (Left Dock).

Displays tree/list with heat indicators for activated nodes.
Subscribes to ACTIVATION_TOP and VIEW_CHANGED events.
Renders differently based on view mode (Structural, Semantic, Search).
"""

from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass

from ..event_bus import get_event_bus, ACTIVATION_TOP, VIEW_CHANGED
from .view_palette import ViewMode


@dataclass
class HeatIndicator:
    """Visual heat indicator for a target."""
    target_type: str
    target_id: str
    score: float
    explanation: List[str]  # Top contributing event descriptions


class ExplorerPane:
    """
    Explorer Pane - displays activated nodes with heat indicators.

    Respects the view mode (Structural, Semantic, Search) from VIEW_CHANGED events.
    Also stores hierarchy data when loaded from a cartridge.
    """

    def __init__(self):
        """Initialize the explorer pane."""
        self.hot_targets: Dict[Tuple[str, str], HeatIndicator] = {}
        self.bus = get_event_bus()
        self.view_mode: ViewMode = ViewMode.STRUCTURAL
        self.unsubscribe_activation = None
        self.unsubscribe_view = None
        # Hierarchy data: list of (indent_level, display_text, node_id, node_type) tuples
        self.hierarchy_lines: List[Tuple[int, str, str, str]] = []

    def start(self) -> None:
        """Start listening to activation and view change events."""
        self.unsubscribe_activation = self.bus.subscribe(ACTIVATION_TOP, self._on_activation_top)
        self.unsubscribe_view = self.bus.subscribe(VIEW_CHANGED, self._on_view_changed)

    def stop(self) -> None:
        """Stop listening to events."""
        if self.unsubscribe_activation:
            self.unsubscribe_activation()
        if self.unsubscribe_view:
            self.unsubscribe_view()

    def _on_view_changed(self, payload: dict) -> None:
        """Handle VIEW_CHANGED event."""
        view_mode_str = payload.get("view_mode", "structural")
        try:
            self.view_mode = ViewMode(view_mode_str)
        except ValueError:
            pass  # Invalid view mode, keep current

    def _on_activation_top(self, payload: dict) -> None:
        """Handle ACTIVATION_TOP event."""
        # payload = {
        #     "query_id": str,
        #     "top_targets": [(target_type, target_id, score), ...],
        #     "explain_fn": callable(target_type, target_id) -> [ActivationEvent]
        # }
        top_targets = payload.get("top_targets", [])

        # Clear old targets
        self.hot_targets.clear()

        # Add new targets
        explain_fn = payload.get("explain_fn")
        for target_type, target_id, score in top_targets:
            key = (target_type, target_id)
            explanation = []
            if explain_fn:
                events = explain_fn(target_type, target_id)
                explanation = [
                    f"{e.kind.value} (weight: {e.weight})"
                    for e in events[:3]
                ]

            self.hot_targets[key] = HeatIndicator(
                target_type=target_type,
                target_id=target_id,
                score=score,
                explanation=explanation
            )

    def load_hierarchy(self, roots, get_children_fn, max_depth: int = 4) -> None:
        """
        Load a tree hierarchy for structural view display.

        Args:
            roots: List of root TreeNode objects
            get_children_fn: Callable(node_id) -> List[TreeNode]
            max_depth: Maximum tree depth to walk
        """
        self.hierarchy_lines.clear()

        type_icons = {
            "module": "\U0001F4C1",
            "file": "\U0001F4C4",
            "class": "\U0001F537",
            "function": "\u25C7",
            "method": "\u25C7",
            "import": "\u2192",
            "variable": "\u25CB",
            "decorator": "@",
        }

        def walk(node, depth):
            icon = type_icons.get(node.node_type, "\u25AA")
            name = node.name or node.node_type
            self.hierarchy_lines.append((depth, f"{icon} {name}", node.node_id, node.node_type))

            if depth < max_depth:
                children = get_children_fn(node.node_id)
                for child in children:
                    walk(child, depth + 1)

        for root in roots:
            walk(root, 0)

    def get_hot_targets(self) -> List[HeatIndicator]:
        """Get list of currently hot targets sorted by score."""
        return sorted(
            self.hot_targets.values(),
            key=lambda h: h.score,
            reverse=True
        )

    def render_content(self) -> str:
        """
        Render the explorer pane content based on current view mode.

        Returns a string representation of the current view.
        """
        if self.view_mode == ViewMode.STRUCTURAL:
            return self._render_structural_view()
        elif self.view_mode == ViewMode.SEMANTIC:
            return self._render_semantic_view()
        elif self.view_mode == ViewMode.SEARCH:
            return self._render_search_view()
        else:
            return "[Unknown view mode]"

    def _render_structural_view(self) -> str:
        """Render as tree-based hierarchical view."""
        if self.hot_targets:
            # Show heat-mapped targets when activations exist
            lines = ["[Structural View] - Active Targets:"]
            for target_type, target_id, score in self._get_sorted_targets():
                indicator = self.render_heat_indicator(score, max_score=10.0)
                lines.append(f"  {indicator} {target_type}: {target_id}")
            return "\n".join(lines)

        if self.hierarchy_lines:
            # Show loaded hierarchy tree
            lines = ["[Structural View] - Hierarchy:"]
            for indent, text, node_id, node_type in self.hierarchy_lines:
                lines.append("  " * indent + text)
            return "\n".join(lines)

        return "[No cartridge loaded — use Load Cartridge to begin]"

    def _render_semantic_view(self) -> str:
        """Render as tag cloud / word cloud visualization."""
        if not self.hot_targets:
            return "[No targets activated yet]"

        lines = ["[Semantic View] - Tag Cloud:"]
        sorted_targets = self._get_sorted_targets()

        # Normalize scores for sizing
        max_score = sorted_targets[0][2] if sorted_targets else 1
        for target_type, target_id, score in sorted_targets:
            # Size corresponds to score (1-5)
            size = max(1, min(5, int((score / max_score) * 5)))
            text_size = ["small", "normal", "medium", "large", "xlarge"][size - 1]
            lines.append(f"  [{text_size:6}] {target_id}")
        return "\n".join(lines)

    def _render_search_view(self) -> str:
        """Render as linear list of search results."""
        if not self.hot_targets:
            return "[No targets activated yet]"

        lines = ["[Search View] - Results List:"]
        for rank, (target_type, target_id, score) in enumerate(self._get_sorted_targets(), 1):
            lines.append(f"  {rank:2}. {target_id:30} ({target_type:10}) Score: {score:.1f}")
        return "\n".join(lines)

    def _get_sorted_targets(self) -> List[Tuple[str, str, float]]:
        """Get sorted targets by score descending."""
        return sorted(
            [(h.target_type, h.target_id, h.score) for h in self.hot_targets.values()],
            key=lambda x: x[2],
            reverse=True
        )

    def get_selected_node_info(self) -> dict:
        """
        Get info about the currently active (highest-scoring) hot target.

        Returns dict with keys: target_type, target_id, score.
        Returns None if nothing is active.
        """
        targets = self.get_hot_targets()
        if not targets:
            return None

        top = targets[0]
        return {
            "target_type": top.target_type,
            "target_id": top.target_id,
            "score": top.score,
        }

    def render_heat_indicator(self, score: float, max_score: float = 100.0) -> str:
        """
        Render a visual heat indicator.

        Returns a string representation (e.g., a progress bar or colored dot).
        """
        # Simple bar representation: 10 levels using ASCII
        level = min(10, max(0, int((score / max_score) * 10)))
        bar = "=" * level + "-" * (10 - level)
        return f"[{bar}] {score:.1f}"
