"""Read and write evacuation scenarios as JSON.

The schema is deliberately close to what a floor plan gives you: a list of
places with their floor area and census, and a list of openings with their
clear width. Everything the solver needs beyond that (which classes fit, how
many patients per minute an opening passes) is derived in ``Arc.derive``, so a
scenario file never has to state a capacity it would have to guess at.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Arc, HazardSource, Instance, Kind, Vertex

INF = float("inf")

_KINDS = {
    "occupied": Kind.OCCUPIED,
    "transit": Kind.TRANSIT,
    "refuge": Kind.REFUGE,
    "exit": Kind.EXIT,
}


def _capacity(raw: Any) -> float:
    if raw is None or raw in ("inf", "infinite"):
        return INF
    return float(raw)


def from_dict(doc: dict) -> Instance:
    """Build an Instance from a parsed scenario document."""
    if not isinstance(doc, dict):
        raise ValueError("scenario must be a JSON object")
    for required in ("vertices", "arcs"):
        if required not in doc:
            raise ValueError(f"scenario is missing '{required}'")

    vertices: dict[str, Vertex] = {}
    for raw in doc["vertices"]:
        kind_name = str(raw.get("kind", "transit")).lower()
        if kind_name not in _KINDS:
            raise ValueError(f"unknown vertex kind {kind_name!r}")
        vid = str(raw["id"])
        if vid in vertices:
            raise ValueError(f"duplicate vertex id {vid!r}")
        vertices[vid] = Vertex(
            id=vid,
            kind=_KINDS[kind_name],
            capacity=_capacity(raw.get("capacity")),
            staff=int(raw.get("staff", 0)),
            patients={int(k): int(v)
                      for k, v in (raw.get("patients") or {}).items()
                      if int(v) > 0},
            acuity=float(raw.get("acuity", 0.0)),
            defer_until=int(raw.get("defer_until", 0)),
            floor=int(raw.get("floor", 0)),
            label=str(raw.get("label", "")),
        )

    arcs: list[Arc] = []
    for raw in doc["arcs"]:
        arc = Arc(
            u=str(raw["u"]),
            v=str(raw["v"]),
            tau=float(raw["tau"]),
            clear_width_mm=int(raw.get("clear_width_mm", 2150)),
            kind=str(raw.get("kind", "corridor")),
            corridor=raw.get("corridor"),
            extra_escorts=int(raw.get("extra_escorts", 0)),
            fits={int(k): bool(v)
                  for k, v in (raw.get("fits") or {}).items()},
            capacity=int(raw.get("capacity", 0)),
        )
        arcs.append(arc)
        if raw.get("bidirectional", True):
            back = Arc(
                u=arc.v, v=arc.u, tau=arc.tau,
                clear_width_mm=arc.clear_width_mm, kind=arc.kind,
                corridor=arc.corridor, extra_escorts=arc.extra_escorts,
                fits=dict(arc.fits), capacity=arc.capacity,
            )
            arcs.append(back)

    inst = Instance(
        vertices=vertices,
        arcs=arcs,
        dt=int(doc.get("dt", 30)),
        horizon=int(doc.get("horizon", 120)),
        hazard=[HazardSource(vertex=str(h["vertex"]),
                             start=int(h.get("start", 0)),
                             spread_factor=float(h.get("spread_factor", 60.0)))
                for h in doc.get("hazard", [])],
        safety_margin=int(doc.get("safety_margin", 1)),
        name=str(doc.get("name", "scenario")),
    )
    inst.validate()
    return inst


def loads(text: str) -> Instance:
    return from_dict(json.loads(text))


def load(path: str | Path) -> Instance:
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def to_dict(inst: Instance) -> dict:
    """Round-trip an Instance back to the scenario schema."""
    names = {v: k for k, v in _KINDS.items()}
    seen: set[str] = set()
    arcs = []
    for a in inst.arcs:
        key = "|".join(sorted((a.u, a.v))) + a.kind
        if key in seen:
            continue
        seen.add(key)
        arcs.append({
            "u": a.u, "v": a.v, "tau": a.tau,
            "clear_width_mm": a.clear_width_mm, "kind": a.kind,
            "corridor": a.corridor, "extra_escorts": a.extra_escorts,
        })
    return {
        "name": inst.name,
        "dt": inst.dt,
        "horizon": inst.horizon,
        "safety_margin": inst.safety_margin,
        "vertices": [
            {
                "id": v.id, "kind": names[v.kind],
                "capacity": None if v.capacity == INF else v.capacity,
                "staff": v.staff,
                "patients": {str(k): n for k, n in v.patients.items()},
                "acuity": v.acuity, "defer_until": v.defer_until,
                "floor": v.floor, "label": v.label,
            }
            for v in inst.vertices.values()
        ],
        "arcs": arcs,
        "hazard": [
            {"vertex": h.vertex, "start": h.start,
             "spread_factor": h.spread_factor}
            for h in inst.hazard
        ],
    }


def dump(inst: Instance, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(to_dict(inst), indent=2), encoding="utf-8")
