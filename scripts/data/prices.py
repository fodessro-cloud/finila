"""Daily closing prices of the screener's stocks, from Deutsche Börse's free delayed data (Xetra, Frankfurt).

Under MiFIR every trading venue must publish its trades free of charge 15 minutes later.
Deutsche Börse does this as JSON files (https://mds.deutsche-boerse.com/mds-en/real-time-data/Delayed-data);
the "daily" Xetra post-trade file holds every Xetra trade of the last trading day, in EUR.
Xetra also trades many non-German European shares (ASML, Nestlé, Novo Nordisk ...), so one
file covers most of the screener.

They also publish every minute's trades (15 minutes late), so an hourly run shows prices
during the trading day; the daily file then fixes the official close.

Writes js/prices.js (last price, change against the previous close, day range, volume per
TICKER@Country) and keeps data/prices-history.json (ISIN -> {date: close}) so the daily
change can be computed. Stocks that traded on neither venue keep their illustrative figures.
"""
import gzip, hashlib, json, os, sys, tempfile, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "js" / "prices.js"
COMPANIES = ROOT / "js" / "companies.json"            # all European issuers with annual reports
OUT_ALL = ROOT / "js" / "company-prices.json"        # last price per ISIN for those issuers
HISTORY = ROOT / "data" / "prices-history.json"
INTRADAY = ROOT / "js" / "intraday"   # minute prices of the last closed day, one file per ISIN
WEEK = ROOT / "data" / "prices-week.json"        # half-hourly prices of the last 5 trading days, for the 1W chart
WEEK_DAYS = 5
STATE = ROOT / "data" / "prices-state.json"      # last closed day + the day in progress
# Minute prices of the day in progress, per venue (not in git: prices.yml keeps it in the Actions cache).
LIVE_BARS = ROOT / "data" / "live-bars.json"
LIST_URL = "https://mfs.deutsche-boerse.com/api/{}-posttrade"
# Xetra first; Börse Frankfurt (floor/Specialist trading) fills in shares Xetra does not trade.
VENUES = [("DETR", "Xetra"), ("DFRA", "Börse Frankfurt")]
FILE_URL = "https://mfs.deutsche-boerse.com/api/download/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; FINILA data refresh; +https://www.finila.com)"}
KEEP_DAYS = 400      # about a year and a half of closes: the price chart on every company page
MAX_MINUTE_FILES = 70    # per venue and run, a bit more than an hour (a trading day has about 900); when further behind, only the newest
MINUTE_BUDGET = 600      # seconds per run for minute files, so the job ends inside its 20 minutes

