"""NPC vendors: what they sell, where they stand, and which one is nearest to you.

Data: data/vendor.json, built by scripts/build_vendor.py from CMaNGOS classic-db (GPL-3.0)
and Classic Era map tables. Only unlimited-stock items are included. Vendors sell in lots
(e.g. Empty Vials come 5 for 20c), so buying 7 means buying 10.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from importlib import resources

from . import travel

RUN_SPEED = 7.0  # yards/second on foot (unmounted)
PATH_FACTOR = 1.3  # real paths are longer than a straight line
MAX_WALK = 1200.0  # beyond this (straight-line yards) we assume you fly closer first
FACTION_MASK = {"alliance": 1, "horde": 2}


@dataclass(frozen=True)
class Spawn:
    map: int
    x: int
    y: int
    zone: str
    zx: float  # in-game map coordinates (0-100)
    zy: float

    @property
    def where(self) -> str:
        return f"{self.zone} {self.zx:.1f}, {self.zy:.1f}" if self.zone else f"map {self.map} ({self.x}, {self.y})"


@dataclass(frozen=True)
class Vendor:
    id: int
    name: str
    title: str
    mask: int  # 1 = Alliance can trade, 2 = Horde can trade
    spawns: tuple[Spawn, ...]

    @property
    def label(self) -> str:
        return f"{self.name} <{self.title}>" if self.title else self.name


@dataclass(frozen=True)
class VendorItem:
    item_id: int
    price: int  # copper per lot
    lot: int  # units per lot
    vendors: tuple[int, ...]

    @property
    def unit(self) -> float:
        return self.price / self.lot

    def cost(self, qty: int) -> tuple[int, int]:
        """(copper, units actually bought) for at least qty units."""
        lots = -(-max(qty, 1) // self.lot)
        return lots * self.price, lots * self.lot


@dataclass
class Visit:
    """Getting to one vendor: fly (optional) then walk."""
    vendor: Vendor
    spawn: Spawn
    cost: int  # copper for flights
    seconds: float
    via: list[int]  # flight nodes, [] when you just walk
    walk_yards: float

    @property
    def minutes(self) -> float:
        return self.seconds / 60

    def total(self, copper_per_hour: int) -> float:
        return self.cost + self.seconds / 3600 * copper_per_hour

    def as_trip(self, src: int) -> travel.Trip:
        return travel.Trip(self.cost, self.seconds, self.via[-1] if self.via else src, self.via or [src])


@cache
def _data() -> tuple[dict[int, Vendor], dict[int, VendorItem], str]:
    raw = json.loads(resources.files("wowf").joinpath("data/vendor.json").read_text())
    vendors = {
        int(k): Vendor(int(k), name, title, mask, tuple(Spawn(*s) for s in spawns))
        for k, (name, title, mask, spawns) in raw["vendors"].items()
    }
    items = {int(k): VendorItem(int(k), p, lot, tuple(vs)) for k, (p, lot, vs) in raw["items"].items()}
    return vendors, items, raw.get("source", "")


def items() -> dict[int, VendorItem]:
    return _data()[1]


def get(item_id: int) -> VendorItem | None:
    return _data()[1].get(item_id)


def vendor(vendor_id: int) -> Vendor:
    return _data()[0][vendor_id]


def source() -> str:
    return _data()[2]


def _nearest_node(spawn: Spawn, faction: str) -> tuple[int, float] | None:
    best = None
    for nid, n in travel.usable_nodes(faction).items():
        if n["continent"] != spawn.map:
            continue
        d = math.dist((n["x"], n["y"]), (spawn.x, spawn.y))
        if best is None or d < best[1]:
            best = (nid, d)
    return best


@cache
def _visit_parts(src: int, vendor_id: int, faction: str) -> list[tuple[Spawn, int, float, list[int], float]]:
    """Every way to reach each spawn: (spawn, flight cost, seconds, via, walk yards)."""
    loc = travel._data()["nodes"][src]
    out = []
    for sp in vendor(vendor_id).spawns:
        if sp.map == loc["continent"]:  # just walk
            d = math.dist((loc["x"], loc["y"]), (sp.x, sp.y))
            if d <= MAX_WALK:
                out.append((sp, 0, d * PATH_FACTOR / RUN_SPEED, [], d))
        near = _nearest_node(sp, faction)
        if near and near[0] != src:
            trip = travel._cheapest_path(src, near[0], faction)
            if trip:
                out.append((sp, trip.cost, trip.seconds + near[1] * PATH_FACTOR / RUN_SPEED, trip.via, near[1]))
    return out


def visits(item_id: int, faction: str, src: int, copper_per_hour: int = 0) -> list[Visit]:
    """Vendors your faction can use that sell item_id, quickest to reach first.

    With a time value set, the gold spent on flights counts too.
    """
    vi = get(item_id)
    if vi is None:
        return []
    bit = FACTION_MASK[faction]
    best: dict[int, Visit] = {}
    for vid in vi.vendors:
        v = vendor(vid)
        if not v.mask & bit:
            continue
        for sp, cost, secs, via, walk in _visit_parts(src, vid, faction):
            visit = Visit(v, sp, cost, secs, via, walk)
            cur = best.get(vid)
            if cur is None or (visit.total(copper_per_hour), visit.seconds) < (cur.total(copper_per_hour), cur.seconds):
                best[vid] = visit
    return sorted(best.values(), key=lambda x: (x.total(copper_per_hour) if copper_per_hour else x.seconds, x.seconds))


def nearest(item_id: int, faction: str, src: int, copper_per_hour: int = 0) -> Visit | None:
    vs = visits(item_id, faction, src, copper_per_hour)
    return vs[0] if vs else None
