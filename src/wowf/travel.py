"""Travel cost from where you are to each auction house.

Flight costs and route lengths come from the Classic Era flight network (data/taxi.json,
see scripts/build_taxi.py). WoW Forever has added zones and may have changed flights,
so treat the numbers as good estimates, not exact figures.
"""

from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources

from rapidfuzz import fuzz, process

FLIGHT_SPEED = 32.0  # yards per second, classic taxi
HOP_PENALTY = 15.0  # seconds lost at each intermediate flight master
FACTION_BIT = {"alliance": 1, "horde": 2}

# Free transports (zeppelins, boats, tram) between flight-master nodes: (a, b, minutes incl. average wait)
TRANSPORTS = {
    "horde": [(23, 11, 5), (23, 20, 5), (11, 20, 5), (18, 80, 6)],  # Org<->UC, Org<->Grom'gol, UC<->Grom'gol, BB<->Ratchet
    "alliance": [(2, 6, 3), (7, 32, 6), (7, 26, 6), (26, 27, 4), (19, 80, 6)],  # tram, Menethil boats, Darnassus boat, BB<->Ratchet
}

# Auction house locations, by flight node. Every Alliance city shares one house, as does every Horde city.
# Blackwater (neutral) is assumed to be the goblin towns, as in Classic. This isn't confirmed for Forever yet.
HOUSE_NODES = {
    "horde": [23, 11, 22],  # Orgrimmar, Undercity, Thunder Bluff
    "alliance": [2, 6, 27],  # Stormwind, Ironforge, Darnassus (Rut'theran)
    "blackwater": [18, 19, 40, 39, 53, 52],  # Booty Bay, Gadgetzan, Everlook (Horde + Alliance masters)
}
HOME_NODE = {"horde": 23, "alliance": 2}
ALIASES = {
    "org": 23, "og": 23, "orgrimmar": 23, "uc": 11, "undercity": 11, "tb": 22, "thunder bluff": 22,
    "xr": 25, "xroads": 25, "bb": (18, 19), "gadget": (40, 39), "sw": 2, "stormwind": 2, "if": 6, "ironforge": 6,
    "darnassus": 27, "darn": 27,
}


@dataclass
class Trip:
    cost: int  # copper spent on flights
    seconds: float
    dest: int | None  # node id of the auctioneer you travel to
    via: list[int] = field(default_factory=list)  # node ids along the route

    @property
    def minutes(self) -> float:
        return self.seconds / 60

    def total(self, copper_per_hour: int) -> float:
        return self.cost + self.seconds / 3600 * copper_per_hour


@cache
def _data() -> dict:
    raw = json.loads(resources.files("wowf").joinpath("data/taxi.json").read_text())
    raw["nodes"] = {int(k): v for k, v in raw["nodes"].items()}
    return raw


def source() -> str:
    return _data()["source"]


def node_name(node_id: int) -> str:
    return _data()["nodes"][node_id]["name"]


def short_name(node_id: int) -> str:
    return node_name(node_id).split(",")[0]


def usable_nodes(faction: str) -> dict[int, dict]:
    bit = FACTION_BIT[faction]
    return {i: n for i, n in _data()["nodes"].items() if n["flags"] & bit}


@cache
def _graph(faction: str) -> dict[int, list[tuple[int, int, float]]]:
    nodes = usable_nodes(faction)
    g: dict[int, list[tuple[int, int, float]]] = {i: [] for i in nodes}
    for a, b, cost, length in _data()["paths"]:
        if a in nodes and b in nodes:
            g[a].append((b, cost, length / FLIGHT_SPEED))
    for a, b, minutes in TRANSPORTS[faction]:
        if a in nodes and b in nodes:
            g[a].append((b, 0, minutes * 60))
            g[b].append((a, 0, minutes * 60))
    return g


