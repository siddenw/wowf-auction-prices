from wowf import travel, vendor


def test_vendor_lots():
    vial = vendor.get(3371)
    assert vial.lot == 5 and vial.unit == 4
    assert vial.cost(1) == (20, 5)
    assert vial.cost(5) == (20, 5)
    assert vial.cost(6) == (40, 10)


def test_non_vendor_item():
    assert vendor.get(2589) is None  # Linen Cloth drops, vendors don't sell it


def test_nearest_vendor_in_city_is_a_short_walk():
    org = travel.find_node("org", "horde")
    v = vendor.nearest(2320, "horde", org)  # Coarse Thread
    assert v.spawn.zone == "Orgrimmar" and not v.via and v.cost == 0 and v.minutes < 2
    assert 0 < v.spawn.zx < 100 and 0 < v.spawn.zy < 100


def test_far_from_vendors_you_fly_then_walk():
    kargath = travel.find_node("kargath", "horde")
    v = vendor.nearest(2678, "horde", kargath)  # Mild Spices
    assert v.via and v.via[0] == kargath and v.cost > 0
    assert v.walk_yards < vendor.MAX_WALK


def test_hostile_vendors_are_excluded():
    org = travel.find_node("org", "horde")
    for v in vendor.visits(2320, "horde", org):
        assert v.vendor.mask & vendor.FACTION_MASK["horde"]
        assert v.spawn.zone not in ("Stormwind City", "Ironforge", "Darnassus")


def test_zone_coordinates_match_known_npc():
    # Tari'qa, Trade Supplies at the Crossroads is at about 51.7, 30.0 in The Barrens.
    xr = travel.find_node("crossroads", "horde")
    v = vendor.nearest(2320, "horde", xr)
    assert v.spawn.zone == "The Barrens"
    assert abs(v.spawn.zx - 51.7) < 1 and abs(v.spawn.zy - 30.0) < 1
