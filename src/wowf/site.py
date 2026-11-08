"""Build the static web site: copy web/ and write fresh price data into <out>/data/.

    uv run wowf-site _site            # EU, all rulesets
    WOWF_REGION=us uv run wowf-site _site

Runs hourly in GitHub Actions (see .github/workflows/pages.yml). Credentials come from
BLIZZARD_CLIENT_ID / BLIZZARD_CLIENT_SECRET and are only used to fetch data. They are never written out.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import time
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import travel, vendor
from .api import Blizzard
from .catalog import Catalog
from .config import MARKET_LABELS, REGION_MARKETS
from .market import Market

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
CATALOG_MAX_AGE = 7 * 86400


def _dump(path: Path, data) -> int:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
    path.write_bytes(raw)
    return len(raw)


def catalog_for(api: Blizzard) -> Catalog:
    cat = Catalog.load(api)
    try:
        built = json.loads(gzip.decompress(Catalog.path(api).read_bytes())).get("built_at", 0)
    except OSError:
        built = 0
    if cat is None or time.time() - built > CATALOG_MAX_AGE:
        print("building item catalogue...", flush=True)
        cat = Catalog.build(api, lambda msg: None)
    return cat


def build(out: Path, region: str) -> None:
    api = Blizzard(region)
    cat = catalog_for(api)
    data_dir = out / "data"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB_DIR, out)
    data_dir.mkdir(parents=True, exist_ok=True)

    listed: Counter[int] = Counter()
    rulesets = []
    for ruleset in REGION_MARKETS[region]:
        m = Market(api, ruleset).load(max_age=0)
        houses = {}
        for faction, h in m.houses.items():
            # item_id -> flat [unit, qty, count, unit, qty, count, ...], cheapest first.
            # Identical listings are merged with a count so "whole stacks" buying stays exact.
            houses[faction] = {
                str(i): [x for (u, q), n in sorted(Counter(book).items()) for x in (u, q, n)]
                for i, book in h.books.items()
            }
            for i, book in h.books.items():
                listed[i] += len(book)
        stamps = [parsedate_to_datetime(h.last_modified).timestamp() for h in m.houses.values() if h.last_modified]
        size = _dump(data_dir / f"prices-{ruleset}.json", {"updated": max(stamps, default=time.time()), "houses": houses})
        rulesets.append({"id": ruleset, "label": MARKET_LABELS[ruleset]})
        print(f"{ruleset}: {size // 1024} KB", flush=True)

    # Item names for everything listed on any ruleset: id -> [name, quality, vendor, listings]
    items = {}
    for i in vendor.items():  # vendor reagents are searchable even when nobody lists them
        listed.setdefault(i, 0)
    for i, n in listed.items():
        it = cat.get(i)
        items[str(i)] = [it.name, it.quality[:1] if it.quality else "C", it.vendor, n]
    _dump(data_dir / "items.json", items)

    # Flight network for the browser-side router.
    raw = travel._data()
    _dump(data_dir / "travel.json", {
        "source": raw["source"],
        "speed": travel.FLIGHT_SPEED,
        "hop": travel.HOP_PENALTY,
        "nodes": raw["nodes"],
        "paths": raw["paths"],
        "transports": travel.TRANSPORTS,
        "houses": travel.HOUSE_NODES,
        "home": travel.HOME_NODE,
    })
    _dump(data_dir / "vendor.json", {str(i): [v.price, v.lot] for i, v in vendor.items().items()})
    _dump(data_dir / "meta.json", {"region": region, "rulesets": rulesets, "built": time.time()})
    print(f"site written to {out} ({len(items):,} items)")


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "_site")
    build(out, os.environ.get("WOWF_REGION", "eu"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
