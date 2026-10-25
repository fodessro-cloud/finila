"""Pull each company's latest annual report from filings.xbrl.org (ESEF xBRL-JSON)
and compute the value-investor numbers FINILA shows.

Reads data/leis.json, writes data/fundamentals.json and js/fundamentals.js.
Amounts are in millions of the report currency; filings.xbrl.org states there
are no restrictions on how its data can be used.
"""
import json, sys, time, urllib.parse, urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://filings.xbrl.org"
UA = {"User-Agent": "FINILA data refresh (finila.com)"}

# First concept found wins.
CONCEPTS = {
    "revenue":   ["Revenue", "RevenueFromContractsWithCustomers", "RevenueFromSaleOfGoods",
                  "RevenueFromRenderingOfServices"],
    "op_profit": ["ProfitLossFromOperatingActivities"],
    "net":       ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "da":        ["DepreciationAndAmortisationExpense",
                  "AdjustmentsForDepreciationAndAmortisationExpense",
                  "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
                  "AdjustmentsForDepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
                  "AdjustmentsForDepreciationAndAmortisationExpenseAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"],
    "cfo":       ["CashFlowsFromUsedInOperatingActivities"],
    "capex_ppe": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                  "PurchaseOfPropertyPlantAndEquipment",
                  "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets"],
    "capex_int": ["PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities"],
    "dividends": ["DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid"],
    "equity":    ["EquityAttributableToOwnersOfParent", "Equity"],
    "assets":    ["Assets"],
    "cash":      ["CashAndCashEquivalents"],
    "debt_nc":   ["NoncurrentBorrowings", "LongtermBorrowings"],
    "debt_c":    ["CurrentBorrowings", "ShorttermBorrowings", "CurrentPortionOfLongtermBorrowings"],
    "debt":      ["Borrowings"],
    "eps":       ["BasicEarningsLossPerShare", "BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsPerShare"],
    # For the quality score (Piotroski F-score): this year against last year.
    "gross":     ["GrossProfit"],
    "cur_assets": ["CurrentAssets"],
    "cur_liab":  ["CurrentLiabilities"],
    "shares":    ["WeightedAverageShares", "AdjustedWeightedAverageShares"],   # rarely tagged; else profit / EPS
    "shares_out": ["NumberOfSharesOutstanding", "NumberOfSharesIssued", "NumberOfSharesIssuedAndFullyPaid"],
    # Used when no combined D&A line is tagged.
    "dep_ppe":   ["DepreciationPropertyPlantAndEquipment", "AdjustmentsForDepreciationPropertyPlantAndEquipment"],
    "dep_rou":   ["DepreciationRightofuseAssets"],
    "amort":     ["AmortisationIntangibleAssetsOtherThanGoodwill", "AdjustmentsForAmortisationExpense"],
}
DEBUG_WORDS = ("Depreciation", "Amortisation", "Amortization", "PurchaseOf", "Revenue", "Operating", "EBIT", "Capital")
NOT_AMOUNTS = ("Explanatory", "Policy", "Description", "Disclosure", "Accumulated", "Percentage", "Ratio", "Margin",
               "PerShare", "Segment")


def extension_key(name):
    """Company-specific (non-IFRS) concepts some issuers use for the lines we need."""
    if any(w in name for w in NOT_AMOUNTS):
        return None
    if "EBITDA" in name or "Ebitda" in name:
        return None if any(w in name for w in ("Adjusted", "Underlying", "Recurring")) else "ebitda_tag"
    if "Depreciation" in name and ("Amortisation" in name or "Amortization" in name):
        return "da"
    if name.startswith(("PurchaseOf", "PurchasesOf", "AcquisitionOf", "AcquisitionsOf", "PaymentsFor", "PaymentsTo")) \
            and ("PropertyPlant" in name or "TangibleAssets" in name or "TangibleFixedAssets" in name):
        return "capex_ppe"
    if "CapitalExpenditure" in name:
        return "capex_ppe"
    if any(w in name for w in ("OperatingProfit", "OperatingIncome", "ProfitFromOperations", "OperatingResult")) \
            and not any(w in name for w in ("Adjusted", "Underlying", "Recurring", "Other", "Before")):
        return "op_profit"
    return None
