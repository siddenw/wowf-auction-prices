"""Build src/wowf/data/vendor.json: what NPC vendors sell, and where they stand.

Sources:
- CMaNGOS classic-db (GPL-3.0, https://github.com/cmangos/classic-db): npc_vendor and
  npc_vendor_template (who sells what), creature_template (name, title, faction),
  creature (spawn positions) and item_template (vendor price and lot size).
- wago.tools Classic Era client tables: FactionTemplate (who can trade with the NPC),
  UiMapAssignment and UiMap (world position to in-game zone map coordinates).

The Blizzard API can't say which items vendors sell, and it has no NPC locations. Forever is
based on Classic, so this is the best available stand-in, but its vendors may differ.

    uv run python scripts/build_vendor.py [path/to/ClassicDB.sql.gz]
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/wowf/data/vendor.json"
CACHE = Path.home() / ".cache/wowf/build"
CMANGOS_URL = "https://raw.githubusercontent.com/cmangos/classic-db/master/Full_DB/ClassicDB_1_12_1_z2815.sql.gz"
WAGO = "https://wago.tools/db2/{}/csv?product=wow_classic_era"
TABLES = {"npc_vendor", "npc_vendor_template", "creature_template", "creature", "item_template"}
GROUP_BIT = {"alliance": 2, "horde": 4}
CITY_AREA = 2_500_000  # square yards: city maps are far smaller than zone maps  # FactionTemplate.FactionGroup / EnemyGroup bits


# -- tiny MySQL dump reader ----------------------------------------------------
def read_dump(path: Path):
    cols: dict[str, list[str]] = {}
    cur = None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("CREATE TABLE `"):
                cur = line.split("`")[1]
                if cur in TABLES:
                    cols[cur] = []
            elif cur in cols and line.startswith("  `"):
                cols[cur].append(line.split("`")[1])
            elif line.startswith("INSERT INTO `"):
                t = line.split("`")[1]
                if t in TABLES:
                    for row in _rows(line[line.index("VALUES") + 6:]):
                        yield t, dict(zip(cols[t], row))


def _rows(s: str):
    i, n = 0, len(s)
    while i < n:
        if s[i] != "(":
            i += 1
            continue
        i += 1
        row, val, inq, quoted = [], "", False, False
        while i < n:
            c = s[i]
            if inq:
                if c == "\\":
                    val += s[i + 1]
                    i += 2
                    continue
                if c == "'":
                    inq = False
                else:
                    val += c
            elif c == "'":
                inq = quoted = True
            elif c in ",)":
                row.append(val if quoted else _num(val))
                val, quoted = "", False
                if c == ")":
                    i += 1
                    break
            else:
                val += c
            i += 1
        yield row


def _num(v: str):
    v = v.strip()
    if v == "NULL":
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def wago(name: str) -> list[dict]:
    r = httpx.get(WAGO.format(name), follow_redirects=True, timeout=60)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


# -- world -> zone map coordinates --------------------------------------------
class ZoneMapper:
    def __init__(self) -> None:
        maps = {int(r["ID"]): r for r in wago("UiMap")}
        self.rects = []
        for r in wago("UiMapAssignment"):
            m = maps.get(int(r["UiMapID"]))
            if not m or m["Type"] != "3":  # zone-level maps only (cities included)
                continue
            self.rects.append((
                int(r["MapID"]), m["Name_lang"],
                float(r["Region_0"]), float(r["Region_1"]), float(r["Region_3"]), float(r["Region_4"]),
                *(float(r[k]) for k in ("UiMin_0", "UiMin_1", "UiMax_0", "UiMax_1")),
            ))

    def locate(self, map_id: int, x: float, y: float) -> tuple[str, float, float] | None:
        """Zone map name and coordinates for a world position.

        Zone map rectangles overlap (Crossroads is also near the corner of the Mulgore map), so we
        pick the map where the point sits furthest from the edges. A city map wins whenever the
        point is comfortably inside it.
        """
        best = None
        for mid, name, x0, y0, x1, y1, u0, v0, u1, v1 in self.rects:
            if mid == map_id and x0 <= x <= x1 and y0 <= y <= y1:
                # On the map, left->right is world Y decreasing; top->bottom is world X decreasing.
                px = (u0 + (y1 - y) / (y1 - y0) * (u1 - u0)) * 100
                py = (v0 + (x1 - x) / (x1 - x0) * (v1 - v0)) * 100
                margin = min(px, 100 - px, py, 100 - py)
                city = (x1 - x0) * (y1 - y0) < CITY_AREA
                score = margin + (50 if city and margin >= 3 else 0)
                if best is None or score > best[0]:
                    best = (score, name, round(px, 1), round(py, 1))
        return best[1:] if best else None


def main() -> None:
    dump = Path(sys.argv[1]) if len(sys.argv) > 1 else CACHE / "ClassicDB.sql.gz"
    if not dump.exists():
        dump.parent.mkdir(parents=True, exist_ok=True)
        print("downloading CMaNGOS classic-db...", flush=True)
        with httpx.stream("GET", CMANGOS_URL, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with open(dump, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    factions = {int(r["ID"]): r for r in wago("FactionTemplate")}
    zones = ZoneMapper()

    sells: dict[int, set[int]] = defaultdict(set)  # vendor entry -> unlimited items
    template_items: dict[int, set[int]] = defaultdict(set)
    templates: dict[int, dict] = {}
    spawns: dict[int, list] = defaultdict(list)
    items: dict[int, dict] = {}
    for table, d in read_dump(dump):
        if table == "npc_vendor" and not d["maxcount"]:
            sells[d["entry"]].add(d["item"])
        elif table == "npc_vendor_template" and not d["maxcount"]:
            template_items[d["entry"]].add(d["item"])
        elif table == "creature_template":
            templates[d["Entry"]] = d
        elif table == "creature" and d["map"] in (0, 1):  # Eastern Kingdoms, Kalimdor
            spawns[d["id"]].append((d["map"], d["position_x"], d["position_y"]))
        elif table == "item_template":
            items[d["entry"]] = d
    for entry, t in templates.items():
        if t.get("VendorTemplateId"):
            sells[entry] |= template_items.get(t["VendorTemplateId"], set())

    def usable_by(faction_template: int) -> int:
        """Bitmask 1 = Alliance can trade, 2 = Horde can trade (not hostile)."""
        ft = factions.get(faction_template)
        if ft is None:
            return 0
        mask = 0
        for i, side in ((1, "alliance"), (2, "horde")):
            if not int(ft["EnemyGroup"]) & GROUP_BIT[side]:
                mask |= i
        return mask

    vendors_out: dict[str, list] = {}
    item_vendors: dict[int, list[int]] = defaultdict(list)
    for entry, sold in sells.items():
        t = templates.get(entry)
        if not t or not spawns.get(entry) or not sold:
            continue
        mask = usable_by(t["Faction"])
        if not mask:
            continue
        locs = []
        for map_id, x, y in spawns[entry]:
            z = zones.locate(map_id, x, y)
            locs.append([map_id, round(x), round(y), *(z if z else ("", 0, 0))])
        vendors_out[str(entry)] = [t["Name"], t["SubName"] or "", mask, locs]
        for i in sold:
            item_vendors[i].append(entry)

    items_out = {}
    for i, vs in item_vendors.items():
        it = items.get(i)
        if not it or not it["BuyPrice"]:
            continue
        items_out[str(i)] = [it["BuyPrice"], it["BuyCount"] or 1, sorted(vs)]

    OUT.write_text(json.dumps({
        "source": "CMaNGOS classic-db (GPL-3.0) + wago.tools wow_classic_era map/faction tables",
        "vendors": vendors_out,
        "items": items_out,
    }, separators=(",", ":")))
    print(f"wrote {OUT}: {len(items_out)} items sold by {len(vendors_out)} vendors "
          f"({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
