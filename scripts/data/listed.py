"""Which issuers have shares listed on a European venue, from the official FIRDS
reference data of ESMA (EU/EEA) and the FCA (UK).

Streams the newest weekly full files for equities (FULINS_E), keeps shares
(CFI code ES*) that are still traded, and writes data/listed.json:
{LEI: {"isin": ..., "name": ..., "mics": [...]}}.
"""
import io, json, sys, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UA = {"User-Agent": "FINILA data refresh (finila.com)"}
TODAY = date.today().isoformat()


def get(url, as_json=True):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
        return json.load(r) if as_json else r.read()


def esma_files():
    q = urllib.parse.urlencode({"q": "*", "fq": "file_name:FULINS_E_*", "wt": "json", "rows": 50,
                                "sort": "publication_date desc"})
    docs = get("https://registers.esma.europa.eu/solr/esma_registers_firds_files/select?" + q)["response"]["docs"]
    return newest([(d["file_name"], d["download_link"]) for d in docs])


def fca_files():
    q = "((file_type:FULINS) AND (file_name:FULINS_E*))"
    data = get("https://api.data.fca.org.uk/fca_data_firds_files?" +
               urllib.parse.urlencode({"q": q, "from": 0, "size": 100, "sort": "publication_date:desc"}))
    return newest([(h["_source"]["file_name"], h["_source"]["download_link"]) for h in data["hits"]["hits"]])


def newest(files):
    """All parts of the newest FULINS_E publication, e.g. FULINS_E_20261010_01of02.zip."""
    files = [f for f in files if f[0].startswith("FULINS_E_")]
    if not files:
        return []
    day = max(f[0].split("_")[2] for f in files)
    return sorted({f for f in files if f[0].split("_")[2] == day})


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse(raw, out):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            with z.open(name) as fh:
                for _, el in ET.iterparse(fh, events=("end",)):
                    if local(el.tag) != "RefData":
                        continue
                    rec = {}
                    for child in el.iter():
                        t = local(child.tag)
                        if t in ("Id", "FullNm", "ClssfctnTp", "Issr", "TermntnDt") and child.text:
                            if t == "Id" and "isin" in rec:
                                rec.setdefault("mic", child.text)   # second Id is the trading venue
                            else:
                                rec.setdefault({"Id": "isin"}.get(t, t), child.text)
                    el.clear()
                    if not rec.get("ClssfctnTp", "").startswith("ES") or not rec.get("Issr"):
                        continue
                    if rec.get("TermntnDt", "9999")[:10] < TODAY:
                        continue
                    e = out.setdefault(rec["Issr"], {}).setdefault(rec["isin"], {"name": rec.get("FullNm"), "mics": []})
                    if rec.get("mic") and rec["mic"] not in e["mics"]:
                        e["mics"].append(rec["mic"])


def main_line(lines, shared=frozenset()):
    """An issuer can have several share ISINs (old lines, other share classes);
    the one traded on the most venues is its main share. A line FIRDS also files under
    another issuer (a parent holding listed against its subsidiary's share) comes last."""
    isin = max(lines, key=lambda i: (i not in shared, len(lines[i]["mics"]), i))
    return {"isin": isin, **lines[isin]}


def main():
    out = {}
    for label, finder in (("ESMA", esma_files), ("FCA", fca_files)):
        try:
            files = finder()
        except Exception as e:
            print(f"  ! {label} file list: {e}")
            continue
        for name, url in files:
            try:
                before = len(out)
                parse(get(url, as_json=False), out)
                print(f"{label} {name}: {len(out) - before} new issuers")
            except Exception as e:
                print(f"  ! {label} {name}: {e}")
    owners = Counter(isin for lines in out.values() for isin in lines)
    shared = frozenset(i for i, n in owners.items() if n > 1)
    out = {lei: main_line(lines, shared) for lei, lines in out.items()}
    if len(out) < 1000:
        print(f"only {len(out)} issuers found, keeping the previous data/listed.json")
        return 1 if not (ROOT / "data/listed.json").exists() else 0
    (ROOT / "data/listed.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(out)} issuers with listed shares")


if __name__ == "__main__":
    sys.exit(main())