# Banks and insurers: EBITDA, free cash flow and net debt do not mean much for them.
FINANCIAL_MARKERS = {"InterestRevenueCalculatedUsingEffectiveInterestMethod", "RevenueFromInterest",
                     "InsuranceRevenue", "LoansAndAdvancesToCustomers", "DepositsFromCustomers",
                     "InsuranceContractsIssuedThatAreLiabilities"}
INSTANT = {"equity", "assets", "cash", "debt_nc", "debt_c", "debt", "cur_assets", "cur_liab", "shares_out"}


def get(url, as_json=True):
    url = urllib.parse.quote(url, safe=":/?&=%[]-_.~")   # some report file names contain spaces
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return json.load(r) if as_json else r.read()
        except Exception as e:
            err = e
            time.sleep(3 * (attempt + 1))
    print(f"  ! {url}: {err}")
    return None


def latest_annual(lei):
    data = get(f"{BASE}/api/entities/{lei}/filings?page[size]=100")
    filings = [f["attributes"] for f in (data or {}).get("data", []) if f["attributes"].get("json_url")]
    filings.sort(key=lambda a: (a["period_end"], a["processed"]), reverse=True)
    return filings


def parse_period(p):
    if "/" in p:
        a, b = p.split("/")
        return date.fromisoformat(a[:10]), date.fromisoformat(b[:10])
    return None, date.fromisoformat(p[:10])


def fiscal_end(facts):
    """Latest end date of a ~1 year profit or revenue period (xBRL-JSON ends are exclusive)."""
    ends = []
    for fact in facts:
        dims = fact.get("dimensions", {})
        if dims.get("concept") in ("ifrs-full:ProfitLoss", "ifrs-full:Revenue") and "/" in dims.get("period", ""):
            start, stop = parse_period(dims["period"])
            if 330 <= (stop - start).days <= 400:
                ends.append(stop)
    return max(ends) if ends else None


def extract(xjson, period_end):
    facts = list(xjson.get("facts", {}).values())
    end = fiscal_end(facts) or date.fromisoformat(period_end) + timedelta(days=1)
    near = lambda d, ref: abs((d - ref).days) <= 7
    prev_end = end - timedelta(days=365)
    found, seen, financial = {}, set(), False   # (key, which) -> (rank, value, unit)
    for fact in facts:
        dims = fact.get("dimensions", {})
        if set(dims) - {"concept", "entity", "period", "unit", "language"}:
            continue   # segment / axis breakdowns, we want group totals
        concept = dims.get("concept", "")
        prefix, _, name = concept.partition(":")
        if any(w in name for w in DEBUG_WORDS) and not any(w in name for w in NOT_AMOUNTS[:4]):
            seen.add(concept)
        if prefix == "ifrs-full" and name in FINANCIAL_MARKERS:
            financial = True
        if prefix == "ifrs-full":
            keys = [(k, names.index(name)) for k, names in CONCEPTS.items() if name in names]
        else:
            ext = extension_key(name)
            keys = [(ext, 100)] if ext else []
        for key, rank in keys:
            start, stop = parse_period(dims.get("period", ""))
            if key in INSTANT:
                if start is not None:
                    continue
            elif start is None or not 330 <= (stop - start).days <= 400:
                continue
            which = "cur" if near(stop, end) else "prev" if near(stop, prev_end) else None
            if not which:
                continue
            try:
                value = float(fact["value"])
            except (TypeError, ValueError):
                continue
            slot = (key, which)
            if slot not in found or rank < found[slot][0]:
                found[slot] = (rank, value, dims.get("unit", ""))
    cur = {k: v[1] for (k, w), v in found.items() if w == "cur"}
    # Banks and insurers list their balance sheet by liquidity, without current assets. Industrial groups
    # with a finance arm (Volvo) or customer loans also tag the bank concepts but keep the current split.
    if financial and "cur_assets" not in cur:
        cur["financial"] = True
    prev = {k: v[1] for (k, w), v in found.items() if w == "prev"}
    units = {v[2] for (k, w), v in found.items() if k not in ("eps", "shares", "shares_out")}
    currency = next((u.split(":")[1] for u in units if u.startswith("iso4217:")), None)
    return cur, prev, currency, end - timedelta(days=1), seen


