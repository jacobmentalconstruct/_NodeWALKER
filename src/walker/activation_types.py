"""
Activation Event Schema for Neural Circuit Highlighting.

Defines the types, weights, and event structure for tracking
node/chunk activations during walker queries.
"""

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, Any
from uuid import uuid4
from datetime import datetime
import json


class ActivationKind(Enum):
    """Types of activation events."""
    ENTRY_HIT = "entry_hit"
    TRAVERSAL_HOP = "traversal_hop"
    COLLECT = "collect"
    PIN = "pin"
    SYNTHESIS_USED = "synthesis_used"
    CITED = "cited"
    EXPORTED = "exported"


class TargetType(Enum):
    """Types of targets that can be activated."""
    TREE_NODE = "tree_node"
    CHUNK = "chunk"
    GRAPH_NODE = "graph_node"
    FILE = "file"


# Activation weights (constants)
W_ENTRY_HIT = 2.0
W_TRAVERSAL_HOP = 0.5
W_COLLECT = 1.0
W_SYNTHESIS_USED = 1.5
W_PIN = 2.5
W_CITED = 3.0
W_EXPORTED = 2.0


# Weight mapping by kind
WEIGHT_BY_KIND = {
    ActivationKind.ENTRY_HIT: W_ENTRY_HIT,
    ActivationKind.TRAVERSAL_HOP: W_TRAVERSAL_HOP,
    ActivationKind.COLLECT: W_COLLECT,
    ActivationKind.PIN: W_PIN,
    ActivationKind.SYNTHESIS_USED: W_SYNTHESIS_USED,
    ActivationKind.CITED: W_CITED,
    ActivationKind.EXPORTED: W_EXPORTED,
}


@dataclass
class ActivationEvent:
    """Represents a single activation event."""
    session_id: str
    query_id: str
    kind: ActivationKind
    target_type: TargetType
    target_id: str
    weight: float
    event_id: str = None
    ts: str = None
    meta: Dict[str, Any] = None

    def __post_init__(self):
        """Initialize default values."""
        if self.event_id is None:
            self.event_id = str(uuid4())
        if self.ts is None:
            self.ts = datetime.utcnow().isoformat() + "Z"
        if self.meta is None:
            self.meta = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        d = asdict(self)
        d["kind"] = self.kind.value
        d["target_type"] = self.target_type.value
        d["meta"] = json.dumps(self.meta) if isinstance(self.meta, dict) else self.meta
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActivationEvent":
        """Reconstruct from dictionary."""
        d_copy = d.copy()
        d_copy["kind"] = ActivationKind(d_copy["kind"])
        d_copy["target_type"] = TargetType(d_copy["target_type"])
        if isinstance(d_copy["meta"], str):
            d_copy["meta"] = json.loads(d_copy["meta"])
        return cls(**d_copy)