# ISIN of the share class each screener stock (js/data.js) stands for.
ISINS = {
    "SAP@Germany": "DE0007164600", "SIE@Germany": "DE0007236101", "ALV@Germany": "DE0008404005",
    "BASF@Germany": "DE000BASF111", "BMW@Germany": "DE0005190003", "MBG@Germany": "DE0007100000",
    "DTE@Germany": "DE0005557508", "DBK@Germany": "DE0005140008", "BAYN@Germany": "DE000BAY0017",
    "VOW3@Germany": "DE0007664039", "IFX@Germany": "DE0006231004", "ADS@Germany": "DE000A1EWWW0",
    "MUV2@Germany": "DE0008430026", "HEN3@Germany": "DE0006048432", "RWE@Germany": "DE0007037129",
    "CON@Germany": "DE0005439004",
    "MC@France": "FR0000121014", "TTE@France": "FR0000120271", "OR@France": "FR0000120321",
    "AIR@France": "NL0000235190", "BNP@France": "FR0000131104", "SAN@France": "FR0000120578",
    "CS@France": "FR0000120628", "RMS@France": "FR0000052292", "KER@France": "FR0000121485",
    "DG@France": "FR0000125486", "CAP@France": "FR0000125338", "RI@France": "FR0000120693",
    "ORA@France": "FR0000133308",
    "ASML@Netherlands": "NL0010273215", "SHEL@Netherlands": "GB00BP6MXD84", "INGA@Netherlands": "NL0011821202",
    "HEIA@Netherlands": "NL0000009165", "PHIA@Netherlands": "NL0000009538", "UNA@Netherlands": "GB00B10RZP78",
    "STLAM@Netherlands": "NL00150001Q9",
    "NESN@Switzerland": "CH0038863350", "ROG@Switzerland": "CH0012032048", "NOVN@Switzerland": "CH0012005267",
    "ABBN@Switzerland": "CH0012221716", "ZURN@Switzerland": "CH0011075394", "UBSG@Switzerland": "CH0244767585",
    "GIVN@Switzerland": "CH0010645932",
    "AZN@UK": "GB0009895292", "HSBA@UK": "GB0005405286", "BP@UK": "GB0007980591", "GLEN@UK": "JE00B4T3BW64",
    "RIO@UK": "GB0007188757", "GSK@UK": "GB00BN7SWP63", "ULVR@UK": "GB00B10RZP78", "DGE@UK": "GB0002374006",
    "BARC@UK": "GB0031348658", "LLOY@UK": "GB0008706128",
    "ITX@Spain": "ES0148396007", "IBE@Spain": "ES0144580Y14", "SAN@Spain": "ES0113900J37",
    "BBVA@Spain": "ES0113211835", "TEF@Spain": "ES0178430E18",
    "ENEL@Italy": "IT0003128367", "ENI@Italy": "IT0003132476", "RACE@Italy": "NL0011585146",
    "UCG@Italy": "IT0005239360", "ISP@Italy": "IT0000072618",
    "ATCO@Sweden": "SE0017486889", "VOLV@Sweden": "SE0000115446", "ERICB@Sweden": "SE0000108656",
    "HMB@Sweden": "SE0000106270", "NOVO@Denmark": "DK0062498333", "MAERSK@Denmark": "DK0010244508",
    "NOKIA@Finland": "FI0009000681", "UCB@Belgium": "BE0003739530", "ABI@Belgium": "BE0974293251",
    "CRH@Ireland": "IE0001827041", "EQNR@Norway": "NO0010096985", "OMV@Austria": "AT0000743059",
    "EDP@Portugal": "PTEDP0AM0009", "PKN@Poland": "PLPKN0000018",
}


def fetch(url, timeout=180, tries=6):
    """Download politely: Deutsche Börse answers 429 Too Many Requests when files are fetched back to back."""
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                body = r.read()
            time.sleep(2.1)   # they allow about 30 downloads a minute
            return body
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500 or n == tries - 1:
                raise
            wait = e.headers.get("Retry-After", "")
            wait = int(wait) if wait.isdigit() else min(90, 30 * (n + 1))
            print(f"  {e.code} from {url.rsplit('/', 1)[-1]}, waiting {wait}s")
            time.sleep(wait)


def list_files(venue):
    """(daily files, minute files) currently offered for a venue, oldest first."""
    files = json.loads(fetch(LIST_URL.format(venue))).get("CurrentFiles", [])
    return sorted(f for f in files if "-daily-" in f), sorted(f for f in files if "-daily-" not in f)


def file_day(name):
    return name.split("-daily-")[1][:10] if "-daily-" in name else name.split("-posttrade-")[1][:10]


def download_summary(name, wanted, bars=False, tries=6):
    with tempfile.NamedTemporaryFile(suffix=".json.gz") as tmp:
        tmp.write(fetch(FILE_URL + name, tries=tries))
        tmp.flush()
        return day_summary(tmp.name, wanted, bars)


def merge(into, more):
    """Add one batch of per-ISIN aggregates to another (both from the same day)."""
    for isin, b in more.items():
        a = into.get(isin)
        if not a:
            into[isin] = b
            continue
        if b["first"] < a["first"]:
            a["first"], a["open"] = b["first"], b["open"]
        if b["last"] >= a["last"]:
            a["last"], a["close"] = b["last"], b["close"]
        a["high"], a["low"] = max(a["high"], b["high"]), min(a["low"], b["low"])
        a["volume"] += b["volume"]
        a["trades"] += b["trades"]