def fscore(cur, prev):
    """Piotroski F-score: nine yes/no tests comparing this year with last year.
    Returns (score, tests) where tests is a 9-character string of 1 (passed), 0 (failed)
    or - (figure missing); None for banks and insurers, where the tests do not apply."""
    if cur.get("financial"):
        return None, None
    ratio = lambda d, a, b: d[a] / d[b] if d.get(a) is not None and d.get(b) else None
    debt = lambda d: d.get("debt_nc", d.get("debt"))
    test = lambda a, b, better: None if a is None or b is None else int(better(a, b))
    net, cfo = cur.get("net"), cur.get("cfo")
    shares = lambda d: d.get("shares") or (d["net"] / d["eps"] if d.get("net") and d.get("eps")
                                           and (d["net"] > 0) == (d["eps"] > 0) and abs(d["eps"]) > 1e-6 else None)
    lev_cur = debt(cur) / cur["assets"] if debt(cur) is not None and cur.get("assets") else None
    lev_prev = debt(prev) / prev["assets"] if debt(prev) is not None and prev.get("assets") else None
    tests = [
        None if net is None else int(net > 0),                                   # 1 profitable
        None if cfo is None else int(cfo > 0),                                   # 2 positive operating cash flow
        test(ratio(cur, "net", "assets"), ratio(prev, "net", "assets"), lambda a, b: a > b),   # 3 return on assets up
        None if net is None or cfo is None else int(cfo > net),                 # 4 cash flow above profit
        test(lev_cur, lev_prev, lambda a, b: a <= b),                            # 5 long-term debt / assets not up
        test(ratio(cur, "cur_assets", "cur_liab"), ratio(prev, "cur_assets", "cur_liab"), lambda a, b: a > b),  # 6 current ratio up
        test(shares(cur), shares(prev), lambda a, b: a <= b * 1.02),           # 7 no new shares (2 % for rounding of EPS)
        test(ratio(cur, "gross", "revenue"), ratio(prev, "gross", "revenue"), lambda a, b: a > b),  # 8 gross margin up
        test(ratio(cur, "revenue", "assets"), ratio(prev, "revenue", "assets"), lambda a, b: a > b),  # 9 asset turnover up
    ]
    known = [t for t in tests if t is not None]
    if len(known) < 6:
        return None, None
    return sum(known), "".join("-" if t is None else str(t) for t in tests)


