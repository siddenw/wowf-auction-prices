"""Build src/wowf/data/taxi.json from Classic Era DB2 tables published by wago.tools.

WoW Forever has no public client-data export yet, so the Classic Era flight network
(costs in copper, path lengths in yards) is used as the best available approximation.

    uv run python scripts/build_taxi.py
"""

import csv
import io
import json
import math
from pathlib import Path

import httpx

PRODUCT = "wow_classic_era"
OUT = Path(__file__).resolve().parents[1] / "src/wowf/data/taxi.json"


def table(name: str) -> list[dict]:
    r = httpx.get(f"https://wago.tools/db2/{name}/csv", params={"product": PRODUCT}, follow_redirects=True, timeout=60)
    r.raise_for_status()
    return list(csv.DictReader(io.StringIO(r.text)))


def main() -> None:
    builds = httpx.get("https://wago.tools/api/builds", timeout=30).json()
    version = builds[PRODUCT][0]["version"]
    nodes = {}
    for r in table("TaxiNodes"):
        flags = int(r["Flags"]) & 3  # 1 = Alliance, 2 = Horde, 3 = both
        name = r["Name_lang"]
        if not flags or name.startswith(("Quest Path", "Transport", "Generic")):
            continue
        nodes[int(r["ID"])] = {
            "name": name, "continent": int(r["ContinentID"]), "flags": flags,
            "x": round(float(r["Pos_0"])), "y": round(float(r["Pos_1"])),
        }
    points: dict[int, list] = {}
    for r in table("TaxiPathNode"):
        points.setdefault(int(r["PathID"]), []).append(
            (int(r["NodeIndex"]), float(r["Loc_0"]), float(r["Loc_1"]), int(r["ContinentID"]))
        )
    paths = []
    for r in table("TaxiPath"):
        a, b, cost = int(r["FromTaxiNode"]), int(r["ToTaxiNode"]), int(r["Cost"])
        if a not in nodes or b not in nodes:
            continue
        p = sorted(points.get(int(r["ID"]), []))
        length = sum(math.dist(u[1:3], v[1:3]) for u, v in zip(p, p[1:]) if u[3] == v[3])
        if not length:
            length = math.dist((nodes[a]["x"], nodes[a]["y"]), (nodes[b]["x"], nodes[b]["y"]))
        paths.append([a, b, cost, round(length)])
    OUT.write_text(json.dumps({"source": f"wago.tools {PRODUCT} {version}", "nodes": nodes, "paths": paths}, indent=0))
    print(f"wrote {OUT}: {len(nodes)} nodes, {len(paths)} paths from {PRODUCT} {version}")


if __name__ == "__main__":
    main()
