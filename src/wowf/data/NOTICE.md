# Data sources and licences

| File | Source | Licence |
|---|---|---|
| `taxi.json` | wago.tools: World of Warcraft Classic Era client tables (TaxiNodes, TaxiPath, TaxiPathNode) | Blizzard game data |
| `vendor.json` | Derived from [CMaNGOS classic-db](https://github.com/cmangos/classic-db) (`npc_vendor`, `npc_vendor_template`, `creature_template`, `creature`, `item_template`), plus wago.tools Classic Era map and faction tables | **GPL-3.0**, as for the classic-db database it's derived from |

Rebuild them with `scripts/build_taxi.py` and `scripts/build_vendor.py`.
