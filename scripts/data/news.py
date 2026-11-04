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
CACHE_VERSION = 1
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
SKIP_CAT = r"managers' transactions|own shares|voting rights|major shareholder|articles of association|prospectus|corporate action|notice to general meeting"
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


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(publ\)|\bp\.?l\.?c\.?\b|\ba/s\b|\bab\b|\basa\b|\boyj\b|\babp\b|\bhf\.?|\bp/f\b|\bse\b|\bn\.?v\.?\b|"
               r"\bs\.?a\.?\b|\bag\b|\bltd\.?\b|\blimited\b|\bcorporation\b|\bcorp\.?\b|\bgroup\b|\bholding\b|\binc\.?\b|"
               r"\bpublic limited company\b|\baktiebolag\b|\bbank\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


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


def quotes(text, year):
    """Results for the period and the company's own outlook, word for word."""
    head = text[:15000]   # the summary at the top of a release: highlights, CEO comment, outlook
    results, seen = [], set()
    for s in ol.sentences(head):
        low = s.lower()
        if re.search(ol.NOISE, low) or not ol.english(low) or sum(ch.isdigit() for ch in s) > len(s) * 0.25:
            continue
        if re.search(fr"\b({RESULT_LINE})", low) and re.search(fr"\b({RESULT_VERB})", low) and re.search(ol.NUMBER, s) \
                and not re.search(fr"\b({ol.EXPECT})", low):
            key = re.sub(r"\W+", "", low)[:100]
            if key not in seen:
                seen.add(key)
                results.append(s)
    out = [i for i in ol.pick(text[:60000], year) if i["kind"] in ("outlook", "target", "backlog")]
    return results[:5], out[:5]


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
    return bool(re.search(REPORT_CAT, a["cat"], re.I) or re.search(REPORT_HEAD, a["title"], re.I))


def keep_news(a):
    return not re.search(SKIP_CAT, a["cat"], re.I) and not re.search(SKIP_HEAD, a["title"], re.I)


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
        if len(best) >= 3000:
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
            by_name.setdefault(k, r["lei"])
        if r.get("country") == "NO" and r.get("ticker"):
            by_sign[r["ticker"].upper()] = r["lei"]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("_v") != CACHE_VERSION:
        cache = {"_v": CACHE_VERSION, "ann": {}}
    today = date.today()
    since = today - timedelta(days=DAYS)
    fresh = nasdaq(since, today) + oslo(since, today)
    print(f"{len(fresh)} announcements since {since}", flush=True)
    unmatched = {}
    for a in fresh:
        lei = by_sign.get((a.get("sign") or "").upper()) or by_name.get(norm(a["company"]))
        if not lei:
            unmatched[a["company"]] = unmatched.get(a["company"], 0) + 1
            continue
        if a["id"] in cache["ann"] or not (is_report(a) or keep_news(a)):
            continue
        a["lei"] = lei
        cache["ann"][a["id"]] = a
    print(f"matched {len({a.get('lei') for a in cache['ann'].values()})} companies; unmatched names (top): "
          f"{sorted(unmatched.items(), key=lambda x: -x[1])[:25]}", flush=True)
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
                           "results": a["results"], "outlook": a["outlook"]}
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"news for {len(out)} companies, {sum('report' in v for v in out.values())} with a report; read {read} reports this run", flush=True)
    names = {r["lei"]: r["name"] for r in rows}
    size = {r["lei"]: r.get("revenueEur") or 0 for r in rows}
    for lei in sorted((l for l in out if "report" in out[l]), key=lambda l: -size.get(l, 0))[:30]:   # samples to check by eye
        rep = out[lei]["report"]
        print(f"\n== {names.get(lei)} | {rep['date']} | {rep['title']} | chars {cache['ann'].get(next((k for k,v in cache['ann'].items() if v['url']==rep['url']), ''), {}).get('chars')}")
        for s in rep["results"]:
            print("  [result]", s)
        for i in rep["outlook"]:
            print(f"  [{i['kind']}]", i["text"])


if __name__ == "__main__":
    main()
