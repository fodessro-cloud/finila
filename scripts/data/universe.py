"""Build FINILA's company universe: every issuer on filings.xbrl.org with an annual
report for fiscal 2024 or later, with the figures from its newest report.

Names, tickers and industries come from Wikidata (CC0), FX rates from the ECB.
Reports already read are cached in data/cache/universe.json, so a daily run only
downloads new reports. Writes js/companies.json (served to the site) and
data/universe-report.txt.
"""
import json, math, os, re, sys, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent))
from fundamentals import BASE, ROOT, get, extract, metrics, latest_annual, UA   # noqa: E402

import urllib.request

MIN_PERIOD = "2024-01-01"
CACHE = ROOT / "data/cache/universe.json"
CACHE_VERSION = 4          # bump when metrics() changes, so every report is read again
RETRY_EMPTY = 2            # bump when read_report() changes, so companies without figures are read again
WIKIDATA_CACHE = ROOT / "data/cache/wikidata.json"
# Shares with a European ISIN only: drops e.g. Toyota or Royal Bank of Canada, which file in London too.
EUROPE = set("AT BE BG CH CY CZ DE DK EE ES FI FR GB GG GR HR HU IE IM IS IT JE LI LT LU LV MT NL NO PL PT RO SE SI SK FO GI".split())
BUDGET = float(os.environ.get("FINILA_BUDGET_MIN", "300")) * 60   # stop downloading after this
WORKERS = 6
START = time.time()

# Wikidata industry words -> FINILA sector. First match wins.
SECTORS = [
    ("Financials", ["bank", "insurance", "financial", "asset management", "investment", "reinsurance", "stock exchange", "payment"]),
    ("Real Estate", ["real estate", "property", "reit"]),
    ("Healthcare", ["pharma", "biotech", "medical", "health", "hospital", "drug", "diagnostic"]),
    ("Technology", ["software", "semiconductor", "information technology", "computer", "electronics", "internet", "it service", "video game"]),
    ("Communication", ["telecom", "media", "broadcast", "publishing", "advertising", "entertainment"]),
    ("Energy", ["oil and gas", "oil industry", "natural gas", "gas industry", "petroleum", "energy", "coal", "renewable"]),
    ("Utilities", ["utility", "electric", "water supply", "power"]),
    ("Materials", ["chemical", "mining", "metal", "steel", "paper", "cement", "building material", "packaging", "gold"]),
    ("Consumer Staples", ["food", "dairy", "consumer goods", "beverage", "brew", "tobacco", "retail", "supermarket", "household", "cosmetic", "agricultur"]),
    ("Consumer Discretionary", ["automotive", "car", "luxury", "fashion", "clothing", "apparel", "hotel", "restaurant", "travel", "leisure", "gambling", "furniture", "e-commerce"]),
    ("Industrials", ["engineering", "construction", "aerospace", "defence", "defense", "transport", "logistics", "shipping", "airline", "machine", "industrial", "rail", "conglomerate"]),
]
WIKIDATA_QUERY = """
SELECT ?lei ?item ?itemLabel ?ticker ?exLabel ?indLabel WHERE {
  ?item wdt:P1278 ?lei; p:P414 ?st.
  ?st ps:P414 ?ex.
  OPTIONAL { ?st pq:P249 ?ticker }
  OPTIONAL { ?st pq:P582 ?end }
  OPTIONAL { ?item wdt:P452 ?ind }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""


def log(msg):
    print(msg, flush=True)


def list_filings():
    """Newest-first candidate reports per LEI, with the issuer name and country."""
    by_lei, url, pages = {}, f"{BASE}/api/filings?include=entity&page[size]=500&sort=-period_end", 0
    while url:
        data = get(url)
        if not data:
            break
        names = {e["id"]: e["attributes"] for e in data.get("included", []) if e["type"] == "entity"}
        oldest = None
        for f in data.get("data", []):
            a = f["attributes"]
            pe = a.get("period_end") or ""
            oldest = pe
            if not a.get("json_url") or not (MIN_PERIOD <= pe <= f"{date.today().year + 1}-12-31"):
                continue
            ent = names.get(f["relationships"]["entity"]["data"]["id"], {})
            lei = ent.get("identifier")
            if not lei:
                continue
            rec = by_lei.setdefault(lei, {"name": ent.get("name"), "country": a.get("country"), "filings": []})
            rec["filings"].append(a)
        pages += 1
        if oldest and oldest < MIN_PERIOD and oldest[:4] > "1900":
            break   # sorted by period end, everything after this is older
        nxt = data.get("links", {}).get("next")
        url = (nxt if nxt.startswith("http") else BASE + nxt) if nxt else None
    for rec in by_lei.values():
        rec["filings"].sort(key=lambda a: (a["period_end"], a["processed"]), reverse=True)
    log(f"{len(by_lei)} issuers with a report for {MIN_PERIOD[:4]} or later ({pages} pages)")
    return by_lei


def read_report(lei, rec):
    """Newest version of each recent period. Half-year reports (Novo Nordisk files them in ESEF
    too) carry no full-year figures, and the filings listing can miss the annual report, so the
    company's own filing list is tried after the listing."""
    def candidates():
        latest = {}
        for f in rec["filings"]:
            latest.setdefault(f["period_end"], f)
        yield from list(latest.values())[:4]
        yield from (latest_annual(lei) or [])[:8]
    tried = set()
    for f in candidates():
        if f["period_end"] in tried:
            continue
        tried.add(f["period_end"])
        xj = get(BASE + f["json_url"])
        if not xj:
            continue
        cur, prev, currency, fy_end, _ = extract(xj, f["period_end"])
        if "revenue" in cur or "net" in cur:
            row = metrics(cur, prev)
            row.update({"currency": currency, "periodEnd": fy_end.isoformat(),
                        "source": BASE + f["viewer_url"] if f.get("viewer_url") else None})
            return f["fxo_id"], row
    return rec["filings"][0]["fxo_id"], None


