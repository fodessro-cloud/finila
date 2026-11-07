"""Annual figures from the SEC for European companies that also file annual reports in the US (Lub,
2026-11-06: fill the gaps with official sources). Linde, Accenture, Chubb, Novartis, UBS and others file
10-K or 20-F reports with XBRL facts at data.sec.gov (public data, no use restrictions). Used for companies
that have no ESEF history (Swiss, Irish-US, German dual listings), in the same shape as data/history.json.

A company is matched by its legal name on the SEC list, then checked: the SEC's newest revenue must be
within 20% of the figure FINILA already shows for that year, else the match is dropped.
Output: data/sec-history.json {lei: {"currency", "cik", "secName", "years": {year: row}}}.
Runs in GitHub Actions (the container proxy blocks data.sec.gov)."""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "data"))
import fundamentals as fd   # noqa: E402
import sec_preview as sp    # noqa: E402

OUT = ROOT / "data/sec-history.json"
YEARS = 6
FORMS = ("10-K", "20-F", "40-F")
SUFFIX = r"\b(plc|ag|sa|se|nv|n\.v\.|ltd|limited|inc|corp|corporation|group|holding|holdings|co|company|asa|ab|oyj|a/s|s\.a\.|spa|s\.p\.a\.|the)\b"


def norm(name):
    n = re.sub(r"[^a-z0-9/ .]", " ", (name or "").lower().replace("&", " and "))
    n = re.sub(SUFFIX, " ", n)
    return re.sub(r"[ .]+", " ", n).strip()


def year_values(facts, fy_end, accn, currency):
    """Like sec_preview.year_values, for US GAAP (10-K) and IFRS (20-F) facts in the report's own currency."""
    out = {}
    for tax, names in (("us-gaap", sp.GAAP), ("ifrs-full", fd.CONCEPTS)):
        concepts = facts["facts"].get(tax, {})
        if not concepts:
            continue
        for key, tags in names.items():
            if key in out:
                continue
            for t in tags:
                units = concepts.get(t, {}).get("units", {})
                vals = units.get(f"{currency}/shares" if key == "eps" else "shares" if key in ("shares", "shares_out") else currency, [])
                hits = [v for v in vals if v.get("accn") == accn and v["end"] == fy_end and (
                    key in sp.INSTANT or key in fd.INSTANT or (v.get("start") and 350 <= (date.fromisoformat(v["end"]) - date.fromisoformat(v["start"])).days <= 380))]
                if hits:
                    out[key] = hits[-1]["val"]
                    break
    return out


def main():
    comp = json.loads((ROOT / "js/companies.json").read_text())
    rows = [dict(zip(comp["fields"], r)) for r in comp["rows"]]
    hist = json.loads((ROOT / "data/history.json").read_text()) if (ROOT / "data/history.json").exists() else {}
    want = {}
    for r in rows:
        if r.get("origin") == "listing" or r.get("sec") or len(hist.get(r["lei"], {}).get("years", {})) >= 3:
            continue
        if r.get("revenue") is None and r.get("netIncome") is None:
            continue
        want.setdefault(norm(r["name"]), []).append(r)
    sec = sp.get("https://www.sec.gov/files/company_tickers_exchange.json")
    if not sec:
        sys.exit("no SEC company list")
    i_cik, i_name = sec["fields"].index("cik"), sec["fields"].index("name")
    matches = {}
    for d in sec["data"]:
        n = norm(d[i_name])
        if n in want and len(want[n]) == 1:
            matches.setdefault(d[i_cik], (want[n][0], d[i_name]))
    print(f"{len(want)} companies without ESEF history, {len(matches)} with the same name on the SEC list", flush=True)
    out, dropped = {}, []
    for cik, (r, sec_name) in sorted(matches.items(), key=lambda x: -(x[1][0].get("revenueEur") or 0)):
        cur = r.get("currency")
        c10 = f"{cik:010d}"
        subs = sp.get(f"https://data.sec.gov/submissions/CIK{c10}.json")
        facts = sp.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c10}.json")
        if not subs or not facts:
            continue
        rec = subs["filings"]["recent"]
        reps = [dict(accn=rec["accessionNumber"][i], end=rec["reportDate"][i], doc=rec["primaryDocument"][i])
                for i in range(len(rec["form"])) if rec["form"][i] in FORMS][:YEARS + 1]
        raw = {}
        for f in reps:
            v = year_values(facts, f["end"], f["accn"], cur)
            if "revenue" in v or "net" in v:
                raw[f["end"]] = (v, f)
        if not raw:
            dropped.append(f"{r['name']} ({sec_name}): no {cur} figures")
            continue
        ends = sorted(raw)
        years = {}
        for i, end in enumerate(ends):
            row, _ = sp.row_of(raw[end][0], raw[ends[i - 1]][0] if i else {})
            f = raw[end][1]
            row["source"] = f"https://www.sec.gov/Archives/edgar/data/{cik}/{f['accn'].replace('-', '')}/{f['doc']}"
            years[end[:4]] = {**row, "end": end}
        years = dict(sorted(years.items())[-YEARS:])
        # Sanity check against the figure FINILA shows: same company, same currency, same scale.
        y = (r.get("periodEnd") or "")[:4]
        a, b = (years.get(y) or {}).get("revenue"), r.get("revenue")
        if a and b and not 0.8 < a / b < 1.25:
            dropped.append(f"{r['name']} ({sec_name}): SEC revenue {a} vs {b} {cur} in {y}")
            continue
        if not a and not (years.get(y) or {}).get("netIncome"):
            dropped.append(f"{r['name']} ({sec_name}): no SEC year {y} to compare")
            continue
        if len(out.get(r["lei"], {}).get("years", {})) >= len(years):
            dropped.append(f"{r['name']} ({sec_name}): another SEC filer of the same name has more years")
            continue
        out[r["lei"]] = {"currency": cur, "cik": cik, "secName": sec_name, "years": years}
        print(f"  {r['name']} = {sec_name} (CIK {cik}): {', '.join(years)}", flush=True)
    for d in dropped:
        print(f"  dropped {d}")
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"wrote {len(out)} companies to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
