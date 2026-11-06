"""Market Radar: warning signs for the US stock market on one page, each with a traffic light.

Free sources, fetched once a day:
  FRED (St. Louis Fed)  yield curve, Sahm rule, Chicago Fed financial conditions, St. Louis Fed financial
                        stress, M2, Fed balance sheet, bank reserves, ECB balance sheet, corporate equities and
                        GDP (Buffett indicator). Only series of public bodies: index data owned by S&P, Cboe or
                        ICE may not be republished without their permission (Lub, 2026-11-06).
  ECB data portal       euro area systemic stress (CISS), yield curve and bank lending survey
  shillerdata.com       Shiller CAPE by month (Robert Shiller's own data file)
Indicators without a free daily source (forward P/E, earnings revisions, margin debt, breadth,
equal weight vs cap weight) come from data/radar-manual.json, with the date they were last checked.

Writes js/radar.json: every indicator with its value, date, light (green/amber/red), limits and a
monthly history for its chart, plus the election cycle table. A source that fails keeps the values
from the previous run, so the page never goes blank.
"""
import csv, html, io, json, re, statistics, sys, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "js" / "radar.json"
MANUAL = ROOT / "data" / "radar-manual.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; FINILA data refresh; +https://www.finila.com)"}
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
HIST_FROM = "1990-01"

GROUPS = [
    ["valuation", "Valuation", "How expensive the market is. High valuations predict lower returns over the next ten years, not the date of a fall."],
    ["economy", "Economy and earnings", "Recession signals. Most big stock market falls came together with a recession."],
    ["credit", "Credit and leverage", "When lending dries up or there is too much debt in the market, falls tend to be sharper."],
    ["market", "Market and sentiment", "Whether the whole market carries the rise or only a few stocks, and how fearful investors are."],
    ["europe", "Euro area", "The same signals for Europe from the ECB. US signals usually move first, but European stocks fall with them; these show whether the stress reaches Europe."],
    ["liquidity", "Liquidity", "How much money central banks put into the system. Shrinking balance sheets and falling reserves drain the fuel for markets."],
]

# Election cycle: S&P 500 from November to November by phase of the four-year US cycle.
# Averages since 1950 are approximate (Stock Trader's Almanac, LPL Research); actuals are filled by hand.
CYCLE = [
    {"period": "Nov 2024 – Nov 2025", "phase": "Post-election year", "average": 6, "actual": 17},
    {"period": "Nov 2025 – Nov 2026", "phase": "Midterm year", "average": 3, "actual": 9},
    {"period": "Nov 2026 – Nov 2027", "phase": "Post-midterm year", "average": 15, "actual": None},
    {"period": "Nov 2027 – Nov 2028", "phase": "Pre-election year", "average": 7, "actual": None},
    {"period": "Nov 2028 – Nov 2029", "phase": "Post-election year", "average": 6, "actual": None},
    {"period": "Nov 2029 – Nov 2030", "phase": "Midterm year", "average": 3, "actual": None},
]


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def fred(sid, start="1950-01-01"):
    """[(date string, value)] in date order; FRED marks missing days with '.' or an empty cell."""
    rows = list(csv.reader(io.StringIO(fetch(f"{FRED}{sid}&cosd={start}"))))
    out = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        try:
            out.append((r[0][:10], float(r[1])))
        except ValueError:
            continue
    if not out:
        raise RuntimeError(f"{sid}: no observations")
    return out


ECB = "https://data-api.ecb.europa.eu/service/data/"


def ecb(key, start="1999-01-01"):
    """[(date string, value)] from the ECB data portal (public data), e.g. CISS/D.U2.Z0Z.4F.EC.SS_CIN.IDX.
    Monthly and quarterly periods (2026-10, 2026-Q3) become the period's first day."""
    rows = list(csv.DictReader(io.StringIO(fetch(f"{ECB}{key}?format=csvdata&startPeriod={start[:7]}"))))
    out = []
    for r in rows:
        t, v = r.get("TIME_PERIOD", ""), r.get("OBS_VALUE", "")
        try:
            val = float(v)
        except ValueError:
            continue
        if re.fullmatch(r"\d{4}-Q[1-4]", t):
            t = f"{t[:4]}-{(int(t[-1]) - 1) * 3 + 1:02d}-01"
        elif re.fullmatch(r"\d{4}-\d{2}", t):
            t += "-01"
        out.append((t[:10], val))
    if not out:
        raise RuntimeError(f"ECB {key}: no observations")
    return sorted(out)


