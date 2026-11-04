"""Five years of annual figures per company, each year read from its own ESEF annual report
on filings.xbrl.org (no use restrictions). Used by the company pages (/stock/<name>).

For every company in js/companies.json with an ESEF report, the annual reports of the last
six financial years are read; each report also gives the year before it, which is used when
that year's own report is missing. Reports never change once filed, so they are cached in
data/cache/history.json and a run only reads new ones.

Writes data/history.json: {lei: {"currency": "EUR", "years": {"2025": {...}, ...}}}, amounts in
millions of the report currency, EPS per share. When a later report restates a year, the
restated figures are used (marked "restated"). A year is left out when its figures disagree with
the other years by a factor of ~1000 (a report tagged in thousands) or use another currency.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fundamentals as fd   # noqa: E402

# Working capital lines on top of what fundamentals.py reads (balance sheet, year end).
fd.CONCEPTS.update({
    "inventories": ["Inventories", "CurrentInventoriesHeldForSale"],
    "receivables": ["TradeAndOtherCurrentReceivables", "CurrentTradeReceivables", "TradeReceivables"],
    "payables":    ["TradeAndOtherCurrentPayables", "CurrentTradePayables", "TradeAndOtherCurrentPayablesToTradeSuppliers"],
    "leases":      ["LeaseLiabilities"],
})
fd.INSTANT.update({"inventories", "receivables", "payables", "leases"})

ROOT = fd.ROOT
CACHE = ROOT / "data/cache/history.json"
OUT = ROOT / "data/history.json"
CACHE_VERSION = 1          # bump when the figures read change, so every report is read again
YEARS = 6                  # newest report and the five before it
BUDGET = float(os.environ.get("FINILA_BUDGET_MIN", "300")) * 60
WORKERS = 6
START = time.time()
FIELDS = ["revenue", "ebitda", "operatingProfit", "netIncome", "operatingCashFlow", "capex", "fcf", "dividendsPaid",
          "equity", "assets", "cash", "debt", "netDebt", "eps", "sharesTagged", "da", "inventories", "receivables",
          "payables", "leases"]


def log(msg):
    print(msg, flush=True)


def year_row(cur, prev):
    m = fd.metrics(cur, prev)
    r = {k: m.get(k) for k in FIELDS if m.get(k) is not None}
    mil = lambda v: round(v / 1e6, 1)
    da = cur.get("da")
    if da is None and ("dep_ppe" in cur or "amort" in cur):
        da = sum(abs(cur.get(k, 0)) for k in ("dep_ppe", "dep_rou", "amort"))
    if da is not None and not cur.get("financial"):
        r["da"] = mil(abs(da))
    for k in ("inventories", "receivables", "payables", "leases"):
        if cur.get(k) is not None:
            r[k] = mil(cur[k])
    if cur.get("financial"):
        r["financial"] = True
    return r


def read_company(lei, cached):
    """Annual reports of one company -> {fxo_id: {"end": ..., "currency": ..., "cur": row, "prev": row}}."""
    filings = fd.latest_annual(lei)
    if filings is None:
        return None
    newest = {}
    for f in filings:   # newest version of each period
        newest.setdefault(f["period_end"], f)
    out, years = {}, set()
    for pe, f in sorted(newest.items(), reverse=True):
        if len(years) >= YEARS:
            break
        fid = f["fxo_id"]
        if fid in cached:
            out[fid] = cached[fid]
            if cached[fid].get("end"):
                years.add(cached[fid]["end"][:4])
            continue
        xj = fd.get(fd.BASE + f["json_url"])
        if not xj:
            continue
        cur, prev, currency, end, _ = fd.extract(xj, pe)
        facts = list(xj.get("facts", {}).values())
        if fd.fiscal_end(facts) is None or not ("revenue" in cur or "net" in cur):
            out[fid] = {"end": None}   # half-year report or no full-year figures
            continue
        if end.isoformat()[:4] in years:
            out[fid] = {"end": None}   # a second report for a year already read
            continue
        years.add(end.isoformat()[:4])
        out[fid] = {"end": end.isoformat(), "currency": currency, "cur": year_row(cur, prev),
                    "prev": year_row(prev, {}) if prev else None,
                    "source": fd.BASE + f["viewer_url"] if f.get("viewer_url") else None}
    return out


def clean(years):
    """Drops a year whose revenue or assets are ~1000x off the median of the others (unit errors)."""
    def off(field):
        vals = sorted(abs(y[field]) for y in years.values() if y.get(field))
        if len(vals) < 3:
            return set()
        med = vals[len(vals) // 2]
        return {k for k, y in years.items() if y.get(field) and med and not 1 / 200 < abs(y[field]) / med < 200}
    bad = off("revenue") | off("assets")
    return {k: y for k, y in years.items() if k not in bad}, sorted(bad)


def main():
    comp = json.loads((ROOT / "js/companies.json").read_text())
    rows = [dict(zip(comp["fields"], r)) for r in comp["rows"]]
    leis = sorted({r["lei"] for r in rows if r.get("origin") is None and r.get("source", "") and "filings.xbrl.org" in (r.get("source") or "")})
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("_v") != CACHE_VERSION:
        cache = {"_v": CACHE_VERSION}
    # Companies whose newest report is already cached are read again only once a week (new filings).
    today = date.today().isoformat()
    todo = [l for l in leis if cache.get(l, {}).get("checked", "") < today and
            (l not in cache or cache[l].get("checked", "") <= date.fromordinal(date.today().toordinal() - 7).isoformat())]
    log(f"{len(leis)} companies with ESEF reports, {len(todo)} to check")
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(lambda l=l: read_company(l, cache.get(l, {}).get("reports", {})) if time.time() - START < BUDGET else None): l for l in todo}
        for fut in as_completed(futs):
            res = fut.result()
            if res is None:
                continue
            cache[futs[fut]] = {"checked": today, "reports": res}
            done += 1
            if done % 100 == 0:
                log(f"  {done} companies read")
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cache, separators=(",", ":")))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, separators=(",", ":")))

    out, dropped, cur_mix = {}, 0, 0
    for lei in leis:
        reports = [r for r in cache.get(lei, {}).get("reports", {}).values() if r.get("end")]
        if not reports:
            continue
        reports.sort(key=lambda r: r["end"], reverse=True)
        currency = reports[0]["currency"]
        years = {}
        for r in reports:
            if r["currency"] != currency:
                cur_mix += 1
                continue
            y = r["end"][:4]
            years[y] = {**r["cur"], "end": r["end"], "source": r.get("source")}
        # The next report shows each year again, restated where the company changed it (a business sold,
        # new accounting rules). Like a due diligence, the newest version wins, so the years compare.
        for r in reports:
            if r["currency"] != currency or not r.get("prev") or r["prev"].get("revenue") is None and r["prev"].get("netIncome") is None:
                continue
            y = str(int(r["end"][:4]) - 1)
            old = years.get(y)
            if old is None:
                years[y] = {**r["prev"], "end": f"{y}{r['end'][4:]}", "source": r.get("source"), "fromNext": True}
                continue
            if old.get("restated") or old.get("fromNext"):
                continue
            changed = any(old.get(k) is not None and r["prev"].get(k) is not None and abs(r["prev"][k] - old[k]) > abs(old[k]) * 0.01 + 0.05
                          for k in ("revenue", "netIncome", "operatingProfit"))
            years[y] = {**old, **r["prev"], "end": old["end"], "source": r.get("source") if changed else old["source"],
                        **({"restated": True, "originalSource": old["source"]} if changed else {})}
        years, bad = clean(years)
        dropped += len(bad)
        keep = sorted(years)[-YEARS:]
        if len(keep) >= 2:
            out[lei] = {"currency": currency, "years": {y: years[y] for y in keep}}
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    n = [len(v["years"]) for v in out.values()]
    log(f"history for {len(out)} companies ({sum(x >= 5 for x in n)} with 5+ years); read {done} this run; "
        f"{dropped} years dropped as unit errors, {cur_mix} reports in another currency skipped")


if __name__ == "__main__":
    main()