def _cheapest_path(src: int, dst: int, faction: str) -> Trip | None:
    """The route the flight master would sell you: cheapest in gold, faster on ties."""
    g = _graph(faction)
    if src not in g or dst not in g:
        return None
    if src == dst:
        return Trip(0, 0, src, [src])
    heap: list = [(0, 0.0, src, [src])]
    done: set[int] = set()
    while heap:
        cost, secs, node, path = heapq.heappop(heap)
        if node == dst:
            return Trip(cost, secs, node, path)
        if node in done:
            continue
        done.add(node)
        for nxt, c, s in g[node]:
            if nxt not in done:
                hop = HOP_PENALTY if len(path) > 1 else 0  # landing/take-off at intermediate nodes
                heapq.heappush(heap, (cost + c, secs + s + hop, nxt, path + [nxt]))
    return None


def route(src: int, dests: list[int], faction: str, copper_per_hour: int = 0) -> Trip | None:
    """Best of the given destinations, valuing time at copper_per_hour."""
    trips = [t for d in dests if (t := _cheapest_path(src, d, faction))]
    return min(trips, key=lambda t: (t.total(copper_per_hour), t.seconds), default=None)


def trip_to_house(src: int, house: str, faction: str, copper_per_hour: int = 0, round_trip: bool = False) -> Trip | None:
    there = route(src, HOUSE_NODES[house], faction, copper_per_hour)
    if there is None or not round_trip or there.dest == src:
        return there
    back = route(there.dest, [src], faction, copper_per_hour)
    if back is None:
        return there
    return Trip(there.cost + back.cost, there.seconds + back.seconds, there.dest, there.via + back.via[1:])


def find_node(query: str, faction: str) -> int:
    """Fuzzy-match a place name ('org', 'gadget', 'booty bay') to a flight node your faction can use."""
    nodes = usable_nodes(faction)
    q = query.strip().lower()
    if re.fullmatch(r"\d+", q) and int(q) in nodes:
        return int(q)
    alias = ALIASES.get(q)
    for a in (alias if isinstance(alias, tuple) else (alias,)):
        if a in nodes:
            return a
    choices = {i: n["name"].lower() for i, n in nodes.items()}
    prefix = [i for i, n in choices.items() if n.startswith(q) or any(w.startswith(q) for w in re.split(r"[ ,']+", n))]
    if prefix:
        # Prefer the town name over the zone ("barrens" -> Crossroads before Camp Taurajo is fine either way)
        return min(prefix, key=lambda i: (not choices[i].startswith(q), len(choices[i])))
    hit = process.extractOne(q, choices, scorer=fuzz.WRatio, score_cutoff=60)
    if not hit:
        raise ValueError(f"no {faction} flight point matches {query!r}")
    return hit[2]


def place_names(faction: str) -> list[str]:
    return sorted(n["name"] for n in usable_nodes(faction).values())


@dataclass
class Tour:
    cost: int
    seconds: float
    via: list[int]
    stops: dict[str, int]  # house -> node where you buy

    def total(self, copper_per_hour: int) -> float:
        return self.cost + self.seconds / 3600 * copper_per_hour

    @property
    def minutes(self) -> float:
        return self.seconds / 60


def tour(src: int, houses: list[str], faction: str, copper_per_hour: int = 0, round_trip: bool = False) -> Tour | None:
    """Cheapest order to visit every house in `houses`, starting from src."""
    from itertools import permutations

    best: Tour | None = None
    for order in permutations(houses):
        cur, cost, secs, via, stops = src, 0, 0.0, [src], {}
        for h in order:
            leg = route(cur, HOUSE_NODES[h], faction, copper_per_hour)
            if leg is None:
                break
            cost, secs, cur = cost + leg.cost, secs + leg.seconds, leg.dest
            via += leg.via[1:]
            stops[h] = leg.dest
        else:
            if round_trip and cur != src:
                back = _cheapest_path(cur, src, faction)
                if back is None:
                    continue
                cost, secs = cost + back.cost, secs + back.seconds
                via += back.via[1:]
            t = Tour(cost, secs, via, stops)
            if best is None or t.total(copper_per_hour) < best.total(copper_per_hour):
                best = t
    return best