def wikidata():
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(WIKIDATA_QUERY)
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/sparql-results+json"})
    for attempt in range(3):   # the query service rate-limits to about one query a minute when busy
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                rows = json.load(r)["results"]["bindings"]
            break
        except Exception as e:
            err = e
            time.sleep(75)
    else:
        log(f"  ! Wikidata: {err}; using the last saved copy")
        try:
            return {k: {**v, "tickers": [tuple(t) for t in v["tickers"]], "industries": set(v["industries"])}
                    for k, v in json.loads(WIKIDATA_CACHE.read_text()).items()}
        except Exception:
            return {}
    out = {}
    for b in rows:
        lei = b["lei"]["value"]
        w = out.setdefault(lei, {"name": b["itemLabel"]["value"], "tickers": [], "industries": set()})
        if "ticker" in b:
            # Delisted lines go last, so the current ticker wins (renames without an end date: RENAMED).
            w["tickers"].append((b["ticker"]["value"], b.get("exLabel", {}).get("value", ""), "end" in b))
        if "indLabel" in b:
            w["industries"].add(b["indLabel"]["value"])
    log(f"{len(out)} listed companies with an LEI on Wikidata")
    WIKIDATA_CACHE.parent.mkdir(parents=True, exist_ok=True)
    WIKIDATA_CACHE.write_text(json.dumps({k: {**v, "industries": sorted(v["industries"])} for k, v in out.items()},
                                         ensure_ascii=False, separators=(",", ":")))
    return out


def ecb_rates():
    try:
        with urllib.request.urlopen("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml", timeout=60) as r:
            root = ET.fromstring(r.read())
        rates = {c.get("currency"): float(c.get("rate")) for c in root.iter() if c.get("currency")}
        rates["EUR"] = 1.0
        return rates
    except Exception as e:
        log(f"  ! ECB rates: {e}")
        return {"EUR": 1.0}