def monthly(obs, start=HIST_FROM):
    """Last value of each month, from `start` on: [[YYYY-MM, value]]."""
    m = {}
    for d, v in obs:
        if d[:7] >= start:
            m[d[:7]] = v
    return [[k, round(v, 4)] for k, v in sorted(m.items())]


def yoy(obs):
    """Year-on-year change in per cent of each observation against the one nearest a year earlier."""
    dates = [datetime.strptime(d, "%Y-%m-%d").date() for d, _ in obs]
    out, j = [], 0
    for i, (d, v) in enumerate(obs):
        target = dates[i] - timedelta(days=365)
        while j + 1 < i and dates[j + 1] <= target:
            j += 1
        if dates[j] <= target and abs((target - dates[j]).days) <= 40 and obs[j][1]:
            out.append((d, (v / obs[j][1] - 1) * 100))
    return out


def shiller_cape():
    """Shiller CAPE by month from Robert Shiller's own data file (ie_data.xls on shillerdata.com):
    [(YYYY-MM-01, value)] oldest first."""
    import xlrd   # pip install xlrd (the workflow installs it)
    page = fetch("https://shillerdata.com/")
    link = re.search(r'href="([^"]+ie_data\.xls[^"]*)"', page)
    if not link:
        raise RuntimeError("shillerdata.com: no ie_data.xls link")
    url = html.unescape(link.group(1))
    url = ("https:" + url) if url.startswith("//") else url
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        book = xlrd.open_workbook(file_contents=r.read())
    sh = book.sheet_by_name("Data")
    # The file has several ratio columns (CAPE, TR CAPE, excess CAPE yield...) and its header rows move:
    # take the column whose values match the known CAPE peaks (44.2 in Dec 1999, 32.6 in Sep 1929).
    rows = {round(sh.cell_value(i, 0), 2): i for i in range(sh.nrows) if isinstance(sh.cell_value(i, 0), float)}
    def near(c, d, want):
        v = sh.cell_value(rows[d], c) if d in rows else None
        return isinstance(v, float) and abs(v - want) < 1.0
    col = next((c for c in range(1, sh.ncols) if near(c, 1999.12, 44.2) and near(c, 1929.09, 32.6)), None)
    if col is None:
        raise RuntimeError("shiller: no column matches the known CAPE peaks")
    head = min(rows.values()) - 1
    out = []
    for i in range(head + 1, sh.nrows):
        d, v = sh.cell_value(i, 0), sh.cell_value(i, col)
        if isinstance(d, float) and isinstance(v, float) and v > 0:
            y, m = int(d), int(round((d - int(d)) * 100))
            if 1 <= m <= 12:
                out.append((f"{y}-{m:02d}-01", v))
    if len(out) < 100:
        raise RuntimeError(f"shiller: only {len(out)} rows")
    return sorted(out)


def last(obs):
    return obs[-1]


