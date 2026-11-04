"""Outlook, mid-term targets and order backlog, quoted word for word from each company's newest ESEF
report on filings.xbrl.org (annual, or half-year/quarterly where the company files those in ESEF).

ESEF does not tag these figures, so they are found in the report text: a sentence is kept only when it
names a financial line (revenue, margin, capex, ...), an expectation (expected, guidance, outlook,
target, ...), a year ahead and a number; backlog sentences need the word backlog / order book and a
number. Sustainability, pay and market-forecast sentences are left out. Nothing is rewritten: the page
shows the sentence, the report it comes from and its date.

Reports never change once filed, so results are cached per report in data/cache/outlook.json.
Writes data/outlook.json: {lei: {"period": "2026-06-30", "source": url, "items": [{"kind", "text"}]}}.
"""
import html, io, json, os, re, sys, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fundamentals as fd   # noqa: E402

ROOT = fd.ROOT
CACHE = ROOT / "data/cache/outlook.json"
OUT = ROOT / "data/outlook.json"
CACHE_VERSION = 3
BUDGET = float(os.environ.get("FINILA_BUDGET_MIN", "300")) * 60
WORKERS = 3
START = time.time()
MAX_ITEMS = {"outlook": 5, "target": 3, "backlog": 3, "event": 6}

_get = fd.get


def polite_get(url, as_json=True):
    for wait in (0, 20, 40, 60, 90):
        time.sleep(wait)
        res = _get(url, as_json)
        if res is not None:
            return res
    return None


fd.get = polite_get

FIN = (r"revenue|revenues|sales|turnover|net sales|ebitda|ebit|ebita|operating (profit|income|margin|result)|"
       r"margin|earnings per share|eps|net (profit|income)|free cash flow|cash flow|capex|capital expenditure|"
       r"investments?|dividend|order intake|deliveries|book-to-bill")
EXPECT = (r"expect|expected|expects|guidance|outlook|forecast|anticipate|anticipates|target|targets|aim to|aims to|"
          r"ambition|should (reach|be|amount)|will (reach|be|amount)|projected|plan to|plans to")
NUMBER = r"\d+(?:[.,]\d+)?\s*(?:%|percent|per cent|pp|percentage points?|bn|billion|mn|m\b|million)|(?:EUR|USD|DKK|SEK|NOK|CHF|GBP|€|\$|£)\s?\d"
BACKLOG = r"order backlog|backlog|order book|orderbook|remaining performance obligations|order intake"
# What happened: deals, restructuring, approvals and launches, prices, management changes, incidents.
EVENT = (r"\b(completed|closed|finali[sz]ed|announced|agreed|signed)\b.{0,80}\b(acquisition|merger|divestment|divestiture|sale of|disposal|takeover|joint venture|spin-off|demerger)\b|"
         r"\b(we|the group|the company) (acquired|divested|sold)\b|\bacquisition of [A-Z]|"
         r"\b(restructuring|transformation programme|transformation program|cost[- ]saving programme)\b.{0,120}\b(\d|announced|launched|initiated)|"
         r"\breduc\w* (of )?(the )?(workforce|headcount|approximately [\d,.]+|around [\d,.]+|about [\d,.]+) ?(employees|positions|jobs|fte)?|"
         r"\b(launched|received (marketing |regulatory )?approval|approved by the (fda|ema|european commission)|fda approv\w+)\b|"
         r"\b(price (cuts?|reductions?|increases?|pressure)|lower (realised |net )?prices|higher (realised |net )?prices)\b|"
         r"\b(ceo|chief executive officer|cfo|chief financial officer)\b.{0,80}\b(appointed|stepped down|resigned|succeeded)\b|"
         r"\b(product recall|cyber[- ]?attack|strike action|fined|antitrust|class action)\b")
NOISE = (r"emission|scope [123]|co2|ghg|carbon|climate|renewable electricity|energy consumption|waste|water|diversity|"
         r"female|women|gender|employee engagement|injur|safety|remuneration|incentive|bonus|\bsti\b|\blti\b|"
         r"share-based|vesting|options? granted|pension|discount rate|impairment test|sensitivity|wacc|"
         r"gdp|inflation is|the market is expected|market is projected|industry is (expected|projected)|"
         r"sustainab|esg|csrd|taxonomy|biodiversity|children|patients reached|hedg|interest rate swap|materiality|auditor|audit |"
         r"contingent consideration|held for sale|remuneration|pension|ceteris paribus|would result in|own holding|"
         r"forward-looking statements|risks and uncertainties|actual results")


def sentences(text):
    """Sentences, cut also at bullets, table bars and footnote marks, which a report converted from PDF
    glues to the next heading. Long run-ons (headings + sentence) are left out rather than cut wrongly."""
    for s in re.split(r"(?<=[.!?])\s+(?=[A-Z•●▶(\[])|\s[•●▶|]\s|\s\[[A-Z]\]\s", text):
        s = s.strip(" •●▶-")
        if 50 <= len(s) <= 320 and not re.search(r"annual report( and accounts)? 20\d\d|strategic report|financial report for the period", s.lower()):
            yield s


