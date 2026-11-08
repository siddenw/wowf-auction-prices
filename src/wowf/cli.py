"""wowf: find the cheapest WoW Forever auction house for an item.

  wowf                          interactive search with fuzzy autocomplete
  wowf linen cloth              price check (fuzzy, typos ok)
  wowf 40 linen                 cost to buy 40
  wowf search copper            list matching items with their best price
  wowf config faction horde     save defaults (region, ruleset, faction)
  wowf houses                   market overview and data freshness
  wowf at booty bay             set where you are (any flight point; travel cost is added)
  wowf travel                   travel cost from your location to each house
  wowf config time 20g          value your time (per hour) so long trips cost more
  wowf config return fly        count the flight back too (default: hearth)
  wowf update-items             rebuild the item-name catalogue
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from email.utils import parsedate_to_datetime

from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import money
from .api import ApiError, Blizzard
from .catalog import Catalog, Item
from .config import (
    FACTIONS, MARKET_LABELS, MARKETS, REGION_MARKETS, REGIONS, Settings,
    normalise_faction, normalise_region, normalise_ruleset,
)
from . import buylist, vendor
from . import travel as travel_mod
from .market import SELLER_CUT, Market, Quote

console = Console(highlight=False)
REFRESH_AFTER = 300  # seconds before re-checking Blizzard for new hourly data
HOUSE_LABEL = {"alliance": "Alliance AH", "horde": "Horde AH", "blackwater": "Blackwater (neutral)", "vendor": "Vendor"}
VENDOR_STOCK = 10**9  # vendors never run out
QTY_RE = [re.compile(r"^(\d+)\s*x?\s+(.+)$", re.I), re.compile(r"^(.+?)\s+x\s*(\d+)$", re.I)]


def parse_qty(text: str) -> tuple[int, str]:
    text = text.strip()
    m = QTY_RE[0].match(text)
    if m and not text.isdigit():
        return max(1, int(m.group(1))), m.group(2)
    m = QTY_RE[1].match(text)
    if m:
        return max(1, int(m.group(2))), m.group(1)
    return 1, text


def vendor_quote(item_id: int, qty: int, faction: str | None = None) -> Quote | None:
    """A Quote for buying from an NPC vendor, or None if no vendor you can use sells it."""
    v = vendor.get(item_id)
    if v is None:
        return None
    if faction and not any(vendor.vendor(vid).mask & vendor.FACTION_MASK[faction] for vid in v.vendors):
        return None
    total, bought = v.cost(qty)
    return Quote("vendor", round(v.unit), VENDOR_STOCK, 0, total, bought, round(v.unit), 0)


def all_quotes(m: Market, item_id: int, qty: int = 1, faction: str | None = None) -> list[Quote]:
    quotes = m.quotes(item_id, qty)
    vq = vendor_quote(item_id, qty, faction)
    return quotes + [vq] if vq else quotes


def fmt_visit(v: "vendor.Visit", rich: bool = True) -> str:
    """How to reach a vendor: 'walk 1 min' or 'fly Kargath > Hammerfall (6s 30c), walk 37 yd · 4 min'."""
    mins = max(1, round(v.minutes))
    if not v.via:
        return f"walk {mins} min"
    route = " > ".join(travel_mod.short_name(n) for n in v.via)
    return f"fly {route} ({money.fmt(v.cost, rich)}), walk {v.walk_yards * vendor.PATH_FACTOR:.0f} yd · {mins} min"


def fmt_trip(trip) -> str:
    if trip.cost == 0 and trip.seconds == 0:
        return "[green]here[/]"
    return f"{money.fmt(trip.cost)} · {max(1, round(trip.minutes))}m"


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._api: Blizzard | None = None
        self._catalog: Catalog | None = None
        self._market: Market | None = None

    # -- lazy resources -----------------------------------------------------
    @property
    def api(self) -> Blizzard:
        if self._api is None or self._api.region != self.settings.region:
            self._api = Blizzard(self.settings.region)
            self._catalog = None
            self._market = None
        return self._api

    @property
    def catalog(self) -> Catalog:
        api = self.api
        if self._catalog is None:
            self._catalog = Catalog.load(api)
            if self._catalog is None:
                console.print("[cyan]First run: downloading the item catalogue (one-off, about a minute)...[/]")
                with console.status("") as status:
                    self._catalog = Catalog.build(api, lambda msg: status.update(msg))
                console.print(f"[cyan]Catalogue ready: {len(self._catalog.items):,} items.[/]")
        return self._catalog

    @property
    def market(self) -> Market:
        api = self.api
        if self._market is None or self._market.ruleset != self.settings.ruleset:
            with console.status(f"Fetching {self.market_name()} auction houses..."):
                self._market = Market(api, self.settings.ruleset).load()
            self.catalog.add_unknown(self._market.listed_ids())
        elif time.time() - self._market.loaded_at >= REFRESH_AFTER:
            with console.status("Checking for new auction data..."):
                if self._market.refresh(REFRESH_AFTER):
                    self.catalog.add_unknown(self._market.listed_ids())
        return self._market

    def market_name(self) -> str:
        return f"{self.settings.region.upper()} {MARKET_LABELS[self.settings.ruleset]}"

    def location(self) -> int:
        f = self.settings.faction or "horde"
        loc = self.settings.location
        if loc is None or loc not in travel_mod.usable_nodes(f):
            return travel_mod.HOME_NODE[f]
        return loc

    def trips(self) -> dict | None:
        """Travel from your location to each house you can use; None when no faction is set."""
        f = self.settings.faction
        if f is None:
            return None
        s = self.settings
        key = (f, self.location(), s.time_value, s.round_trip)
        if getattr(self, "_trips_key", None) != key:
            self._trips = {
                h: travel_mod.trip_to_house(self.location(), h, f, s.time_value, s.round_trip)
                for h in FACTIONS if self.usable(h)
            }
            self._trips_key = key
        return self._trips

    # -- buy lists ------------------------------------------------------------
    @property
    def lists(self) -> buylist.BuyLists:
        if getattr(self, "_lists", None) is None:
            self._lists = buylist.BuyLists()
        return self._lists

    def list_add(self, text: str) -> None:
        qty, query = parse_qty(text)
        item, _ = self.resolve(query)
        if item is None:
            console.print(f"[red]No item matches[/] {query!r}.")
            return
        total = self.lists.add(item.id, qty)
        console.print(f"Added {qty} [{item.style}]{item.name}[/] -> {total} on '{self.lists.active}'.")

    def list_remove(self, text: str) -> None:
        qty, query = parse_qty(text)
        explicit = text.strip()[:1].isdigit()
        on_list = {i: self.catalog.get(i) for i in self.lists.items}
        if not on_list:
            console.print("The list is empty.")
            return
        hits = Catalog(on_list).search(query, limit=1)
        if not hits:
            console.print(f"[red]Nothing on the list matches[/] {query!r}.")
            return
        item = hits[0]
        left = self.lists.remove(item.id, qty if explicit else None)
        console.print(f"Removed [{item.style}]{item.name}[/]" + (f" ({left} left)." if left else "."))

    def show_list(self) -> None:
        bl = self.lists
        if not bl.items:
            console.print(f"List '{bl.active}' is empty. Add things with [bold]/add 20 linen cloth[/].")
            return
        m = self.market
        s = self.settings
        usable = [h for h in FACTIONS if self.usable(h)]
        plans = buylist.plan(m, bl.items, usable, s.faction, self.location(), s.time_value, s.round_trip)
        if not plans:
            console.print("[red]No way to buy this list from here.[/]")
            return
        best = plans[0]

        console.print()
        console.print(f"[bold]Buy list '{bl.active}'[/] · {len(bl.items)} items · {self.market_name()}")
        t = Table(box=None, pad_edge=False, padding=(0, 2), header_style="bold dim")
        for col, just in (("Item", "left"), ("Want", "right"), ("Buy at", "left"), ("Units", "right"),
                          ("Cost", "right"), ("Each", "right")):
            t.add_column(col, justify=just)
        by_house = best.by_house()
        order = (["vendor"] if "vendor" in by_house else []) + (list(best.trip.stops) if best.trip else list(best.houses))
        for h in order:
            where = "Vendor" if h == "vendor" else h.capitalize()
            if best.trip and best.trip.stops.get(h) is not None:
                where += f" · {travel_mod.short_name(best.trip.stops[h])}"
            for p in by_house.get(h, []):
                it = self.catalog.get(p.item_id)
                at = where
                if h == "vendor" and (v := self.vendor_visit(p.item_id)):
                    at = f"Vendor · {v.vendor.name}, {v.spawn.where}"
                t.add_row(f"[{it.style}]{it.name}[/]", str(bl.items[p.item_id]), at, f"{p.units:,}",
                          money.fmt(p.cost), money.fmt(p.cost / p.units))
        bought = {p.item_id for p in best.purchases}
        for item_id, qty in bl.items.items():
            if item_id not in bought:
                it = self.catalog.get(item_id)
                t.add_row(f"[{it.style}]{it.name}[/]", str(qty), "[yellow]not listed[/]", "", "", "")
        console.print(t)
        for item_id, missing in best.short.items():
            if item_id in bought:
                console.print(f"  [yellow]Only {bl.items[item_id] - missing} of {bl.items[item_id]} "
                              f"{self.catalog.get(item_id).name} available.[/]")

        def trip_txt(p: buylist.Plan) -> str:
            if not p.trip:
                return ""
            return fmt_trip(p.trip)

        console.print(f"  Goods {money.fmt(best.goods)}"
                      + (f" + travel {trip_txt(best)}" if best.trip else "")
                      + (f" + time {money.fmt(best.trip.total(s.time_value) - best.trip.cost)}"
                         if best.trip and s.time_value else "")
                      + f" = [bold]{money.fmt(best.total)}[/]")

        local = [p for p in plans if p.trip and p.trip.cost == 0 and p.trip.seconds == 0
                 and sum(p.short.values()) == sum(best.short.values())]
        if best.trip and (best.trip.cost or best.trip.seconds):
            if local:
                saved = local[0].total - best.total
                console.print(f"  [green]The trip is worth it:[/] saves [bold]{money.fmt(saved)}[/] vs buying "
                              f"everything at {' + '.join(HOUSE_LABEL[h] for h in local[0].houses) or 'vendors'}.")
            console.print(f"  [dim]Route: {' > '.join(travel_mod.short_name(n) for n in best.trip.via)}"
                          f"{' (round trip)' if s.round_trip else ', then hearth back'}[/]")
        elif best.trip:
            console.print(f"  [green]Buy it all here in {travel_mod.short_name(self.location())}.[/]")

        if len(plans) > 1:
            console.print("  [dim]Other options:[/]")
            for p in plans[1:4]:
                diff = p.total - best.total
                extra = f", {sum(p.short.values())} short" if sum(p.short.values()) > sum(best.short.values()) else ""
                console.print(f"  [dim]  {' + '.join(HOUSE_LABEL[h] for h in p.houses) or 'Vendors only':40} {money.fmt(p.total, rich=False):>14}"
                              f"  (+{money.fmt(diff, rich=False)}{extra}){'  · ' + trip_txt(p).replace('[green]', '').replace('[/]', '') if p.trip else ''}[/]")
        console.print(f"  [dim]{self.freshness()}[/]")

    def vendor_visit(self, item_id: int):
        """Nearest vendor you can use that sells item_id (None without a faction or a vendor)."""
        if not self.settings.faction or not vendor.get(item_id):
            return None
        return vendor.nearest(item_id, self.settings.faction, self.location(), self.settings.time_value)

    def show_vendors(self, query: str, limit: int = 6) -> None:
        item, _ = self.resolve(query)
        if item is None:
            console.print(f"[red]No item matches[/] {query!r}.")
            return
        vi = vendor.get(item.id)
        if vi is None:
            console.print(f"[{item.style}]{item.name}[/] isn't sold by any vendor (in Classic data).")
            return
        if not self.settings.faction:
            console.print("[yellow]Set your faction to find vendors you can use: /faction horde[/]")
            return
        vs = vendor.visits(item.id, self.settings.faction, self.location(), self.settings.time_value)
        each = f"{money.fmt(vi.unit)} each" + (f", sold in {vi.lot}s" if vi.lot > 1 else "")
        console.print(f"\n[bold {item.style}]{item.name}[/]  [dim]#{item.id}[/]  {each}  ·  {len(vs)} vendors you can use")
        t = Table(box=None, pad_edge=False, padding=(0, 2), header_style="bold dim")
        for col in ("Vendor", "Where", "Getting there"):
            t.add_column(col)
        for v in vs[:limit]:
            t.add_row(v.vendor.label, v.spawn.where, fmt_visit(v))
        console.print(t)
        console.print(f"  [dim]From {travel_mod.node_name(self.location())}. NPC data from Classic "
                      "(CMaNGOS), so Forever's vendors may differ.[/]")

    def show_travel(self) -> None:
        trips = self.trips()
        if trips is None:
            console.print("[yellow]Set your faction first: wowf config faction horde[/]")
            return
        s = self.settings
        console.print(
            f"From [bold]{travel_mod.node_name(self.location())}[/]"
            f" · time valued at {money.fmt(s.time_value) + '/h' if s.time_value else 'nothing'}"
            f" · {'round trip' if s.round_trip else 'one way, hearth back'}"
        )
        for h, trip in trips.items():
            if trip is None:
                console.print(f"  {HOUSE_LABEL[h]:22} no route")
            else:
                route = " > ".join(travel_mod.short_name(n) for n in trip.via)
                console.print(f"  {HOUSE_LABEL[h]:22} {fmt_trip(trip):>16}   [dim]{route}[/]")
        console.print(f"  [dim]Flight costs from {travel_mod.source()}; Forever may differ. "
                      "Blackwater assumed at Booty Bay / Gadgetzan / Everlook.[/]")

    def usable(self, faction: str) -> bool:
        mine = self.settings.faction
        return mine is None or faction in (mine, "blackwater", "vendor")

    # -- actions ------------------------------------------------------------
    def resolve(self, query: str) -> tuple[Item | None, list[Item]]:
        listed = self.market.listed_ids()
        hits = self.catalog.search(query, limit=6, listed=listed)
        if not hits:
            return None, []
        return hits[0], hits[1:]

    def price(self, query: str, qty: int | None = None) -> None:
        if qty is None:
            qty, query = parse_qty(query)
        item, others = self.resolve(query)
        if item is None:
            console.print(f"[red]No item matches[/] {query!r}.")
            return
        self.show_item(item, qty)
        if others and item.name.lower() != " ".join(query.lower().split()):
            alts = ", ".join(f"[{o.style}]{o.name}[/]" for o in others[:4])
            console.print(f"[dim]  Also matched: {alts}[/]")

    def show_item(self, item: Item, qty: int) -> None:
        m = self.market
        quotes = all_quotes(m, item.id, qty, self.settings.faction)
        title = Text.assemble((item.name, f"bold {item.style}"), (f"  #{item.id}", "dim"))
        if qty > 1:
            title.append(f"  ×{qty}", "bold")
        console.print()
        console.print(title)
        if not any(q.stock for q in quotes):
            console.print(f"  [yellow]Not listed on any {self.market_name()} auction house right now.[/]")
            if item.vendor:
                console.print(f"  [dim]Vendors buy it for {money.fmt(item.vendor)} each.[/]")
            return

        trips = self.trips()
        visit = self.vendor_visit(item.id)
        if trips is not None:
            trips = {**trips, "vendor": visit.as_trip(self.location()) if visit else None}
        rate = self.settings.time_value

        def goods(q: Quote) -> float:
            return q.total if qty > 1 else q.lowest

        def travel(q: Quote) -> float:
            trip = trips.get(q.faction) if trips else None
            return trip.total(rate) if trip else 0

        def landed(q: Quote) -> float:
            return goods(q) + travel(q)

        candidates = [q for q in quotes if q.stock and self.usable(q.faction) and (not trips or trips.get(q.faction))]
        enough = [q for q in candidates if q.bought >= qty]
        if enough:
            best = min(enough, key=landed)
            cheapest = min(enough, key=goods)
        else:  # nobody has enough: compare per unit across what exists
            best = min(candidates, key=lambda q: landed(q) / q.bought, default=None)
            cheapest = min(candidates, key=lambda q: goods(q) / q.bought, default=None)

        t = Table(box=None, pad_edge=False, padding=(0, 2), header_style="bold dim")
        t.add_column("")
        t.add_column("House")
        t.add_column("Lowest", justify="right")
        t.add_column("Market", justify="right")
        if qty > 1:
            t.add_column(f"Buy {qty}", justify="right")
        t.add_column("Stock", justify="right")
        if trips:
            t.add_column("Travel", justify="right")
            t.add_column("Total", justify="right")
        t.add_column("Notes", style="dim")
        hidden = [q for q in quotes if not self.usable(q.faction)]
        for q in quotes:
            usable = self.usable(q.faction)
            if not usable:
                continue
            trip = trips.get(q.faction) if trips else None
            notes = []
            if not usable:
                notes.append("enemy faction")
            if q.bid_only:
                notes.append(f"{q.bid_only} bid-only")
            if qty > 1 and q.stock and q.bought < qty:
                notes.append(f"only {q.stock}")
            elif qty > 1 and q.bought > qty and q.faction != "vendor":
                notes.append(f"{q.bought} in stacks")
            row = [
                "[green]★[/]" if q is best else "",
                HOUSE_LABEL[q.faction],
                money.fmt(q.lowest),
                money.fmt(q.market),
            ]
            if qty > 1:
                row.append(money.fmt(q.total))
            if q.faction == "vendor":
                row.append("unlimited")
                lot = vendor.get(item.id).lot
                if lot > 1:
                    notes.append(f"sold in {lot}s")
            else:
                row.append(f"{q.stock:,}" if q.stock else "[dim]none[/]")
            if trips:
                if not usable:
                    row += ["", ""]
                elif trip is None:
                    row += ["[dim]no route[/]", ""]
                elif q.faction == "vendor":
                    row += ["here" if not visit.via and visit.minutes < 1 else
                            (f"walk {max(1, round(visit.minutes))}m" if not visit.via else fmt_trip(trip)),
                            money.fmt(landed(q))]
                else:
                    row += [fmt_trip(trip), money.fmt(landed(q)) if q.stock else ""]
            row.append(", ".join(notes))
            t.add_row(*row, style="dim" if not usable or not q.stock else None)
        console.print(t)
        for q in hidden:
            if q.stock and best is not None and goods(q) < goods(best):
                console.print(f"  [dim]{HOUSE_LABEL[q.faction]} (enemy faction) has it for {money.fmt(goods(q))}: an alt could buy it there.[/]")
        if trips and self.settings.time_value:
            console.print(f"  [dim]Total includes your time at {money.fmt(self.settings.time_value)}/h.[/]")

        if best is not None:
            self._verdict(best, cheapest, candidates, goods, travel, trips, qty, item.id)
        if visit is not None:
            console.print(f"  Nearest vendor: [bold]{visit.vendor.label}[/], {visit.spawn.where} "
                          f"[dim]({fmt_visit(visit)})[/]")
        if item.vendor and best is not None and best.lowest is not None and best.lowest < item.vendor:
            console.print(f"  [yellow]Cheaper than vendor price ({money.fmt(item.vendor)})[/]: buy and vendor it for profit.")
        console.print(f"  [dim]{self.freshness()}[/]")

    def _verdict(self, best, cheapest, candidates, goods, travel, trips, qty, item_id: int) -> None:
        here = HOUSE_LABEL[best.faction]
        vq = next((q for q in candidates if q.faction == "vendor"), None)
        if vq is not None:
            v = vendor.get(item_id)
            ah = [q for q in candidates if q.faction != "vendor"]
            each = f"{money.fmt(v.unit)} each" + (f", sold in {v.lot}s" if v.lot > 1 else "")
            if best is vq:
                msg = f"  [green]Buy from a vendor[/] ({each})"
                if ah:
                    land = lambda q: goods(q) + travel(q)  # noqa: E731
                    a = min(ah, key=land)
                    if land(a) > land(vq):
                        msg += (f": {money.fmt(land(a) - land(vq))} cheaper than the {HOUSE_LABEL[a.faction]}"
                                + (", travel included" if trips else ""))
                console.print(msg + ".")
                return
            console.print(f"  [dim]Vendors sell it too ({each}), but the AH is cheaper right now.[/]")
        if not trips:
            others = [q for q in candidates if q is not best]
            msg = f"  [green]Best: {here}[/]"
            if others:
                runner = min(others, key=goods)
                a, b = goods(best), goods(runner)
                if b > a:
                    msg += f" - saves {money.fmt(b - a)} ({(b - a) / b:.0%}) vs {HOUSE_LABEL[runner.faction]}"
                elif a == b:
                    msg += f" [dim](tied with {HOUSE_LABEL[runner.faction]})[/]"
            console.print(msg)
            return
        trip = trips[best.faction]
        loc = travel_mod.short_name(self.location())
        if trip.cost == 0 and trip.seconds == 0:
            msg = f"  [green]Best: {here}, right here in {loc}.[/]"
            if cheapest is not None and cheapest is not best and goods(cheapest) < goods(best):
                save = goods(best) - goods(cheapest)
                ctrip = trips[cheapest.faction]
                timecost = ctrip.total(self.settings.time_value) - ctrip.cost
                msg += (
                    f" {HOUSE_LABEL[cheapest.faction]} is {money.fmt(save)} cheaper, but getting there costs "
                    f"{fmt_trip(ctrip)}{f' (+{money.fmt(timecost)} of your time)' if timecost else ''}: "
                    "[bold]not worth the trip[/]."
                )
            console.print(msg)
            return
        # Best option needs travel: compare with the best place you don't have to fly to
        local = [q for q in candidates if trips[q.faction].cost == 0 and trips[q.faction].seconds == 0]
        dest = travel_mod.short_name(trip.dest)
        if local:
            alt = min(local, key=goods)
            net = goods(alt) - goods(best) - travel(best)
            console.print(
                f"  [green]Worth the trip: {here} in {dest}[/] saves {money.fmt(net)} net "
                f"({money.fmt(goods(alt) - goods(best))} cheaper, minus {money.fmt(travel(best))} travel) vs buying here."
            )
        else:
            console.print(f"  [green]Best: {here} in {dest}[/] ({fmt_trip(trip)} away).")
        console.print(f"  [dim]Route: {' > '.join(travel_mod.short_name(n) for n in trip.via)}"
                      f"{' (round trip)' if self.settings.round_trip else ', then hearth back'}[/]")

    def freshness(self) -> str:
        times = [h.last_modified for h in self._market.houses.values() if h.last_modified] if self._market else []
        if not times:
            return ""
        newest = max(parsedate_to_datetime(t) for t in times)
        return f"{self.market_name()} · data from {newest:%H:%M} UTC (Blizzard updates hourly)"

    def search(self, query: str, limit: int = 15) -> None:
        m = self.market
        listed = m.listed_ids()
        hits = self.catalog.search(query, limit=limit, listed=listed)
        if not hits:
            console.print(f"[red]No item matches[/] {query!r}.")
            return
        t = Table(box=None, padding=(0, 2), header_style="bold dim")
        for col, just in (("ID", "right"), ("Item", "left"), ("Cheapest", "right"), ("Where", "left"), ("Stock", "right")):
            t.add_column(col, justify=just)
        for it in hits:
            quotes = [q for q in all_quotes(m, it.id, self.settings.faction) if q.stock and self.usable(q.faction)]
            best = min(quotes, key=lambda q: q.lowest, default=None)
            t.add_row(
                str(it.id), f"[{it.style}]{it.name}[/]",
                money.fmt(best.lowest) if best else "[dim]-[/]",
                HOUSE_LABEL[best.faction] if best else "[dim]not listed[/]",
                ("unlimited" if any(q.faction == "vendor" for q in quotes) else f"{sum(q.stock for q in quotes):,}") if quotes else "",
            )
        console.print(t)

    def houses(self) -> None:
        m = self.market
        t = Table(title=self.market_name(), box=None, padding=(0, 2), header_style="bold dim")
        for col in ("House", "ID", "Items", "Listings", "Seller cut", "Updated"):
            t.add_column(col)
        for f in FACTIONS:
            h = m.houses[f]
            upd = parsedate_to_datetime(h.last_modified).strftime("%H:%M UTC") if h.last_modified else "-"
            t.add_row(
                HOUSE_LABEL[f], str(h.house_id), f"{len(h.books):,}",
                f"{sum(len(b) for b in h.books.values()):,}", f"{SELLER_CUT[f]:.0%}", upd,
                style=None if self.usable(f) else "dim",
            )
        console.print(t)

    def show_config(self) -> None:
        s = self.settings
        console.print(f"region  [bold]{s.region}[/]   ({', '.join(REGIONS)})")
        console.print(f"ruleset [bold]{s.ruleset}[/]   ({', '.join(MARKETS)}; rp4/rp5 = Roleplay & RP-PvP, mapping unknown)")
        console.print(f"faction [bold]{s.faction or 'none'}[/]   (alliance, horde, none: hides the enemy house from 'best')")
        if s.faction:
            console.print(f"location [bold]{travel_mod.node_name(self.location())}[/]   (any flight point: wowf at gadgetzan)")
        console.print(f"time    [bold]{money.fmt(s.time_value) + '/h' if s.time_value else 'off'}[/]   (what an hour of travel is worth to you, e.g. 20g)")
        console.print(f"return  [bold]{'fly' if s.round_trip else 'hearth'}[/]   (hearth = one-way trips, fly = pay the flight back too)")

    def set_config(self, key: str, value: str, save: bool = True) -> None:
        s = self.settings
        if key in ("region", "r"):
            s.region = normalise_region(value)
            if s.ruleset not in REGION_MARKETS[s.region]:
                s.ruleset = "normal"
        elif key in ("ruleset", "s", "market"):
            s.ruleset = normalise_ruleset(value, s.region)
        elif key in ("faction", "f"):
            s.faction = normalise_faction(value)
        elif key in ("location", "at", "loc"):
            if not s.faction:
                raise ValueError("set your faction first (wowf config faction horde)")
            s.location = travel_mod.find_node(value, s.faction)
        elif key in ("time", "time_value", "timevalue"):
            s.time_value = 0 if value.lower() in ("off", "none", "0") else money.parse(value)
        elif key in ("return", "round_trip"):
            v = value.lower()
            if v not in ("fly", "hearth", "yes", "no", "on", "off"):
                raise ValueError("return is 'hearth' (one-way) or 'fly' (round trip)")
            s.round_trip = v in ("fly", "yes", "on")
        else:
            raise ValueError(f"unknown setting {key!r} (region, ruleset, faction, location, time, return)")
        if save:
            s.save()


# -- interactive shell -------------------------------------------------------
SHELL_HELP = """[bold]Type an item name[/] (typos fine, Tab/arrows pick a suggestion). Prefix a number to price a quantity: [bold]40 linen cloth[/]
[bold]Buy list[/]: /add 20 linen cloth · /rm linen (or /rm 5 linen) · /list shows the cheapest plan incl. travel
  /clear             empty the list        /use <name>     switch list (/lists shows all)
  /vendors <item>    nearest vendors that sell it, with map coordinates
  /search <text>     list matches          /houses         market overview
  /at <place>        where you are         /travel         trip cost to each house
  /time 20g          value of your time/h  /return hearth|fly
  /region eu|us|kr|tw                      /ruleset normal|pvp|rp4|rp5
  /faction alliance|horde|none             /config         show settings
  /refresh           force a recheck       /quit           exit (or Ctrl-D)"""

PT_QUALITY = {
    "POOR": "#9d9d9d", "COMMON": "#ffffff", "UNCOMMON": "#1eff00", "RARE": "#0070dd",
    "EPIC": "#a335ee", "LEGENDARY": "#ff8000",
}


def shell(app: App) -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory

    commands = ["/vendors", "/add", "/rm", "/list", "/clear", "/use", "/lists", "/search", "/houses", "/at", "/travel",
                "/time", "/return", "/region", "/ruleset", "/faction", "/config", "/refresh", "/help", "/quit"]

    class ItemCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if text.startswith("/"):
                if " " not in text:
                    for c in commands:
                        if c.startswith(text):
                            yield Completion(c, -len(text))
                    return
                cmd, _, arg = text.partition(" ")
                opts = {"/region": list(REGIONS), "/ruleset": list(MARKETS), "/faction": ["alliance", "horde", "none"],
                        "/return": ["hearth", "fly"]}.get(cmd)
                if opts:
                    for o in opts:
                        if o.startswith(arg):
                            yield Completion(o, -len(arg))
                    return
                if cmd == "/at":
                    a = arg.lower()
                    for name in travel_mod.place_names(app.settings.faction or "horde"):
                        if not a or any(w.startswith(a) for w in name.lower().replace(",", "").split()) or name.lower().startswith(a):
                            yield Completion(name, -len(arg))
                    return
                if cmd == "/rm":
                    _, q = parse_qty(arg)
                    on_list = {i: app.catalog.get(i) for i in app.lists.items}
                    for it in (Catalog(on_list).search(q, limit=10) if q.strip() else on_list.values()):
                        yield Completion(it.name, -len(q), display_meta=f"×{app.lists.items[it.id]}",
                                         style=f"fg:{PT_QUALITY.get(it.quality, '#ffffff')}")
                    return
                if cmd == "/use":
                    for name in app.lists.lists:
                        if name.startswith(arg):
                            yield Completion(name, -len(arg))
                    return
                if cmd == "/add":
                    _, query = parse_qty(arg)
                    if not query.strip() or query.strip().isdigit():
                        return
                elif cmd in ("/search", "/vendors"):
                    query = arg
                else:
                    return
            else:
                _, query = parse_qty(text)
                if not query.strip() or query.strip().isdigit():
                    return
            m = app._market
            listed = m.listed_ids() if m else None
            for it in app.catalog.search(query, limit=10, listed=listed):
                meta = ""
                if m:
                    lows = [q.lowest for q in all_quotes(m, it.id, app.settings.faction) if q.lowest and app.usable(q.faction)]
                    meta = money.fmt(min(lows), rich=False) if lows else "not listed"
                yield Completion(
                    it.name, -len(query), display=it.name, display_meta=meta,
                    style=f"fg:{PT_QUALITY.get(it.quality, '#ffffff')}",
                )

    # Warm up before the first prompt so completions are instant.
    _ = app.market
    console.print(f"[bold]wowf[/] · {app.market_name()} · faction: {app.settings.faction or 'any'}   [dim](/help for commands)[/]")
    hist = app.api.cache_dir / "history"
    session = PromptSession(history=FileHistory(str(hist)), completer=ItemCompleter(), complete_while_typing=True)
    while True:
        try:
            label = f"{app.settings.region} {app.settings.ruleset}" + (
                f" {app.settings.faction} @ {travel_mod.short_name(app.location())}" if app.settings.faction else "")
            if app.lists.items:
                label += f" · list {len(app.lists.items)}"
            line = session.prompt(f"[{label}] > ").strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            return
        if not line:
            continue
        try:
            if line.startswith("/"):
                cmd, _, arg = line.partition(" ")
                arg = arg.strip()
                if cmd in ("/quit", "/exit", "/q"):
                    return
                elif cmd in ("/help", "/h", "/?"):
                    console.print(SHELL_HELP)
                elif cmd == "/search":
                    app.search(arg)
                elif cmd == "/houses":
                    app.houses()
                elif cmd == "/config":
                    app.show_config()
                elif cmd == "/refresh":
                    if app._market:
                        app._market.loaded_at = 0
                    _ = app.market
                    console.print(f"[green]Up to date.[/] [dim]{app.freshness()}[/]")
                elif cmd in ("/vendors", "/vendor", "/v"):
                    app.show_vendors(arg) if arg else console.print("Usage: /vendors empty vial")
                elif cmd == "/add":
                    app.list_add(arg) if arg else console.print("Usage: /add 20 linen cloth")
                    if arg:
                        app.show_list()
                elif cmd in ("/rm", "/remove", "/del"):
                    app.list_remove(arg) if arg else console.print("Usage: /rm linen cloth  (or /rm 5 linen cloth)")
                elif cmd in ("/list", "/plan", "/l"):
                    app.show_list()
                elif cmd == "/clear":
                    app.lists.clear()
                    console.print(f"Cleared '{app.lists.active}'.")
                elif cmd == "/use":
                    if arg:
                        app.lists.use(arg)
                        console.print(f"Using list '{arg}'.")
                    app.show_list()
                elif cmd == "/lists":
                    for name, items in app.lists.lists.items():
                        mark = "*" if name == app.lists.active else " "
                        console.print(f" {mark} {name}  ({len(items)} items)")
                elif cmd == "/travel":
                    app.show_travel()
                elif cmd == "/at":
                    if arg:
                        app.set_config("location", arg)
                        console.print(f"[green]Location set to {travel_mod.node_name(app.location())} (saved).[/]")
                    app.show_travel()
                elif cmd in ("/time", "/return"):
                    if arg:
                        app.set_config(cmd[1:], arg)
                    app.show_travel()
                elif cmd in ("/region", "/ruleset", "/faction"):
                    if not arg:
                        app.show_config()
                    else:
                        app.set_config(cmd[1:], arg)
                        console.print(f"[green]{cmd[1:]} set to {arg} (saved).[/]")
                        _ = app.market
                else:
                    console.print(f"[red]Unknown command {cmd}[/] - /help")
            else:
                app.price(line)
        except (ValueError, ApiError) as e:
            console.print(f"[red]{e}[/]")


# -- entry point -------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wowf", description="Find the cheapest WoW Forever auction house for an item.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-r", "--region", help="eu (default), us, kr, tw")
    p.add_argument("-s", "--ruleset", help="normal (default), pvp, rp4, rp5")
    p.add_argument("-f", "--faction", help="alliance, horde or none")
    p.add_argument("-n", "--qty", type=int, help="how many you want to buy")
    p.add_argument("words", nargs="*", help="item name, or a command: search, config, houses, update-items, shell")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.load()
    app = App(settings)
    try:
        # One-off overrides (not saved)
        if args.region:
            app.set_config("region", args.region, save=False)
        if args.ruleset:
            app.set_config("ruleset", args.ruleset, save=False)
        if args.faction:
            app.set_config("faction", args.faction, save=False)

        words = args.words
        cmd = words[0].lower() if words else "shell"
        rest = " ".join(words[1:])
        if cmd == "shell":
            shell(app)
        elif cmd == "search":
            app.search(rest)
        elif cmd == "houses":
            app.houses()
        elif cmd == "travel":
            app.show_travel()
        elif cmd == "at":
            if not rest:
                app.show_travel()
            else:
                app.settings = Settings.load()
                app.set_config("location", rest)
                console.print(f"[green]Location set to {travel_mod.node_name(app.location())} (saved).[/]")
                app.show_travel()
        elif cmd == "config":
            if len(words) == 3:
                saved = Settings.load()  # don't persist one-off flags
                app.settings = saved
                app.set_config(words[1].lower(), words[2])
                console.print("[green]Saved.[/]")
            app.show_config()
        elif cmd in ("update-items", "update"):
            with console.status("Rebuilding item catalogue...") as status:
                cat = Catalog.build(app.api, lambda msg: status.update(msg))
            console.print(f"[green]Catalogue rebuilt: {len(cat.items):,} items.[/]")
        else:
            app.price(" ".join(words), args.qty)
    except (ValueError, ApiError, RuntimeError) as e:
        console.print(f"[red]error:[/] {e}")
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
