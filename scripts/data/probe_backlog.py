"""Test (GitHub Actions only): can order backlog, outlook and mid-term targets be read from ESEF reports?
Prints the tagged facts and the report sentences that mention them, for a few companies."""
import html, io, re, sys, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import fundamentals as fd   # noqa: E402

LEIS = {"MINO79WLOO247M1IL051": "Airbus", "549300DYMC8BGZZC8844": "Vestas", "5967007LIEEXZXJ9HK73": "Kongsberg Gruppen",
        "529900FNDVTQJOVVPZ19": "Thales", "724500Y6DUVHQD6OXN27": "ASML", "969500A1YF1XUYYXS284": "Schneider Electric",
        "549300DAQ1CVT6CXN342": "Novo Nordisk"}
CONCEPT = re.compile(r"RemainingPerformanceObligation|Backlog|OrderIntake|OrderBook|OrdersReceived|Orderbook", re.I)
TEXT = re.compile(r"[^.]{0,200}\b(order backlog|backlog|order intake|order book|orderbook|book-to-bill|outlook for 20\d\d|guidance|"
                  r"we expect|expected to|mid-term|medium-term|financial targets?|ambition|by 20(2[7-9]|3\d))\b[^.]{0,250}\.", re.I)

for lei, name in LEIS.items():
    print(f"\n===== {name} ({lei})")
    fs = fd.latest_annual(lei) or []
    if not fs:
        print("no filings"); continue
    f = fs[0]
    print("period", f["period_end"], "keys", sorted(f.keys()))
    xj = fd.get(fd.BASE + f["json_url"]) or {}
    seen = {}
    for fact in xj.get("facts", {}).values():
        c = fact.get("dimensions", {}).get("concept", "")
        if CONCEPT.search(c):
            seen.setdefault(c, []).append((fact.get("dimensions", {}).get("period"), str(fact.get("value"))[:80]))
    for c, v in seen.items():
        print("  FACT", c, v[:4])
    if not seen:
        print("  no backlog facts")
    url = f.get("package_url") or f.get("report_url")
    if not url:
        continue
    raw = fd.get(fd.BASE + url, as_json=False)
    if not raw:
        print("  report not downloaded", url); continue
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = [n for n in z.namelist() if n.endswith((".xhtml", ".html", ".htm"))]
        raw = b"".join(z.read(n) for n in names)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "replace")))
    text = re.sub(r"\s+", " ", text)
    print("  report text", len(text), "chars")
    hits = [m.group(0).strip() for m in TEXT.finditer(text)]
    by = {}
    for h in hits:
        k = TEXT.search(h).group(1).lower()
        by.setdefault(k, [])
        if len(by[k]) < 3:
            by[k].append(h)
    for k, v in by.items():
        for h in v:
            print(f"  [{k}] {h[:420]}")