def report_text(raw):
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw))
        raw = b" ".join(z.read(n) for n in z.namelist() if n.endswith((".xhtml", ".html", ".htm")) and "/reports/" in n
                        or n.endswith((".xhtml", ".html")) and "META-INF" not in n)
    src = raw.decode("utf-8", "replace")
    src = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", src)
    block = re.compile(r"(?i)</?(div|p|br|td|th|tr|li|h\d|table|section)\b[^>]*>")

    def flat(inline_join):
        t = block.sub(" ", src)
        t = re.sub(r"<[^>]+>", inline_join, t)
        return re.sub(r"\s+", " ", html.unescape(t))

    # Reports converted from PDF often split words into pieces ("ou t l ook"): when a plain flattening
    # leaves many 1-2 letter fragments, join inline pieces without a space instead and use whichever reads better.
    a, b = flat(" "), flat("")
    frag = lambda t: sum(len(w) <= 2 for w in t[:200000].split()) / max(1, len(t[:200000].split()))
    return b if frag(a) > 0.32 and frag(b) < frag(a) else a


def english(low):
    w = low.split()
    en = sum(x in ("the", "and", "of", "to", "in", "was", "were", "our", "we") for x in w)
    other = sum(x in ("le", "la", "les", "des", "et", "du", "och", "att", "der", "die", "und", "het", "een", "og") for x in w)
    return en >= 2 and en > other * 2