def metrics(cur, prev):
    m = lambda v: None if v is None else round(v / 1e6, 1)
    pct = lambda a, b: None if a is None or not b else round(a / b * 100, 1)
    rev, op, net, da = cur.get("revenue"), cur.get("op_profit"), cur.get("net"), cur.get("da")
    if da is None and ("dep_ppe" in cur or "amort" in cur):
        da = sum(abs(cur.get(k, 0)) for k in ("dep_ppe", "dep_rou", "amort"))
    ebitda = op + abs(da) if op is not None and da is not None else cur.get("ebitda_tag")
    capex = sum(abs(cur[k]) for k in ("capex_ppe", "capex_int") if k in cur) if "capex_ppe" in cur else None
    fcf = cur["cfo"] - capex if "cfo" in cur and capex is not None else None
    debt = cur.get("debt")
    if debt is None and ("debt_nc" in cur or "debt_c" in cur):
        debt = cur.get("debt_nc", 0) + cur.get("debt_c", 0)
    net_debt = debt - cur["cash"] if debt is not None and "cash" in cur else None
    if cur.get("financial"):
        ebitda = capex = fcf = net_debt = None
    score, tests = fscore(cur, prev)
    return {
        "financial": bool(cur.get("financial")),
        "revenue": m(rev), "ebitda": m(ebitda), "operatingProfit": m(op), "netIncome": m(net),
        "operatingCashFlow": m(cur.get("cfo")), "capex": m(capex), "fcf": m(fcf),
        "dividendsPaid": m(abs(cur["dividends"])) if "dividends" in cur else None,
        "equity": m(cur.get("equity")), "assets": m(cur.get("assets")), "cash": m(cur.get("cash")),
        "debt": m(debt), "netDebt": m(net_debt), "eps": cur.get("eps"),
        # The share count where the report tags one (weighted average, else shares in issue at the year end).
        "sharesTagged": round((cur.get("shares") or cur.get("shares_out")) / 1e6, 6) if (cur.get("shares") or cur.get("shares_out") or 0) > 0 else None,
        "roe": pct(net, cur.get("equity")),
        "ebitdaMargin": pct(ebitda, rev), "operatingMargin": pct(op, rev), "netMargin": pct(net, rev),
        "fcfMargin": pct(fcf, rev),
        "netDebtToEbitda": round(net_debt / ebitda, 2) if net_debt is not None and ebitda and ebitda > 0 else None,
        "revenueGrowth": pct(rev - prev["revenue"], prev["revenue"]) if rev is not None and prev.get("revenue") else None,
        "netIncomeGrowth": pct(net - prev["net"], abs(prev["net"])) if net is not None and prev.get("net") else None,
        "fscore": score, "fscoreTests": tests,
        # Share of free cash flow and of profit paid out as dividends; above 100 % is not covered.
        "payoutFcf": pct(abs(cur["dividends"]), fcf) if "dividends" in cur and fcf and fcf > 0 else None,
        "payoutNet": pct(abs(cur["dividends"]), net) if "dividends" in cur and net and net > 0 else None,
    }


def main():
    leis = json.loads((ROOT / "data/leis.json").read_text())
    out, log = {}, []
    for ticker, hit in leis.items():
        if not hit:
            continue
        filings = latest_annual(hit["lei"])
        for f in filings[:3]:   # newest report that yields a revenue or net income figure
            xj = get(BASE + f["json_url"])
            if not xj:
                continue
            cur, prev, currency, fy_end, seen = extract(xj, f["period_end"])
            if "revenue" in cur or "net" in cur:
                row = metrics(cur, prev)
                row.update({"currency": currency, "periodEnd": fy_end.isoformat(),
                            "source": BASE + f["viewer_url"] if f.get("viewer_url") else None})
                out[ticker] = row
                missing = [k for k in ("revenue", "ebitda", "fcf") if row[k] is None]
                if missing:
                    log.append(f"        missing {missing}; tagged: {sorted(seen)[:40]}")
                log.append(f"{ticker:14} {row['periodEnd']} {currency} rev={row['revenue']} ebitda={row['ebitda']} "
                           f"net={row['netIncome']} fcf={row['fcf']} roe={row['roe']} nd/ebitda={row['netDebtToEbitda']}")
                break
        else:
            log.append(f"{ticker:7} no usable annual report ({len(filings)} filings)")
        time.sleep(0.3)
    stamp = date.today().isoformat()
    (ROOT / "data/fundamentals.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (ROOT / "data/fundamentals-report.txt").write_text("\n".join(log) + "\n")
    (ROOT / "js/fundamentals.js").write_text(
        "// Generated by scripts/data/fundamentals.py from filings.xbrl.org (ESEF annual reports).\n"
        "// Amounts in millions of the report currency. Do not edit by hand.\n"
        f"const FUNDAMENTALS_UPDATED = '{stamp}';\n"
        f"const FUNDAMENTALS = {json.dumps(out, ensure_ascii=False, separators=(',', ':'))};\n")
    print("\n".join(log))
    print(f"\n{len(out)} companies with real fundamentals")


if __name__ == "__main__":
    sys.exit(main())
