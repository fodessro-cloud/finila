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
CACHE_VERSION = 9
DAYS = int(os.environ.get("FINILA_NEWS_DAYS", "14"))   # how far back each run looks
KEEP_DAYS = 120                                         # how long an announcement stays on the page data
MAX_NEWS = 8
UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "application/json, text/html, */*"}

REPORT_HEAD = (r"interim report|quarter(ly)? report|\bq[1-4]\b.{0,12}\b(report|results|interim|revenue|sales|turnover)|"
               r"(first|second|third|fourth)[- ]quarter.{0,20}(report|results)|results for the (first|second|third|fourth) quarter|"
               r"half[- ]year(ly)? (report|results|financial report)|year[- ]end report|full[- ]year results|annual results|"
               r"nine[- ]month|six[- ]month|january\s*[-–]\s*(march|june|september|december)|trading update|"
               r"interim management statement|business review|"
               r"chiffre d.affaires (du|au) (premier|deuxi[eè]me|troisi[eè]me|quatri[eè]me|1er|[1-4]e|[1-4][eè]me) trimestre|"
               r"r[ée]sultats (annuels|semestriels|du (premier|deuxi[eè]me|troisi[eè]me|quatri[eè]me|1er|[1-4]e) trimestre)|"
               r"information financi[eè]re trimestrielle|rapport financier (semestriel|annuel)|"
               r"\b(q[1-4]|h[12]|9m|[1-4]q)\s?20\d\d\b|\b(first|second|third|fourth)[- ]quarter\b|fiscal 20\d\d results|sales at 3\d (march|june|september|december)|"
               r"resultados (del|correspondientes al) (primer|segundo|tercer|cuarto|[1-4]t)|(primer|segundo|tercer|cuarto) trimestre|primer semestre|\b[1-4]t ?20\d\d\b|"
               r"(order intake|sales|revenue) as of (march|june|september|december) 3\d|activity indicators")
REPORT_CAT = r"interim report|half[- ]year|annual financial|year[- ]end|quarterly financial information|financial report"
SKIP_HEAD = (r"invitation|presentation of|webcast|conference call|audiocast|teleconference|capital markets day|"
             r"contratos? de liquidez|acciones propias|operaciones (realizadas )?(al amparo|sobre acciones propias)|autocartera|recompra de acciones|"
             r"franchissement de seuils?|d[ée]claration des transactions|nombre total de droits de vote|contrat de liquidit[ée]|"
             r"buy-?back|own shares|treasury shares|managers'? transactions|transactions? (by|of) (persons|insiders)|"
             r"voting rights|share capital|total number of shares|listing of|admission to trading|prospectus|"
             r"notice (to|of) (the )?(annual|extraordinary) general meeting|bond|notes? due|coupon|nomination committee")
