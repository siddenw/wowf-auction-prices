# wowf-auction-prices

Find the cheapest WoW Forever auction house for an item, using live data from the Blizzard API.
Item search is fuzzy and autocompletes as you type. If the cheaper house is somewhere else, wowf also works out whether the flight there is worth it.
The defaults are the **EU Normal** market, **Horde**, starting in **Orgrimmar**.

```
$ uv run wowf 50 linnen clth
Linen Cloth  #2589  ×50
     House                   Lowest    Market     Buy 50     Stock         Travel      Total
★    Horde AH                   16c       23c    10s 24c    25,842           here    10s 24c
     Blackwater (neutral)       22c       22c    12s 10c     1,429    2s 20c · 9m    14s 30c
  Best: Horde AH, right here in Orgrimmar.
```

## Setup

1. Create a free API client at <https://develop.battle.net/access/clients>.
2. Put its credentials in `.env`, which git ignores, or in `~/.config/wowf/.env`:
   ```
   BLIZZARD_CLIENT_ID=...
   BLIZZARD_CLIENT_SECRET=...
   ```
3. Run it with uv:
   ```
   uv run wowf                 # interactive mode with autocomplete
   uv tool install .           # optional: puts `wowf` on your PATH
   ```

On first run, wowf downloads an item-name catalogue of about 28k items. This takes about 20s and only happens once.

## Usage

| Command | What it does |
|---|---|
| `wowf` | Interactive search. Type to get fuzzy suggestions with prices, Tab or arrow keys to pick one. Commands: `/search`, `/houses`, `/at`, `/travel`, `/time`, `/return`, `/region`, `/ruleset`, `/faction`, `/refresh`, `/help` |
| `wowf linen cloth` | Price check across Alliance, Horde and Blackwater (neutral) |
| `wowf 40 linen` · `wowf linen x40` · `wowf linen -n 40` | Cost to buy 40, walking the cheapest listings. Whole stacks are counted |
| `wowf "#2589"` | Look up an item by ID |
| `wowf search copper` | List matching items with the cheapest house for each |
| `wowf houses` | Show item and listing counts, seller cut and data time for each house |
| `wowf at gadgetzan` | Set where you are. Accepts any flight point, fuzzy matched, with short names like `org`, `uc`, `tb`, `bb` and `xr` |
| `wowf travel` | Show the trip cost from your location to each house you can use |
| `wowf config time 20g` | What an hour of your time is worth. This counts travel time against the price difference |
| `wowf config return fly` | Count the flight back too. The default is `hearth` (one-way) |
| `wowf config faction horde` | Save a default. Keys are `region` (eu/us/kr/tw), `ruleset` (normal/pvp/rp4/rp5), `faction` (alliance/horde/none), `location`, `time` and `return` |
| `-r us -s pvp -f alliance` | One-off overrides for a single command |

Once a faction is set, the enemy faction's house is hidden. A footnote mentions it if it's cheaper, in case an alt can buy there. Blackwater is open to both factions.

### Travel cost

**Travel** is the cheapest flight route from your location to the nearest auctioneer for that house, including free zeppelins and boats. **Total** is the goods plus the travel, plus your time if you've set a time value. The ★ goes to the lowest total, and a line underneath tells you whether the trip is worth it:

```
Black Pearl  #7971
     House                   Lowest    Market    Stock         Travel      Total
     Horde AH                2g 78s    2g 78s       61           here     2g 78s
★    Blackwater (neutral)       50s       50s        4    2s 20c · 9m    52s 20c
  Worth the trip: Blackwater (neutral) in Booty Bay saves 2g 25s 80c net (2g 28s cheaper, minus 2s 20c travel) vs buying here.
  Route: Orgrimmar > Crossroads > Ratchet > Booty Bay, then hearth back
```

Caveats:
- **Flight data:** costs and route lengths come from the **Classic Era** flight network, which `scripts/build_taxi.py` pulls from wago.tools. Forever has no client-data export yet, and its new zones and any changed prices aren't covered.
- **Flight times** are estimates: path length ÷ 32 yd/s, plus 15s per stopover. Zeppelin and boat trips are 5–6 min including the average wait.
- **Blackwater auctioneers** are assumed to be in Booty Bay, Gadgetzan and Everlook. This isn't confirmed for Forever yet.
- **Your location:** the Blizzard API doesn't expose a character's location, so set it yourself with `wowf at`.

### Buy lists (interactive mode)

In `wowf` interactive mode, build a shopping list and let it work out the cheapest way to buy all of it, travel included:

```
/add 40 linen cloth      /add 200 copper bar      /add black pearl
/rm 10 linen             /list                    /clear      /use crafting   /lists
```

`/list` tries every combination of houses you could visit. It buys each item from the cheapest whole listings across those houses, splitting an item between houses if one runs short. Then it adds the cost of a multi-stop route, using `/time` and `/return` if you've set them, and shows the best plan:

```
Item                   Want    Buy at                  Units          Cost    Each
Linen Cloth              30    Horde · Orgrimmar          36        6s 28c     17c
Copper Bar              200    Horde · Orgrimmar         205    1g 83s 39c     89c
Black Pearl               1    Blackwater · Booty Bay      1           50s     50s
  Goods 2g 40s 7c + travel 2s 20c · 9m = 2g 42s 27c
  The trip is worth it: saves 5g 3s 80c vs buying everything at Horde AH.
  Route: Orgrimmar > Crossroads > Ratchet > Booty Bay, then hearth back
  Other options:
    Blackwater (neutral)       3g 76s 36c  (+1g 34s 9c)  · 2s 20c · 9m
    Horde AH                    7g 46s 7c  (+5g 3s 80c)  · here
```

Lists are saved to `~/.config/wowf/lists.json`.

**Columns:**
- **Lowest** is the cheapest single unit.
- **Market** is the mean of the cheapest 15% of stock, so one cheap listing doesn't skew it.
- **Buy N** is what N units actually cost.

## Website (for the WhatsApp group)

There's also a static, phone-friendly version in `web/`, rebuilt hourly by GitHub Actions and hosted on GitHub Pages. It has search with autocomplete, faction and location, travel cost, and buy lists (saved on each phone). Share buttons create links that open an item or a whole buy list.

- **Data:** `.github/workflows/pages.yml` runs `uv run wowf-site _site` every hour. That fetches all EU rulesets, writes `_site/data/*.json` and deploys it.
- **Credentials:** the job reads them from the repository secrets `BLIZZARD_CLIENT_ID` and `BLIZZARD_CLIENT_SECRET`. They're only used to fetch data and never appear in the site.
- **Build locally:** `uv run wowf-site _site && python3 -m http.server -d _site`
- **Keep-alive:** `.github/workflows/keepalive.yml` adds an empty commit after 45 quiet days, so GitHub doesn't switch the hourly schedule off.

## How it works

- Blizzard publishes every Forever auction house once an hour. wowf fetches the three houses for your market live when you search, which is about 2 MB gzipped.
- It caches the data on disk in `~/.cache/wowf`. After 5 minutes it asks Blizzard whether anything changed, and gets a cheap "not modified" reply until the next hourly update.
- The parsed index is cached too, so a warm run takes under a second.
- Item names come from the Forever item data, with Classic Era data filling the roughly 40% of items Forever's data is missing.
- See `docs/RESEARCH.md` for the API details, how house IDs map to markets, and prior art.

## Development

```
uv run --group dev pytest
```
