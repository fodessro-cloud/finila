"""Test (GitHub Actions only): how many shares from outside Europe trade in EUR on Xetra and Börse Frankfurt?
Counts distinct ISINs in the latest daily post-trade file of each venue, by ISIN country."""
import gzip, json, sys, tempfile
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from prices import fetch, list_files, VENUES, FILE_URL   # noqa: E402

EUROPE = set("AT BE BG CH CY CZ DE DK EE ES FI FR GB GG GR HR HU IE IM IS IT JE LI LT LU LV MT NL NO PL PT RO SE SI SK FO GI".split())
for venue, label in VENUES:
    daily, _ = list_files(venue)
    name = daily[-1]
    with tempfile.NamedTemporaryFile(suffix=".json.gz") as tmp:
        tmp.write(fetch(FILE_URL + name)); tmp.flush()
        isins, keys, cfi = {}, None, Counter()
        with gzip.open(tmp.name, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                t = json.loads(line)
                keys = keys or sorted(t)
                if t.get("priceCurrency") != "EUR":
                    continue
                isin = t.get("instrumentIdentificationCode") or ""
                kind = t.get("instrumentClassification") or t.get("cfiCode") or t.get("instrumentType") or ""
                isins[isin] = kind
    print(f"\n===== {label} {name}")
    print("fields:", keys)
    kinds = Counter((v or "?")[:1] for v in isins.values())
    print("all EUR instruments:", len(isins), "by first CFI letter:", kinds.most_common(8))
    shares = {i: k for i, k in isins.items() if (k or "E").startswith("E")}
    by = Counter(i[:2] for i in shares)
    non = {c: n for c, n in by.items() if c not in EUROPE}
    print("shares (CFI E or unknown):", len(shares), "European:", sum(n for c, n in by.items() if c in EUROPE),
          "non-European:", sum(non.values()))
    print("non-European by ISIN country:", sorted(non.items(), key=lambda x: -x[1])[:25])
