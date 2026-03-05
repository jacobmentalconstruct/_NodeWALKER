"""
Tool Palette - Interaction Mode Switcher.

Provides tool selection like Photoshop:
- Read Tool: Normal scrolling and viewing
- Pin Tool: Selected text is pinned to chat context
- Export Tool: Selected text is exported as formatted markdown
"""

from typing import Callable, Optional, List
from enum import Enum
from dataclasses import dataclass

from ..event_bus import get_event_bus, TOOL_CHANGED


class Tool(Enum):
    """Available interaction tools."""
    READ = "read"        # View/read mode
    PIN = "pin"          # Pin text to context
    EXPORT = "export"    # Export text as markdown


@dataclass
class ToolOption:
    """A tool option with icon and metadata."""
    tool: Tool
    label: str
    icon: str  # Icon path or name
    description: str
    cursor: str = "default"  # Cursor type for this tool


class ToolPalette:
    """
    Tool Palette - dictates interaction behavior in Preview Pane.

    Emits TOOL_CHANGED events when the user selects a different tool.
    """

    # Predefined tool options
    TOOLS = [
        ToolOption(
            Tool.READ,
            "Read",
            "read-icon",
            "View and scroll content normally",
            "default"
        ),
        ToolOption(
            Tool.PIN,
            "Pin",
            "pin-icon",
            "Select text to pin to chat context",
            "pointer"
        ),
        ToolOption(
            Tool.EXPORT,
            "Export",
            "export-icon",
            "Select text to export as markdown",
            "copy"
        ),
    ]

    def __init__(self):
        """Initialize the tool palette."""
        self.current_tool: Tool = Tool.READ
        self.bus = get_event_bus()

    def set_tool(self, tool: Tool) -> None:
        """
        Switch to a different tool.

        Args:
            tool: The tool to activate
        """
        if tool == self.current_tool:
            return  # Already using this tool

        self.current_tool = tool

        # Emit TOOL_CHANGED event
        self.bus.emit(TOOL_CHANGED, {
            "tool": tool.value,
            "label": self._get_label(tool),
            "cursor": self._get_cursor(tool),
        })

    def set_tool_by_name(self, tool_name: str) -> None:
        """
        Switch to a tool by name (string).

        Args:
            tool_name: Name of the tool (e.g., "read", "pin", "export")
        """
        try:
            tool = Tool(tool_name)
            self.set_tool(tool)
        except ValueError:
            pass  # Invalid tool name

    def get_current_tool(self) -> Tool:
        """Get the currently active tool."""
        return self.current_tool

    def get_all_tools(self) -> List[ToolOption]:
        """Get all available tools."""
        return self.TOOLS.copy()

    def _get_label(self, tool: Tool) -> str:
        """Get the label for a tool."""
        for t in self.TOOLS:
            if t.tool == tool:
                return t.label
        return tool.value

    def _get_cursor(self, tool: Tool) -> str:
        """Get the cursor type for a tool."""
        for t in self.TOOLS:
            if t.tool == tool:
                return t.cursor
        return "default"

    def render_palette(self) -> str:
        """
        Render a text representation of the palette.

        Returns a string showing available tools and current selection.
        """
        lines = ["Tool Palette:"]
        for tool in self.TOOLS:
            marker = ">" if tool.tool == self.current_tool else " "
            lines.append(f"  {marker} [{tool.label}] - {tool.description}")
        return "\n".join(lines)