SKIP_CAT = (r"managers.? transaction|own shares|liquidity contract|terms of availability|total number of shares|description of the share buyback|voting rights|major shareholder|articles of association|prospectus|corporate action|"
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


LEGAL = (r"\(publ\)|\bp\.?l\.?c\.?(?!\w)|\ba/s\b|\bab\b|\basa\b|\boyj\b|\babp\b|\bhf\b|\bp/f\b|\bse\b|\bn\.?v\.?(?!\w)|"
         r"\bs\.?a\.?(?!\w)|\bs\.?p\.?a\.?(?!\w)|\bag\b|\bltd\b|\blimited\b|\bcorporation\b|\bcorp\b|\bgroup\b|\bholding\b|\binc\b|"
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


BROKEN = r"[a-z] -[a-z]|[a-z]- [a-z]|[a-z] - [a-z]|(?<![\w'’.])[b-hj-z] [a-z]{2,}"
JUNK = (r"please call|investor relations|\+\d\d \d|visit (www|home)\.|press release|interim report|quarterly report|"
        r"half[- ]year report|year[- ]end report|figures in parentheses|previously|estimated to|webcast|conference call")


def split_release(text):
    """Releases list key figures as bullets glued together by PDF extraction: cut at dashes used as bullets,
    at opening quotes and after a prior-year figure in brackets followed by a new sentence."""
    months = r"(?i:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    text = re.sub(fr"\b({months})((?:\s\d{{1,2}})?)\s?[–-]\s?(?=(?:\d{{1,2}}\s)?{months}\b)", r"\1\2–", text)   # "January 1 - September 30" is a period, not a bullet
    text = re.sub(r"\b([1-4])\s(st|nd|rd|th)\s(?=quarter|half)", r"\1\2 ", text)   # "3 rd quarter" from superscripts
    text = re.sub(r"\s[o•]\s(?=[A-Z])", ". ", text)                               # "o" used as a bullet
    t = re.sub(r"\s[–•▪■]\s|\s-\s(?=[A-Z])", ". ", text)
    t = re.sub(r"\s[“\"]\s?(?=[A-Z])", ". ", t)
    t = re.sub(r"(\(\s?-?[\d.,\s]+\)|\d%\)|\d\))\s+(?=[A-Z][a-z])", r"\1. ", t)
    return t


YTD = (r"nine months|nine-month|\b9m\b|first nine|first half|six months|six-month|\bh1\b|half[- ]year|full[- ]year|year to date|\bytd\b|"
       r"january\s*[-–]\s*(june|september|december)|jan\s*[-–]\s*(jun|sep|dec)|\b1\.1\.?\s*[-–]\s*3\d\.\d|\b1\s*[-–]\s*(6|9|12)/20|for the year 20\d\d")
QUARTER = (r"\bq[1-4]\b|(first|second|third|fourth|1st|2nd|3rd|4th) quarter|the quarter|quarterly|"
           r"(january|april|july|october)\s*[-–]\s*(march|june|september|december)|\b(4\s*[-–]\s*6|7\s*[-–]\s*9|10\s*[-–]\s*12)/20")


def period_of(s):
    """'q' when a sentence speaks of the quarter, 'ytd' of the year so far, None when it does not say."""
    low = s.lower()
    q, y = re.search(QUARTER, low), re.search(YTD, low)
    if q and y:
        return "q" if q.start() < y.start() else "ytd"
    return "q" if q else "ytd" if y else None


QUARTER_CUES = [
    (r"\bq([1-4])\b", None), (r"\b(first)[- ]quarter", 1), (r"\b(second)[- ]quarter", 2), (r"\b(third)[- ]quarter", 3), (r"\b(fourth)[- ]quarter", 4),
    (r"january\s*[-–]\s*march|jan\.?\s*[-–]\s*mar|three months", 1),
    (r"april\s*[-–]\s*june|january\s*[-–]\s*june|jan\.?\s*1?\s*(to|[-–])\s*jun|half[- ]year|first half|six months|\bh1\b|30 june|june 30", 2),
    (r"july\s*[-–]\s*september|january\s*[-–]\s*september|jan\.?\s*1?\s*(to|[-–])\s*sep|nine months|nine-month|30 september|september 30|30\.9\.", 3),
    (r"primer trimestre|\b1t\b", 1), (r"segundo trimestre|primer semestre|\b2t\b", 2), (r"tercer trimestre|\b3t\b|nueve meses", 3), (r"cuarto trimestre|\b4t\b", 4),
    (r"premier trimestre|1er trimestre", 1), (r"deuxi[eè]me trimestre|2e trimestre|semestriel|premier semestre", 2),
    (r"troisi[eè]me trimestre|3e trimestre|neuf (premiers )?mois", 3), (r"quatri[eè]me trimestre|4e trimestre|r[ée]sultats annuels|rapport financier annuel", 4),
    (r"october\s*[-–]\s*december|january\s*[-–]\s*december|year[- ]end report|full[- ]year (report|results)|annual (report|results)", 4)]


def report_quarter(title, text, published):
    """Which quarter a report is for: the latest quarter its title names ("Q1-Q3", "1 January - 30 September"
    are Q3), else its first lines, else its publication date."""
    m = int(published[5:7]) if published[5:7].isdigit() else 0
    by_date = {1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 7: 2, 8: 2, 10: 3, 11: 3}.get(m)
    for i, t in enumerate((title, text[:600])):
        low = (t or "").lower()
        found = []
        for rx, n in QUARTER_CUES:
            for m in re.finditer(rx, low):
                found.append(int(m.group(1)) if n is None else n)
        if found:
            # The first lines also name comparison periods ("three months"), so there the date decides
            # unless the text names the quarter the date points to.
            return max(found) if i == 0 or not by_date or by_date in found else by_date
    return by_date


def quotes(text, year):
    """Results for the period and the company's own outlook, word for word. Each result carries the period
    it speaks of in its own words ("in Q3", "in the first nine months"), else none."""
    head = split_release(text[:15000])   # the summary at the top: highlights, CEO comment, outlook
    results, seen = [], set()
    for s in ol.sentences(head):
        own = period_of(s)
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
            if re.search(r"(?<![\d,.])\d \d+[.,]\d+\s?%|\b(would|if)\b", s):
                continue   # a number split by the PDF ("8 4.7%" for 84.7%) or a what-if, not a result
            s = re.sub(r"^(?:[A-Z0-9&,'’/\- ]{3,}\s){2,}(?=[A-Z][a-z])", "", s)   # headings in capitals glued in front
            if re.search(r"[a-z] -[a-z]|[a-z]- [a-z]|[a-z] - [a-z]|(?<![\w'’.])[b-hj-z] [a-z]{2,}", s):
                continue   # a word the PDF broke across lines ("contrib - uted", "l ike-for-like"): not quoted as it reads
            s = re.sub(r"[,\s]*[\"”]?,?[\"”]?\s+said\s+[A-Z][\w.\- ]+,.*$", ".", s)   # '..., said Jane Doe, CEO' after a quote
            s = re.sub(r"\s+(Comments? from|Quote from|CEO comment|CEO's comment)\b.*$", "", s)
            s = re.sub(r"^Page \d+\s+", "", s)
            s = re.sub(r"^(?:[A-Z][a-z]+ \d{1,2}|[A-Z][a-z]+ 20\d\d)\s+(?=[A-Z])", "", s)   # a date heading left in front
            head_glue = re.match(r"^([A-Z][^.:;]{0,70}?\b20\d\d)\s+(?=[A-Z])", s)        # "Revenue for the first nine months of 2026 At constant..."
            if head_glue and not re.search(fr"\b({RESULT_VERB})\b", head_glue.group(1).lower()):
                s = s[head_glue.end():]
            if re.match(r"(In|For|During) the (comparison|corresponding) period", s):
                continue   # last year's figures
            if re.search(r"\bsaid\b|\bsays\b|\(\s*\)|ticker:|\(was\b", s, re.I) or not s[:1].isupper():
                continue   # quotes, empty footnote marks, ticker lines and guidance changes ("was around ...") are not results
            key = re.sub(r"\W+", "", low)[:100]
            if key not in seen:
                seen.add(key)
                # A heading the PDF glued to the sentence ("January–September 2026 Orders received ...") is cut off.
                t = re.sub(r"^(?i:(january|april|july|october)\s*[-–]\s*(march|june|september|december)|"
                           r"(first|second|third|fourth) quarter|summary of [\w-]+ results)( 20\d\d)?:?\s+(?=[A-Z])", "", s)
                if re.match(r"(January|February|March|April|May|June|July|August|September|October|November|December)\b", t):
                    continue   # the start of the sentence was lost in the PDF ("September 2026, Group revenue ...")
                # Only the sentence's own words give its period: a heading above it can be lost in the PDF,
                # and a wrong "quarter" label is worse than none.
                results.append({"text": re.sub(r"\.\.$", ".", t), "period": own})
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


def france(since, today):
    """AMF regulated information (info-financiere.gouv.fr, open data): every filing carries the issuer's ISIN.
    French and English versions of the same release are both filed: the English one is kept when there is one."""
    base = "https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records?"
    rows, offset = [], 0
    while offset < 9900:
        q = {"limit": 100, "offset": offset, "order_by": "uin_dat_amf desc",
             "where": f"uin_dat_amf >= date'{since}' and identificationsociete_iso_cd_isi != '999999999999'"}
        data = get(base + urllib.parse.urlencode(q))
        page = (data or {}).get("results") or []
        rows += page
        if len(page) < 100:
            break
        offset += 100
    out, english = [], {(r.get("identificationsociete_iso_cd_isi"), (r.get("uin_dat_amf") or "")[:10])
                        for r in rows if r.get("informationdeposee_inf_lng_inf") == "Anglais"}
    for r in rows:
        isin, day = r.get("identificationsociete_iso_cd_isi"), (r.get("uin_dat_amf") or "")[:10]
        if r.get("informationdeposee_inf_lng_inf") != "Anglais" and (isin, day) in english:
            continue
        cat = " / ".join(x for x in (r.get("type_of_information"), r.get("subtype_of_information")) if x)
        out.append({"id": "fr" + str(r.get("uin_idt_uin")), "src": "AMF France", "company": r.get("identificationsociete_iso_nom_soc") or "",
                    "isin": isin, "title": (r.get("informationdeposee_inf_tit_inf") or "").strip(), "cat": cat,
                    "time": (r.get("uin_dat_amf") or "").replace("T", " ")[:19], "url": r.get("url_de_recuperation"), "att": [],
                    "lang": "fr" if r.get("informationdeposee_inf_lng_inf") == "Français" else "en"})
    print(f"AMF France: {len(rows)} filings, {len(out)} kept", flush=True)
    return out


def spain(since, today):
    """CNMV 'other relevant information' feed (in Spanish): the day's notices since the last session; the cache
    keeps earlier days because the job runs several times a day."""
    from email.utils import parsedate_to_datetime
    try:
        req = urllib.request.Request("https://www.cnmv.es/portal/Otra-Informacion-Relevante/RSS.asmx/GetNoticiasCNMV", headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            xml = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! CNMV: {e}", flush=True)
        return []
    out = []
    for item in re.findall(r"(?is)<item>(.*?)</item>", xml):
        tag = lambda t: html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(fr"(?is)<{t}>(.*?)</{t}>", item) or [None, ""])[1])).strip()
        link, desc = tag("link"), tag("description")
        parts = [re.sub(r"<[^>]+>", " ", x).strip() for x in re.split(r"(?i)<br\s*/?>\s*<br\s*/?>", desc)]
        cat = re.sub(r"^\d\d:\d\d\s+\d\d/\d\d/\d{4}\s*", "", parts[0]).strip() if parts else ""
        try:
            t = parsedate_to_datetime(tag("pubDate")).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            t = today.isoformat() + " 00:00:00"
        out.append({"id": "es" + (re.search(r"nreg=(\d+)", link) or [None, link])[1], "src": "CNMV Spain", "company": tag("Title"),
                    "title": " ".join(parts[1:]).strip() or cat, "cat": cat, "time": t, "url": link, "att": [], "lang": "es"})
    print(f"CNMV Spain: {len(out)} notices", flush=True)
    return out


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


