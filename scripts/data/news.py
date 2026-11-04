"""Official company announcements from the exchanges (Nasdaq Nordic: Stockholm, Helsinki, Copenhagen,
Iceland, Baltics; Oslo Børs), matched to the companies on FINILA.

For each company it keeps:
  - news: the recent announcements that matter to an owner (reports, inside information, management
    changes, company announcements), as headline + date + link to the official text. Nothing is rewritten.
  - report: the newest quarterly / half-year / annual report announced, with sentences quoted word for
    word from the release: results for the period and the company's own outlook.

Announcements and their texts never change once published, so each one is read once and cached in
data/cache/news.json. Writes data/news.json: {lei: {"news": [...], "report": {...}}}.
"""
import html, io, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import outlook as ol   # noqa: E402  (sentence splitting and the outlook rules are shared)

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/news.json"
OUT = ROOT / "data/news.json"
CACHE_VERSION = 5
DAYS = int(os.environ.get("FINILA_NEWS_DAYS", "14"))   # how far back each run looks
KEEP_DAYS = 120                                         # how long an announcement stays on the page data
MAX_NEWS = 8
UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "application/json, text/html, */*"}

REPORT_HEAD = (r"interim report|quarter(ly)? report|\bq[1-4]\b.{0,12}\b(report|results|interim)|"
               r"(first|second|third|fourth)[- ]quarter.{0,20}(report|results)|results for the (first|second|third|fourth) quarter|"
               r"half[- ]year(ly)? (report|results|financial report)|year[- ]end report|full[- ]year results|annual results|"
               r"nine[- ]month|six[- ]month|january\s*[-–]\s*(march|june|september|december)|trading update|"
               r"interim management statement|business review")
REPORT_CAT = r"interim report|half[- ]year|annual financial|year[- ]end"
SKIP_HEAD = (r"invitation|presentation of|webcast|conference call|audiocast|teleconference|capital markets day|"
             r"buy-?back|own shares|treasury shares|managers'? transactions|transactions? (by|of) (persons|insiders)|"
             r"voting rights|share capital|total number of shares|listing of|admission to trading|prospectus|"
             r"notice (to|of) (the )?(annual|extraordinary) general meeting|bond|notes? due|coupon|nomination committee")
SKIP_CAT = (r"managers.? transaction|own shares|voting rights|major shareholder|articles of association|prospectus|corporate action|"
            r"notice to general meeting|ex date|adjustment of interest rate|trading halt|announcement from oslo|non-regulatory|investor news")
# Oslo Børs carries most releases twice, in Norwegian and English: the Norwegian one is left out.
NORWEGIAN = r"\b(og|av|til|med|ved|fra|eller|inngår|melding|kvartal|kvartalet|resultat|resultater|selskap|utlån|vekst i|økt|invitasjon|korreksjon|flaggemelding|meldepliktig)\b"
# Results for the period: a financial line, a number and a word that says what happened to it.
RESULT_LINE = (r"net sales|revenue|revenues|sales|turnover|ebitda|ebita|ebit|operating (profit|income|result|margin)|"
               r"net (profit|income|result)|profit (before|after) tax|earnings per share|eps|free cash flow|"
               r"cash flow from operat|order intake|orders received|order backlog|order book|organic growth")
RESULT_VERB = (r"increased|decreased|rose|fell|grew|declined|amounted|was|were|totall?ed|reached|improved|"
               r"came in|ended at|up \d|down \d|growth of|increase of|decrease of|compared")


def get(url, as_json=True, tries=(0, 5, 20)):
    for wait in tries:
        time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return (json.loads(body) if as_json else (body, r.headers.get("Content-Type", "")))
        except Exception as e:   # one failed download must not stop the run
            err = e
    print(f"  ! {url[:90]}: {err}", flush=True)
    return None


LEGAL = (r"\(publ\)|\bp\.?l\.?c\.?|\ba/s\b|\bab\b|\basa\b|\boyj\b|\babp\b|\bhf\b|\bp/f\b|\bse\b|\bn\.?v\.?|"
         r"\bs\.?a\.?|\bag\b|\bltd\b|\blimited\b|\bcorporation\b|\bcorp\b|\bgroup\b|\bholding\b|\binc\b|"
         r"\bpublic limited company\b|\baktiebolag\b|\baktieselskabet\b|\bbank\b|\bpankki\b|\bd/s\b")
