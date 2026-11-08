"""Local item-name catalogue with fuzzy search.

Forever's own item data is missing a lot of Classic-era items (about 40% of the IDs seen on the AH),
so the catalogue is built from the Forever item search, with the Classic Era namespace filling the gaps.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Callable

from rapidfuzz import fuzz, process

from .api import Blizzard

CATALOG_VERSION = 1
SOURCES = ("static-forever", "static-classic1x")  # first wins
QUALITY_STYLE = {
    "POOR": "grey50", "COMMON": "white", "UNCOMMON": "green3", "RARE": "dodger_blue1",
    "EPIC": "medium_purple1", "LEGENDARY": "dark_orange", "ARTIFACT": "light_goldenrod2", "HEIRLOOM": "light_sky_blue1",
}


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    quality: str = "COMMON"
    kind: str = ""
    stackable: bool = False
    vendor: int = 0  # what a vendor pays you, per unit

    @property
    def style(self) -> str:
        return QUALITY_STYLE.get(self.quality, "white")


def _sweep(api: Blizzard, namespace: str, progress: Callable[[str], None]) -> dict[int, list]:
    out: dict[int, list] = {}
    start = 1
    while True:
        data = api.get_json(
            "/data/wow/search/item", namespace,
            {"id": f"[{start},99999999]", "orderby": "id", "_pageSize": 1000, "_page": 1},
        )
        results = data.get("results") or []
        for r in results:
            d = r["data"]
            names = d.get("name") or {}
            name = names.get(api.locale) or names.get("en_US") or next(iter(names.values()), None)
            if not name:
                continue
            out[d["id"]] = [
                name,
                (d.get("quality") or {}).get("type", "COMMON"),
                ((d.get("item_class") or {}).get("name") or {}).get(api.locale, ""),
                bool(d.get("is_stackable")),
                d.get("sell_price", 0) or 0,
            ]
        progress(f"{namespace}: {len(out):,} items (id {start:,}+)")
        if len(results) < 1000:
            return out
        start = results[-1]["data"]["id"] + 1


class Catalog:
    def __init__(self, items: dict[int, Item]):
        self.items = items
        self._choices = {i: it.name.lower() for i, it in items.items()}

    # -- persistence --------------------------------------------------------
    @staticmethod
    def path(api: Blizzard):
        return api.cache_dir / f"items-{api.region}.json.gz"

    @classmethod
    def load(cls, api: Blizzard) -> "Catalog | None":
        try:
            raw = json.loads(gzip.decompress(cls.path(api).read_bytes()))
        except (OSError, ValueError):
            return None
        if raw.get("version") != CATALOG_VERSION:
            return None
        return cls({int(k): Item(int(k), *v) for k, v in raw["items"].items()})

    @classmethod
    def build(cls, api: Blizzard, progress: Callable[[str], None] = lambda _: None) -> "Catalog":
        merged: dict[int, list] = {}
        for ns in reversed(SOURCES):  # later (preferred) sources overwrite earlier ones
            merged.update(_sweep(api, ns, progress))
        payload = {"version": CATALOG_VERSION, "built_at": time.time(), "items": merged}
        cls.path(api).write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode()))
        return cls({k: Item(k, *v) for k, v in merged.items()})

    # -- lookup -------------------------------------------------------------
    def get(self, item_id: int) -> Item:
        return self.items.get(item_id) or Item(item_id, f"Item #{item_id}", "COMMON")

    def add_unknown(self, ids) -> None:
        """Make AH items missing from both namespaces searchable by their ID."""
        for i in ids:
            if i not in self.items:
                self.items[i] = Item(i, f"Item #{i}")
                self._choices[i] = f"item #{i}"

    def search(self, query: str, limit: int = 10, listed: "set[int] | dict[int, int] | None" = None) -> list[Item]:
        """Fuzzy search. `listed` boosts items on the AH; pass {item_id: listings} to also favour busy items."""
        q = " ".join(query.lower().split())
        if not q:
            return []
        m = re.fullmatch(r"#?(\d+)", q)
        if m and int(m.group(1)) in self.items:
            return [self.items[int(m.group(1))]]
        words = q.split()
        hits = process.extract(q, self._choices, scorer=fuzz.WRatio, limit=300, score_cutoff=55)
        # rapidfuzz may miss strong prefix matches on long lists; add them explicitly
        prefix_ids = [i for i, n in self._choices.items() if n.startswith(q)][:200] if len(q) >= 2 else []
        scored: dict[int, float] = {}
        for name, score, item_id in hits + [(self._choices[i], 0, i) for i in prefix_ids]:
            s = float(score) or fuzz.WRatio(q, name)
            if name == q:
                s += 100
            elif name.startswith(q):
                s += 30
            elif all(any(t.startswith(w) for t in name.split()) for w in words):
                s += 22  # "lin clo" -> "linen cloth"
            elif q in name:
                s += 10
            if listed is not None and item_id in listed:
                s += 20
                if isinstance(listed, dict):
                    s += min(8.0, math.log10(listed[item_id] + 1) * 2)  # popular items win ties
            s -= len(name) / 40  # prefer shorter, more specific names on ties
            scored[item_id] = max(scored.get(item_id, -1e9), s)
        # Collapse duplicate names (Classic data has many), keeping the best-scored copy.
        best_by_name: dict[str, tuple[float, int]] = {}
        for item_id, s in scored.items():
            key = self._choices[item_id]
            if key not in best_by_name or s > best_by_name[key][0]:
                best_by_name[key] = (s, item_id)
        ranked = sorted(best_by_name.values(), key=lambda t: -t[0])
        return [self.items[i] for _, i in ranked[:limit]]
