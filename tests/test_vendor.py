from wowf import vendor


def test_vendor_lots():
    vial = vendor.get(3371)
    assert vial.name == "Empty Vial" and vial.lot == 5 and vial.unit == 4
    assert vial.cost(1) == (20, 5)
    assert vial.cost(5) == (20, 5)
    assert vial.cost(6) == (40, 10)


def test_non_vendor_item():
    assert vendor.get(2589) is None  # Linen Cloth drops, vendors don't sell it
