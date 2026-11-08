from wowf.catalog import Catalog, Item
from wowf.cli import parse_qty
from wowf.market import buy_cost, market_price
from wowf.money import fmt


def test_fmt():
    assert fmt(0, rich=False) == "0c"
    assert fmt(16, rich=False) == "16c"
    assert fmt(500, rich=False) == "5s"
    assert fmt(1234567, rich=False) == "123g 45s 67c"
    assert fmt(10005, rich=False) == "1g 5c"
    assert fmt(None) == "-"


def test_buy_cost_walks_book_and_buys_whole_stacks():
    book = [(10, 5), (12, 20), (50, 1)]
    assert buy_cost(book, 3) == (50, 5)  # one stack of 5 covers it
    assert buy_cost(book, 6) == (50 + 240, 25)  # needs the 20-stack too
    assert buy_cost(book, 100) == (50 + 240 + 50, 26)  # short: buys everything


def test_market_price_ignores_expensive_tail():
    book = [(10, 15), (20, 5), (9999, 80)]
    assert market_price(book) == 10  # cheapest 15 of 100 units
    assert market_price([]) is None


def test_parse_qty():
    assert parse_qty("linen cloth") == (1, "linen cloth")
    assert parse_qty("40 linen cloth") == (40, "linen cloth")
    assert parse_qty("40x linen") == (40, "linen")
    assert parse_qty("linen cloth x20") == (20, "linen cloth")
    assert parse_qty("123") == (1, "123")  # bare number is an item id


def _cat():
    names = ["Linen Cloth", "Linen Cloak", "Bolt of Linen Cloth", "Silverleaf", "Silver Ore", "Copper Bar"]
    return Catalog({i: Item(i, n) for i, n in enumerate(names, 1)})


def test_search_handles_typos_and_abbreviations():
    c = _cat()
    assert c.search("linnen clth")[0].name == "Linen Cloth"
    assert c.search("silverlef")[0].name == "Silverleaf"
    assert c.search("#6")[0].name == "Copper Bar"


def test_search_prefers_popular_listed_items_on_ties():
    c = _cat()
    assert c.search("lin clo", listed={1: 5000, 2: 3})[0].name == "Linen Cloth"
    assert c.search("lin clo", listed={1: 3, 2: 5000})[0].name == "Linen Cloak"
