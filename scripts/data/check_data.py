"""Checks FINILA's data for logical errors, so a visitor never meets a broken number.

Reads the data files the site shows (from the live site with CHECK_LIVE=1, else from the
working copy) and tests what a reader would notice: stale prices, market values off by a
unit, duplicate companies, big companies with no sector or no market value, absurd ratios,
sources that stopped answering. Writes data/check-report.txt and prints GitHub annotations.
Exit code 1 when an ERROR is found (the workflow turns red), warnings never fail the run.
"""
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = os.environ.get("CHECK_LIVE") == "1"
SITE = "https://www.finila.com/"
TODAY = date.today()

errors, warnings, notes = [], [], []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def load(path):
    if LIVE and path.startswith("js/"):
        req = urllib.request.Request(f"{SITE}{path}?check={TODAY}", headers={"User-Agent": "finila-data-check"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    return json.loads((ROOT / path).read_text())


def day(s):
    return date.fromisoformat(str(s)[:10])


def age(s):
    return (TODAY - day(s)).days


def trading_days_since(d):
    """Weekdays between d and today (holidays aside), so a Monday morning is not 'stale'."""
    n, cur = 0, d
    while cur < TODAY:
        cur = date.fromordinal(cur.toordinal() + 1)
        n += cur.weekday() < 5
    return n


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def companies_with_values():
    """Companies joined with prices, market value worked out exactly as the pages do (heatmap.js)."""
    c = load("js/companies.json")
    p = load("js/company-prices.json")
    rows = [dict(zip(c["fields"], r)) for r in c["rows"]]
    pf = p["fields"]
    for r in rows:
        pr = p["rows"].get(r.get("isin") or "")
        if pr:
            r.update({f"p_{k}": v for k, v in zip(pf, pr)})
        fx = (r["revenueEur"] / r["revenue"] if is_num(r.get("revenueEur")) and is_num(r.get("revenue")) and r["revenue"]
              else 1 if r.get("currency") == "EUR" else None)
        shares = r["shares"] if is_num(r.get("shares")) and r["shares"] > 0 else None   # as the pages: universe.py fills it
        r["fx"], r["sharesUsed"] = fx, shares
        r["mcap"] = r["p_price"] * shares if is_num(r.get("p_price")) and shares else None   # EUR millions
        r["pe"] = r["p_price"] / (r["eps"] * fx) if fx and is_num(r.get("eps")) and r["eps"] > 0 and is_num(r.get("p_price")) else None
    return c, p, rows


def check_companies():
    c, p, rows = companies_with_values()
    if age(c["updated"]) > 3:
        err(f"Company list is {age(c['updated'])} days old (updated {c['updated']}): the nightly refresh is not running.")
    newest = max(day(r["p_date"]) for r in rows if r.get("p_date"))
    late = trading_days_since(newest)
    if late > 1:
        err(f"Newest share price is from {newest} ({late} trading days ago): price updates stopped.")
    priced = [r for r in rows if r.get("p_date")]
    fresh = sum(1 for r in priced if day(r["p_date"]) == newest)
    if priced and fresh / len(priced) < 0.5:
        warn(f"Only {fresh} of {len(priced)} prices are from the newest day {newest}.")
    notes.append(f"{len(rows)} companies, {len(priced)} with a price, {sum(1 for r in rows if r['mcap'])} with a market value; newest price {newest}.")

    for key in ("isin", "lei"):
        dup = [k for k, n in Counter(r.get(key) for r in rows if r.get(key)).items() if n > 1]
        for k in dup:
            err(f"Two companies share {key.upper()} {k}: {', '.join(r['name'] for r in rows if r.get(key) == k)}.")

    for r in rows:
        name, m = f"{r['name']} ({r.get('isin')})", r["mcap"]
        if m and m > 1_000_000:
            err(f"{name}: market value €{m / 1000:,.0f}B is impossible, a unit is wrong (shares {r['sharesUsed']}).")
        if m and is_num(r.get("revenueEur")) and r["revenueEur"] > 500:
            ps = m / r["revenueEur"]
            if ps > 150 or ps < 0.003:
                err(f"{name}: market value €{m:,.0f}M against revenue €{r['revenueEur']:,.0f}M (P/S {ps:.3g}): shares, EPS or price unit is off.")
        if r["pe"] and r["pe"] < 0.5:
            warn(f"{name}: P/E {r['pe']:.2f} is impossible (price {r.get('p_price')}, EPS {r.get('eps')} {r.get('currency')}).")
        if is_num(r.get("shares")) and is_num(r.get("eps")) and is_num(r.get("netIncome")) and r["eps"] * r["netIncome"] > 0:
            implied = r["netIncome"] / r["eps"]
            if not 0.6 < implied / r["shares"] < 1.6:
                warn(f"{name}: shares in issue {r['shares']:,.1f}M but report implies {implied:,.1f}M.")
        chg = r.get("p_change")
        if is_num(chg) and abs(chg) >= 25 and m and m > 1000:
            warn(f"{name}: price moved {chg:+.1f}% on {r['p_date']} (check it is real news, not a data error).")
        if is_num(r.get("revenueGrowth")) and r["revenueGrowth"] > 1000 and is_num(r.get("revenueEur")) and r["revenueEur"] > 100:
            warn(f"{name}: revenue growth {r['revenueGrowth']:,.0f}% looks like a unit change between reports.")
        if r.get("periodEnd") and age(r["periodEnd"]) > 900 and m and m > 1000:
            warn(f"{name}: newest annual report ends {r['periodEnd']}, older than two years.")

    big = sorted((r for r in rows if r["mcap"] and r["mcap"] > 3000 and not r.get("sector")), key=lambda r: -r["mcap"])
    for r in big:
        warn(f"{r['name']} ({r['isin']}): €{r['mcap'] / 1000:,.0f}B company has no sector, falls into 'Other' on the maps.")
    # Priced companies with no market value drop off the maps and the front of the screener.
    gap = defaultdict(list)
    for r in rows:
        if r.get("p_price") and not r["mcap"] and not r.get("revenueEur"):
            gap[r["country"]].append(r["name"])
    if gap:
        notes.append("Priced but no market value (no report figures, no shares count): " +
                     ", ".join(f"{k} {len(v)}" for k, v in sorted(gap.items(), key=lambda x: -len(x[1]))[:12]) + ".")


def check_shares_file():
    s = json.loads((ROOT / "data/shares.json").read_text())["rows"]
    old = [k for k, r in s.items() if age(r["asOf"]) > 200]
    if old:
        warn(f"{len(old)} share counts in data/shares.json are older than 200 days: time for the quarterly refresh.")
    fin = json.loads((ROOT / "data/financials.json").read_text())["rows"] if (ROOT / "data/financials.json").exists() else {}
    old = [r["name"] for r in fin.values() if age(r["periodEnd"]) > 480]
    if old:
        warn(f"{len(old)} hand-entered annual figures in data/financials.json are for a year that ended over 16 months ago "
             f"({', '.join(old[:5])}): enter the new annual report.")


def check_history():
    h = json.loads((ROOT / "data/prices-history.json").read_text())
    jumps = []
    for isin, closes in h.items():
        days = sorted(closes)
        for a, b in zip(days, days[1:]):
            x, y = closes[a], closes[b]
            if x and y and (y / x > 2 or y / x < 0.4):
                jumps.append(f"{isin} {a} {x} -> {b} {y}")
    if jumps:
        warn(f"{len(jumps)} daily closes jump more than 2x (share split or wrong line); first: {'; '.join(jumps[:5])}.")


def check_pages_data():
    limits = {"js/invest.json": 3, "js/housing.json": 3, "js/housing-levels.json": 3, "js/rates.json": 3}
    for path, days in limits.items():
        d = load(path)
        if age(d["updated"]) > days:
            err(f"{path} is {age(d['updated'])} days old: its daily job stopped.")
        if d.get("failed"):
            warn(f"{path}: sources that failed last run (old values kept): {', '.join(map(str, d['failed']))}.")
    s = load("js/shorts.json")
    if age(s["updated"]) > 2:
        err(f"Shorts are {age(s['updated'])} days old.")
    for code, c in s["countries"].items():
        if c.get("latest") and trading_days_since(day(c["latest"])) > 7:
            warn(f"Shorts {c['name']}: newest position is from {c['latest']}, the regulator's file may have moved.")
    i = load("js/insiders.json")
    if age(i["updated"]) > 2:
        err(f"Insider trades are {age(i['updated'])} days old.")
    inv = load("js/invest.json")
    for code, n in inv["now"]["c"].items():
        v = (n.get("inflation") or {}).get("value")
        if is_num(v) and not -5 < v < 30:
            err(f"Inflation for {code} is {v} %: not believable.")
        for k in ("deposit1y", "savings", "bond10"):
            v = (n.get(k) or {}).get("value")
            if is_num(v) and not -2 < v < 25:
                err(f"{k} for {code} is {v} %: not believable.")
    for code, c in load("js/housing.json")["countries"].items():
        for k, v in (c.get("nominal") or {}).items():
            if is_num(v) and abs(v) > 400:
                err(f"House prices {c['name']} {k} change {v} %: not believable.")


def main():
    for check in (check_companies, check_shares_file, check_history, check_pages_data):
        try:
            check()
        except Exception as e:   # a check that cannot run is itself a finding
            err(f"{check.__name__} could not run: {type(e).__name__}: {e}")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"FINILA data check {stamp} ({'live site' if LIVE else 'working copy'})",
             f"{len(errors)} errors, {len(warnings)} warnings", ""]
    lines += [f"ERROR   {m}" for m in errors] + [f"WARNING {m}" for m in warnings] + [f"NOTE    {m}" for m in notes]
    (ROOT / "data/check-report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    for m in errors:
        print(f"::error title=Data check::{m}")
    for m in warnings[:40]:
        print(f"::warning title=Data check::{m}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
