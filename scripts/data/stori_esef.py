"""Annual figures from the ESEF annual reports in FSMA STORI, Belgium's official storage of regulated
information (fsma.be/en/stori; site content under CC BY 4.0, robots.txt asks 30 s between requests).

filings.xbrl.org lags for many Belgian issuers (bpost's 2024 and 2025 reports are missing there), but every
Belgian listed company files its ESEF report (inline XBRL, .xhtml) with the FSMA. Each report is read once:
its tagged figures (group totals, no segment breakdowns) are turned into the same yearly rows as
data/history.json, for the report's year and the year before it.

Writes data/stori-history.json: {lei: {"currency", "years": {year: row}}} with each row's "source" the
report in STORI. The company pages join these years with the ESEF years from filings.xbrl.org.
Runs in GitHub Actions (the container proxy blocks fsma.be)."""
import html
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fundamentals as fd   # noqa: E402
import history as hi        # noqa: E402  (its extra concepts and year rows)
import news                 # noqa: E402  (polite get with the FSMA crawl delay)

ROOT = fd.ROOT
OUT = ROOT / "data/stori-history.json"
CACHE = ROOT / "data/cache/stori-esef.json"
CACHE_VERSION = 1
MONTHS = 30          # how far back to look for annual reports
BUDGET = 40 * 60     # seconds of downloads per run; the rest waits for the next run
START = time.time()

CTX = re.compile(r"(?is)<xbrli:context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</xbrli:context>")
UNIT = re.compile(r"(?is)<xbrli:unit\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</xbrli:unit>")
FACT = re.compile(r"(?is)<ix:nonFraction\b([^>]*?)(/>|>(.*?)</ix:nonFraction>)")
ATTR = re.compile(r"([\w:.-]+)=\"([^\"]*)\"")


def ixbrl_facts(raw):
    """Inline XBRL -> facts shaped like xBRL-JSON (what fundamentals.extract reads). Period ends are
    exclusive there, so a year ending 31 Dec is written as ending 1 Jan; facts with dimensions keep them."""
    ctx = {}
    for cid, body in CTX.findall(raw):
        inst = re.search(r"<xbrli:instant>\s*([\d-]+)", body)
        st, en = re.search(r"<xbrli:startDate>\s*([\d-]+)", body), re.search(r"<xbrli:endDate>\s*([\d-]+)", body)
        nxt = lambda d: (date.fromisoformat(d) + timedelta(days=1)).isoformat() + "T00:00:00"
        try:
            period = nxt(inst.group(1)) if inst else f"{st.group(1)}T00:00:00/{nxt(en.group(1))}" if st and en else None
        except ValueError:
            period = None
        ctx[cid] = (period, bool(re.search(r"(?i)<xbrli:segment|<xbrli:scenario", body)))
    units = {}
    for uid, body in UNIT.findall(raw):
        m = re.findall(r"<xbrli:measure>\s*([^<\s]+)", body)
        units[uid] = "/".join(m)
    facts = {}
    for i, (attrs, _, inner) in enumerate(FACT.findall(raw)):
        a = dict(ATTR.findall(attrs))
        period, dims = ctx.get(a.get("contextRef"), (None, False))
        if not period or a.get("xsi:nil") == "true":
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", inner or "")).strip()
        fmt = a.get("format", "").lower()
        if "zero" in fmt or text in ("-", "–", "—"):
            v = 0.0
        else:
            t = re.sub(r"[\s ']", "", text)
            t = t.replace(".", "").replace(",", ".") if "comma" in fmt else t.replace(",", "")
            try:
                v = float(t)
            except ValueError:
                continue
        v *= 10 ** int(a.get("scale") or 0)
        if a.get("sign") == "-":
            v = -v
        d = {"concept": a.get("name", ""), "entity": "", "period": period, "unit": units.get(a.get("unitRef"), "")}
        if dims:
            d["axis"] = "segment"   # fundamentals.extract skips facts with other dimensions
        facts[str(i)] = {"value": v, "dimensions": d}
    return {"facts": facts}


def annual_filings(base, since, today):
    """Annual financial reports in STORI since `since`, newest first, with the ESEF .xhtml document."""
    types = news.get(base + "/document-type") or []
    t = next((x for x in types if re.fullmatch(r"(?i)annual financial report", (x.get("name") or x.get("documentTypeName") or "").strip())), None)
    out, start = [], 0
    while start < 5000:
        q = {"startRowIndex": start, "pageSize": 100, "sortDirection": "Descending",
             "publicationStart": since.isoformat(), "publicationEnd": today.isoformat()}
        if t:
            q.update(documentTypeId=t.get("documentTypeId"), isDocumentTypeGroup=bool(t.get("isGroup")))
        d = news.get(base + "/result", data=q)
        part = (d or {}).get("storiResultItems") or []
        out += [r for r in part if re.search(r"(?i)annual financial report", r.get("reportingTopicName") or "")]
        if len(part) < 100:
            break
        start += 100
    print(f"STORI: {len(out)} annual financial reports since {since}" + ("" if t else " (no type filter)"), flush=True)
    return out