# Each indicator: how to get it, how to show it, and its limits. direction "up": higher is worse;
# "down": lower is worse. Limits are a simplification from history, not official rules.
DEFS = [
    {"id": "shiller-cape", "group": "valuation", "name": "Shiller P/E (CAPE)", "short": "Shiller CAPE",
     "direction": "up", "amber": 25, "red": 30, "fmt": "x1", "source": "Robert Shiller, Yale (shillerdata.com)",
     "rule": "Amber above 25, red above 30. Long-run median about 16.",
     "about": ["The cyclically adjusted price-to-earnings ratio divides the S&P 500 by the average of the last ten years of inflation-adjusted earnings. Averaging a decade smooths out booms and recessions, so the ratio shows how expensive stocks are against what companies normally earn.",
               "A high CAPE has never told you when a fall will come. In 1996 it was already about 28 and the market rose for four more years. What it does predict well is the return of the next ten years: from a CAPE near 40, history points to low single-digit yearly returns rather than the long-run 10%.",
               "Peaks: 32.6 in September 1929, 44.2 in December 1999, 38.6 in late 2021. Lows: about 5 in 1920 and 1932, 6.6 in 1982."]},
    {"id": "buffett-indicator", "group": "valuation", "name": "Buffett indicator", "short": "Buffett indicator",
     "direction": "up", "fmt": "pct0", "source": "Federal Reserve Z.1 (NCBEILQ027S), BEA GDP via FRED",
     "rule": "Amber above 1.4 times its long-run median, red above its March 2000 peak.",
     "about": ["The total value of US companies' shares divided by the size of the US economy (GDP). Warren Buffett called it in 2001 probably the best single measure of where valuations stand at any given moment.",
               "Here it uses the Federal Reserve's quarterly figure for the market value of US non-financial companies' shares, so it changes once a quarter, about ten weeks after the quarter ends.",
               "The limits come from the series itself: amber when it is 40% above its median since 1950, red when it is above the level of the dot-com peak in early 2000.",
               "A weaker signal than it looks: US companies earn more and more abroad, outside US GDP, so the ratio has drifted up for decades and has been red almost all the time since 2014."]},
    {"id": "forward-pe", "group": "valuation", "name": "S&P 500 forward P/E", "short": "Forward P/E",
     "direction": "up", "amber": 20, "red": 22, "fmt": "x1", "manual": True, "source": "FactSet Earnings Insight",
     "rule": "Amber above 20, red above 22. 10-year average about 19.",
     "about": ["The S&P 500 price divided by the earnings analysts expect over the next twelve months. It is lower than the CAPE when earnings are growing fast, because it looks forward instead of back.",
               "Above 22 the market was in 2000 and briefly in 2020 and 2024. A forward P/E near its average while the CAPE is at a record means investors expect earnings to keep growing strongly; if those expectations are cut, both the earnings and the multiple fall together."]},
    {"id": "yield-curve-10y-2y", "group": "economy", "name": "Yield curve 10y − 2y", "short": "10y−2y curve",
     "direction": "down", "amber": 0.5, "red": 0, "fmt": "pp2", "fred": "T10Y2Y", "source": "FRED (T10Y2Y)",
     "rule": "Red below 0 (inverted), amber from 0 to +0.5.",
     "about": ["The 10-year US Treasury yield minus the 2-year yield. Normally long-term debt pays more. When short-term rates rise above long-term ones the curve is inverted, a sign that markets expect the Fed to cut rates because the economy will weaken.",
               "Every US recession since the 1970s came after an inversion, usually 6 to 24 months later. The most dangerous moment has often been when the curve turns positive again (re-steepens), as in 2001 and 2007.",
               "The 2022–24 inversion was the longest on record. It ended in 2024 without a recession so far."]},
    {"id": "yield-curve-10y-3m", "group": "economy", "name": "Yield curve 10y − 3m", "short": "10y−3m curve",
     "direction": "down", "amber": 0.5, "red": 0, "fmt": "pp2", "fred": "T10Y3M", "source": "FRED (T10Y3M)",
     "rule": "Red below 0 (inverted), amber from 0 to +0.5.",
     "about": ["The 10-year Treasury yield minus the 3-month bill. Research at the New York Fed found this version of the curve the most reliable recession predictor, and the Fed's own recession probability model uses it.",
               "It inverted in late 2022 and turned positive again in late 2024."]},
    {"id": "sahm-rule", "group": "economy", "name": "Sahm rule", "short": "Sahm rule",
     "direction": "up", "amber": 0.3, "red": 0.5, "fmt": "n2", "fred": "SAHMREALTIME", "source": "FRED (SAHMREALTIME)",
     "rule": "Amber from 0.3, red from 0.5, the recession signal.",
     "about": ["Created by economist Claudia Sahm: the three-month average of the unemployment rate minus its lowest three-month average of the previous twelve months.",
               "When it reaches 0.5 percentage points, the US has been in a recession in every case since 1970, usually already at its start. It briefly reached 0.5 in August 2024 without a recession following, so it is a strong signal, not a certainty."]},
    {"id": "jobless-claims", "group": "economy", "name": "Jobless claims, rise from the 1-year low", "short": "Jobless claims",
     "direction": "up", "amber": 15, "red": 25, "fmt": "pct0s", "fred": "IC4WSA", "source": "US Department of Labor via FRED (IC4WSA)",
     "rule": "Amber when the 4-week average is 15% above its 1-year low, red at 25%.",
     "about": ["New claims for unemployment benefit in the US, as a 4-week average, against their lowest level of the past year. It is the fastest official reading of the job market, published every Thursday.",
               "Layoffs come before recessions: claims rose 20–30% from their low before the recessions of 2001 and 2008. A rise that fades, as in 2022–23, gives a false alarm, so it counts most together with the yield curve and the Sahm rule."]},
    {"id": "earnings-revisions", "group": "economy", "name": "Change in next-quarter earnings estimate", "short": "Earnings revisions",
     "direction": "down", "amber": 0, "red": -3, "fmt": "pct1s", "manual": True, "source": "FactSet Earnings Insight",
     "rule": "Amber below 0, red below −3%.",
     "about": ["How much analysts changed their estimate of S&P 500 earnings per share for the current quarter since it began. Normally estimates fall a few per cent during a quarter.",
               "Estimates that rise mean companies are doing better than expected. Broad cuts mean an expensive market loses the earnings that hold its price up."]},
    {"id": "financial-conditions", "group": "credit", "name": "Chicago Fed financial conditions index", "short": "Financial conditions",
     "direction": "up", "amber": -0.3, "red": 0, "fmt": "n2", "fred": "NFCI", "source": "Federal Reserve Bank of Chicago, NFCI via FRED (NFCI)",
     "rule": "Amber above −0.3, red above 0 (tighter than average).",
     "about": ["The Chicago Fed's weekly index of US financial conditions, built from 105 measures of money markets, debt and equity markets and bank lending. Zero is the average since 1971; positive values mean credit and funding are tighter than usual.",
               "It rose above zero in 2000, 2007–09 and March 2020, each time together with a large fall in stocks. Readings well below zero mean money flows easily."]},
    {"id": "bank-lending-standards", "group": "credit", "name": "Banks tightening business loans", "short": "Lending standards",
     "direction": "up", "amber": 10, "red": 30, "fmt": "pct0s", "fred": "DRTSCILM", "source": "Federal Reserve Senior Loan Officer Survey via FRED (DRTSCILM)",
     "rule": "Amber above +10%, red above +30% of banks (net) tightening.",
     "about": ["The share of US banks that made their rules for loans to companies stricter in the last three months, minus the share that eased them (Federal Reserve survey of loan officers, quarterly).",
               "Net tightening above about 30% came before or with every US recession since 1990: 1990, 2001, 2008 and 2020. When banks lend less, companies invest and hire less a few quarters later."]},
    {"id": "margin-debt", "group": "credit", "name": "Margin debt, year-on-year", "short": "Margin debt",
     "direction": "up", "amber": 25, "red": 50, "fmt": "pct0s", "manual": True, "source": "FINRA margin statistics",
     "rule": "Amber above +25%, red above +50% a year.",
     "about": ["Money investors borrowed from brokers to buy stocks, as reported monthly by FINRA. Leverage pushes prices up on the way up and forces selling on the way down.",
               "Year-on-year growth of 50% or more came near the tops of 2000, 2007 and 2021."]},
    {"id": "market-breadth", "group": "market", "name": "S&P 500 stocks above their 200-day average", "short": "Market breadth",
     "direction": "down", "amber": 50, "red": 40, "fmt": "pct1", "manual": True, "source": "thetrading.tools",
     "rule": "Amber below 50%, red below 40%.",
     "about": ["The share of S&P 500 companies whose price is above its own 200-day average. When the index rises while this share falls, only a few large stocks carry the market, as in 1999 and 2007."]},
    {"id": "equal-weight-gap", "group": "market", "name": "Average stock against the index, 3 years", "short": "Concentration",
     "direction": "down", "amber": -5, "red": -10, "fmt": "pct1s", "manual": True, "source": "S&P 500 Equal Weight vs S&P 500, thetrading.tools",
     "rule": "Amber below −5%, red below −10%.",
     "about": ["The return of the equal-weight S&P 500 (every company counts the same) minus the normal index (big companies count more) over three years. A large negative gap means a handful of giants did most of the work, which leaves the index exposed if they stumble."]},
    {"id": "financial-stress", "group": "market", "name": "St. Louis Fed financial stress index", "short": "Financial stress",
     "direction": "up", "amber": 0, "red": 1, "fmt": "n2", "fred": "STLFSI4", "source": "Federal Reserve Bank of St. Louis (STLFSI4)",
     "rule": "Amber above 0 (more stress than average), red above 1.",
     "about": ["The St. Louis Fed's weekly measure of stress in financial markets, from 18 series: interest rates, yield spreads and expected volatility of stocks and bonds. Zero is normal; each point is one standard deviation above it.",
               "It reached about 5 in March 2020 and above 9 in late 2008. Values below zero mean calm markets."]},
    {"id": "euro-stress-ciss", "group": "europe", "name": "ECB systemic stress index (CISS)", "short": "Euro stress (CISS)",
     "direction": "up", "amber": 0.2, "red": 0.35, "fmt": "n2", "ecb": "CISS/D.U2.Z0Z.4F.EC.SS_CIN.IDX", "source": "European Central Bank, CISS",
     "rule": "Amber above 0.2, red above 0.35 (scale 0 to 1).",
     "about": ["The ECB's composite indicator of systemic stress in the euro area: money markets, bonds, stocks, currencies and banks in one daily number from 0 (calm) to 1 (crisis).",
               "It reached about 0.8 in late 2008, 0.6 in the euro crisis of 2011–12, 0.4 in March 2020 and 0.35 in 2022. Values below 0.1 mean calm markets."]},
    {"id": "euro-yield-curve", "group": "europe", "name": "Euro area yield curve 10y − 2y", "short": "Euro curve",
     "direction": "down", "amber": 0.5, "red": 0, "fmt": "pp2", "source": "European Central Bank, AAA government bond yield curve",
     "rule": "Red below 0 (inverted), amber from 0 to +0.5.",
     "about": ["The 10-year yield minus the 2-year yield of the best-rated euro area government bonds (ECB yield curve). Like the US curve, an inversion means markets expect the ECB to cut rates because the economy will weaken.",
               "It inverted in 2022–24, the first long inversion since the euro started; the euro area economy stagnated in 2023 without a deep recession."]},
    {"id": "euro-bank-lending", "group": "europe", "name": "Euro area banks tightening business loans", "short": "Euro lending",
     "direction": "up", "amber": 10, "red": 25, "fmt": "pct0s", "ecb": "BLS/Q.U2.ALL.O.E.Z.B3.ST.S.WFNET", "source": "European Central Bank, Bank Lending Survey",
     "rule": "Amber above +10%, red above +25% of banks (net) tightening.",
     "about": ["The ECB's quarterly survey of about 150 euro area banks: the share that tightened their credit standards for loans to companies minus the share that eased them.",
               "Net tightening of 25% or more came with the recessions of 2008–09 and 2011–13 and in 2022–23."]},
    {"id": "fed-balance-sheet", "group": "liquidity", "name": "Fed balance sheet, year-on-year", "short": "Fed balance sheet",
     "direction": "down", "amber": 0, "red": -5, "fmt": "pct1s", "fred": "WALCL", "source": "Federal Reserve H.4.1 via FRED (WALCL)",
     "rule": "Amber when shrinking, red when shrinking more than 5% a year.",
     "about": ["Total assets of the Federal Reserve. When the Fed buys bonds (quantitative easing) it creates money; when it lets them run off (quantitative tightening) it takes money out.",
               "The balance sheet peaked at almost $9 trillion in April 2022 and shrank under QT until the Fed stopped in late 2025.",
               "Popular with traders, but a weaker signal than the yield curve or credit: stocks rose through most of the 2022–25 shrinking."]},
    {"id": "bank-reserves", "group": "liquidity", "name": "Bank reserves at the Fed", "short": "Bank reserves",
     "direction": "down", "amber": 3.0, "red": 2.7, "fmt": "usdT", "fred": "WRESBAL", "source": "Federal Reserve H.4.1 via FRED (WRESBAL)",
     "rule": "Amber below $3.0 trillion, red below $2.7 trillion.",
     "about": ["Cash that banks hold at the Federal Reserve. It is the money banks lend and trade with; when it gets scarce, short-term funding markets seize up, as in September 2019.",
               "Near $2.8 trillion in late 2025, money market rates showed strain and the Fed ended quantitative tightening."]},
    {"id": "ecb-balance-sheet", "group": "liquidity", "name": "ECB balance sheet, year-on-year", "short": "ECB balance sheet",
     "direction": "down", "amber": 0, "red": -5, "fmt": "pct1s", "fred": "ECBASSETSW", "source": "European Central Bank via FRED (ECBASSETSW)",
     "rule": "Amber when shrinking, red when shrinking more than 5% a year.",
     "about": ["Total assets of the Eurosystem (the ECB and euro area national banks). It shrinks as the ECB lets the bonds it bought in 2015–2022 mature and as banks repay cheap loans."]},
    {"id": "m2-money-supply", "group": "liquidity", "name": "Money supply M2, year-on-year", "short": "M2 money supply",
     "direction": "down", "amber": 2, "red": 0, "fmt": "pct1s", "fred": "M2SL", "source": "Federal Reserve H.6 via FRED (M2SL)",
     "rule": "Red when money shrinks, amber below +2% a year.",
     "about": ["Cash, bank deposits and money market funds held by the public in the US. Money that grows faster than the economy tends to end up in asset prices.",
               "Watched by many investors, but the statistical link to stock returns is weak; treat it as background rather than a timing signal.",
               "In 2022–23 M2 fell for the first time since the 1930s, together with a bear market in stocks and bonds."]},
]


