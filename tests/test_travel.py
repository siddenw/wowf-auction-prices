import pytest

from wowf import money, travel


def test_money_parse():
    assert money.parse("20g") == 200000
    assert money.parse("1g50s") == 15000
    assert money.parse("1g 50s 3c") == 15003
    assert money.parse("75s") == 7500
    assert money.parse("150") == 150
    with pytest.raises(ValueError):
        money.parse("lots")


def test_find_node_aliases_and_fuzzy():
    assert travel.short_name(travel.find_node("org", "horde")) == "Orgrimmar"
    assert travel.short_name(travel.find_node("tb", "horde")) == "Thunder Bluff"
    assert travel.short_name(travel.find_node("gadgetzan", "horde")) == "Gadgetzan"
    assert travel.short_name(travel.find_node("tarren", "horde")) == "Tarren Mill"
    with pytest.raises(ValueError):
        travel.find_node("stormwind", "horde")  # alliance-only flight point


def test_in_city_is_free():
    org = travel.find_node("org", "horde")
    trip = travel.trip_to_house(org, "horde", "horde")
    assert trip.cost == 0 and trip.seconds == 0


def test_route_uses_free_transport_and_is_cheapest():
    org = travel.find_node("org", "horde")
    trip = travel.trip_to_house(org, "blackwater", "horde")
    assert trip.cost > 0
    names = [travel.short_name(n) for n in trip.via]
    assert names[0] == "Orgrimmar" and names[-1] in ("Booty Bay", "Gadgetzan", "Everlook")


def test_time_value_picks_faster_destination_not_pricier_path():
    org = travel.find_node("org", "horde")
    slow = travel.trip_to_house(org, "blackwater", "horde", 0)
    fast = travel.trip_to_house(org, "blackwater", "horde", 600_000)  # 60g/h
    assert fast.seconds <= slow.seconds
    # whatever destination is chosen, the path to it is the flight master's cheapest one
    direct = travel._cheapest_path(org, fast.dest, "horde")
    assert fast.cost == direct.cost


def test_round_trip_costs_more():
    kargath = travel.find_node("kargath", "horde")
    one = travel.trip_to_house(kargath, "horde", "horde")
    both = travel.trip_to_house(kargath, "horde", "horde", round_trip=True)
    assert both.cost > one.cost and both.seconds > one.seconds