# ---- Report sections by their headings (Lub, 2026-11-05): the company's own "highlights", "events" and
# "outlook" paragraphs, taken whole, instead of single sentences picked from the text.
BLOCK = re.compile(r"(?i)<(/?)(h[1-6]|p|li|div|tr|td|ul|ol|table|br)\b[^>]*>")
H_EVENTS = (r"(significant|important|key|material|other) (business )?events|events (during|in|after)|"
            r"(business|operational|operating|commercial|strategic) (highlights|progress|update|developments?|review)|business review")
H_OUTLOOK = r"\b(outlook|guidance|prospects|financial targets|forecast for)\b"
H_HIGHLIGHTS = (r"highlights|key (figures|operating results|ratios|financials)|in brief|summary of (financial )?(performance|results)|"
                r"^(the )?(first|second|third|fourth) quarter|^q[1-4]\b|^(july|april|january|october)( 1)?\s*[-–]\s*(september|june|march|december)|"
                r"^reporting period|^\d{1,2}\s*[–-]\s*\d{1,2}/ ?20\d\d|^nine months|^(first|1st) (half|nine months)|group performance in|^1 january")
QUARTER_ONLY = r"(first|second|third|fourth) quarter|\bq[1-4]\b|^(july|april|october)\b|^reporting period (july|april|october)|^\d{1,2}\s*[–-]\s*\d{1,2}/|^1 january\s*[-–]\s*31 march|^january\s*[-–]\s*march"
COMMERCIAL = (r"\b(contract|order|orders|agreement|customer|client|acqui|merger|divest|launch|launched|introduc|approv|licen|partnership|"
              r"collaborat|new market|expan|opened|opening|signed|awarded|won|wins|tender|deliver|capacity|plant|factory|store|"
              r"product|platform|patent|trial|study|portfolio)")
