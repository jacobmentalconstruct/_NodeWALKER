"""
Event Bus for UI ↔ Engine Communication.

Implements a simple pub/sub system for event-driven architecture.
Keeps the UI decoupled from engine implementation details.
"""

from typing import Callable, Dict, List, Any
from collections import defaultdict


class EventBus:
    """
    Simple publish/subscribe event bus.

    No UI dependencies inside the bus - purely functional.
    """

    def __init__(self):
        """Initialize the event bus."""
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: Callable) -> Callable:
        """
        Subscribe to an event.

        Args:
            event_name: Name of the event (e.g., "ACTIVATION_EVENT")
            handler: Callable that receives event payload

        Returns:
            Unsubscribe function
        """
        self._subscribers[event_name].append(handler)

        def unsubscribe():
            if handler in self._subscribers[event_name]:
                self._subscribers[event_name].remove(handler)

        return unsubscribe

    def emit(self, event_name: str, payload: Dict[str, Any] = None) -> None:
        """
        Emit an event to all subscribers.

        Args:
            event_name: Name of the event
            payload: Event data (dict)
        """
        if payload is None:
            payload = {}

        for handler in self._subscribers[event_name]:
            try:
                handler(payload)
            except Exception as e:
                print(f"Error in event handler for {event_name}: {e}")

    def subscribers_count(self, event_name: str) -> int:
        """Get number of subscribers for an event."""
        return len(self._subscribers[event_name])


# Global event bus instance
_global_bus = EventBus()


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    return _global_bus


# Event names (constants)
ACTIVATION_EVENT = "ACTIVATION_EVENT"
ACTIVATION_TOP = "ACTIVATION_TOP"
FOCUS_TARGET = "FOCUS_TARGET"
PIN_CONTEXT = "PIN_CONTEXT"
QUERY_STARTED = "QUERY_STARTED"
QUERY_FINISHED = "QUERY_FINISHED"
VIEW_CHANGED = "VIEW_CHANGED"
TOOL_CHANGED = "TOOL_CHANGED"
PATCH_PROPOSED = "PATCH_PROPOSED"
