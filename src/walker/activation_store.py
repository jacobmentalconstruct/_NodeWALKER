"""
In-Memory Activation Aggregator.

Maintains cumulative scores for targets and recent contributing events.
Provides quick lookups for UI display without DB queries.
"""

from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from .activation_types import ActivationEvent, TargetType


class ActivationStore:
    """
    In-memory store for aggregating activations during a query.

    Tracks cumulative score per (target_type, target_id) and keeps
    a small list of recent contributing events for explanation.
    """

    def __init__(self):
        """Initialize the store."""
        self.scores: Dict[Tuple[str, str], float] = {}  # (target_type, target_id) -> score
        self.events: Dict[Tuple[str, str], List[ActivationEvent]] = defaultdict(list)  # cap at 20 per target
        self.session_id: Optional[str] = None
        self.query_id: Optional[str] = None

    def reset_for_query(self, session_id: str, query_id: str) -> None:
        """Reset the store for a new query."""
        self.session_id = session_id
        self.query_id = query_id
        self.scores.clear()
        self.events.clear()

    def add(self, event: ActivationEvent) -> None:
        """Add an activation event and update cumulative score."""
        key = (event.target_type.value, event.target_id)

        # Update cumulative score
        self.scores[key] = self.scores.get(key, 0.0) + event.weight

        # Keep recent events (cap at 20)
        event_list = self.events[key]
        event_list.append(event)
        if len(event_list) > 20:
            event_list.pop(0)

    def top_targets(self, limit: int = 25) -> List[Tuple[str, str, float]]:
        """
        Get top targets by cumulative score.

        Returns list of (target_type, target_id, cumulative_weight) tuples.
        """
        sorted_items = sorted(
            self.scores.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return [
            (target_type, target_id, score)
            for (target_type, target_id), score in sorted_items[:limit]
        ]

    def explain(self, target_type: str, target_id: str) -> List[ActivationEvent]:
        """
        Get the top 5 contributing events for a target.

        Returns list of ActivationEvent objects sorted by weight (descending).
        """
        key = (target_type, target_id)
        events = self.events.get(key, [])

        # Sort by weight descending, take top 5
        sorted_events = sorted(events, key=lambda e: e.weight, reverse=True)
        return sorted_events[:5]