NOT_COMMERCIAL = (r"\b(appoint|appointed|ceo|cfo|chief|board|annual general meeting|agm|egm|nomination|share buyback|repurchase|"
                  r"own shares|dividend|interim report|webcast|resigned|step down|succeed|named winner|award(s|ed)? for|ranked)\b")


SECTIONS_VERSION = 3
OUTLOOK_JUNK = r"this (release|report) is a summary|is attached|available (at|on)|www\.|please refer|refer to|defined in|^\d+ "
OUTLOOK_WORD = r"\b(expect|estimat|anticipat|forecast|guidance|outlook|target|project|will|aim|believ|plan|foresee|assum|predict)"


def html_blocks(raw):
    """Report HTML -> [(is_heading, text)]. A heading is an h1-h6 or a short line that is all bold, italic or capitals."""
    src = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    src = re.sub(r"(?is)<(script|style|head|nav|footer)\b.*?</\1>", " ", src)
    out, last, tag = [], 0, ""
    for m in list(BLOCK.finditer(src)) + [None]:
        inner = src[last:m.start() if m else len(src)]
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).replace("\xa0", " ").strip()
        if text and tag != "td":
            styled = re.fullmatch(r"(?is)\s*(<(strong|b|em|i|span|u)\b[^>]*>\s*)+[^<]+(</(strong|b|em|i|span|u)>\s*)+:?\s*", inner) is not None \
                and re.search(r"(?i)<(strong|b|em|i)\b", inner) is not None
            out.append(((tag[:1] == "h" or styled or (text.isupper() and len(text) > 3)) and len(text) < 100 and not text.endswith("."), text))
        if m:
            last, tag = m.end(), ("" if m.group(1) else m.group(2).lower())
    return out


