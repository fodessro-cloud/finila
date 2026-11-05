"""Test pages for US companies (Lub, 2026-11-05): figures from the company's own 10-K annual reports as filed
with the SEC (XBRL company facts, data.sec.gov), in the same shape as the European ESEF figures, so the company
page template can show them. The pages are not public: no sitemap, no search engines, no screener, no peers.

Output: data/preview/companies.json {"rows": [company row like js/companies.json], "history": {lei: {...}}}.
Runs in GitHub Actions (the container proxy blocks data.sec.gov)."""
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "data"))
import fundamentals as fd   # noqa: E402

OUT = ROOT / "data/preview/companies.json"
# SEC asks automated clients to name themselves (https://www.sec.gov/os/accessing-edgar-data).
UA = {"User-Agent": "FINILA research info@finila.com", "Accept-Encoding": "identity"}
YEARS = 6
COMPANIES = [
    {"cik": 50863, "isin": "US4581401001", "name": "Intel", "ticker": "INTC", "country": "US",
     "exchange": "Nasdaq", "mic": "XNAS", "sector": "Technology", "industry": "semiconductor industry"},
]
# US-GAAP concepts for the lines fundamentals.metrics() reads (ESEF names on the left), first one found wins.
GAAP = {
    "revenue":    ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "op_profit":  ["OperatingIncomeLoss"],
    "net":        ["NetIncomeLoss"],
    "da":         ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"],
    "cfo":        ["NetCashProvidedByUsedInOperatingActivities"],
    "capex_ppe":  ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "dividends":  ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "equity":     ["StockholdersEquity"],
    "assets":     ["Assets"],
    "cash":       ["CashAndCashEquivalentsAtCarryingValue", "CashAndDueFromBanks", "Cash",
                   "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt_nc":    ["LongTermDebtNoncurrent"],
    "debt_c":     ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"],
    "eps":        ["EarningsPerShareBasic"],
    "gross":      ["GrossProfit"],
    "cur_assets": ["AssetsCurrent"],
    "cur_liab":   ["LiabilitiesCurrent"],
    "shares":     ["WeightedAverageNumberOfSharesOutstandingBasic"],
    "inventories": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "payables":   ["AccountsPayableCurrent"],
    "leases":     ["OperatingLeaseLiability"],
    "interest":   ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "pbt":        ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                   "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax":        ["IncomeTaxExpenseBenefit"],
    "minorities": ["MinorityInterest"],
    "buybacks":   ["PaymentsForRepurchaseOfCommonStock"],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "disposals":  ["ProceedsFromDivestitureOfBusinessesNetOfCashDivested"],
    "borrowed":   ["ProceedsFromIssuanceOfLongTermDebt"],
    "repaid":     ["RepaymentsOfLongTermDebt"],
    "cogs":       ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
}
INSTANT = {"equity", "assets", "cash", "debt_nc", "debt_c", "cur_assets", "cur_liab", "inventories", "receivables",
           "payables", "leases", "minorities"}
POSITIVE = {"interest", "buybacks", "acquisitions", "disposals", "borrowed", "repaid", "cogs"}


def get(url):
    for wait in (0, 5, 20):
        time.sleep(wait + 0.3)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"  ! {url}: {e}", flush=True)
    return None


def ecb_usd():
    with urllib.request.urlopen("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml", timeout=60) as r:
        root = ET.fromstring(r.read())
    return {c.get("currency"): float(c.get("rate")) for c in root.iter() if c.get("currency")}["USD"]


def lei_of(name, state):
    """The LEI of the US legal entity with exactly this name (GLEIF), else None."""
    want = name.upper().replace(" CORP", " CORPORATION").replace("CORPORATIONORATION", "CORPORATION").strip()
    d = get("https://api.gleif.org/api/v1/lei-records?page[size]=50&filter[fulltext]=" + urllib.request.quote(name))
    for rec in (d or {}).get("data") or []:
        e = rec["attributes"]["entity"]
        if e["legalName"]["name"].upper().replace(".", "").strip() == want and (e.get("jurisdiction") or "").startswith("US"):
            return rec["id"]
    print(f"  ! no LEI found for {name}", flush=True)
    return None


def year_values(facts, fy_end, accn):
    """{ESEF-style key: value} for the fiscal year ending fy_end, as stated in that year's own 10-K
    (accn); a figure the report does not state is left out. Flows must cover about a year."""
    gaap = facts["facts"].get("us-gaap", {})
    cur = {}
    for key, names in GAAP.items():
        for n in names:
            units = gaap.get(n, {}).get("units", {})
            vals = units.get("USD/shares" if key == "eps" else "shares" if key == "shares" else "USD", [])
            hits = [v for v in vals if v.get("accn") == accn and v["end"] == fy_end and (
                key in INSTANT or (v.get("start") and 350 <= (date.fromisoformat(v["end"]) - date.fromisoformat(v["start"])).days <= 380))]
            if hits:
                cur[key] = hits[-1]["val"]
                break
    return cur


