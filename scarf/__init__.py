"""SCARF: Staff-Coupled Acuity-Ranked Flow for hospital ward evacuation."""

from .model import (
    ALPHA,
    RHO,
    WIDTH_UNITS,
    Arc,
    HazardSource,
    Instance,
    Kind,
    Mobility,
    Vertex,
)
from .scenario import load, loads, to_dict
from .solver import Move, Plan, Scarf, solve
from .verify import Report, verify

__version__ = "1.0.0"

__all__ = [
    "ALPHA", "RHO", "WIDTH_UNITS",
    "Arc", "HazardSource", "Instance", "Kind", "Mobility", "Vertex",
    "Move", "Plan", "Scarf", "solve",
    "Report", "verify",
    "load", "loads", "to_dict",
]
