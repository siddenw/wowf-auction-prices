"""Saved buy lists and multi-house purchase planning."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import combinations

from . import travel, vendor
from .config import CONFIG_DIR
from .market import Market

LISTS_FILE = CONFIG_DIR / "lists.json"


class BuyLists:
    def __init__(self) -> None:
        try:
            data = json.loads(LISTS_FILE.read_text())
        except (OSError, ValueError):
            data = {}
        self.active: str = data.get("active", "default")
        self.lists: dict[str, dict[int, int]] = {
            name: {int(k): int(v) for k, v in items.items()} for name, items in data.get("lists", {}).items()
        }
        self.lists.setdefault(self.active, {})

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LISTS_FILE.write_text(json.dumps({"active": self.active, "lists": self.lists}, indent=2))

    @property
    def items(self) -> dict[int, int]:
        return self.lists.setdefault(self.active, {})

    def add(self, item_id: int, qty: int) -> int:
        self.items[item_id] = self.items.get(item_id, 0) + qty
        self.save()
        return self.items[item_id]

    def remove(self, item_id: int, qty: int | None = None) -> int:
        """Remove qty (or all). Returns what's left."""
        left = 0 if qty is None else max(0, self.items.get(item_id, 0) - qty)
        if left:
            self.items[item_id] = left
        else:
            self.items.pop(item_id, None)
        self.save()
        return left

    def clear(self) -> None:
        self.items.clear()
        self.save()

    def use(self, name: str) -> None:
        self.active = name
        self.lists.setdefault(name, {})
        self.save()


@dataclass
class Purchase:
    item_id: int
    house: str
    units: int
    cost: int


@dataclass
class Plan:
    houses: tuple[str, ...]  # auction houses visited (vendor purchases need no trip)
    purchases: list[Purchase]
    goods: int
    trip: travel.Tour | None
    total: float
    short: dict[int, int] = field(default_factory=dict)  # item_id -> units still missing

    def by_house(self) -> dict[str, list[Purchase]]:
        out: dict[str, list[Purchase]] = {}
        for p in self.purchases:
            out.setdefault(p.house, []).append(p)
        return out


def allocate(market: Market, wanted: dict[int, int], houses: tuple[str, ...]) -> tuple[list[Purchase], dict[int, int]]:
    """Buy each item from the cheapest listings across `houses` plus NPC vendors.

    AH listings are bought whole and may split an item across houses; vendors sell in lots
    and never run out (they count as house "vendor", with no travel).
    """
    purchases: list[Purchase] = []
    short: dict[int, int] = {}
    for item_id, qty in wanted.items():
        # (unit price, units, house, cost of taking this entry)
        book = [(unit, q, h, unit * q) for h in houses for unit, q in market.houses[h].books.get(item_id, ())]
        v = vendor.get(item_id)
        if v:
            book += [(v.unit, v.lot, "vendor", v.price)] * -(-qty // v.lot)
        book.sort(key=lambda e: e[0])
        per_house: dict[str, list[int]] = {}
        got = 0
        for unit, q, h, cost in book:
            if got >= qty:
                break
            acc = per_house.setdefault(h, [0, 0])
            acc[0] += q
            acc[1] += cost
            got += q
        for h, (units, cost) in per_house.items():
            purchases.append(Purchase(item_id, h, units, cost))
        if got < qty:
            short[item_id] = qty - got
    return purchases, short


def plan(
    market: Market, wanted: dict[int, int], usable: list[str], faction: str | None, location: int,
    copper_per_hour: int = 0, round_trip: bool = False,
) -> list[Plan]:
    """Every combination of houses to visit, best first. Plans that leave items unbought rank last."""
    plans: list[Plan] = []
    for n in range(0, len(usable) + 1):  # 0 = vendors only, no AH trip
        for houses in combinations(usable, n):
            purchases, short = allocate(market, wanted, houses)
            used = tuple(h for h in houses if any(p.house == h for p in purchases))
            if used != houses:
                continue  # a house you'd visit but buy nothing at: the smaller combination covers it
            goods = sum(p.cost for p in purchases)
            trip = travel.tour(location, list(houses), faction, copper_per_hour, round_trip) if faction else None
            if faction and trip is None:
                continue
            total = goods + (trip.total(copper_per_hour) if trip else 0)
            plans.append(Plan(houses, purchases, goods, trip, total, short))
    # Get everything if at all possible, then cheapest.
    plans.sort(key=lambda p: (sum(p.short.values()), p.total))
    return plans