# Words that some announcements add to the registered name and others leave out ("Ericsson, Telefonab. L M",
# "Kinnevik, Investment AB", "UPM-Kymmene Oyj", "Lindab International"). Only these may differ.
FILLER = {"telefonab", "l", "m", "h", "investment", "investmentab", "international", "sweden", "aktieselskab", "eiendom",
          "kymmene", "publ", "as", "asa", "co", "kgaa", "mtg", "holdings", "the", "and", "sol", "abp"}
# Renamed or differently styled names, checked by hand: announcement name -> name on FINILA.
ALIASES = {"tieto": "tietoevry", "novonesisnovozymes": "novonesis", "alsydbank": "sydbank"}


def words(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(LEGAL, " ", s)
    return [w for w in re.split(r"[^a-z0-9]+", s) if w]


def norm(name):
    return "".join(words(name))


def matcher(by_name):
    """Exact normalised name; else the same name once the filler words above are left out on both sides.
    Never a partial match (Vincit is not Vinci). Such matches are logged so they can be checked by eye."""
    fuzzy = {}

    def core(ws):
        return "".join(w for w in ws if w not in FILLER)

    by_core = {}
    for k, (lei, ws) in by_name.items():
        c = core(ws)
        if len(c) >= 3:
            by_core.setdefault(c, set()).add(lei)

    def find(name):
        a = norm(name)
        a = ALIASES.get(a, a)
        if a in by_name:
            return by_name[a][0]
        c = core(words(name))
        hit = by_core.get(ALIASES.get(c, c))
        if hit and len(hit) == 1:
            fuzzy[name] = next(iter(hit))
            return next(iter(hit))
        return None
    return find, fuzzy


def text_of(raw, ctype):
    """Plain text of an announcement page or its PDF attachment."""
    if b"%PDF" in raw[:1024] or "pdf" in (ctype or "").lower():
        try:
            from pypdf import PdfReader
            pdf = PdfReader(io.BytesIO(raw))
            return re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in pdf.pages[:12]))
        except Exception as e:
            print(f"  ! pdf: {e}", flush=True)
            return ""
    src = raw.decode("utf-8", "replace")
    src = re.sub(r"(?is)<(script|style|head|nav|footer)\b.*?</\1>", " ", src)
    src = re.sub(r"(?i)</?(div|p|br|td|th|tr|li|h\d|table|section)\b[^>]*>", " ", src)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", src)))


JUNK = (r"please call|investor relations|\+\d\d \d|visit (www|home)\.|press release|interim report|quarterly report|"
        r"half[- ]year report|year[- ]end report|figures in parentheses|previously|estimated to|webcast|conference call")


def split_release(text):
    """Releases list key figures as bullets glued together by PDF extraction: cut at dashes used as bullets,
    at opening quotes and after a prior-year figure in brackets followed by a new sentence."""
    t = re.sub(r"\s[–•▪■]\s|\s-\s(?=[A-Z])", ". ", text)
    t = re.sub(r"\s[“\"]\s?(?=[A-Z])", ". ", t)
    t = re.sub(r"(\(\s?-?[\d.,\s]+\)|\d%\)|\d\))\s+(?=[A-Z][a-z])", r"\1. ", t)
    return t


YTD = (r"nine months|nine-month|\b9m\b|first nine|first half|six months|six-month|\bh1\b|half[- ]year|full[- ]year|year to date|\bytd\b|"
       r"january\s*[-–]\s*(june|september|december)|jan\s*[-–]\s*(jun|sep|dec)|\b1\.1\.?\s*[-–]\s*3\d\.\d|\b1\s*[-–]\s*(6|9|12)/20|for the year 20\d\d")
QUARTER = (r"\bq[1-4]\b|(first|second|third|fourth) quarter|the quarter|quarterly|"
           r"(january|april|july|october)\s*[-–]\s*(march|june|september|december)|\b(4\s*[-–]\s*6|7\s*[-–]\s*9|10\s*[-–]\s*12)/20")


