"""Test (GitHub Actions only), round 4: the search API inside the UK FCA data portal's script."""
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


st, js = get("https://data.fca.org.uk/main-AQSD5GC2.js")
t = js.decode("utf-8", "replace")
print("main.js", st, len(t))
for pat in (r'"[^"]*api[^"]*"', r"'[^']*api[^']*'", r"`[^`]*api[^`]*`"):
    print(pat, sorted(set(m for m in re.findall(pat, t) if len(m) < 160))[:60])
for word in ("nsm", "NSM", "search?index", "index="):
    for m in list(re.finditer(re.escape(word), t))[:4]:
        print(f"--- {word} @ {m.start()}:", t[max(0, m.start() - 300):m.start() + 300].replace("\n", " "))

for idx in ("fca-nsm-searchdata", "fca-nsm-searchdata-prod", "nsm", "fca-nsm"):
    body = {"from": 0, "size": 2, "sort": "submitted_date", "sortorder": "desc", "criteriaObj": {"criteria": [], "dateCriteria": []}}
    st, b = get(f"https://api.data.fca.org.uk/search?index={idx}", json.dumps(body).encode(), "application/json")
    print("== index", idx, st, b[:500])