def html_sections(raw):
    """{"highlights": [...], "events": [...], "outlook": [...], "outlook_head": heading} from the report's own headings.
    Highlights prefer the section about the quarter alone over the year-to-date one."""
    found, heads, cur = {}, {}, None
    for head, text in html_blocks(raw):
        if head:
            t = text.strip(" :*").lower()
            cur = "events" if re.search(H_EVENTS, t) else "outlook" if re.search(H_OUTLOOK, t) \
                else "highlights" if re.search(H_HIGHLIGHTS, t) else None
            if cur == "highlights":
                cur = "highlights_q" if re.search(QUARTER_ONLY, t) else "highlights_ytd"
            if cur in found:
                cur = None   # the first section of each kind only
            elif cur:
                heads[cur] = text
            continue
        if cur and not re.search(r"^(for (further|more|additional) information|contact|about )", text, re.I):
            items = found.setdefault(cur, [])
            if len(items) < 10 and sum(len(x) for x in items) < 2500:
                items.append(text)
    hl = found.get("highlights_q") or found.get("highlights_ytd") or []
    return {"highlights": hl, "highlights_head": heads.get("highlights_q") or heads.get("highlights_ytd"),
            "events": found.get("events") or [], "outlook": found.get("outlook") or [], "outlook_head": heads.get("outlook")}