# Companies Wikidata does not link by LEI: look them up by ISIN too, and use what the company
# is (bank, airline, mining company) when it has no industry.
EXTRA_QUERY = """
SELECT ?key ?indLabel ?typeLabel WHERE {
  { VALUES ?key { %s } ?item wdt:P946 ?key. } UNION { VALUES ?key { %s } ?item wdt:P1278 ?key. }
  OPTIONAL { ?item wdt:P452 ?ind }
  OPTIONAL { ?item wdt:P31 ?type }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""
EXTRA_CACHE = ROOT / "data/cache/wikidata-extra.json"


def wikidata_extra(isins, leis):
    """{ISIN or LEI: [industry and type labels]}, saved so a failed query keeps the last answer."""
    try:
        saved = json.loads(EXTRA_CACHE.read_text())
    except Exception:
        saved = {}
    if not isins and not leis:
        return saved
    q = EXTRA_QUERY % (" ".join(f'"{i}"' for i in sorted(isins)), " ".join(f'"{l}"' for l in sorted(leis)))
    req = urllib.request.Request("https://query.wikidata.org/sparql", data=urllib.parse.urlencode({"query": q}).encode(),
                                 headers={**UA, "Accept": "application/sparql-results+json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                rows = json.load(r)["results"]["bindings"]
            break
        except Exception as e:
            err = e
            time.sleep(75)
    else:
        log(f"  ! Wikidata by ISIN: {err}; using the last saved copy")
        return saved
    out = {}
    for b in rows:
        labels = out.setdefault(b["key"]["value"], set())
        for k in ("indLabel", "typeLabel"):
            if k in b and not re.fullmatch(r"Q\d+", b[k]["value"]):
                labels.add(b[k]["value"])
    saved.update({k: sorted(v) for k, v in out.items()})
    EXTRA_CACHE.write_text(json.dumps(saved, ensure_ascii=False, separators=(",", ":")))
    log(f"Wikidata by ISIN/LEI: labels for {len(out)} of {len(isins) + len(leis)} keys")
    return saved


# Last resort: words in the company name (Fastigheter, Bank, Pharma, Energia...).
NAME_SECTORS = [
    ("Real Estate", r"fastighet|real estate|propert|immobili|inmobili|reit\b|estates?\b|eiendom|kiinteist|nieruchomo|wohnen|wonen|vastgoed|logistics? property"),
    ("Financials", r"\bbank|banca|banco|banque|sparebank|insurance|assurance|assicura|seguros|versicher|försäkring|forsikring|vakuutus|capital\b|invest|finans|finance|credit|kredit|leasing"),
    ("Healthcare", r"pharm|\bbio|medic|health|thera|diagnost|clinic|dental|life science|genetic|genom"),
    ("Energy", r"\boil\b|petrol|\bgas\b|energ|offshore|drilling|solar|wind|hydrogen"),
    ("Utilities", r"utilit|electric|power|water\b|\bvann|wasser"),
    ("Technology", r"software|tech|digital|data\b|semicon|\bsystems?\b|\bit\b|cyber|cloud|electronic|games"),
    ("Communication", r"telecom|media|\btv\b|broadcast|publish|entertain|music"),
    ("Materials", r"mining|minerals?|metal|steel|chem|paper|pulp|cement|gold|silver|copper|lithium|forest|timber|packag"),
    ("Industrials", r"shipping|tankers?|maritime|airlines?|aviation|logistic|transport|construct|bygg|engineer|industr|machin|defen[cs]e|infrastruct"),
    ("Consumer Staples", r"\bfood|foods\b|beverage|brew|wine|spirits|dairy|agri|\bfarms?\b|farmland|seafood|salmon|laks|retail|grocer|supermarket"),
    ("Consumer Discretionary", r"hotel|resort|travel|fashion|apparel|cloth|automotive|motors?\b|\bcar\b|cars\b|furniture|home\b|leisure|sport|casino|betting|gaming"),
]


def sector_by_name(name):
    for sector, pattern in NAME_SECTORS:
        if re.search(pattern, name or "", re.I):
            return sector
    return None


def sector_of(industries):
    """The sector most of the company's Wikidata industries point to (Unilever: food, dairy and
    consumer goods, not the one industry that mentions oils)."""
    votes = {}
    for ind in industries:
        for rank, (sector, words) in enumerate(SECTORS):
            if any(re.search(r"\b" + re.escape(w), ind.lower()) for w in words):
                votes[sector] = (votes.get(sector, (0, rank))[0] + 1, rank)
                break
    return max(votes, key=lambda k: (votes[k][0], -votes[k][1])) if votes else None


# Main exchange of each country, to pick the home ticker among Wikidata's listings.
HOME_EXCHANGE = {
    "FR": "Paris", "NL": "Amsterdam", "BE": "Brussels", "PT": "Lisbon", "IE": "Dublin|Irish", "GB": "London",
    "SE": "Stockholm", "DK": "Copenhagen", "FI": "Helsinki", "NO": "Oslo", "IS": "Iceland", "IT": "Milan|Italiana",
    "ES": "Madrid|Spanish|Bolsa", "AT": "Vienna|Wiener", "PL": "Warsaw", "GR": "Athens", "LU": "Luxembourg",
    "DE": "Frankfurt|Xetra", "CZ": "Prague", "HU": "Budapest", "SK": "Bratislava", "SI": "Ljubljana",
    "HR": "Zagreb", "RO": "Bucharest", "BG": "Sofia", "EE": "Tallinn", "LV": "Riga", "LT": "Vilnius",
    "CY": "Cyprus", "MT": "Malta",
}
FOREIGN = re.compile(r"New York|NASDAQ(?! (Stockholm|Copenhagen|Helsinki|Iceland|Riga|Tallinn|Vilnius))|OTC|Over-the-counter|Pink|Tokyo|Toronto", re.I)


# Wikidata keeps old tickers after a rename, mostly without an end date, next to the new one.
# Old -> new, used only when the company's listings include the new ticker (checked 2026-10-21).
RENAMED = {"RDSA": "SHEL", "RDSB": "SHEL", "TOT": "TTE", "FP": "TTE", "DAI": "MBG", "BELG": "PROX",
           "DPW": "DHL", "SPD": "FRAS", "SLA": "ABDN", "888": "EVOK", "COM": "CBK", "THQN B": "EMBRACER",
           "AQU": "GGS", "LVC": "TXT"}


def pick_ticker(tickers, country):
    tickers = sorted(tickers, key=lambda t: len(t) > 2 and bool(t[2]))
    tickers = [(t[0], t[1]) for t in tickers]
    names = {t for t, _ in tickers}
    home = HOME_EXCHANGE.get(country)
    picked = next(((t, ex) for t, ex in tickers if home and re.search(home, ex, re.I)), None) \
        or next(((t, ex) for t, ex in tickers if not FOREIGN.search(ex)), None)
    if not picked:
        return None, None
    t, ex = picked
    return (RENAMED[t] if RENAMED.get(t) in names else t), ex


def tidy(name):
    name = re.sub(r"\s+", " ", name or "").strip()
    if not (name.isupper() and len(name) > 4):
        return name
    # SAP AG, not Sap Ag: short words are kept as acronyms and legal forms.
    return " ".join(w if len(w) <= 3 and w.isalpha() else w.title() for w in name.split())


FIELDS = ["lei", "isin", "name", "country", "ticker", "exchange", "mic", "sector", "industry", "currency", "periodEnd",
          "revenueEur", "revenue", "ebitda", "operatingProfit", "netIncome", "operatingCashFlow", "capex", "fcf",
          "dividendsPaid", "equity", "assets", "cash", "debt", "netDebt", "eps", "roe", "ebitdaMargin",
          "operatingMargin", "netMargin", "fcfMargin", "netDebtToEbitda", "revenueGrowth", "netIncomeGrowth",
          "fscore", "fscoreTests", "payoutFcf", "payoutNet", "financial", "source", "origin", "shares"]


def add_shares(rows, listed):
    """Shares in issue from data/shares.json (German and Swiss companies have no reports on filings.xbrl.org,
    so without it they have no market value and drop off the maps). Adds the companies the file
    names that are not on the list yet (Wikidata lists no ticker for them, Porsche has no FIRDS row)."""
    path = ROOT / "data/shares.json"
    if not path.exists():
        return 0
    known = json.loads(path.read_text())["rows"]
    by_isin = {r["isin"]: r for r in rows if r.get("isin")}
    lei_of = {l["isin"]: lei for lei, l in listed.items()}
    have = {r["lei"] for r in rows}
    for isin, k in known.items():
        r = by_isin.get(isin)
        if not r and k.get("name"):
            lei = k.get("lei") or lei_of.get(isin)
            if not lei or lei in have:
                continue
            l = listed.get(lei, {})
            r = {"lei": lei, "isin": isin, "name": k["name"], "country": isin[:2], "ticker": k.get("ticker"),
                 "exchange": "Xetra", "mic": (l.get("mics") or ["XETR"])[0], "sector": k.get("sector"), "origin": "listing"}
            rows.append(r)
            have.add(lei)
        if r:
            r["shares"] = round(k["shares"] / 1e6, 6)   # millions, the unit of the report figures
            # Checked by hand, so they beat Wikidata (Siemens filed under Utilities, BMW still as BMW3).
            r.update({f: k[f] for f in ("name", "ticker", "sector") if k.get(f)})
    return sum(1 for r in rows if r.get("shares"))


def add_financials(rows, rates):
    """Annual figures for big companies with no report on filings.xbrl.org (SAP, Roche, Nestlé ...),
    copied by hand from their annual accounts into data/financials.json (millions, report currency).
    A report wins unless it is older or lacks revenue and EPS; the same ratios as fundamentals.metrics() are derived."""
    path = ROOT / "data/financials.json"
    if not path.exists():
        return 0
    known = json.loads(path.read_text())["rows"]
    pct = lambda a, b: None if a is None or not b else round(a / b * 100, 1)
    n = 0
    for r in rows:
        k = known.get(r.get("isin") or "")
        if not k:
            continue
        # a report wins when it is as new and has revenue and EPS; banks' reports often lack both
        # (Generali, DNB, CaixaBank), so they had no market value
        if (r.get("revenue") is not None or r.get("netIncome") is not None) and (
                (r.get("revenue") is not None and r.get("eps") is not None)
                or (r.get("periodEnd") or "") > k["periodEnd"]):
            continue
        rev, op, net, da = k.get("revenue"), k.get("operatingProfit"), k.get("netIncome"), k.get("da")
        fin = bool(k.get("financial"))
        ebitda = op + da if op is not None and da is not None and not fin else None
        capex = None if fin else k.get("capex")
        fcf = k["operatingCashFlow"] - capex if k.get("operatingCashFlow") is not None and capex is not None else None
        net_debt = k["debt"] - k["cash"] if not fin and k.get("debt") is not None and k.get("cash") is not None else None
        div = k.get("dividendsPaid")
        rate = rates.get(k["currency"])
        r.update({
            "currency": k["currency"], "periodEnd": k["periodEnd"], "source": k["source"], "financial": fin,
            "revenue": rev, "revenueEur": round(rev / rate, 1) if rev is not None and rate else None,
            "ebitda": ebitda, "operatingProfit": op, "netIncome": net, "operatingCashFlow": k.get("operatingCashFlow"),
            "capex": capex, "fcf": fcf, "dividendsPaid": div, "equity": k.get("equity"), "assets": k.get("assets"),
            "cash": k.get("cash"), "debt": k.get("debt"), "netDebt": net_debt, "eps": k.get("eps"),
            "roe": pct(net, k.get("equity")), "ebitdaMargin": pct(ebitda, rev), "operatingMargin": pct(op, rev),
            "netMargin": pct(net, rev), "fcfMargin": pct(fcf, rev),
            "netDebtToEbitda": round(net_debt / ebitda, 2) if net_debt is not None and ebitda and ebitda > 0 else None,
            "revenueGrowth": pct(rev - k["revenuePrev"], k["revenuePrev"]) if rev is not None and k.get("revenuePrev") else None,
            "netIncomeGrowth": pct(net - k["netIncomePrev"], abs(k["netIncomePrev"])) if net is not None and k.get("netIncomePrev") else None,
            "payoutFcf": pct(div, fcf) if div and fcf and fcf > 0 else None,
            "payoutNet": pct(div, net) if div and net and net > 0 else None,
            "origin": "hand",
        })
        n += 1
    return n


def shares_from_report(rows):
    """Everyone without a hand-checked count gets the one in the annual report, so loss-makers and
    companies with no revenue line in another currency still get a market value: the tagged count,
    else profit / EPS (after fix_eps_scale). A tagged count far from profit / EPS is a unit slip and
    the division wins; a count that puts price-to-sales out of reach is left out. Shares are in
    millions, like the report figures."""
    path = ROOT / "js/company-prices.json"
    prices = json.loads(path.read_text())["rows"] if path.exists() else {}
    n = 0
    for r in rows:
        if r.get("shares"):
            continue
        eps, ni, tagged = r.get("eps"), r.get("netIncome"), r.get("sharesTagged")
        implied = ni / eps if eps and ni and eps * ni > 0 else None
        if implied and abs(eps) < 0.05 and abs(eps * 100 - round(eps * 100)) < 1e-9:
            implied = None   # EPS rounded to whole cents (TIM 0.01): profit / EPS can be off by half
        shares = tagged if tagged and (implied is None or 0.5 < tagged / implied < 2) else implied
        price = (prices.get(r.get("isin") or "") or [None])[0]
        if shares and price and (r.get("revenueEur") or 0) > 50 and not 0.01 <= price * shares / r["revenueEur"] <= 100:
            continue
        if shares:
            r["shares"], n = round(shares, 6), n + 1
    return n


def fix_eps_scale(rows, rates=None):
    """Some filers tag earnings per share in cents or thousands (3i in pence, Logista 2,130 instead of
    2.13), which turns market value and P/E into nonsense. Against today's price, an EPS that gives an
    impossible P/E and price-to-sales is moved by powers of ten to the believable value nearest a
    typical one; a loss, or a profit with no believable scale, loses its EPS. Returns counts."""
    path = ROOT / "js/company-prices.json"
    if not path.exists():
        return 0, 0
    prices = json.loads(path.read_text())["rows"]
    fixed = dropped = 0
    for r in rows:
        price = (prices.get(r.get("isin") or "") or [None])[0]
        eps, ni = r.get("eps"), r.get("netIncome")
        if not price or not eps or not ni or eps * ni <= 0:
            continue
        fx = r["revenueEur"] / r["revenue"] if r.get("revenueEur") and r.get("revenue") else 1 if r.get("currency") == "EUR" \
            else 1 / rates[r["currency"]] if rates and rates.get(r.get("currency") or "") else None
        if not fx:
            continue
        rev = r["revenueEur"] if (r.get("revenueEur") or 0) > 50 else None

        def fit(f):
            """How far (in decades) from typical values the EPS times f puts P/E and price-to-sales;
            None when either is impossible. Both must hold, so a real one-off gain (P/E 0.8,
            ordinary price-to-sales) is left alone."""
            d = 0
            if eps > 0:
                pe = price / (eps * f * fx)
                if not 2 <= pe <= 300:
                    return None
                d += abs(math.log10(pe / 15))
            if rev:
                ps = price * ni / (eps * f) / rev
                if not 0.01 <= ps <= 100:
                    return None
                d += abs(math.log10(ps / 1.5))
            return d
        pe = price / (eps * fx) if eps > 0 else None
        ps = price * ni / eps / rev if rev else None
        wrong = (ps is not None and not 0.003 <= ps <= 300) and (pe is None or not 0.8 <= pe <= 1000) \
            or ps is None and pe is not None and not 0.2 <= pe <= 5000 \
            or pe is not None and pe < 0.5   # a P/E under 0.5 is never real; often EPS in cents
        if not wrong:
            continue
        # A loss tests against price-to-sales alone, too loose to pick a scale: drop it instead.
        options = [(fit(10 ** k), k) for k in range(-6, 7) if k and fit(10 ** k) is not None] if eps > 0 else []
        if options:
            r["eps"] = eps * 10 ** min(options)[1]
            fixed += 1
        else:
            r["eps"] = None
            dropped += 1
    return fixed, dropped


def one_company_per_isin(rows):
    """Two issuers must never share a share ISIN (FIRDS sometimes files a subsidiary's share under its
    parent too). The one with the larger revenue keeps it; the other loses the ISIN, so no price and
    no market value is attached to the wrong company. Returns how many lost it."""
    owner, lost = {}, 0
    for r in sorted(rows, key=lambda r: -(r.get("revenueEur") or 0)):
        if not r.get("isin"):
            continue
        if r["isin"] in owner:
            log(f"  ISIN {r['isin']} already belongs to {owner[r['isin']]}, dropped from {r['name']}")
            r["isin"], r["mic"] = None, None
            lost += 1
        else:
            owner[r["isin"]] = r["name"]
    return lost


def apply_fixes(rows):
    """Hand-checked sector, name and ticker by ISIN (data/fixes.json) over what Wikidata and the
    name rules give (LVMH filed under Consumer Staples, Rolls-Royce under Energy), and companies left
    off. Returns the rows to keep and how many were fixed."""
    path = ROOT / "data/fixes.json"
    if not path.exists():
        return rows, 0
    fix = json.loads(path.read_text())
    rows = [r for r in rows if r["lei"] not in fix.get("drop", {})]
    n = 0
    for r in rows:
        f = fix["rows"].get(r.get("isin") or "")
        if f:
            r.update({k: f[k] for k in ("name", "ticker", "sector") if f.get(k)})
            n += 1
    return rows, n


def main():
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    issuers = list_filings()
    listed_file = ROOT / "data/listed.json"
    listed = json.loads(listed_file.read_text()) if listed_file.exists() else {}
    todo = [(lei, rec) for lei, rec in issuers.items()
            if (cache.get(lei, {}).get("fxo") != rec["filings"][0]["fxo_id"] or cache[lei].get("v") != CACHE_VERSION
                or (cache[lei].get("row") is None and cache[lei].get("retry") != RETRY_EMPTY))
            and (not listed or lei in listed)]
    log(f"{len(todo)} reports to read, {len(issuers) - len(todo)} cached")
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {}
        for lei, rec in todo:
            futures[pool.submit(lambda l=lei, r=rec: read_report(l, r) if time.time() - START < BUDGET else None)] = lei
        for fut in as_completed(futures):
            res = fut.result()
            if res is None:
                continue
            cache[futures[fut]] = {"fxo": res[0], "row": res[1], "v": CACHE_VERSION, "retry": RETRY_EMPTY}
            done += 1
            if done % 200 == 0:
                log(f"  read {done} reports")
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cache, separators=(",", ":")))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, separators=(",", ":")))
    log(f"read {done} reports this run")

    wd, fx = wikidata(), ecb_rates()
    log(f"{len(listed)} issuers with listed shares (FIRDS); {sum(1 for l in issuers if l in listed)} of them file reports here")
    rows, no_figures = [], 0
    for lei, rec in issuers.items():
        row = cache.get(lei, {}).get("row")
        if not row:
            no_figures += 1
            continue
        w = wd.get(lei, {})
        ticker, exchange = pick_ticker(w.get("tickers", []), rec["country"])
        listing = listed.get(lei)
        if listed and not listing:
            continue   # no shares on a European venue: bond-only issuers, unlisted companies
        if listing and listing["isin"][:2] not in EUROPE:
            continue
        rate = fx.get(row.get("currency") or "")
        rev_eur = round(row["revenue"] / rate, 1) if row.get("revenue") is not None and rate else None
        name = w.get("name") if w.get("name") and not re.fullmatch(r"Q\d+", w["name"]) else tidy(rec["name"])
        if " " not in name and tidy(rec["name"]).lower().startswith(name.lower() + " "):
            name = tidy(rec["name"])   # Wikidata's short label "Novo" for Novo Nordisk A/S
        # The ISIN shows the company's home country; the filing country can be where it filed (Boliden in Norway).
        country = listing["isin"][:2] if listing and listing["isin"][:2] in EUROPE - {"GG", "JE", "IM", "GI", "FO"} else rec["country"]
        rows.append({**row, "lei": lei, "name": name, "country": country,
                     "isin": listing["isin"] if listing else None,
                     "mic": (listing or {}).get("mics", [None])[0] if listing and listing.get("mics") else None,
                     "ticker": ticker, "exchange": exchange, "sector": sector_of(w.get("industries", [])),
                     "industry": ", ".join(sorted(w.get("industries", [])))[:60] or None, "revenueEur": rev_eur})
    # Listed European issuers with no usable report on filings.xbrl.org (all of Germany, most of
    # Poland, part of Belgium and France: Lufthansa, bpost) still get a row with name, ticker and
    # price, without figures. Only those Wikidata lists on an exchange, which leaves out
    # thousands of tiny or inactive issuers.
    have = {r["lei"] for r in rows}
    for lei, l in listed.items():
        w = wd.get(lei)
        if lei in have or not w or l["isin"][:2] not in EUROPE - {"GG", "JE", "IM", "GI", "FO"}:
            continue
        ticker, exchange = pick_ticker(w.get("tickers", []), l["isin"][:2])
        rows.append({"lei": lei, "isin": l["isin"], "name": tidy(l.get("name") or w.get("name")),
                     "country": l["isin"][:2], "mic": (l.get("mics") or [None])[0], "ticker": ticker,
                     "exchange": exchange, "sector": sector_of(w.get("industries", [])),
                     "industry": ", ".join(sorted(w.get("industries", [])))[:60] or None, "origin": "listing"})
    log(f"shares in issue for {add_shares(rows, listed)} companies (data/shares.json)")
    log(f"annual figures by hand for {add_financials(rows, fx)} companies (data/financials.json)")
    missing = [r for r in rows if not r["sector"]]
    extra = wikidata_extra({r["isin"] for r in missing if r["isin"]}, {r["lei"] for r in missing})
    via = {"wikidata": 0, "bank": 0, "name": 0}
    for r in missing:
        labels = extra.get(r["isin"] or "", []) + extra.get(r["lei"], [])
        if sector_of(labels):
            r["sector"] = sector_of(labels)
            via["wikidata"] += 1
        elif r.get("financial"):   # balance sheet of a bank or insurer
            r["sector"] = "Financials"
            via["bank"] += 1
        elif sector_by_name(r["name"]):
            r["sector"] = sector_by_name(r["name"])
            via["name"] += 1
    log(f"sector found for {sum(via.values())} of {len(missing)} without a Wikidata industry: {via}")
    log("earnings per share moved by powers of ten for %d companies, dropped for %d (checked against the price)" % fix_eps_scale(rows, fx))
    log(f"shares in issue from the annual report for {shares_from_report(rows)} more companies")
    rows, fixed = apply_fixes(rows)
    log(f"shared ISINs taken from {one_company_per_isin(rows)} companies (kept by the larger one)")
    log(f"hand-checked fixes for {fixed} companies (data/fixes.json)")
    rows.sort(key=lambda r: -(r.get("revenueEur") or 0))
    out = {"updated": date.today().isoformat(), "fields": FIELDS,
           "rows": [[r.get(k) for k in FIELDS] for r in rows]}
    (ROOT / "js/companies.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))

    by_country = {}
    for r in rows:
        by_country[r["country"]] = by_country.get(r["country"], 0) + 1
    report = [f"{len(rows)} companies with figures, {no_figures} without usable figures",
              f"with ticker: {sum(1 for r in rows if r['ticker'])}, with sector: {sum(1 for r in rows if r['sector'])}",
              f"with F-score: {sum(1 for r in rows if r.get('fscore') is not None)}, "
              f"F-score 8-9: {sum(1 for r in rows if (r.get('fscore') or 0) >= 8)}, "
              f"with dividend cover: {sum(1 for r in rows if r.get('payoutFcf') is not None)}",
              "by country: " + ", ".join(f"{k} {v}" for k, v in sorted(by_country.items(), key=lambda x: -x[1])),
              "", "largest 40 by revenue:"]
    report += [f"{r['name'][:34]:34} {r['country']} {r['ticker'] or '-':8} {r['sector'] or '-':22} "
               f"{r.get('periodEnd')} rev€={r.get('revenueEur')} ebitda={r.get('ebitda')} fcf={r.get('fcf')} roe={r.get('roe')} F={r.get('fscore')} {r.get('fscoreTests')}"
               for r in rows[:40]]
    (ROOT / "data/universe-report.txt").write_text("\n".join(report) + "\n")
    log("\n".join(report))


if __name__ == "__main__":
    sys.exit(main())