def light(d, v):
    a, r = d["amber"], d["red"]
    if d["direction"] == "down":
        return "red" if v < r else "amber" if v < a else "green"
    return "red" if v >= r else "amber" if v >= a else "green"


def compute(d, cache):
    """Fills value, date and history of one indicator from its source."""
    i = d["id"]
    if i == "shiller-cape":
        obs = shiller_cape()
        v = last(obs)
        return v[1], v[0], [[k[:7], val] for k, val in obs if k[:4] >= "1900"]
    if i == "buffett-indicator":
        eq, gdp = cache("NCBEILQ027S"), cache("GDP")
        g = dict(gdp)
        ratio = [(dd, e / 1000 / g[dd] * 100) for dd, e in eq if dd in g and g[dd]]
        vals = [v for dd, v in ratio if dd >= "1950"]
        d["amber"] = round(statistics.median(vals) * 1.4)
        d["red"] = round(max(v for dd, v in ratio if "1999-10" <= dd <= "2000-04"))
        qd, qv = ratio[-1]
        hist = [[dd[:7], round(x, 1)] for dd, x in ratio if dd >= "1950"]
        d["note"] = f"Quarterly figure for the quarter starting {qd[:7]}."
        return qv, qd, hist
    if i == "jobless-claims":
        obs = cache("IC4WSA", "1967-01-01")
        dates = [datetime.strptime(x, "%Y-%m-%d").date() for x, _ in obs]
        out = []
        for k, (dd, v) in enumerate(obs):
            win = [obs[j][1] for j in range(k, -1, -1) if (dates[k] - dates[j]).days <= 365]
            if len(win) >= 40:
                out.append((dd, (v / min(win) - 1) * 100))
        d["note"] = f"4-week average {obs[-1][1] / 1000:.0f} thousand a week."
        return out[-1][1], out[-1][0], monthly(out)
    if i == "euro-yield-curve":
        y10 = dict(ecb("YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y", "2004-09-01"))
        y2 = ecb("YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y", "2004-09-01")
        obs = [(dd, y10[dd] - v) for dd, v in y2 if dd in y10]
        d["note"] = f"10-year {y10[obs[-1][0]]:.2f}%, 2-year {dict(y2)[obs[-1][0]]:.2f}%."
        return obs[-1][1], obs[-1][0], monthly(obs, "2004-09")
    if d.get("ecb"):
        obs = ecb(d["ecb"])
        v = last(obs)
        return v[1], v[0], monthly(obs, "1999-01")
    obs = cache(d["fred"])
    if i in ("fed-balance-sheet", "ecb-balance-sheet", "m2-money-supply"):
        lv = obs[-1][1]
        if i == "fed-balance-sheet":
            d["note"] = f"Total assets ${lv / 1e6:.2f} trillion."
        elif i == "ecb-balance-sheet":
            d["note"] = f"Total assets €{lv / 1e6:.2f} trillion."
        else:
            d["note"] = f"M2 ${lv / 1e3:.1f} trillion."
        obs = yoy(obs)
    if i == "bank-reserves":
        scale = 1e6 if obs[-1][1] > 100000 else 1e3  # FRED reports millions or billions of dollars
        obs = [(dd, v / scale) for dd, v in obs]
    v = last(obs)
    return v[1], v[0], monthly(obs)