def day_summary(path, wanted, bars=False):
    """Open, high, low, last price, volume and trade count per ISIN, from one day's trades.
    With bars=True also the last trade of every minute ("bars": {minute of day UTC: (time, price)})."""
    days = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            t = json.loads(line)
            isin = t.get("instrumentIdentificationCode")
            if isin not in wanted or t.get("priceCurrency") != "EUR":
                continue
            if t.get("mmtModificationInd", "-") != "-":      # cancelled or amended trade reports
                continue
            p, q, ts = float(t["price"]), float(t.get("quantity") or 0), t["tradingDateAndTime"]
            d = days.get(isin)
            if bars:
                minute = int(ts[11:13]) * 60 + int(ts[14:16])
                b = (d or {}).get("bars", {}).get(minute)
                if d and (not b or ts >= b[0]):
                    d["bars"][minute] = (ts, p)
            if not d:
                days[isin] = {"first": ts, "open": p, "last": ts, "close": p, "high": p, "low": p, "volume": q, "trades": 1}
                if bars:
                    days[isin]["bars"] = {minute: (ts, p)}
                continue
            if ts < d["first"]:
                d["first"], d["open"] = ts, p
            if ts >= d["last"]:
                d["last"], d["close"] = ts, p
            d["high"], d["low"] = max(d["high"], p), min(d["low"], p)
            d["volume"] += q
            d["trades"] += 1
    return days




def company_isins():
    if not COMPANIES.exists():
        return set()
    data = json.loads(COMPANIES.read_text())
    i = data["fields"].index("isin")
    out = {r[i] for r in data["rows"] if r[i]}
    # US test companies (scripts/data/sec_preview.py): priced the same way, shown only on their own test page.
    preview = ROOT / "data" / "preview" / "companies.json"
    if preview.exists():
        out |= {r["isin"] for r in json.loads(preview.read_text())["rows"] if r.get("isin")}
    return out


def quote(isin, a, day, final, history):
    past = history.get(isin, {})
    prev = max((k for k in past if k < day), default=None)
    prev_close = past[prev] if prev else None
    return {
        "isin": isin, "venue": a["venue"], "date": day, "time": a["last"][11:16], "final": final,
        "price": round(a["close"], 4), "open": round(a["open"], 4),
        "high": round(a["high"], 4), "low": round(a["low"], 4),
        "volume": int(a["volume"]), "trades": a["trades"],
        "prevClose": prev_close,
        "change": round((a["close"] / prev_close - 1) * 100, 2) if prev_close else None,
    }