def first_sentences(t, limit=300):
    parts = re.split(r"(?<=[a-z0-9%\)][.])\s+(?=[A-Z])", t)
    out = parts[0]
    for p in parts[1:]:
        if len(out) + len(p) + 1 > limit:
            break
        out += " " + p
    return out if len(out) <= limit + 120 else ""


def pick_sections(sec, year):
    """The few lines shown: 3 quarter highlights with a figure, 2 business events, the outlook paragraph and
    whether the company raised, lowered or confirmed it."""
    hl = []
    for x in sec["highlights"]:
        t = first_sentences(x)
        if t and re.search(ol.NUMBER, t) and re.search(fr"\b({RESULT_LINE})", t.lower()) and not re.search(BROKEN, t):
            hl.append(t)
    ev = []
    for x in sec["events"]:
        t = first_sentences(x, 280)
        low = t.lower()
        if t and re.search(COMMERCIAL, low) and not re.search(NOT_COMMERCIAL, low) and not re.search(BROKEN, t):
            ev.append(t)
    ev.sort(key=lambda t: -bool(re.search(ol.NUMBER, t)))   # a contract value or volume first
    out_txt = ""
    for x in sec["outlook"]:
        t = first_sentences(x, 420)
        if t and len(t) > 40 and not re.search(BROKEN, t) and not re.search(OUTLOOK_JUNK, t, re.I) \
                and re.search(OUTLOOK_WORD, t, re.I):
            out_txt = t
            break
    return {"highlights": hl[:3], "events": ev[:2], "outlook": out_txt,
            "change": outlook_change(" ".join([sec.get("title") or "", sec.get("outlook_head") or ""] + sec["outlook"][:3] + sec["highlights"]))}


def outlook_change(t):
    """'lowered' / 'raised' / 'confirmed' / 'updated' only when the company says so about its outlook, guidance or
    expectations (in the report title, the outlook heading or its text)."""
    # Sentence by sentence, leaving out sentences about a customer's or the market's forecasts.
    low = " . ".join(x for x in re.split(r"(?<=[.!?;])\s+", t.lower())
                     if not re.search(r"\b(customers?|clients?|consensus|analysts?|market forecasts?|imf|central bank)\b", x))
    obj = r"(outlook|guidance|forecast|expectations?|targets?|estimate|ambition)"
    near = lambda verbs: re.search(fr"\b(?:re)?{verbs}\w*\W+(?:\w+\W+){{0,5}}{obj}|\b{obj}\W+(?:\w+\W+){{0,6}}{verbs}", low) or \
        re.search(fr"\({verbs}\w*[,)]", low)
    if near(r"(lowered|lowers|reduced|reduces|downgrad|cuts? (its|our|the)|revised down|narrowed down|narrow\w* (towards|to) the lower)") \
            or re.search(r"\(lower(ed)?[,)]|towards (the )?lower (end|half)", low):
        return "lowered"
    if near(r"(raised|raises|raising|upgrad|revised up|increas(ed|es|ing) (its|our|the))") \
            or re.search(r"\(rais(ed)?[,)]|towards (the )?upper (end|half)|(improved|higher|upgraded) (outlook|guidance)", low):
        return "raised"
    if near(r"(unchanged|confirm|affirm|reaffirm|reiterat|maintain|remains? unchanged)") or \
            re.search(r"in line with (the )?previous(ly)? (communicat|guidance|outlook|announced)|as previously (communicated|announced)|\(unchanged[,)]", low):
        return "confirmed"
    if near(r"(updated|updates|narrow|specified|specifies|revised|revises)"):
        return "updated"
    return None


