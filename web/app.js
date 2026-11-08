"use strict";
// WoW Forever AH prices, the browser version. Data in data/*.json is rebuilt hourly by GitHub Actions.

const FACTIONS = ["alliance", "horde", "blackwater"];
const HOUSE = { alliance: "Alliance AH", horde: "Horde AH", blackwater: "Blackwater (neutral)", vendor: "Vendor" };
const BIT = { alliance: 1, horde: 2 };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ---------- storage (per-viewer conveniences only) ----------
const store = {
  get(k, d) { try { const v = localStorage.getItem("wowf." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("wowf." + k, JSON.stringify(v)); } catch {} },
};
const S = Object.assign({ ruleset: "normal", faction: "horde", location: null, timeValue: 0, roundTrip: false }, store.get("settings", {}));
let LIST = store.get("list", {}); // item id -> qty
const saveS = () => store.set("settings", S);
const saveList = () => { store.set("list", LIST); renderListCount(); };

// ---------- money ----------
function money(c, plain) {
  if (c == null || !isFinite(c)) return "–";
  if (c < 0) return "−" + money(-c, plain);
  c = Math.round(c);
  const g = Math.floor(c / 10000), s = Math.floor((c % 10000) / 100), cc = c % 100;
  const parts = [];
  if (g) parts.push(plain ? `${g.toLocaleString()}g` : `<span class="g">${g.toLocaleString()}g</span>`);
  if (s) parts.push(plain ? `${s}s` : `<span class="s">${s}s</span>`);
  if (cc || !parts.length) parts.push(plain ? `${cc}c` : `<span class="c">${cc}c</span>`);
  return plain ? parts.join(" ") : `<span class="money">${parts.join(" ")}</span>`;
}

// ---------- data ----------
const D = { meta: null, items: new Map(), prices: {}, travel: null, fuse: null, vendor: {} };

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function loadPrices(ruleset) {
  if (!D.prices[ruleset]) D.prices[ruleset] = await getJSON(`data/prices-${ruleset}.json`);
  return D.prices[ruleset];
}

const item = (id) => D.items.get(+id) || { id: +id, name: `Item #${id}`, q: "C", vendor: 0, n: 0 };

// ---------- market maths ----------
function book(id, faction) { return (D.prices[S.ruleset].houses[faction] || {})[id] || []; }

function quote(id, faction, qty) {
  const b = book(id, faction);
  let stock = 0, listings = 0, total = 0, bought = 0;
  for (let i = 0; i < b.length; i += 3) {
    const u = b[i], q = b[i + 1], n = b[i + 2];
    stock += q * n; listings += n;
    for (let k = 0; k < n && bought < qty; k++) { total += u * q; bought += q; }
  }
  // market = mean unit price of the cheapest 15% of stock
  let want = Math.max(1, Math.floor(stock * 0.15)), got = 0, sum = 0;
  for (let i = 0; i < b.length && got < want; i += 3) {
    const take = Math.min(b[i + 1] * b[i + 2], want - got);
    sum += b[i] * take; got += take;
  }
  return { faction, lowest: b.length ? b[0] : null, stock, listings, total: b.length ? total : null, bought, market: got ? sum / got : null };
}

const usable = (f) => !S.faction || f === S.faction || f === "blackwater" || f === "vendor";

// NPC vendors sell some reagents in lots (e.g. 5 Empty Vials for 20c), never run out, and are in every town.
function vendorQuote(id, qty) {
  const v = D.vendor[id];
  if (!v) return null;
  const [price, lot] = v, lots = Math.ceil(Math.max(qty, 1) / lot), unit = price / lot;
  return { faction: "vendor", lowest: Math.round(unit), stock: Infinity, listings: 0, total: lots * price, bought: lots * lot, market: Math.round(unit), lot };
}
function allQuotes(id, qty) {
  const qs = FACTIONS.map((f) => quote(id, f, qty)), v = vendorQuote(id, qty);
  return v ? qs.concat(v) : qs;
}

// ---------- travel ----------
let graphCache = {};
function graph(faction) {
  if (graphCache[faction]) return graphCache[faction];
  const T = D.travel, bit = BIT[faction], g = {};
  for (const [id, n] of Object.entries(T.nodes)) if (n.flags & bit) g[id] = [];
  for (const [a, b, cost, len] of T.paths) if (g[a] && g[b]) g[a].push([b, cost, len / T.speed]);
  for (const [a, b, min] of T.transports[faction] || []) if (g[a] && g[b]) { g[a].push([b, 0, min * 60]); g[b].push([a, 0, min * 60]); }
  return (graphCache[faction] = g);
}

function cheapestPath(src, dst, faction) {
  // What the flight master sells you: cheapest gold, faster on ties.
  const g = graph(faction);
  src = String(src); dst = String(dst);
  if (!g[src] || !g[dst]) return null;
  if (src === dst) return { cost: 0, secs: 0, dest: +src, via: [+src] };
  const open = [[0, 0, src, [+src]]], done = new Set();
  while (open.length) {
    let bi = 0;
    for (let i = 1; i < open.length; i++) if (open[i][0] < open[bi][0] || (open[i][0] === open[bi][0] && open[i][1] < open[bi][1])) bi = i;
    const [cost, secs, node, path] = open.splice(bi, 1)[0];
    if (node === dst) return { cost, secs, dest: +node, via: path };
    if (done.has(node)) continue;
    done.add(node);
    for (const [nxt, c, s] of g[node]) {
      if (done.has(String(nxt))) continue;
      const hop = path.length > 1 ? D.travel.hop : 0;
      open.push([cost + c, secs + s + hop, String(nxt), path.concat(+nxt)]);
    }
  }
  return null;
}

const tripTotal = (t, rate) => t.cost + (t.secs / 3600) * rate;

function route(src, dests, faction, rate) {
  let best = null;
  for (const d of dests) {
    const t = cheapestPath(src, d, faction);
    if (t && (!best || tripTotal(t, rate) < tripTotal(best, rate))) best = t;
  }
  return best;
}

function permutations(a) {
  if (a.length <= 1) return [a];
  return a.flatMap((x, i) => permutations(a.slice(0, i).concat(a.slice(i + 1))).map((p) => [x, ...p]));
}

function tour(src, houses, faction, rate, roundTrip) {
  let best = null;
  for (const order of permutations(houses)) {
    let cur = src, cost = 0, secs = 0, via = [src], stops = {}, ok = true;
    for (const h of order) {
      const leg = route(cur, D.travel.houses[h], faction, rate);
      if (!leg) { ok = false; break; }
      cost += leg.cost; secs += leg.secs; via = via.concat(leg.via.slice(1)); stops[h] = leg.dest; cur = leg.dest;
    }
    if (!ok) continue;
    if (roundTrip && cur !== src) {
      const back = cheapestPath(cur, src, faction);
      if (!back) continue;
      cost += back.cost; secs += back.secs; via = via.concat(back.via.slice(1));
    }
    const t = { cost, secs, via, stops };
    if (!best || tripTotal(t, rate) < tripTotal(best, rate)) best = t;
  }
  return best;
}

const nodeName = (id) => (D.travel.nodes[id] || { name: "?" }).name;
const shortName = (id) => nodeName(id).split(",")[0];
const rate = () => (S.timeValue || 0) * 10000;
const here = (t) => t && t.cost === 0 && t.secs === 0;
const fmtTrip = (t) => (here(t) ? "here" : `${money(t.cost)} · ${Math.max(1, Math.round(t.secs / 60))} min`);
const routeText = (t) => t.via.map(shortName).join(" › ") + (S.roundTrip ? " (round trip)" : ", then hearth back");

function myLoc() {
  if (!S.faction) return null;
  const loc = S.location;
  const n = D.travel.nodes[loc];
  return n && n.flags & BIT[S.faction] ? +loc : D.travel.home[S.faction];
}

function trips() {
  if (!S.faction) return null;
  const out = {};
  for (const f of FACTIONS) if (usable(f)) {
    const t = route(myLoc(), D.travel.houses[f], S.faction, rate());
    if (t && S.roundTrip && !here(t)) {
      const back = cheapestPath(t.dest, myLoc(), S.faction);
      if (back) out[f] = { cost: t.cost + back.cost, secs: t.secs + back.secs, dest: t.dest, via: t.via.concat(back.via.slice(1)) };
      else out[f] = t;
    } else out[f] = t;
  }
  return out;
}

// ---------- search ----------
function search(q, limit = 8) {
  q = q.trim().toLowerCase().replace(/\s+/g, " ");
  if (!q) return [];
  const idm = q.match(/^#?(\d+)$/);
  if (idm && D.items.has(+idm[1])) return [D.items.get(+idm[1])];
  const words = q.split(" ");
  const hits = D.fuse.search(q, { limit: 60 });
  // Fuse can miss word-start abbreviations ("cop bar"); add those directly (4-5k names is cheap to scan).
  const seen = new Set(hits.map((h) => h.item.id));
  for (const it of D.items.values()) {
    if (seen.has(it.id)) continue;
    const toks = it.name.toLowerCase().split(/[ \-:',]+/);
    if (words.every((w) => toks.some((t) => t.startsWith(w)))) hits.push({ item: it, score: 0.5 });
  }
  const scored = hits.map(({ item: it, score }) => {
    const name = it.name.toLowerCase(), toks = name.split(/[ \-:',]+/);
    let s = (1 - score) * 60;
    if (name === q) s += 100;
    else if (name.startsWith(q)) s += 30;
    else if (words.every((w) => toks.some((t) => t.startsWith(w)))) s += 22 + (toks[0].startsWith(words[0]) ? 6 : 0);
    else if (name.includes(q)) s += 10;
    if (bestLowest(it.id) != null) s += 15; // on sale in your market right now
    s += Math.min(12, Math.log10(it.n + 1) * 3) - name.length / 40;
    return [s, it];
  });
  scored.sort((a, b) => b[0] - a[0]);
  return scored.slice(0, limit).map((x) => x[1]);
}

function bestLowest(id) {
  let best = null;
  for (const f of FACTIONS) if (usable(f)) { const b = book(id, f); if (b.length && (best == null || b[0] < best)) best = b[0]; }
  const v = vendorQuote(id, 1);
  if (v && (best == null || v.lowest < best)) best = v.lowest;
  return best;
}

// ---------- price view ----------
function renderItem(id, qty) {
  const it = item(id), tr = trips(), r = rate();
  const quotes = allQuotes(id, qty);
  if (tr) tr.vendor = { cost: 0, secs: 0, dest: myLoc(), via: [myLoc()] };
  const goods = (q) => (qty > 1 ? q.total : q.lowest);
  const travelCost = (q) => (tr && tr[q.faction] ? tripTotal(tr[q.faction], r) : 0);
  const landed = (q) => goods(q) + travelCost(q);
  const cands = quotes.filter((q) => q.stock && usable(q.faction) && (!tr || tr[q.faction]));
  const enough = cands.filter((q) => q.bought >= qty);
  const pool = enough.length ? enough : cands;
  const per = (q) => (enough.length ? 1 : q.bought);
  const best = pool.reduce((a, q) => (!a || landed(q) / per(q) < landed(a) / per(a) ? q : a), null);
  const cheapest = pool.reduce((a, q) => (!a || goods(q) / per(q) < goods(a) / per(a) ? q : a), null);

  let html = `<div class="card"><div class="item-title"><h2 class="q-${it.q}">${esc(it.name)}${qty > 1 ? ` ×${qty}` : ""} <span class="id">#${it.id}</span></h2>
    <button class="iconbtn" id="share">Share</button></div>`;
  if (!cands.length && !quotes.some((q) => q.stock)) {
    html += `<p class="warn">Not listed on any ${esc(rulesetLabel())} auction house right now.</p>`;
    if (it.vendor) html += `<p class="note">Vendors buy it for ${money(it.vendor)}.</p>`;
    $("#result").innerHTML = html + "</div>";
    bindShare(id, qty);
    return;
  }
  html += `<div class="houses">`;
  for (const q of quotes) {
    if (!usable(q.faction)) continue;
    const t = tr && tr[q.faction];
    const notes = [];
    if (q.faction === "vendor") { if (q.lot > 1) notes.push(`sold in ${q.lot}s`); }
    else if (qty > 1 && q.stock && q.bought < qty) notes.push(`only ${q.stock.toLocaleString()} listed`);
    else if (qty > 1 && q.bought > qty) notes.push(`${q.bought} in whole stacks`);
    html += `<div class="house ${q === best ? "best" : ""} ${q.stock ? "" : "none"}">
      <div class="name">${HOUSE[q.faction]}${q === best ? '<span class="badge">★ BEST</span>' : ""}</div>
      <div class="total">${q.stock ? money(tr ? landed(q) : goods(q)) : "none"}</div>
      <div class="sub">${q.stock ? `${qty > 1 ? `${qty} for ${money(q.total)} · ` : ""}lowest ${money(q.lowest)} · market ${money(q.market)}` : "not listed"}</div>
      <div class="sub r">${q.faction === "vendor" ? "unlimited" : q.stock ? `${q.stock.toLocaleString()} in stock` : ""}</div>
      ${q.faction === "vendor" ? `<div class="sub">✓ trade supplies vendors in every town</div>` : t ? `<div class="sub">${here(t) ? "✓ you're here" : `✈ ${fmtTrip(t)} to ${esc(shortName(t.dest))}`}</div>` : ""}
      ${notes.length ? `<div class="sub r">${notes.join(", ")}</div>` : t ? "<div></div>" : ""}
    </div>`;
  }
  html += `</div>`;

  // enemy-faction footnote
  for (const q of quotes) if (!usable(q.faction) && q.stock && best && goods(q) < goods(best))
    html += `<p class="note">${HOUSE[q.faction]} (enemy faction) has it for ${money(goods(q))}. An alt could buy it there.</p>`;

  const vq = cands.find((q) => q.faction === "vendor");
  if (best && vq) {
    const ah = cands.filter((q) => q.faction !== "vendor");
    const each = `${money(vq.market)} each${vq.lot > 1 ? `, sold in ${vq.lot}s` : ""}`;
    if (best === vq) {
      const a = ah.length ? ah.reduce((x, q) => (goods(q) < goods(x) ? q : x)) : null;
      html += `<p class="verdict good"><b>Buy from a trade supplies vendor</b> (${each})${a && goods(a) > goods(vq) ? `. That's ${money(goods(a) - goods(vq))} less than the ${HOUSE[a.faction]}` : ""}.</p>`;
    } else {
      html += `<p class="note">Vendors sell it too (${each}), but the AH is cheaper right now.</p>` + verdict(best, cheapest, ah, goods, travelCost, tr);
    }
  } else if (best) html += verdict(best, cheapest, cands, goods, travelCost, tr);
  if (it.vendor && best && best.lowest < it.vendor) html += `<p class="note warn">Listed below vendor price (${money(it.vendor)}): buy and vendor it for profit.</p>`;
  if (tr && r) html += `<p class="note">Totals include your time at ${S.timeValue}g/hour.</p>`;
  $("#result").innerHTML = html + "</div>";
  bindShare(id, qty);
}

function verdict(best, cheapest, cands, goods, travelCost, tr) {
  const H = HOUSE[best.faction];
  if (!tr) {
    const others = cands.filter((q) => q !== best);
    let s = `<p class="verdict good"><b>Best: ${H}</b>`;
    if (others.length) {
      const run = others.reduce((a, q) => (goods(q) < goods(a) ? q : a));
      const a = goods(best), b = goods(run);
      if (b > a) s += `. That's ${money(b - a)} (${Math.round(((b - a) / b) * 100)}%) less than ${HOUSE[run.faction]}`;
      else if (a === b) s += `, tied with ${HOUSE[run.faction]}`;
    }
    return s + ".</p>";
  }
  const t = tr[best.faction], loc = shortName(myLoc());
  if (here(t)) {
    let s = `<p class="verdict good"><b>Buy at the ${H} right here in ${esc(loc)}.</b>`;
    if (cheapest && cheapest !== best && goods(cheapest) < goods(best)) {
      const ct = tr[cheapest.faction], timeCost = tripTotal(ct, rate()) - ct.cost;
      s += ` ${HOUSE[cheapest.faction]} is ${money(goods(best) - goods(cheapest))} cheaper, but getting there costs ${fmtTrip(ct)}${timeCost ? ` (+${money(timeCost)} of your time)` : ""}, so it's <b>not worth the trip</b>.`;
    }
    return s + "</p>";
  }
  const local = cands.filter((q) => here(tr[q.faction]));
  let s;
  if (local.length) {
    const alt = local.reduce((a, q) => (goods(q) < goods(a) ? q : a));
    const net = goods(alt) - goods(best) - travelCost(best);
    s = `<p class="verdict good"><b>Worth the trip: ${H} in ${esc(shortName(t.dest))}</b> saves <b>${money(net)}</b> net. It's ${money(goods(alt) - goods(best))} cheaper, minus ${money(travelCost(best))} travel.</p>`;
  } else {
    s = `<p class="verdict good"><b>Best: ${H} in ${esc(shortName(t.dest))}</b>, ${fmtTrip(t)} away.</p>`;
  }
  return s + `<p class="route">Route: ${esc(routeText(t))}</p>`;
}

function bindShare(id, qty) {
  const btn = $("#share");
  if (!btn) return;
  btn.onclick = async () => {
    const url = `${location_href()}#i=${id}${qty > 1 ? `&n=${qty}` : ""}${S.ruleset !== "normal" ? `&r=${S.ruleset}` : ""}`;
    const text = `${item(id).name}${qty > 1 ? ` ×${qty}` : ""}: WoW Forever AH prices`;
    try {
      if (navigator.share) await navigator.share({ title: text, url });
      else { await navigator.clipboard.writeText(url); toast("Link copied"); }
    } catch {}
  };
}
const location_href = () => window.location.href.split("#")[0];

// ---------- buy list ----------
function combos(arr) {
  const out = [];
  for (let m = 0; m < 1 << arr.length; m++) out.push(arr.filter((_, i) => m & (1 << i))); // m=0: vendors only
  return out;
}

function allocate(wanted, houses) {
  const purchases = [], short = {};
  for (const [id, qty] of Object.entries(wanted)) {
    const ls = [];
    // [unit, units per entry, entries, house, cost per entry]
    for (const h of houses) { const b = book(id, h); for (let i = 0; i < b.length; i += 3) ls.push([b[i], b[i + 1], b[i + 2], h, b[i] * b[i + 1]]); }
    const v = D.vendor[id];
    if (v) ls.push([v[0] / v[1], v[1], Math.ceil(qty / v[1]), "vendor", v[0]]);
    ls.sort((a, b) => a[0] - b[0]);
    const per = {};
    let got = 0;
    outer: for (const [, q, n, h, cost] of ls) for (let k = 0; k < n; k++) {
      if (got >= qty) break outer;
      per[h] = per[h] || [0, 0]; per[h][0] += q; per[h][1] += cost; got += q;
    }
    for (const [h, [units, cost]] of Object.entries(per)) purchases.push({ id: +id, house: h, units, cost });
    if (got < qty) short[id] = qty - got;
  }
  return { purchases, short };
}

function planList() {
  const usableH = FACTIONS.filter(usable), r = rate(), plans = [];
  for (const houses of combos(usableH)) {
    const { purchases, short } = allocate(LIST, houses);
    if (!houses.every((h) => purchases.some((p) => p.house === h))) continue;
    const goods = purchases.reduce((s, p) => s + p.cost, 0);
    const trip = S.faction ? tour(myLoc(), houses, S.faction, r, S.roundTrip) : null;
    if (S.faction && !trip) continue;
    const missing = Object.values(short).reduce((a, b) => a + b, 0);
    plans.push({ houses, purchases, short, missing, goods, trip, total: goods + (trip ? tripTotal(trip, r) : 0) });
  }
  plans.sort((a, b) => a.missing - b.missing || a.total - b.total);
  return plans;
}

function renderList() {
  const ids = Object.keys(LIST);
  if (!ids.length) {
    $("#list").innerHTML = `<div class="card"><p class="empty">Your buy list is empty.<br>Search above and tap an item to add it.</p></div>`;
    return;
  }
  let html = `<div class="card">`;
  for (const id of ids) {
    const it = item(id);
    html += `<div class="list-row"><span class="q-${it.q}">${esc(it.name)}</span>
      <input type="number" min="1" inputmode="numeric" value="${LIST[id]}" data-id="${id}" aria-label="Quantity">
      <button class="x" data-rm="${id}" aria-label="Remove">×</button></div>`;
  }
  html += `<div style="display:flex;justify-content:space-between;margin-top:8px">
    <button class="iconbtn" id="sharelist">Share list</button><button class="iconbtn" id="clearlist">Clear</button></div></div>`;

  const plans = planList();
  if (plans.length) {
    const p = plans[0], r = rate();
    html += `<div class="card"><div class="item-title"><h2>Cheapest plan</h2><b>${money(p.total)}</b></div>`;
    const order = (p.purchases.some((x) => x.house === "vendor") ? ["vendor"] : []).concat(p.trip ? Object.keys(p.trip.stops) : p.houses);
    for (const h of order) {
      const at = h === "vendor" ? " · any town" : p.trip ? ` · ${esc(shortName(p.trip.stops[h]))}` : "";
      html += `<div class="stop"><h3>${HOUSE[h]}${at}</h3>`;
      for (const x of p.purchases.filter((x) => x.house === h)) {
        const it = item(x.id);
        html += `<div class="line"><span><span class="q-${it.q}">${esc(it.name)}</span> ×${x.units}${x.units !== LIST[x.id] && !p.short[x.id] && order.length === 1 ? ` <span class="note">(stacks)</span>` : ""}</span><span>${money(x.cost)}</span></div>`;
      }
      html += `</div>`;
    }
    const bought = new Set(p.purchases.map((x) => x.id));
    for (const id of ids) if (!bought.has(+id)) html += `<p class="note warn">${esc(item(id).name)}: not listed anywhere you can buy it.</p>`;
    for (const [id, miss] of Object.entries(p.short)) if (bought.has(+id)) html += `<p class="note warn">${esc(item(id).name)}: only ${LIST[id] - miss} of ${LIST[id]} available.</p>`;
    html += `<p class="note">Goods ${money(p.goods)}${p.trip && !here(p.trip) ? ` + travel ${fmtTrip(p.trip)}` : ""}${p.trip && r ? ` + time ${money(tripTotal(p.trip, r) - p.trip.cost)}` : ""} = <b>${money(p.total)}</b></p>`;
    if (p.trip && !here(p.trip)) {
      const local = plans.find((x) => x.trip && here(x.trip) && x.missing === p.missing);
      if (local) html += `<p class="verdict good"><b>The trip is worth it.</b> It saves <b>${money(local.total - p.total)}</b> compared with buying everything at the ${local.houses.map((h) => HOUSE[h]).join(" + ") || "vendors"}.</p>`;
      html += `<p class="route">Route: ${esc(routeText(p.trip))}</p>`;
    } else if (p.trip) {
      html += `<p class="verdict good"><b>Buy it all here in ${esc(shortName(myLoc()))}.</b></p>`;
    }
    if (plans.length > 1) {
      html += `<div class="alts">Other options:`;
      for (const o of plans.slice(1, 4))
        html += `<div><span>${o.houses.map((h) => HOUSE[h]).join(" + ") || "Vendors only"}${o.missing > p.missing ? ` (${o.missing} short)` : ""}</span><span>${o.total >= p.total ? "+" : ""}${money(o.total - p.total, true)}</span></div>`;
      html += `</div>`;
    }
    html += `</div>`;
  }
  $("#list").innerHTML = html;
  $("#list").querySelectorAll("input[data-id]").forEach((el) => (el.onchange = () => {
    const v = Math.max(1, parseInt(el.value) || 1); LIST[el.dataset.id] = v; saveList(); renderList();
  }));
  $("#list").querySelectorAll("[data-rm]").forEach((el) => (el.onclick = () => { delete LIST[el.dataset.rm]; saveList(); renderList(); }));
  $("#clearlist").onclick = () => { if (confirm("Clear the buy list?")) { LIST = {}; saveList(); renderList(); } };
  $("#sharelist").onclick = async () => {
    const enc = Object.entries(LIST).map(([i, n]) => `${i}x${n}`).join(",");
    const url = `${location_href()}#list=${enc}`;
    try { if (navigator.share) await navigator.share({ title: "WoW Forever buy list", url }); else { await navigator.clipboard.writeText(url); toast("Link copied"); } } catch {}
  };
}

function renderListCount() { const n = Object.keys(LIST).length; $("#listcount").textContent = n ? `(${n})` : ""; }

// ---------- UI wiring ----------
let tab = "price", current = null, sel = -1, results = [];
const rulesetLabel = () => (D.meta.rulesets.find((r) => r.id === S.ruleset) || {}).label || S.ruleset;

function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 1600); }

function setTab(t) {
  tab = t;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  $("#tab-price").hidden = t !== "price";
  $("#tab-list").hidden = t !== "list";
  $("#q").placeholder = t === "list" ? "Add an item to your list…" : "Search an item… (typos ok)";
  if (t === "list") renderList();
}

function rerender() {
  graphCache = {};
  if (current) renderItem(current.id, current.qty);
  if (tab === "list") renderList();
}

function renderSugg() {
  const box = $("#sugg");
  if (!results.length) { box.classList.remove("open"); box.innerHTML = ""; return; }
  box.innerHTML = results.map((it, i) => {
    const p = bestLowest(it.id);
    return `<button data-i="${i}" class="${i === sel ? "act" : ""}"><span class="q-${it.q}">${esc(it.name)}</span><span class="p">${p == null ? "not listed" : money(p, true)}</span></button>`;
  }).join("");
  box.classList.add("open");
  box.querySelectorAll("button").forEach((b) => b.addEventListener("mousedown", (e) => { e.preventDefault(); choose(results[+b.dataset.i]); }));
}

function choose(it) {
  if (!it) return;
  const qty = Math.max(1, parseInt($("#qty").value) || 1);
  results = []; renderSugg();
  if (tab === "list") {
    LIST[it.id] = (LIST[it.id] || 0) + qty; saveList();
    $("#q").value = ""; $("#qty").value = 1;
    toast(`Added ${qty} × ${it.name}`);
    renderList();
    return;
  }
  $("#q").value = it.name;
  $("#q").blur();
  current = { id: it.id, qty };
  history.replaceState(null, "", `#i=${it.id}${qty > 1 ? `&n=${qty}` : ""}`);
  renderItem(it.id, qty);
}

function populateLocations() {
  const sel = $("#location");
  $("#locwrap").hidden = !S.faction;
  if (!S.faction) return;
  const nodes = Object.entries(D.travel.nodes).filter(([, n]) => n.flags & BIT[S.faction]).sort((a, b) => a[1].name.localeCompare(b[1].name));
  const cities = new Set(Object.values(D.travel.houses).flat().map(String));
  sel.innerHTML = `<optgroup label="Auction house towns">${nodes.filter(([id]) => cities.has(id)).map(([id, n]) => `<option value="${id}">${esc(n.name)}</option>`).join("")}</optgroup>
    <optgroup label="Other flight points">${nodes.filter(([id]) => !cities.has(id)).map(([id, n]) => `<option value="${id}">${esc(n.name)}</option>`).join("")}</optgroup>`;
  sel.value = String(myLoc());
}

function syncControls() {
  document.querySelectorAll("#faction button").forEach((b) => b.classList.toggle("on", b.dataset.v === (S.faction || "")));
  document.querySelectorAll("#ret button").forEach((b) => b.classList.toggle("on", (b.dataset.v === "fly") === !!S.roundTrip));
  $("#ruleset").value = S.ruleset;
  $("#timevalue").value = S.timeValue || "";
  populateLocations();
}

function updateFresh() {
  const p = D.prices[S.ruleset];
  if (!p) return;
  const mins = Math.round((Date.now() / 1000 - p.updated) / 60);
  const t = new Date(p.updated * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("#fresh").textContent = `${D.meta.region.toUpperCase()} · prices from ${t} (${mins < 1 ? "just now" : mins < 120 ? `${mins} min ago` : `${Math.round(mins / 60)} h ago`})`;
}

async function applyHash() {
  const h = new URLSearchParams(window.location.hash.slice(1));
  if (h.get("r") && D.meta.rulesets.some((r) => r.id === h.get("r"))) { S.ruleset = h.get("r"); await loadPrices(S.ruleset); syncControls(); updateFresh(); }
  if (h.get("list")) {
    const incoming = {};
    for (const part of h.get("list").split(",")) { const [i, n] = part.split("x").map(Number); if (i && n) incoming[i] = n; }
    if (Object.keys(incoming).length && (!Object.keys(LIST).length || confirm("Replace your buy list with the shared one?"))) { LIST = incoming; saveList(); }
    setTab("list");
    history.replaceState(null, "", location_href());
    return;
  }
  const id = +h.get("i");
  if (id) {
    const qty = Math.max(1, +h.get("n") || 1);
    $("#qty").value = qty;
    $("#q").value = item(id).name;
    current = { id, qty };
    renderItem(id, qty);
  }
}

async function init() {
  try {
    const [meta, items, travel, vend] = await Promise.all([getJSON("data/meta.json"), getJSON("data/items.json"), getJSON("data/travel.json"), getJSON("data/vendor.json").catch(() => ({}))]);
    D.meta = meta; D.travel = travel; D.vendor = vend;
    for (const [id, [name, q, vendor, n]] of Object.entries(items)) D.items.set(+id, { id: +id, name, q, vendor, n });
    D.fuse = new Fuse([...D.items.values()], { keys: ["name"], includeScore: true, threshold: 0.42, ignoreLocation: true, minMatchCharLength: 2 });
    if (!meta.rulesets.some((r) => r.id === S.ruleset)) S.ruleset = meta.rulesets[0].id;
    $("#ruleset").innerHTML = meta.rulesets.map((r) => `<option value="${r.id}">${esc(r.label)}</option>`).join("");
    await loadPrices(S.ruleset);
  } catch (e) {
    $("#fresh").textContent = "couldn't load prices";
    $("#result").innerHTML = `<div class="card warn">Couldn't load the price data (${esc(e.message)}). Try again in a minute.</div>`;
    return;
  }
  syncControls(); updateFresh(); renderListCount();
  setInterval(updateFresh, 60000);

  const q = $("#q");
  q.addEventListener("input", () => { results = search(q.value); sel = results.length ? 0 : -1; renderSugg(); });
  q.addEventListener("focus", () => { if (q.value && (!current || q.value !== item(current.id).name)) { results = search(q.value); renderSugg(); } });
  q.addEventListener("blur", () => setTimeout(() => { results = []; renderSugg(); }, 120));
  q.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && results.length) { sel = (sel + 1) % results.length; renderSugg(); e.preventDefault(); }
    else if (e.key === "ArrowUp" && results.length) { sel = (sel - 1 + results.length) % results.length; renderSugg(); e.preventDefault(); }
    else if (e.key === "Enter") { const r = results.length ? results : search(q.value); choose(r[Math.max(sel, 0)]); e.preventDefault(); }
    else if (e.key === "Escape") { results = []; renderSugg(); }
  });
  $("#qty").addEventListener("change", () => { if (current && tab === "price") { current.qty = Math.max(1, parseInt($("#qty").value) || 1); renderItem(current.id, current.qty); } });

  document.querySelectorAll("#faction button").forEach((b) => (b.onclick = () => { S.faction = b.dataset.v || null; saveS(); syncControls(); rerender(); }));
  document.querySelectorAll("#ret button").forEach((b) => (b.onclick = () => { S.roundTrip = b.dataset.v === "fly"; saveS(); syncControls(); rerender(); }));
  $("#ruleset").onchange = async (e) => { S.ruleset = e.target.value; saveS(); await loadPrices(S.ruleset); updateFresh(); rerender(); };
  $("#location").onchange = (e) => { S.location = +e.target.value; saveS(); rerender(); };
  $("#timevalue").onchange = (e) => { S.timeValue = Math.max(0, +e.target.value || 0); saveS(); rerender(); };
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
  window.addEventListener("hashchange", applyHash);
  await applyHash();
}

init();