def add_week(week, day, closed):
    """Keep the last trade of every half hour of the closed day next to the 4 days before it."""
    for isin, a in closed.items():
        halves = {}
        for m, (_, p) in sorted(a.get("bars", {}).items()):
            halves[m // 30] = [m, round(p, 4)]
        days = week.setdefault(isin, {})
        days[day] = list(halves.values())
        week[isin] = dict(sorted(days.items())[-WEEK_DAYS:])


def write_intraday(day, closed, week):
    """js/intraday/<ISIN>.json: the last trade of every minute of the closed day, for the 1H/4H/1D charts
    on the company pages. Not kept in git (about 2,000 files a day); prices.yml uploads them."""
    INTRADAY.mkdir(parents=True, exist_ok=True)
    for old in INTRADAY.glob("*.json"):
        old.unlink()
    for isin, a in closed.items():
        bars = sorted(a.pop("bars", {}).items())
        (INTRADAY / f"{isin}.json").write_text(json.dumps(
            {"date": day, "venue": a["venue"], "bars": [[m, round(p, 4)] for m, (_, p) in bars],
             "week": [[d, m, p] for d, ps in week.get(isin, {}).items() if d in week["_days"] for m, p in ps]}, separators=(",", ":")))
    print(f"Intraday charts for {day}: {len(closed)} shares")


def write_live_intraday(day, rows, bars, week):
    """During the trading day: js/intraday/<ISIN>.json from today's minute prices so far, with today's
    half hours added to the week. Shares that have not traded today keep the closed day's file online."""
    INTRADAY.mkdir(parents=True, exist_ok=True)
    past = [d for d in week.get("_days", []) if d < day][-(WEEK_DAYS - 1):]
    n = 0
    for isin, a in rows.items():
        mins = bars.get(a["venue_code"], {}).get(isin)
        if not mins:
            continue
        series = sorted((int(m), round(p, 4)) for m, (_, p) in mins.items())
        halves = {}
        for m, p in series:
            halves[m // 30] = [m, p]
        weekly = [[d, m, p] for d in past for m, p in week.get(isin, {}).get(d, [])] + [[day, m, p] for m, p in halves.values()]
        (INTRADAY / f"{isin}.json").write_text(json.dumps(
            {"date": day, "venue": a["venue"], "bars": [[m, p] for m, p in series], "week": weekly}, separators=(",", ":")))
        n += 1
    print(f"Intraday charts for {day} (trading day in progress): {n} shares")


def pick(per_venue):
    """One aggregate per ISIN: Xetra where it traded, otherwise Börse Frankfurt."""
    rows = {}
    for venue, label in reversed(VENUES):
        for isin, a in per_venue.get(venue, {}).items():
            rows[isin] = {**a, "venue": label}
    return rows


def main():
    wanted = set(ISINS.values()) | company_isins()
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    final = state.get("final", {"day": "", "rows": {}})
    live = state.get("live", {"day": "", "seen": {}, "agg": {}})
    listings = {venue: list_files(venue) for venue, _ in VENUES}

    # 1. A new consolidated daily file closes the day: its last prices become the official closes.
    daily = {venue: {file_day(f): f for f in listings[venue][0]} for venue, _ in VENUES}
    day = max(daily["DETR"], default="")
    asked = hashlib.sha1(",".join(sorted(wanted)).encode()).hexdigest()[:12]
    # Re-read the same day too when the list of shares changed (e.g. new issuers on the Fundamentals page).
    redo = os.environ.get("PRICES_REDO_DAY") == "1"   # workflow_dispatch input: rebuild the intraday charts
    if day > final["day"] or (day == final["day"] and (final.get("asked") != asked or redo)):
        per_venue = {}
        for venue, label in VENUES:
            if day in daily[venue]:
                per_venue[venue] = download_summary(daily[venue][day], wanted, bars=True)
                print(f"Daily {label} file for {day}: {len(per_venue[venue])} of our shares traded")
        closed = {isin: {**a, "day": day} for isin, a in pick(per_venue).items()}
        week = json.loads(WEEK.read_text()) if WEEK.exists() else {}
        have = set(week.pop("_days", []))
        # Days still missing from the week (first run, missed runs): read their daily files if still offered.
        for past in sorted(daily["DETR"])[-WEEK_DAYS:]:
            if past < day and past not in have:
                older = {v: download_summary(daily[v][past], wanted, bars=True) for v, _ in VENUES if past in daily[v]}
                add_week(week, past, pick(older))
                have.add(past)
                print(f"Week chart: added {past}")
        add_week(week, day, closed)
        have.add(day)
        week["_days"] = sorted(have)[-WEEK_DAYS:]
        WEEK.parent.mkdir(exist_ok=True)
        WEEK.write_text(json.dumps(week, separators=(",", ":"), sort_keys=True))
        write_intraday(day, closed, week)
        # A share that did not trade that day keeps its last real close.
        final = {"day": day, "asked": asked, "rows": {**final["rows"], **closed}}
        for isin, a in closed.items():
            past = history.setdefault(isin, {})
            past[day] = round(a["close"], 4)
            history[isin] = dict(sorted(past.items())[-KEEP_DAYS:])

    # 2. Minute files of a later day (the trading day in progress) give prices during the day.
    minutes = {venue: [f for f in listings[venue][1] if file_day(f) > final["day"]] for venue, _ in VENUES}
    today = max((file_day(f) for fs in minutes.values() for f in fs), default="")
    if live["day"] != today:
        live = {"day": today, "seen": {}, "agg": {}}
    try:
        bars = json.loads(LIVE_BARS.read_text())
    except (OSError, ValueError):
        bars = {}
    if bars.get("day") != today:
        bars = {"day": today, "venues": {}}
    started = time.monotonic()
    for venue, label in VENUES:
        new = [f for f in minutes[venue] if file_day(f) == today and f > live["seen"].get(venue, "")]
        if len(new) > MAX_MINUTE_FILES:
            # Far behind (missed runs): the newest minutes give the current price; the day's
            # volume and range stay partial until the daily file closes the day.
            print(f"{label} {today}: {len(new)} minute files waiting, reading the newest {MAX_MINUTE_FILES}")
            new = new[-MAX_MINUTE_FILES:]
        agg = live["agg"].setdefault(venue, {})
        vbars = bars["venues"].setdefault(venue, {})
        done = []
        for f in new:
            if time.monotonic() - started > MINUTE_BUDGET:
                print(f"{label} {today}: time budget used, the next run continues")
                break
            try:
                batch = download_summary(f, wanted, bars=True, tries=3)
                for isin, b in batch.items():
                    mins = vbars.setdefault(isin, {})
                    for m, (ts, p) in b.pop("bars").items():
                        if str(m) not in mins or ts >= mins[str(m)][0]:
                            mins[str(m)] = [ts, p]
                merge(agg, batch)
            except (urllib.error.URLError, OSError) as e:
                # Keep what was read; the next run carries on from the last file that came through.
                print(f"{label} {today}: stopped at {f} ({e}); the next run continues from there")
                break
            done.append(f)
        if done:
            live["seen"][venue] = done[-1]
            print(f"{label} {today}: read {len(done)} new minute files, up to {done[-1]}")
    live_rows = pick(live["agg"]) if today else {}
    if today:
        LIVE_BARS.parent.mkdir(exist_ok=True)
        LIVE_BARS.write_text(json.dumps(bars, separators=(",", ":")))
        codes = {label: venue for venue, label in VENUES}
        week = json.loads(WEEK.read_text()) if WEEK.exists() else {}
        write_live_intraday(today, {i: {**a, "venue_code": codes[a["venue"]]} for i, a in live_rows.items()}, bars["venues"], week)

    def quote_of(isin):
        a = live_rows.get(isin) or final["rows"].get(isin)
        if not a:
            return None
        return quote(isin, a, today if isin in live_rows else a["day"], isin not in live_rows, history)

    prices, missing = {}, []
    for key, isin in ISINS.items():
        q = quote_of(isin)
        if q:
            prices[key] = q
        else:
            missing.append(key)
    # Every issuer on the Fundamentals page: [price €, change %, venue, date, final].
    everyone = {}
    for isin in sorted(company_isins()):
        q = quote_of(isin)
        if q:
            everyone[isin] = [q["price"], q["change"], q["venue"], q["date"], q["final"]]

    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(history, separators=(",", ":"), sort_keys=True))
    STATE.write_text(json.dumps({"final": final, "live": live}, separators=(",", ":"), sort_keys=True))
    shown = max((p["date"] for p in prices.values()), default="")
    OUT.write_text(
        "// Generated by scripts/data/prices.py from Deutsche Börse's free delayed data (Xetra, Börse Frankfurt). Do not edit by hand.\n"
        f"const PRICES_DATE = '{shown}';\n"
        f"const PRICES_UPDATED = '{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC';\n"
        f"const PRICES = {json.dumps(prices, separators=(',', ':'), ensure_ascii=False)};\n"
    )
    OUT_ALL.write_text(json.dumps({
        "date": shown, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "Deutsche Börse delayed data (Xetra, Börse Frankfurt)",
        "fields": ["price", "change", "venue", "date", "final"], "rows": everyone,
    }, separators=(",", ":"), ensure_ascii=False))
    print(f"Fundamentals issuers with a price: {len(everyone)} of {len(company_isins())}")
    print(f"Prices for {len(prices)} of {len(ISINS)} stocks (closed day {final['day'] or '-'}, day in progress {today or '-'})")
    if missing:
        print(f"  no trades on Xetra or Frankfurt: {', '.join(missing)}")
    for k, p in prices.items():
        print(f"  {k:20} {p['isin']} {p['venue']:16} {p['date']} {p['time']} {p['price']:>10} trades {p['trades']:>6} change {p['change']}")


if __name__ == "__main__":
    main()