def period_of(s):
    """'q' when a sentence speaks of the quarter, 'ytd' of the year so far, None when it does not say."""
    low = s.lower()
    q, y = re.search(QUARTER, low), re.search(YTD, low)
    if q and y:
        return "q" if q.start() < y.start() else "ytd"
    return "q" if q else "ytd" if y else None


def report_quarter(title, text, published):
    """Which quarter a report is for: from its title, else its first lines, else its publication date."""
    for t in (title, text[:600]):
        low = (t or "").lower()
        m = re.search(r"\bq([1-4])\b", low) or re.search(r"\b(first|second|third|fourth)[- ]quarter", low)
        if m:
            g = m.group(1)
            return {"first": 1, "second": 2, "third": 3, "fourth": 4}.get(g, int(g) if g.isdigit() else None)
        if re.search(r"half[- ]year|first half|six months|january\s*[-–]\s*june", low):
            return 2
        if re.search(r"nine months|nine-month|january\s*[-–]\s*september|1\.1\.?\s*[-–]\s*30\.9", low):
            return 3
        if re.search(r"year[- ]end report|full[- ]year (report|results)|annual (report|results)|january\s*[-–]\s*december", low):
            return 4
    m = int(published[5:7]) if published[5:7].isdigit() else 0
    return {1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 7: 2, 8: 2, 10: 3, 11: 3}.get(m)


def quotes(text, year):
    """Results for the period and the company's own outlook, word for word. Each result carries the period
    it speaks of: its own words ("in Q3", "in the first nine months"), else the heading above it."""
    head = split_release(text[:15000])   # the summary at the top: highlights, CEO comment, outlook
    results, seen = [], set()
    current = None
    for s in ol.sentences(head):
        own = period_of(s)
        current = own or current
        low = s.lower()
        if re.search(ol.NOISE, low) or re.search(JUNK, low) or not ol.english(low) or sum(ch.isdigit() for ch in s) > len(s) * 0.25:
            continue
        years = {int(y) for y in re.findall(r"\b(20\d\d)\b", s)}
        if years and year not in years:   # a sentence only about earlier years is history, not this period
            continue
        if re.search(fr"\b({RESULT_LINE})", low) and re.search(fr"\b({RESULT_VERB})", low) and re.search(ol.NUMBER, s) \
                and not re.search(fr"\b({ol.EXPECT})", low):
            if len(re.findall(r"\d+(?:[.,]\d+)?", s)) > 8 or re.search(r"\b(excl|incl|approx)\.$|\b\d+ of \d+\b", s):
                continue   # table rows, cut-off fragments and page footers
            key = re.sub(r"\W+", "", low)[:100]
            if key not in seen:
                seen.add(key)
                # A heading the PDF glued to the sentence ("January–September 2026 Orders received ...") is cut off.
                t = re.sub(r"^(?i:(january|april|july|october)\s*[-–]\s*(march|june|september|december)|"
                           r"(first|second|third|fourth) quarter|summary of [\w-]+ results)( 20\d\d)?:?\s+(?=[A-Z])", "", s)
                results.append({"text": re.sub(r"\.\.$", ".", t), "period": own or current})
    # Group figures before divisions and regions: releases put the group first, but not always.
    def rank(s):
        low = s.lower()
        return (-2 if re.match(r"(in|at) (?!q[1-4]|the |our |total|\d)[A-Z]", s) or re.match(r"[A-Z][\w&-]+( [A-Z][\w&-]+)* (achieved|reported|generated|increased|delivered) ", s) and not re.match(r"(Net|Group|Total|Revenue|Sales|Operating|Adjusted|Reported|Organic|Orders|Order|Earnings|Profit|EBIT|Cash|Free)", s) else 0) \
            + (2 if re.search(r"\b(group|net sales|total|we delivered|our net sales|revenues? (was|were|amounted|increased|decreased|grew)|net sales (was|were|amounted|increased|decreased|grew))\b", low) else 0)
    top = sorted(range(len(results)), key=lambda i: -rank(results[i]["text"]))[:6]
    results = [results[i] for i in sorted(top)]
    out = []
    for i in ol.pick(split_release(text[:60000]), year):
        low = i["text"].lower()
        if i["kind"] not in ("outlook", "target", "backlog") or re.search(r"please call|investor relations|interim report|press release", low):
            continue
        if re.search(r"\b(transaction|completion|closing|expected to close|fair value)\b", low):
            continue   # deal timetables and accounting effects, not the company's business outlook
        if re.search(r"previously expected|previous (estimate|guidance|outlook)", low) and not re.search(r"\b(now|rais|lower|updat|narrow|chang)", low):
            continue   # the old guidance on its own
        if i["kind"] == "backlog" and (i["text"] in [r["text"] for r in results] or not re.search(r"backlog|order book|orderbook|order stock", low)):
            continue   # order intake is a result of the period; backlog means orders not yet delivered
        out.append(i)
    return results, out[:5]


