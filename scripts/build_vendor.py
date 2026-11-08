"""Build src/wowf/data/vendor.json: crafting reagents that NPC vendors sell, with vendor prices.

The Blizzard API sets `purchase_price` on every item (drops too), so it can't tell us which
items vendors actually sell. This hand-picked list covers the standard Classic trade-supply
reagents. Prices come from the API: `purchase_price` buys a lot of `purchase_quantity` units.

    uv run python scripts/build_vendor.py
"""

import json
from pathlib import Path

from wowf.api import Blizzard
from wowf.catalog import Catalog

OUT = Path(__file__).resolve().parents[1] / "src/wowf/data/vendor.json"

# Standard trade-goods / general-goods vendor reagents (Classic). Not yet confirmed in Forever.
VENDOR_REAGENTS = [
    "Coarse Thread", "Fine Thread", "Silken Thread", "Heavy Silken Thread", "Rune Thread",
    "Red Dye", "Blue Dye", "Gray Dye", "Green Dye", "Black Dye", "Orange Dye", "Purple Dye", "Yellow Dye", "Pink Dye",
    "Bleach", "Salt",
    "Empty Vial", "Leaded Vial", "Crystal Vial", "Imbued Vial",
    "Mild Spices", "Hot Spices", "Soothing Spices", "Refreshing Spring Water", "Ice Cold Milk",
    "Weak Flux", "Strong Flux",
    "Copper Rod", "Simple Wood", "Wooden Stock",
]


def main() -> None:
    api = Blizzard("eu")
    cat = Catalog.load(api) or Catalog.build(api)
    by_name: dict[str, list[int]] = {}
    for item_id, it in cat.items.items():
        by_name.setdefault(it.name, []).append(item_id)
    out = {}
    for name in VENDOR_REAGENTS:
        ids = by_name.get(name, [])
        if len(ids) != 1:
            raise SystemExit(f"{name!r}: expected exactly one item, found {ids}")
        item_id = ids[0]
        d = None
        for ns in ("static-forever", "static-classic1x"):
            r = api._get(f"/data/wow/item/{item_id}", ns)
            if r.status_code == 200:
                d = r.json()
                break
        if not d or not d.get("purchase_price"):
            raise SystemExit(f"{name!r}: no purchase price")
        out[str(item_id)] = {"name": name, "price": d["purchase_price"], "lot": d.get("purchase_quantity") or 1}
    OUT.write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT}: {len(out)} vendor reagents")


if __name__ == "__main__":
    main()