def esef_doc(r):
    docs = [m for m in (r.get("mainDocuments") or []) + (r.get("attachments") or []) if (m.get("originalFileName") or m.get("title") or "").lower().endswith(".xhtml")]
    return next((m for m in docs if m.get("language") == "en"), docs[0] if docs else None)


def main():
    comp = json.loads((ROOT / "js/companies.json").read_text())
    rows = {r["lei"]: r for r in (dict(zip(comp["fields"], x)) for x in comp["rows"])}
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("_v") != CACHE_VERSION:
        cache = {"_v": CACHE_VERSION, "reports": {}}
    page = news.get("https://www.fsma.be/en/stori", as_json=False)
    m = re.search(rb'"vueToolsApi":"([^"]+)"', page[0]) if page else None
    if not m:
        sys.exit("STORI: no API address on the page")
    base = m.group(1).decode().replace("\\/", "/") + "/api/v1/en/stori"
    today = date.today()
    filings = [r for r in annual_filings(base, today - timedelta(days=MONTHS * 31), today) if r.get("lei") in rows]
    print(f"{len(filings)} of them by companies on FINILA", flush=True)
    read = 0
    # Each company's newest report first (it gives two years), largest companies first; older reports after.
    newest = {}
    for r in sorted(filings, key=lambda r: r.get("datePublication") or "", reverse=True):
        newest.setdefault(r["lei"], r)
    first = set(id(r) for r in newest.values())
    for r in sorted(filings, key=lambda r: (id(r) not in first, -(rows[r["lei"]].get("revenueEur") or 0))):
        doc = esef_doc(r)
        if not doc or doc["fileDataId"] in cache["reports"]:
            continue
        if time.time() - START > BUDGET:
            print("  time budget used, the rest next run", flush=True)
            break
        url = f"{base}/download?fileDataId={doc['fileDataId']}"
        got = news.get(url, as_json=False, tries=(0, 30))
        if not got:
            continue
        raw = got[0].decode("utf-8", "replace")
        xj = ixbrl_facts(raw)
        facts = list(xj["facts"].values())
        end = fd.fiscal_end(facts)
        if not end:
            cache["reports"][doc["fileDataId"]] = {"lei": r["lei"], "end": None}
            continue
        cur, prev, currency, end, _ = fd.extract(xj, (end - timedelta(days=1)).isoformat())
        cache["reports"][doc["fileDataId"]] = {
            "lei": r["lei"], "end": end.isoformat(), "currency": currency, "published": (r.get("datePublication") or "")[:10],
            "cur": hi.year_row(cur, prev), "prev": hi.year_row(prev, {}) if prev else None, "source": url}
        read += 1
        print(f"  {rows[r['lei']]['name']}: FY {end.isoformat()} {currency}, {len(facts)} facts, revenue {cache['reports'][doc['fileDataId']]['cur'].get('revenue')}", flush=True)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, separators=(",", ":")))
    out = {}
    by_lei = {}
    for rep in cache["reports"].values():
        if rep.get("end"):
            by_lei.setdefault(rep["lei"], []).append(rep)
    for lei, reps in by_lei.items():
        reps.sort(key=lambda x: (x["end"], x.get("published", "")), reverse=True)
        currency, years = reps[0]["currency"], {}
        for rep in reps:   # newest report first: its year, then the year before it as it now shows it
            if rep["currency"] != currency:
                continue
            years.setdefault(rep["end"][:4], {**rep["cur"], "end": rep["end"], "source": rep["source"]})
            if rep.get("prev") and (rep["prev"].get("revenue") is not None or rep["prev"].get("netIncome") is not None):
                y = str(int(rep["end"][:4]) - 1)
                years.setdefault(y, {**rep["prev"], "end": f"{y}{rep['end'][4:]}", "source": rep["source"], "fromNext": True})
        years, bad = hi.clean(years)
        if years:
            out[lei] = {"currency": currency, "years": dict(sorted(years.items()))}
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print(f"read {read} reports this run; wrote {len(out)} companies to {OUT.relative_to(ROOT)}", flush=True)
    for lei in sorted(out, key=lambda l: -(rows[l].get("revenueEur") or 0))[:15]:
        print(f"  {rows[lei]['name']}: " + ", ".join(f"{y} rev {v.get('revenue')} net {v.get('netIncome')}" for y, v in out[lei]["years"].items()))


if __name__ == "__main__":
    main()