def nasdaq(since, today):
    items, start = [], 0
    while start < 4000:
        q = {"type": "json", "showAttachments": "true", "showCnsSpecific": "true", "showCompany": "true",
             "countResults": "false", "freeText": "", "company": "", "market": "", "cnscategory": "",
             "fromDate": since.isoformat(), "toDate": today.isoformat(), "globalGroup": "exchangeNotice",
             "globalName": "NordicAllMarkets", "displayLanguage": "en", "language": "en", "timeZone": "CET",
             "dateMask": "yyyy-MM-dd HH:mm:ss", "limit": "200", "start": str(start), "dir": "DESC"}
        data = get("https://api.news.eu.nasdaq.com/news/query.action?" + urllib.parse.urlencode(q))
        page = ((data or {}).get("results") or {}).get("item") or []
        for it in page:
            items.append({"id": "nq" + str(it.get("disclosureId")), "src": "Nasdaq Nordic", "company": it.get("company") or "",
                          "title": (it.get("headline") or "").strip(), "cat": it.get("cnsCategory") or "",
                          "time": it.get("releaseTime") or it.get("published") or "", "url": it.get("messageUrl"),
                          "att": [a.get("attachmentUrl") for a in it.get("attachment") or [] if a.get("attachmentUrl")]})
        if len(page) < 200:
            break
        start += 200
    return items


def oslo(since, today):
    data = get("https://api3.oslo.oslobors.no/v1/newsreader/list?category=&issuer=&fromDate=%s&toDate=%s&market=&messageTitle="
               % (since, today))
    out = []
    for m in ((data or {}).get("data") or {}).get("messages") or []:
        mid = m.get("messageId") or m.get("id")
        out.append({"id": "os" + str(mid), "src": "Oslo Børs", "company": m.get("issuerName") or "", "sign": m.get("issuerSign"),
                    "title": (m.get("title") or "").strip(), "cat": ", ".join(c.get("category_en", "") for c in m.get("category") or []).title(),
                    "time": (m.get("publishedTime") or "").replace("T", " ")[:19], "url": f"https://newsweb.oslobors.no/message/{mid}",
                    "api": f"https://api3.oslo.oslobors.no/v1/newsreader/message?messageId={mid}", "att": []})
    return out


def is_report(a):
    if norwegian(a):
        return False
    if re.search(SKIP_HEAD + r"|to (present|publish|release)|will (present|publish|release)|ceo comments|comments on", a["title"], re.I):
        return False
    return bool(re.search(REPORT_CAT, a["cat"], re.I) or re.search(REPORT_HEAD, a["title"], re.I))


def norwegian(a):
    return a["src"] == "Oslo Børs" and bool(re.search(NORWEGIAN, a["title"], re.I))


def keep_news(a):
    return not re.search(SKIP_CAT, a["cat"], re.I) and not re.search(SKIP_HEAD + r"|flagg|shareholding disclosure|primary insider|mandatory notification", a["title"], re.I) \
        and not norwegian(a)


def read_report(a):
    """Text of a report announcement: the release itself, else its first readable attachment."""
    best = ""
    if a.get("api"):   # Oslo: the message API carries the release text
        d = get(a["api"])
        body = ((d or {}).get("data") or {}).get("message") or {}
        best = text_of((body.get("body") or "").encode(), "text/html")
        for att in body.get("attachments") or []:
            if len(best) < 3000 and att.get("id"):
                r = get(f"https://api3.oslo.oslobors.no/v1/newsreader/attachment?messageId={a['id'][2:]}&attachmentId={att['id']}", as_json=False)
                if r:
                    t = text_of(*r)
                    best = t if len(t) > len(best) else best
        return best
    r = get(a["url"], as_json=False) if a.get("url") else None
    if r:
        best = text_of(*r)
    for u in a["att"][:2]:
        if len(best) >= 8000:
            break
        r = get(u, as_json=False)
        if r:
            t = text_of(*r)
            best = t if len(t) > len(best) else best
    return best


