# Prior-art research: WoW Forever price finder

Researched 2026-11-08, two days after Blizzard turned on the Forever auction API. Everything about the API is **unofficial and undocumented**, so re-check it before building on it.

## Goal tiers
1. Pick a ruleset (default Normal), fuzzy-search or autocomplete an item, and see the best price on each market.
2. *Stretch:* get a character's location and weigh the flight cost against the price difference.
3. *Super stretch:* build a buy list and see when one trip is worth it for the whole list.
4. *Mega stretch:* break a recipe down into reagents, compare them with inventory, and add the missing ones to the buy list.

## 1. Data source: Battle.net Game Data API (Forever namespaces)
The source is a [Blizzard forum thread](https://us.forums.blizzard.com/en/blizzard/t/wow-forever-api-what-works-so-far-plus-a-ts-client/60334), confirmed by [Everloot PR #867](https://github.com/Everloot/everloot/pull/867).

| Namespace | Use |
|---|---|
| `static-forever-{region}` | items, classes (build 1.60.1_70399) |
| `dynamic-forever-{region}` | rulesets, auctions |
| `profile-forever-{region}` | characters, guilds |

The namespaces work in US, EU, KR and TW, but not CN. Client-credentials OAuth is enough for everything except the account summary.

**Endpoints that work:**
- `/data/wow/ruleset/index` returns these IDs: **1 PvP, 2 Normal, 3 Roleplay, 15 Roleplay PvP**. KR and TW only have 1 and 2.
- `/data/wow/ruleset/{id}/auctions/` (the trailing slash is required) returns the house index.
- `/data/wow/ruleset/{id}/auctions/{houseId}` returns `id, item{id,…}, buyout, bid?, quantity, time_left`.
- `/data/wow/ruleset/{id}/auctions/{houseId}/commodities` returns `id, item, quantity, unit_price, time_left`.
- `/data/wow/item/{id}` (static namespace).
- Realm, connected-realm and token endpoints, and the old top-level commodities route, all return 404.

**Gotchas:**
- Every ruleset ID returns the **same 15 houses** (IDs 110–121 and 123–125): 5 × Alliance, 5 × Horde and 5 × "Blackwater". The ruleset ID is validated but doesn't filter, and the API doesn't say which ruleset a house belongs to. [ForeverMastery](https://forevermastery.com/en/prices) works it out from the data, which gives 5 ruleset groups × 3 houses. **We will need our own house→(ruleset, faction) mapping.**
- Houses 112, 115 and 118 sometimes have no `auctions` field.
- The busiest houses have 50–68k auctions (about 9 MB per response). The data refreshes hourly.
- **About 43% of item IDs seen on the auction house return 404** from `/data/wow/item/{id}`. Item names and search need a second source (see §4).
- The API gives no sales or volume data.

**Client library:** [Pinta365/blizzard_api](https://github.com/Pinta365/blizzard_api) (MIT; Deno, Node and Bun; Forever support since 0.8.0). It offers `client.wowForever.rulesets()` and `characterProfile(name, surname)`, among others.

## 2. Market structure (what "where to go" means)
- There are no realms. Each region × ruleset has **three markets**: Alliance, Horde and **Blackwater** (neutral, with a 15% cut versus 5% at faction houses; deposits have a minimum of 1s for now). [AHledger](https://ahledger.com/wow-forever/auction-house), [ForeverMastery](https://forevermastery.com/en/prices)
- The Alliance cities share one house and the Horde cities share another. Blackwater auctioneers are most likely Gadgetzan, Booty Bay and Everlook, and possibly Three Corners (Redridge). **This isn't verified**: see the open question in [goldbook #80](https://github.com/jkc-be/goldbook/issues/80). It doesn't look like any of the new Forever zones have an auctioneer.
- So "where to buy" is really a choice between the **own-faction house and Blackwater**. The location question is which Blackwater auctioneer is cheapest to reach.
- Rough price ratios: RP is about 1.63× Normal, and RP-PvP about 1.88× ([AHledger](https://ahledger.com/)). Normal and PvP each list about 3–3.7k distinct items.

## 3. Existing tools (competitors and references)
| Tool | What it does | Gap compared with this project |
|---|---|---|
| [ForeverMastery](https://forevermastery.com/en/prices) | Hourly prices by region, ruleset and faction (including Blackwater). Shows lowest, market (cheapest 15%) and average, plus vendor price, history, a search box and category filters | No fuzzy search or travel cost, no lists, no public API |
| [AHledger](https://ahledger.com/) | Price pages and guides | Same gaps; it rate-limited our fetches |
| [timesaver.gg](https://timesaver.gg/blog/wow-forever-auction-house-prices-week-1) | Blog snapshots | Static |
| goldbook (jkc-be) | Addon, server and web UI | **All rights reserved**, so it can't be reused |
| Everloot | A knowledge base that is wiring up Forever namespaces | No pricing UI yet |
| [WoW Forever Companion (Overwolf)](https://overwolf.com/app/convexapps-wow_forever_companion) | Item search, recipe lookup and a **shopping-list builder** | The closest match to tiers 3 and 4; it doesn't weigh travel |
| [Market addon](https://www.curseforge.com/wow/addons/market) | Records prices you see, locally, with no network access | In-game only |
| [ProfitProphet](https://www.curseforge.com/wow/addons/profitprophet) | Fuzzy item search using bounded Damerau-Levenshtein (≤2 edits) | A good reference for the fuzzy algorithm (TBC addon) |

Nobody yet combines fuzzy search, a cross-house comparison and a travel-adjusted cost. That combination is this project's niche.

## 4. Item names and recipes (for search and tier 4)
- The API's item coverage is incomplete, so we need another source for names and the recipe graph:
  - [Warcraft Wiki "Forever items"](https://warcraft.wiki.gg/wiki/Category:Forever_items) has item and recipe pages.
  - [SeeMeta](https://seemeta.com/en/wow-forever/professions) and [Metabot](https://metabot.gg/en/wow-forever/professions) have per-profession recipe lists with reagents. Some of this is beta data.
  - Addons that ship client-extracted recipe data: [Forever Guide](https://www.curseforge.com/wow/addons/forever-guide) (11.6k items), [PRR](https://www.curseforge.com/wow/addons/prr), [ForeverCodex](https://www.curseforge.com/wow/addons/forevercodex). Check each licence first.
  - The best option is to extract the client DB2 tables ourselves (ItemSparse, SkillLineAbility, SpellReagents) with a tool like wow.tools.local or DBC2CSV, if Forever's CASC can be read.
- The `profile-forever` character `/professions` endpoint works, so it can tell us which recipes a character knows.

## 5. Character location and inventory (tiers 2–4)
- **The Profile API exposes no zone or position**, only equipment, professions, reputation and so on. A character appears only after logout, and only from level 10.
- So location and bags have to come from **the user or an addon**:
  - The user picks their current zone or city manually. This is the simplest option and fine for an MVP.
  - The user pastes an addon export string. [PatchVault](https://www.curseforge.com/wow/addons/patchvault) has a `/pv` export of bags and bank. [Alts Forever](https://www.curseforge.com/wow/addons/alts-forever) tracks bags, bank and mail. Or we write our own small addon that dumps position, bags and bank to SavedVariables or an export string. Note that a beta bug wipes SavedVariables for other characters on restart, and a junction-link workaround exists.
- **Flight costs:** there's no published Forever table. [LibTaxiData](https://www.curseforge.com/wow/addons/libtaxidata) has Forever node positions (beta profile). Costs come from the in-game `TaxiNodeCost` (copper, before any faction discount, and only while the taxi map is open). Options are to crowd-scan with our addon or to approximate cost from distance between nodes. Travel *time* also matters as a gold-per-hour figure, and [Taxi Flight Times](https://www.curseforge.com/wow/addons/taxi-flight-times) is a reference for that.

## 6. Suggested architecture (from the above)
1. **Ingest worker** (hourly): pull the house index and every house's auctions and commodities for one region, then aggregate each house × item into min, p15-market, quantity and listing count. Keep compact snapshots in SQLite or Postgres. Don't store raw 9 MB dumps.
2. **Item catalogue**: merge `/data/wow/item` with the extracted or wiki names. Fuzzy autocomplete client-side with Fuse.js, or server-side with SQLite FTS5 trigram or pg_trgm.
3. **Compare view**: item → own-faction house versus Blackwater, net of the cut where relevant.
4. **Travel model** (tier 2): a graph of flight nodes plus auctioneer locations, with edge = gold cost (+ optional time × gold/hr). Buy at the remote house only if `Σ(savings over list) > route cost`. Tier 3 is the same check summed over the buy list.
5. **Recipe expansion** (tier 4): recursively expand reagents to a tree, take what's already in the inventory (from the addon export) away from it, and send the rest to the buy list.

## Open questions to verify early
- Which house IDs belong to which ruleset and faction. Fetch all 15 and cross-check against ForeverMastery.
- Exactly where the Blackwater auctioneers are.
- Blizzard API rate limits for the Forever namespaces.

## 7. Verified against the live API (2026-11-08, US)
Calls used client credentials and a token valid for 24h. Everything below was observed directly.

- `/data/wow/ruleset/index` returns IDs 1 PvP, 2 Normal, 3 Roleplay and 15 Roleplay PvP. All four have status UP.
- The house index has 15 houses. **The ruleset in the URL does not filter.** House 119 has the same auction IDs under ruleset 1 and ruleset 2, and the house's `ruleset` link just echoes whatever ruleset you requested.
- **House → market map.** I matched exact Linen Cloth quantities against ForeverMastery for 110, 116, 117, 119, 120, 121, 123, 124 and 125. The other houses follow the same ID pattern.

  | Market group | Alliance | Horde | Blackwater (neutral) |
  |---|---|---|---|
  | **Normal** | **111** | **114** | **117** |
  | PvP | 110 | 113 | 116 |
  | RP or RP-PvP ("group 4") | 119 | 120 | 121 |
  | RP or RP-PvP ("group 5") | 123 | 124 | 125 |
  | empty (unused or test?) | 112 | 115 | 118 |

  Which of 119-125 is RP and which is RP-PvP is still unknown. ForeverMastery doesn't know either.
- **Two feeds per house:**
  - `/auctions/{id}` holds non-stackable items (gear, bags, scrolls). Every entry has `quantity: 1` and a `buyout`. A few have only a `bid`. Some carry `item.bonus_lists`.
  - `/auctions/{id}/commodities` holds stackables (cloth, herbs, bars), each with `unit_price` and `quantity`. **Its top-level key is `auctions`, not `commodities`.**
- Prices are in copper. `Last-Modified` updates hourly, at about :16 past the hour.
- Size: the Normal Alliance house has about 150k item auctions plus 57k commodity listings.
- Sample from US Normal (minimum unit price): Linen Cloth is 19c at Alliance, 26c at Horde and 22c at Blackwater. Copper Bar is 65c, 114c and 140c.

## 8. Decisions
- **The default market is EU Normal**: houses 111 (Alliance), 114 (Horde) and 117 (Blackwater). EU uses the same house IDs as US. Distinct items in EU house 111 come to 3,660, against ForeverMastery's 3,614-3,640 for EU Normal Alliance.
- **Prices are pulled live when the user searches, with no hourly ingest job.** One EU Normal search makes 6 requests (3 houses × item and commodity feeds). Gzipped, that's about 2.2 MB in total and 0.2-0.5 s per request, so under about 1.5 s in sequence and about 0.5 s in parallel. Each feed is cached in memory with its `Last-Modified`. The next search sends `If-Modified-Since` and gets a **304** (no body) until Blizzard's hourly refresh, at about :12 past the hour on EU.
- An hourly ingest only becomes worthwhile for price *history* or trends, which aren't in scope yet.
