"""Reagents sold by NPC vendors (data/vendor.json, built by scripts/build_vendor.py).

Vendors sell in lots (e.g. Empty Vials come 5 for 20c), so buying 7 means buying 2 lots.
They're assumed to be available in every capital and most towns, so there's no travel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True)
class VendorItem:
    item_id: int
    name: str
    price: int  # copper per lot
    lot: int  # units per lot

    @property
    def unit(self) -> float:
        return self.price / self.lot

    def cost(self, qty: int) -> tuple[int, int]:
        """(copper, units actually bought) for at least qty units."""
        lots = -(-max(qty, 1) // self.lot)
        return lots * self.price, lots * self.lot


@cache
def items() -> dict[int, VendorItem]:
    raw = json.loads(resources.files("wowf").joinpath("data/vendor.json").read_text())
    return {int(k): VendorItem(int(k), v["name"], v["price"], v["lot"]) for k, v in raw.items()}


def get(item_id: int) -> VendorItem | None:
    return items().get(item_id)