def main():
    comp = json.loads((ROOT / "js/companies.json").read_text())
    rows = [dict(zip(comp["fields"], r)) for r in comp["rows"]]
    by_name, by_sign = {}, {}
    for r in rows:
        k = norm(r["name"])
        if len(k) >= 3:
            by_name.setdefault(k, (r["lei"], words(r["name"])))
        if r.get("country") == "NO" and r.get("ticker"):
            by_sign[r["ticker"].upper()] = r["lei"]
    find, fuzzy = matcher(by_name)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("_v") != CACHE_VERSION:
        cache = {"_v": CACHE_VERSION, "ann": {}}
    today = date.today()
    since = today - timedelta(days=DAYS)
    fresh = nasdaq(since, today) + oslo(since, today)
    print(f"{len(fresh)} announcements since {since}", flush=True)
    unmatched = {}
    for a in fresh:
        lei = by_sign.get((a.get("sign") or "").upper()) or find(a["company"])
        if not lei:
            unmatched[a["company"]] = unmatched.get(a["company"], 0) + 1
            continue
        if a["id"] in cache["ann"] or not (is_report(a) or keep_news(a)):
            continue
        a["lei"] = lei
        cache["ann"][a["id"]] = a
    print(f"matched {len({a.get('lei') for a in cache['ann'].values()})} companies; unmatched names (top): "
          f"{sorted(unmatched.items(), key=lambda x: -x[1])[:25]}", flush=True)
    names = {r["lei"]: r["name"] for r in rows}
    print("filler-word name matches:", sorted((k, names[v]) for k, v in fuzzy.items()), flush=True)
    # Reports: read the newest per company once.
    newest = {}
    for a in cache["ann"].values():
        if is_report(a) and a["time"] > newest.get(a["lei"], {}).get("time", ""):
            newest[a["lei"]] = a
    read = 0
    for lei, a in sorted(newest.items(), key=lambda x: x[1]["time"], reverse=True):
        if "results" in a:
            continue
        text = read_report(a)
        year = int(a["time"][:4]) if a["time"][:4].isdigit() else today.year
        a["chars"] = len(text)
        a["results"], a["outlook"] = quotes(text, year) if text else ([], [])
        a["quarter"] = report_quarter(a["title"], text, a["time"])
        read += 1
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    cache["ann"] = {k: v for k, v in cache["ann"].items() if v["time"][:10] >= cutoff}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, separators=(",", ":"), ensure_ascii=False))
    out = {}
    for a in sorted(cache["ann"].values(), key=lambda a: a["time"], reverse=True):
        o = out.setdefault(a["lei"], {"news": []})
        if len(o["news"]) < MAX_NEWS:
            o["news"].append({"date": a["time"][:16], "title": a["title"], "cat": a["cat"], "url": a["url"], "src": a["src"]})
        if is_report(a) and "report" not in o and "results" in a:
            o["report"] = {"date": a["time"][:16], "title": a["title"], "url": a["url"], "src": a["src"],
                           "results": a["results"], "outlook": a["outlook"],
                           "quarter": a.get("quarter")}
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"news for {len(out)} companies, {sum('report' in v for v in out.values())} with a report; read {read} reports this run", flush=True)
    names = {r["lei"]: r["name"] for r in rows}
    size = {r["lei"]: r.get("revenueEur") or 0 for r in rows}
    for lei in sorted((l for l in out if "report" in out[l]), key=lambda l: -size.get(l, 0))[:30]:   # samples to check by eye
        rep = out[lei]["report"]
        print(f"\n== {names.get(lei)} | {rep['date']} | {rep['title']} | chars {cache['ann'].get(next((k for k,v in cache['ann'].items() if v['url']==rep['url']), ''), {}).get('chars')}")
        print("  quarter", rep.get("quarter"))
        for s in rep["results"]:
            print(f"  [result {s['period']}]", s["text"])
        for i in rep["outlook"]:
            print(f"  [{i['kind']}]", i["text"])


if __name__ == "__main__":
    main()
