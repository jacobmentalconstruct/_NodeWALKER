"""
View Palette - Icon-Based View Switcher.

Provides tight, icon-based controls to switch between different rendering modes:
- Structural Lens: Tree view of code structure
- Semantic Lens: Tag cloud / relevance visualization
- Search Lens: Linear search results
"""

from typing import Callable, Optional, List
from enum import Enum
from dataclasses import dataclass

from ..event_bus import get_event_bus, VIEW_CHANGED


class ViewMode(Enum):
    """Available view rendering modes."""
    STRUCTURAL = "structural"  # Tree-based view
    SEMANTIC = "semantic"      # Tag cloud / relevance visualization
    SEARCH = "search"          # Linear search results


@dataclass
class ViewOption:
    """A view option with icon and metadata."""
    mode: ViewMode
    label: str
    icon: str  # Icon path or name
    description: str


class ViewPalette:
    """
    View Palette - toggles between different Explorer rendering modes.

    Emits VIEW_CHANGED events when the user selects a different lens.
    """

    # Predefined view options
    VIEWS = [
        ViewOption(
            ViewMode.STRUCTURAL,
            "Structural",
            "tree-icon",
            "Tree view of code structure"
        ),
        ViewOption(
            ViewMode.SEMANTIC,
            "Semantic",
            "cloud-icon",
            "Tag cloud of activated nodes by relevance"
        ),
        ViewOption(
            ViewMode.SEARCH,
            "Search",
            "results-icon",
            "Linear list of search results"
        ),
    ]

    def __init__(self):
        """Initialize the view palette."""
        self.current_view: ViewMode = ViewMode.STRUCTURAL
        self.bus = get_event_bus()

    def set_view(self, mode: ViewMode) -> None:
        """
        Switch to a different view mode.

        Args:
            mode: The view mode to switch to
        """
        if mode == self.current_view:
            return  # Already on this view

        self.current_view = mode

        # Emit VIEW_CHANGED event
        self.bus.emit(VIEW_CHANGED, {
            "view_mode": mode.value,
            "label": self._get_label(mode),
        })

    def set_view_by_name(self, mode_name: str) -> None:
        """
        Switch to a view by name (string).

        Args:
            mode_name: Name of the view mode (e.g., "structural", "semantic", "search")
        """
        try:
            mode = ViewMode(mode_name)
            self.set_view(mode)
        except ValueError:
            pass  # Invalid mode name

    def get_current_view(self) -> ViewMode:
        """Get the currently active view mode."""
        return self.current_view

    def get_all_views(self) -> List[ViewOption]:
        """Get all available view options."""
        return self.VIEWS.copy()

    def _get_label(self, mode: ViewMode) -> str:
        """Get the label for a view mode."""
        for view in self.VIEWS:
            if view.mode == mode:
                return view.label
        return mode.value

    def render_palette(self) -> str:
        """
        Render a text representation of the palette.

        Returns a string showing available views and current selection.
        """
        lines = ["View Palette:"]
        for view in self.VIEWS:
            marker = ">" if view.mode == self.current_view else " "
            lines.append(f"  {marker} [{view.label}] - {view.description}")
        return "\n".join(lines)