def pick(text, year):
    # Events only from the management report, which comes before the financial statements.
    events = []
    recent = "|".join(str(y) for y in (year - 2, year - 1, year))
    for s in sentences(text[:int(len(text) * 0.4)]):
        low = s.lower()
        # Only this year's events, in English, stated as done: a sentence naming a recent year, month or
        # quarter, or "this year" / "during the year". History, policies and bios fall out this way.
        when = re.search(fr"\b({recent})\b", s) or re.search(r"\b(this year|during the year|in the year|in q[1-4]|"
                         r"in (january|february|march|april|may|june|july|august|september|october|november|december))\b", low)
        years_named = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", s)]
        if re.search(EVENT, s, re.I) and when and not any(y < year - 2 for y in years_named) and english(low) \
                and not re.search(NOISE, low) and not re.search(fr"\b({EXPECT})", low) \
                and not re.search(r"\b(policy|policies|procedures?|framework|appointed:|follow \w+ on linkedin)\b", low) \
                and sum(ch.isdigit() for ch in s) <= len(s) * 0.15 and not re.search(r"\b(may|could|might|if)\b", low):
            events.append({"kind": "event", "text": s})
    ahead = "|".join(str(y) for y in range(year, year + 7))
    near = "|".join(str(y) for y in (year, year + 1))
    out, seen = [], set()
    for s in sentences(text):
        low = s.lower()
        if re.search(NOISE, low):
            continue
        is_backlog = re.search(BACKLOG, low) and re.search(r"\d[\d,.]*\d|\d{2,}", s)
        if not (re.search(NUMBER, s) or is_backlog):
            continue
        if s.count("  ") or sum(ch.isdigit() for ch in s) > len(s) * 0.25:   # table rows, not sentences
            continue
        kind = None
        if is_backlog and re.search(r"\b(stood|amount|amounted|was|were|reached|totall?ed|of)\b", low):
            kind = "backlog"
        elif re.search(fr"\b({FIN})\b", low) and re.search(fr"\b({EXPECT})", low):
            if re.search(fr"\b({near})\b", s) or re.search(r"full[- ]year|for the year|next year|coming year", low):
                kind = "outlook"
            elif re.search(fr"\b(by|in|until|through|towards?)\s+({ahead})\b|\b20\d\d\s*[-–]\s*({ahead})\b|mid-term|medium-term|long-term", low):
                kind = "target"
        if not kind:
            continue
        key = re.sub(r"\W+", "", low)[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": kind, "text": s})
    keys = {re.sub(r"\W+", "", i["text"].lower())[:120] for i in out}
    for e in events:
        k = re.sub(r"\W+", "", e["text"].lower())[:120]
        if k not in keys and e["text"].endswith((".", "!")):
            keys.add(k)
            out.append(e)
    # Keep the first few of each kind, in report order (outlook sections come first in most reports).
    kept, n = [], {}
    for it in out:
        n[it["kind"]] = n.get(it["kind"], 0) + 1
        if n[it["kind"]] <= MAX_ITEMS[it["kind"]]:
            kept.append(it)
    return kept


YTD_KEYS = {"revenue": fd.CONCEPTS["revenue"], "op_profit": fd.CONCEPTS["op_profit"], "net": fd.CONCEPTS["net"],
            "cfo": fd.CONCEPTS.get("cfo", ["CashFlowsFromUsedInOperatingActivities"])}


def year_to_date(xj):
    """Figures of an interim report (half-year, 9 months, quarter): the longest period from the start of the
    financial year to the report date, and the same period a year earlier, both from the same report."""
    facts = list(xj.get("facts", {}).values())
    if fd.fiscal_end(facts) is not None:
        return None   # a full-year report
    spans = {}
    for fact in facts:
        dims = fact.get("dimensions", {})
        if set(dims) - {"concept", "entity", "period", "unit", "language"} or "/" not in dims.get("period", ""):
            continue
        prefix, _, name = dims.get("concept", "").partition(":")
        if prefix != "ifrs-full":
            continue
        for key, names in YTD_KEYS.items():
            if name not in names:
                continue
            start, stop = fd.parse_period(dims["period"])
            days = (stop - start).days
            if not 80 <= days <= 290:
                continue
            try:
                v = float(fact["value"])
            except (TypeError, ValueError):
                continue
            rank = names.index(name)
            slot = spans.setdefault((start, stop), {})
            if key not in slot or rank < slot[key][0]:
                slot[key] = (rank, v, dims.get("unit", ""))
    if not spans:
        return None
    # The current period: the newest end, the longest span with revenue or profit.
    ends = sorted({stop for _, stop in spans}, reverse=True)
    cur = max((k for k in spans if k[1] == ends[0] and ("revenue" in spans[k] or "net" in spans[k])),
              key=lambda k: k[1] - k[0], default=None)
    if not cur:
        return None
    start, stop = cur
    prev = next((k for k in spans if abs((k[0] - start).days - (-365)) <= 7 and abs((k[1] - stop).days + 365) <= 7), None)
    if not prev:
        return None
    unit = next((v[2] for v in spans[cur].values() if v[2].startswith("iso4217:")), "")
    m = lambda d: {k: round(v[1] / 1e6, 1) for k, v in d.items()}
    return {"start": start.isoformat(), "end": (stop - fd.timedelta(days=1)).isoformat(),
            "months": round((stop - start).days / 30.4), "currency": unit.split(":")[-1],
            "cur": m(spans[cur]), "prev": m(spans[prev])}


def read_company(lei, cached):
    filings = fd.latest_annual(lei)
    if not filings:
        return None
    f = filings[0]
    fid = f["fxo_id"]
    if cached.get("fxo_id") == fid:
        return cached
    url = f.get("report_url") or f.get("package_url")
    raw = fd.get(fd.BASE + url, as_json=False) if url else None
    if not raw:
        return None
    text = report_text(raw)
    year = int(f["period_end"][:4])
    annual = f["period_end"][5:7] == "12" and "interim" not in (url or "").lower() and len(text) > 600000
    items = pick(text, year + 1 if annual else year)
    ytd = None
    if not annual and f.get("json_url"):
        xj = fd.get(fd.BASE + f["json_url"])
        ytd = year_to_date(xj) if xj else None
    return {"fxo_id": fid, "ytd": ytd, "period": f["period_end"], "published": (f.get("date_added") or "")[:10],
            "source": fd.BASE + f["viewer_url"] if f.get("viewer_url") else fd.BASE + url, "items": items}


def main():
    comp = json.loads((ROOT / "js/companies.json").read_text())
    rows = [dict(zip(comp["fields"], r)) for r in comp["rows"]]
    leis = sorted({r["lei"] for r in rows if r.get("origin") is None and "filings.xbrl.org" in (r.get("source") or "")})
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("_v") != CACHE_VERSION:
        cache = {"_v": CACHE_VERSION}
    today = date.today().isoformat()
    # Every company once a day: one small API call each, and a report is downloaded only when it is new.
    todo = [l for l in leis if cache.get(l, {}).get("checked", "") < today]
    size = {r["lei"]: r.get("revenueEur") or 0 for r in rows}
    todo.sort(key=lambda l: -size.get(l, 0))
    if os.environ.get("FINILA_LIMIT"):
        todo = todo[:int(os.environ["FINILA_LIMIT"])]
    print(f"{len(leis)} companies with ESEF reports, {len(todo)} to check", flush=True)
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(lambda l=l: read_company(l, cache.get(l, {})) if time.time() - START < BUDGET else None): l for l in todo}
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except Exception as e:   # one broken report must not stop the run
                print(f"  ! {futs[fut]}: {e}", flush=True)
                continue
            if res is None:
                continue
            cache[futs[fut]] = {**res, "checked": today}
            done += 1
            if done % 50 == 0:
                print(f"  {done} companies read", flush=True)
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cache, separators=(",", ":"), ensure_ascii=False))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, separators=(",", ":"), ensure_ascii=False))
    out = {l: {k: v.get(k) for k in ("period", "published", "source", "items", "ytd")}
           for l, v in cache.items() if l != "_v" and (v.get("items") or v.get("ytd"))}
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    kinds = {}
    for v in out.values():
        for it in v["items"]:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print(f"outlook quotes for {len(out)} companies {kinds}; read {done} this run", flush=True)
    names = {r["lei"]: r["name"] for r in rows}
    for l in sorted(out, key=lambda l: -size.get(l, 0))[:40]:   # samples for checking by eye
        print(f"\n== {names.get(l)} ({out[l]['period']})")
        for it in out[l]["items"]:
            print(f"  [{it['kind']}] {it['text']}")


if __name__ == "__main__":
    main()