def fmt(kind, v):
    sign = "+" if v > 0 else "−" if v < 0 else ""
    a = abs(v)
    return {
        "x1": f"{v:.1f}×", "pct0": f"{v:.0f}%", "pct1": f"{v:.1f}%", "pct2": f"{v:.2f}%",
        "pct0s": f"{sign}{a:.0f}%", "pct1s": f"{sign}{a:.1f}%", "pp2": f"{sign}{a:.2f} pp",
        "n1": f"{v:.1f}", "n2": f"{v:.2f}", "usdT": f"${v:.2f}T",
    }[kind]


def main():
    old = {}
    if OUT.exists():
        try:
            old = {x["id"]: x for x in json.loads(OUT.read_text())["indicators"]}
        except Exception:
            old = {}
    manual = json.loads(MANUAL.read_text()) if MANUAL.exists() else {}
    series = {}

    def cache(sid, start="1950-01-01"):
        key = (sid, start)
        if key not in series:
            series[key] = fred(sid, start)
        return series[key]

    out, failed = [], []
    for d in DEFS:
        d = dict(d)
        row = {k: d[k] for k in ("id", "group", "name", "short", "direction", "rule", "source", "about") if k in d}
        try:
            if d.get("manual"):
                m = manual[d["id"]]
                v, dt, hist = m["value"], m["date"], m.get("history", [])
                d["note"] = m.get("note")
                row["manual"] = True
            else:
                v, dt, hist = compute(d, cache)
            row.update(value=round(v, 4), display=fmt(d["fmt"], v), date=dt, history=hist,
                       amber=d["amber"], red=d["red"], light=light(d, v))
            if d.get("note"):
                row["note"] = d["note"]
            print(f"OK   {d['id']}: {row['display']} ({dt}) {row['light']}")
        except Exception as e:
            failed.append(d["id"])
            print(f"FAIL {d['id']}: {e}")
            if d["id"] not in old:
                continue
            row = old[d["id"]]
            row["stale"] = True
        out.append(row)

    counts = {c: sum(1 for r in out if r.get("light") == c) for c in ("red", "amber", "green")}
    OUT.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "counts": counts, "groups": GROUPS, "indicators": out, "cycle": CYCLE, "failed": failed,
    }, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB), {counts}, failed: {failed or 'none'}")
    if len(failed) == len(DEFS):
        sys.exit(1)


if __name__ == "__main__":
    main()
