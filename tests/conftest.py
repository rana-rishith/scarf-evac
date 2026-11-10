"""Small hand-built instances for the rule and edge-case tests."""

from __future__ import annotations

import pytest

from scarf.model import Arc, Instance, Kind, Vertex

INF = float("inf")


def link(arcs: list[Arc], u: str, v: str, tau: float,
         width: int = 2150, kind: str = "corridor",
         both: bool = True) -> None:
    cid = f"{min(u, v)}|{max(u, v)}"
    pairs = ((u, v), (v, u)) if both else ((u, v),)
    for a, b in pairs:
        arcs.append(Arc(u=a, v=b, tau=tau, clear_width_mm=width,
                        kind=kind, corridor=cid))


def bay(vid: str, patients: dict[int, int], staff: int = 0,
        capacity: float = 10, acuity: float = 0.5,
        defer_until: int = 0) -> Vertex:
    return Vertex(id=vid, kind=Kind.OCCUPIED, capacity=capacity, staff=staff,
                  patients=dict(patients), acuity=acuity,
                  defer_until=defer_until, label=vid)


def transit(vid: str, capacity: float = 10, staff: int = 0) -> Vertex:
    return Vertex(id=vid, kind=Kind.TRANSIT, capacity=capacity, staff=staff,
                  label=vid)


def refuge(vid: str, capacity: float = 20) -> Vertex:
    return Vertex(id=vid, kind=Kind.REFUGE, capacity=capacity, label=vid)


def exit_node(vid: str = "x") -> Vertex:
    return Vertex(id=vid, kind=Kind.EXIT, capacity=INF, label=vid)


def build(nodes: list[Vertex], arcs: list[Arc], **kw) -> Instance:
    inst = Instance(vertices={n.id: n for n in nodes}, arcs=arcs, **kw)
    inst.validate()
    return inst


@pytest.fixture
def straight() -> Instance:
    """bay a -- corridor c -- refuge r. One of everything, nothing clever."""
    arcs: list[Arc] = []
    link(arcs, "a", "c", 3.0, 1100, "door")
    link(arcs, "c", "r", 4.0, 1800, "door")
    return build(
        [bay("a", {1: 2, 2: 1, 3: 1}, staff=3), transit("c"), refuge("r")],
        arcs, name="straight")
