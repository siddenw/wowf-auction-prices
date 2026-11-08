from wowf import buylist, travel
from wowf.market import House


class FakeMarket:
    def __init__(self, books):
        self.houses = {f: House(f, 0, None, {i: sorted(b) for i, b in items.items()}) for f, items in books.items()}


ORG = travel.find_node("org", "horde")
CLOTH, PEARL = 1, 2


def test_allocate_splits_across_houses_and_reports_shortfall():
    m = FakeMarket({"horde": {CLOTH: [(20, 10)]}, "blackwater": {CLOTH: [(10, 5)]}})
    purchases, short = buylist.allocate(m, {CLOTH: 12}, ("horde", "blackwater"))
    got = {p.house: (p.units, p.cost) for p in purchases}
    assert got == {"blackwater": (5, 50), "horde": (10, 200)}
    assert short == {}
    _, short = buylist.allocate(m, {CLOTH: 30}, ("horde",))
    assert short == {CLOTH: 20}


def test_trip_only_when_savings_beat_travel():
    # Pearl is 2g cheaper at Blackwater: worth a ~2s trip.
    m = FakeMarket({"horde": {CLOTH: [(20, 100)], PEARL: [(30000, 1)]},
                    "blackwater": {CLOTH: [(25, 100)], PEARL: [(10000, 1)]}})
    best = buylist.plan(m, {CLOTH: 50, PEARL: 1}, ["horde", "blackwater"], "horde", ORG)[0]
    assert best.houses == ("horde", "blackwater")
    assert {p.item_id: p.house for p in best.purchases} == {CLOTH: "horde", PEARL: "blackwater"}

    # Only 1c cheaper: stay home.
    m = FakeMarket({"horde": {PEARL: [(10001, 1)]}, "blackwater": {PEARL: [(10000, 1)]}})
    best = buylist.plan(m, {PEARL: 1}, ["horde", "blackwater"], "horde", ORG)[0]
    assert best.houses == ("horde",) and best.trip.cost == 0


def test_plans_that_get_everything_rank_first():
    m = FakeMarket({"horde": {CLOTH: [(1, 5)]}, "blackwater": {CLOTH: [(100, 50)]}})
    best = buylist.plan(m, {CLOTH: 50}, ["horde", "blackwater"], "horde", ORG)[0]
    assert sum(best.short.values()) == 0


VIAL = 3371  # Empty Vial: vendor sells 5 for 20c (4c each)


def test_vendor_beats_ah_and_sells_in_lots():
    m = FakeMarket({"horde": {VIAL: [(6, 100)]}, "blackwater": {}})
    best = buylist.plan(m, {VIAL: 7}, ["horde", "blackwater"], "horde", ORG)[0]
    assert best.houses == ()  # vendors only, no AH trip
    (p,) = best.purchases
    assert (p.house, p.units, p.cost) == ("vendor", 10, 40)  # two lots of 5


def test_cheap_ah_listings_used_before_vendor():
    m = FakeMarket({"horde": {VIAL: [(2, 3)]}, "blackwater": {}})
    purchases, short = buylist.allocate(m, {VIAL: 7}, ("horde",))
    got = {p.house: (p.units, p.cost) for p in purchases}
    assert got == {"horde": (3, 6), "vendor": (5, 20)} and not short
