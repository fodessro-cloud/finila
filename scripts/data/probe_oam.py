"""Test (GitHub Actions only), round 3: find the search API the UK FCA National Storage Mechanism page uses,
and try German announcement feeds."""
import re, urllib.request

UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "*/*"}


def get(url, data=None, ctype=None):
    h = dict(UA)
    if ctype:
        h["Content-Type"] = ctype
    try:
        req = urllib.request.Request(url, data=data, headers=h)
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except Exception as e:
        return "ERR " + str(e), None, getattr(e, "read", lambda: b"")()


for page in ("https://data.fca.org.uk/", "https://data.fca.org.uk/#/nsm/nationalstoragemechanism"):
    st, ct, body = get(page)
    print("==", page, st, ct, len(body))
    text = body.decode("utf-8", "replace")
    scripts = re.findall(r'src="([^"]+\.js)"', text)
    print("scripts", scripts[:20])
    for src in scripts[:20]:
        url = src if src.startswith("http") else "https://data.fca.org.uk/" + src.lstrip("/")
        st, ct, js = get(url)
        t = js.decode("utf-8", "replace")
        hits = set(re.findall(r'index=[\w-]+|https://api\.data\.fca\.org\.uk[^"\'` ]*|fca-nsm[\w-]*', t))
        if hits:
            print("  ", url, st, len(js), sorted(hits)[:30])
            for m in re.finditer(r'nsm', t):
                pass
            i = t.find("fca-nsm")
            print("   context:", t[max(0, i - 600):i + 600].replace("\n", " ") if i >= 0 else "")

for url in ("https://www.dgap.de/dgap/Rss/?newsType=ADHOC", "https://www.eqs-news.com/news/feed/?lang=en",
            "https://www.eqs-news.com/?feed=rss2", "https://www.unternehmensregister.de/ureg/"):
    st, ct, body = get(url)
    print("==", url, st, ct, len(body), body[:300])
