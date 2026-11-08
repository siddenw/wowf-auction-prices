"""Live auction house data for one market (region + ruleset), and price maths."""

from __future__ import annotations

import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .api import Blizzard
from .config import FACTIONS, MARKETS

# Cut taken from the *seller* on a sale; buyers pay the listed price everywhere.
SELLER_CUT = {"alliance": 0.05, "horde": 0.05, "blackwater": 0.15}


@dataclass
class House:
    faction: str
    house_id: int
    last_modified: str | None
    # item_id -> [(unit_price, quantity), ...] sorted cheapest first (buyout listings only)
    books: dict[int, list[tuple[int, int]]] = field(default_factory=dict)
    bid_only: dict[int, int] = field(default_factory=dict)  # item_id -> count of no-buyout auctions

    def stock(self, item_id: int) -> int:
        return sum(q for _, q in self.books.get(item_id, ()))

    def lowest(self, item_id: int) -> int | None:
        book = self.books.get(item_id)
        return book[0][0] if book else None


@dataclass
class Quote:
    faction: str
    lowest: int | None
    stock: int
    listings: int
    total: int | None  # cost to buy `qty` units (or all stock if short)
    bought: int  # units that `total` covers
    market: int | None  # mean unit price of the cheapest 15% of stock (ignores single outliers)
    bid_only: int

    @property
    def per_unit(self) -> float | None:
        return self.total / self.bought if self.total is not None and self.bought else None


def buy_cost(book: list[tuple[int, int]], qty: int) -> tuple[int, int]:
    """Walk the order book cheapest-first. Returns (total_copper, units_bought).

    Listings must be bought whole, so this can overshoot `qty` by part of a stack:
    the total covers every unit you'd actually have to buy.
    """
    total = bought = 0
    for unit, q in book:
        if bought >= qty:
            break
        total += unit * q
        bought += q
    return total, bought


def market_price(book: list[tuple[int, int]], fraction: float = 0.15) -> int | None:
    stock = sum(q for _, q in book)
    if not stock:
        return None
    want = max(1, int(stock * fraction))
    total = got = 0
    for unit, q in book:
        take = min(q, want - got)
        total += unit * take
        got += take
        if got >= want:
            break
    return round(total / got)


def _index(house: House, auctions: list[dict], unit_key: str) -> None:
    for a in auctions:
        item_id = a["item"]["id"]
        qty = a.get("quantity", 1) or 1
        if unit_key == "unit_price":
            unit = a.get("unit_price")
        else:
            buyout = a.get("buyout")
            unit = buyout // qty if buyout else None
        if not unit:
            house.bid_only[item_id] = house.bid_only.get(item_id, 0) + 1
            continue
        house.books.setdefault(item_id, []).append((unit, qty))


class Market:
    def __init__(self, api: Blizzard, ruleset: str):
        self.api = api
        self.region = api.region
        self.ruleset = ruleset
        self.houses: dict[str, House] = {}
        self.loaded_at = 0.0
        self._listed: dict[int, int] | None = None

    def _index_file(self):
        return self.api.cache_dir / f"{self.region}-{self.ruleset}-index.pickle"

    def load(self, max_age: float = 300) -> "Market":
        ids = MARKETS[self.ruleset]

        def fetch(job: tuple[str, str], need_body: bool):
            faction, feed = job
            hid = ids[faction]
            suffix = "/commodities" if feed == "commodities" else ""
            data, lm, _ = self.api.get_cached(
                f"{self.region}-house-{hid}-{feed}", f"/data/wow/ruleset/2/auctions/{hid}{suffix}",
                "dynamic-forever", max_age, need_body=need_body,
            )
            return faction, feed, data, lm

        jobs = [(f, feed) for f in FACTIONS for feed in ("items", "commodities")]
        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            first = list(ex.map(lambda j: fetch(j, False), jobs))
        stamp = [(f, feed, lm) for f, feed, _, lm in first]

        # Parsing ~15 MB of JSON is the slow part; reuse the built index while the data is unchanged.
        try:
            cached = pickle.loads(self._index_file().read_bytes())
            if cached["stamp"] == stamp:
                self._set(cached["houses"])
                return self
        except (OSError, pickle.PickleError, KeyError, EOFError, AttributeError):
            pass

        houses = {f: House(f, ids[f], None) for f in FACTIONS}
        for faction, feed, data, lm in first:
            if data is None:  # unchanged: read the cached body from disk
                _, _, data, _ = fetch((faction, feed), True)
            h = houses[faction]
            h.last_modified = h.last_modified or lm
            # Both feeds put their listings under "auctions" (the commodities feed too).
            _index(h, data.get("auctions") or [], "unit_price" if feed == "commodities" else "buyout")
        for h in houses.values():
            for book in h.books.values():
                book.sort()
        try:
            self._index_file().write_bytes(pickle.dumps({"stamp": stamp, "houses": houses}, protocol=pickle.HIGHEST_PROTOCOL))
        except OSError:
            pass
        self._set(houses)
        return self

    def _set(self, houses: dict[str, House]) -> None:
        self.houses = houses
        self._listed = None
        self.loaded_at = time.time()

    def refresh(self, max_age: float = 300) -> bool:
        """Revalidate (cheap 304s) and reload only if Blizzard published new data. Returns True if reloaded."""
        if not self.houses:
            self.load(max_age)
            return True
        if time.time() - self.loaded_at < max_age:
            return False
        ids = MARKETS[self.ruleset]
        changed = False
        for faction in FACTIONS:
            hid = ids[faction]
            for feed, suffix in (("items", ""), ("commodities", "/commodities")):
                _, _, ch = self.api.get_cached(
                    f"{self.region}-house-{hid}-{feed}", f"/data/wow/ruleset/2/auctions/{hid}{suffix}",
                    "dynamic-forever", max_age=0, need_body=False,
                )
                changed |= ch
        if changed:
            self.load(max_age=float("inf"))  # bodies were just refreshed on disk
        else:
            self.loaded_at = time.time()
        return changed

    def listed_ids(self) -> dict[int, int]:
        """item_id -> number of listings across all houses (cached until the next reload)."""
        if self._listed is None:
            counts: dict[int, int] = {}
            for h in self.houses.values():
                for item_id, book in h.books.items():
                    counts[item_id] = counts.get(item_id, 0) + len(book)
            self._listed = counts
        return self._listed

    def quotes(self, item_id: int, qty: int = 1) -> list[Quote]:
        out = []
        for faction in FACTIONS:
            h = self.houses[faction]
            book = h.books.get(item_id, [])
            total, bought = buy_cost(book, qty) if book else (None, 0)
            out.append(
                Quote(
                    faction=faction,
                    lowest=book[0][0] if book else None,
                    stock=sum(q for _, q in book),
                    listings=len(book),
                    total=total,
                    bought=bought,
                    market=market_price(book),
                    bid_only=h.bid_only.get(item_id, 0),
                )
            )
        return out
