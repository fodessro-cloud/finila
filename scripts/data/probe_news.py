"""Test (GitHub Actions only): can quarterly reports be found in the official company announcements of
Nasdaq Nordic and Oslo Børs, and can their text be read? Prints what it finds."""
import io, json, re, urllib.parse, urllib.request
from datetime import date, timedelta

UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "application/json"}


def get(url, data=None):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


today = date.today()
since = today - timedelta(days=3)
print("===== Nasdaq Nordic")
q = {"type": "json", "showAttachments": "true", "showCnsSpecific": "true", "showCompany": "true", "countResults": "false",
     "freeText": "", "company": "", "market": "", "cnscategory": "", "fromDate": since.isoformat(), "toDate": today.isoformat(),
     "globalGroup": "exchangeNotice", "globalName": "NordicAllMarkets", "displayLanguage": "en", "language": "en",
     "timeZone": "CET", "dateMask": "yyyy-MM-dd HH:mm:ss", "limit": "200", "start": "0", "dir": "DESC"}
pdf_url = None
try:
    raw = get("https://api.news.eu.nasdaq.com/news/query.action?" + urllib.parse.urlencode(q))
    data = json.loads(raw)
    items = data.get("results", {}).get("item", [])
    print("items", len(items), "keys", sorted(items[0].keys()) if items else None)
    cats = {}
    for it in items:
        cats[it.get("cnsCategory")] = cats.get(it.get("cnsCategory"), 0) + 1
    print("categories", sorted(cats.items(), key=lambda x: -x[1])[:20])
    for it in items:
        if re.search(r"interim|quarter|half-year|q3|nine months", (it.get("cnsCategory") or "") + " " + (it.get("headline") or ""), re.I):
            att = it.get("attachment") or []
            print(" -", it.get("releaseTime"), it.get("company"), "|", it.get("headline"), "|", it.get("cnsCategory"), "|", it.get("messageUrl"),
                  "|", [a.get("attachmentUrl") for a in att][:2])
            if not pdf_url:
                pdf_url = next((a.get("attachmentUrl") for a in att if (a.get("attachmentUrl") or "").lower().endswith(".pdf")), None)
except Exception as e:
    print("nasdaq error", e)

print("\n===== Oslo Børs NewsWeb")
for url in ("https://api3.oslo.oslobors.no/v1/newsreader/list?category=&issuer=&fromDate=%s&toDate=%s&market=&messageTitle=" % (since, today),
            "https://newsweb.oslobors.no/api/messages?fromDate=%s&toDate=%s" % (since, today)):
    for method in ("GET", "POST"):
        try:
            raw = get(url, data=b"" if method == "POST" else None)
            print(method, url[:70], "->", len(raw), raw[:600])
            break
        except Exception as e:
            print(method, url[:70], "error", e)

if pdf_url:
    print("\n===== PDF", pdf_url)
    try:
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(get(pdf_url)))
        text = " ".join((p.extract_text() or "") for p in pdf.pages[:6])
        text = re.sub(r"\s+", " ", text)
        print("pages", len(pdf.pages), "chars", len(text))
        for s in re.split(r"(?<=[.!?])\s+", text):
            if re.search(r"\b(sales|revenue|net sales|operating profit|EBIT|outlook|expect)", s, re.I) and re.search(r"\d", s) and len(s) < 300:
                print("  >", s)
    except Exception as e:
        print("pdf error", e)