def row_of(cur, prev):
    m = fd.metrics(cur, prev)
    mil = lambda v: round(v / 1e6, 1)
    r = {k: m.get(k) for k in ("revenue", "ebitda", "operatingProfit", "netIncome", "operatingCashFlow", "capex", "fcf",
                               "dividendsPaid", "equity", "assets", "cash", "debt", "netDebt", "eps", "sharesTagged")
         if m.get(k) is not None}
    if cur.get("da") is not None:
        r["da"] = mil(abs(cur["da"]))
    for k, name in (("inventories", "inventories"), ("receivables", "receivables"), ("payables", "payables"),
                    ("leases", "leases"), ("gross", "gross"), ("cur_assets", "curAssets"), ("cur_liab", "curLiab"),
                    ("interest", "interest"), ("pbt", "pbt"), ("tax", "tax"), ("minorities", "minorities"),
                    ("buybacks", "buybacks"), ("acquisitions", "acquisitions"), ("disposals", "disposals"),
                    ("borrowed", "borrowed"), ("repaid", "repaid"), ("cogs", "cogs")):
        if cur.get(k) is not None:
            r[name] = mil(abs(cur[k])) if k in POSITIVE else mil(cur[k])
    return r, m


def main():
    usd = ecb_usd()
    rows, history = [], {}
    for co in COMPANIES:
        cik = f"{co['cik']:010d}"
        facts = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        subs = get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if not facts or not subs:
            print(f"! no SEC data for {co['name']}")
            continue
        rec = subs["filings"]["recent"]
        tenk = [dict(accn=rec["accessionNumber"][i], end=rec["reportDate"][i], filed=rec["filingDate"][i], doc=rec["primaryDocument"][i])
                for i in range(len(rec["form"])) if rec["form"][i] == "10-K"][:YEARS + 1]
        years, raw = {}, {}
        for f in sorted(tenk, key=lambda f: f["end"]):
            raw[f["end"]] = year_values(facts, f["end"], f["accn"])
            f["url"] = f"https://www.sec.gov/Archives/edgar/data/{co['cik']}/{f['accn'].replace('-', '')}/{f['doc']}"
        ends = sorted(raw)
        for i, end in enumerate(ends):
            prev = raw[ends[i - 1]] if i else {}
            r, _ = row_of(raw[end], prev)
            f = next(x for x in tenk if x["end"] == end)
            years[end[:4]] = {**r, "end": end, "source": f["url"]}
        years = dict(sorted(years.items())[-YEARS:])
        newest = ends[-1]
        r, m = row_of(raw[newest], raw[ends[-2]] if len(ends) > 1 else {})
        cover = facts["facts"].get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
        shares = max(cover, key=lambda v: v["end"])["val"] / 1e6 if cover else m.get("sharesTagged")
        lei = lei_of(subs.get("name") or co["name"], subs.get("stateOfIncorporation")) or f"SEC{cik}"
        row = {k: None for k in json.loads((ROOT / "js/companies.json").read_text())["fields"]}
        row.update({k: v for k, v in co.items() if k in row})
        row.update({k: m.get(k) for k in m if k in row})
        row.update(lei=lei, currency="USD", periodEnd=newest, revenueEur=round(m["revenue"] / usd, 1) if m.get("revenue") else None,
                   source=next(x for x in tenk if x["end"] == newest)["url"], origin=None, shares=round(shares, 3) if shares else None,
                   financial=False, sec=True, cik=co["cik"])
        rows.append(row)
        history[lei] = {"currency": "USD", "years": years}
        print(f"{co['name']} ({lei}): 10-K years {', '.join(years)}; newest {newest}:",
              {k: row[k] for k in ("revenue", "operatingProfit", "netIncome", "eps", "fcf", "netDebt", "shares")}, flush=True)
        for end in ends:
            missing = [k for k in GAAP if k not in raw[end]]
            print(f"  {end} not stated: {', '.join(missing) or '-'}")
        for y, v in years.items():
            print(f"  {y}", {k: v.get(k) for k in ("revenue", "gross", "operatingProfit", "netIncome", "eps", "operatingCashFlow", "capex", "debt", "cash", "equity")})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": date.today().isoformat(), "usdPerEur": usd, "rows": rows, "history": history},
                              separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
