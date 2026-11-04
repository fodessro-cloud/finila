"""Test (GitHub Actions only), round 5: the search call inside the UK FCA NSM module."""
import json, re, urllib.request

UA = {"User-Agent": "FINILA data refresh (finila.com)", "Accept": "*/*"}


def get(url, data=None, ctype=None):
    h = dict(UA)
    if ctype:
        h["Content-Type"] = ctype
    try:
        req = urllib.request.Request(url, data=data, headers=h)
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read()
    except Exception as e:
        return "ERR " + str(e), getattr(e, "read", lambda: b"")()


st, js = get("https://data.fca.org.uk/chunk-OAPZGRPP.js")
t = js.decode("utf-8", "replace")
print("nsm chunk", st, len(t))
print("imports", sorted(set(re.findall(r'chunk-[A-Z0-9]+\.js', t))))
for word in ("index", "search", "http", "environment", "apiUrl", "baseUrl"):
    for m in list(re.finditer(word, t))[:5]:
        print(f"--- {word} @ {m.start()}:", t[max(0, m.start() - 250):m.start() + 250].replace("\n", " "))
st, main = get("https://data.fca.org.uk/main-AQSD5GC2.js")
mt = main.decode("utf-8", "replace")
for word in ("fca.org.uk", "apiUrl", "baseUrl", "searchUrl", "environment"):
    for m in list(re.finditer(re.escape(word), mt))[:6]:
        print(f"=== main {word} @ {m.start()}:", mt[max(0, m.start() - 200):m.start() + 200].replace("\n", " "))