def report_change(a):
    """The title says it best; else a raise or cut found in the outlook section or the outlook quotes; else confirmed / updated."""
    found = [outlook_change(a["title"]), (a.get("sections") or {}).get("change"),
             outlook_change(" ".join(o["text"] for o in a.get("outlook") or []))]
    if found[0]:
        return found[0]
    strong = {c for c in found[1:] if c in ("raised", "lowered")}
    if len(strong) == 1:
        return strong.pop()
    if strong:
        return None   # both said somewhere (e.g. one line raised, another lowered): no chip
    return found[1] or found[2]


def read_report(a):
    """Text of a report announcement (the release itself, else its first readable attachment) and the
    sections under its own headings when the release is an HTML page."""
    best, a["_html"] = "", None
    if a.get("api"):   # Oslo: the message API carries the release text
        d = get(a["api"])
        body = ((d or {}).get("data") or {}).get("message") or {}
        a["_html"] = (body.get("body") or "").encode()
        best = text_of(a["_html"], "text/html")
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
        if not (b"%PDF" in r[0][:1024] or "pdf" in (r[1] or "").lower()):
            a["_html"] = r[0]
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
    by_isin = {r["isin"]: r["lei"] for r in rows if r.get("isin")}
    fresh = nasdaq(since, today) + oslo(since, today) + france(since, today) + spain(since, today)
    print(f"{len(fresh)} announcements since {since}", flush=True)
    unmatched = {}
    for a in fresh:
        lei = by_isin.get(a.get("isin") or "") or by_sign.get((a.get("sign") or "").upper()) or (None if a.get("isin") else find(a["company"]))
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
        if "results" in a and a.get("sv") == SECTIONS_VERSION:
            continue   # read already (read once more when the section rules change)
        text = read_report(a)
        year = int(a["time"][:4]) if a["time"][:4].isdigit() else today.year
        a["chars"] = len(text)
        a["results"], a["outlook"] = quotes(text, year) if text else ([], [])
        a["quarter"] = report_quarter(a["title"], text, a["time"])
        raw = a.pop("_html", None)
        try:
            a["sections"] = pick_sections({**html_sections(raw), "title": a["title"]}, year) if raw else None
        except Exception as e:   # a strange page must not stop the run
            print(f"  ! sections {a['id']}: {e}", flush=True)
            a["sections"] = None
        a["sv"] = SECTIONS_VERSION
        read += 1
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    cache["ann"] = {k: v for k, v in cache["ann"].items() if v["time"][:10] >= cutoff}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, separators=(",", ":"), ensure_ascii=False))
    out = {}
    for a in sorted(cache["ann"].values(), key=lambda a: a["time"], reverse=True):
        if not (is_report(a) or keep_news(a)):   # rules tightened after the announcement was cached
            continue
        o = out.setdefault(a["lei"], {"news": []})
        if len(o["news"]) < MAX_NEWS:
            o["news"].append({"date": a["time"][:16], "title": a["title"], "cat": a["cat"], "url": a["url"], "src": a["src"],
                              **({"lang": a["lang"]} if a.get("lang") in ("fr", "es") else {})})
        if is_report(a) and "results" in a:
            a["quarter"] = report_quarter(a["title"], "", a["time"]) if report_quarter(a["title"], "", "") else a.get("quarter")
        if is_report(a) and "report" not in o and "results" in a:
            o["report"] = {"date": a["time"][:16], "title": a["title"], "url": a["url"], "src": a["src"],
                           "results": a["results"], "outlook": a["outlook"],
                           "quarter": a.get("quarter"), "sections": a.get("sections"),
                           "change": report_change(a)}
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
        sec = rep.get("sections") or {}
        for k in ("highlights", "events"):
            for t in sec.get(k) or []:
                print(f"  <{k}>", t)
        if sec.get("outlook"):
            print(f"  <outlook {sec.get('change')}>", sec["outlook"])


if __name__ == "__main__":
    main()
