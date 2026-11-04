"""What each company does, in its own words: the ESEF tag 'Description of nature of entity's operations and
principal activities' (ifrs-full:DescriptionOfNatureOfEntitysOperationsAndPrincipalActivities), which every
ESEF annual report must mark up. The first sentences are quoted word for word, with the report as source.

Reports never change once filed, so results are cached per report in data/cache/about.json.
Writes data/about.json: {lei: {"text": ..., "period": "2025-12-31", "source": url}}.
"""
import html, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fundamentals as fd   # noqa: E402

ROOT = fd.ROOT
CACHE = ROOT / "data/cache/about.json"
OUT = ROOT / "data/about.json"
CACHE_VERSION = 3
BUDGET = float(os.environ.get("FINILA_BUDGET_MIN", "300")) * 60
WORKERS = 3
START = time.time()
CONCEPTS = ("ifrs-full:DescriptionOfNatureOfEntitysOperationsAndPrincipalActivities",
            "ifrs-full:DescriptionOfNatureOfFinancialStatements")
MAX_CHARS = 420
_get = fd.get


def polite_get(url, as_json=True):
    for wait in (0, 20, 40, 60):
        time.sleep(wait)
        res = _get(url, as_json)
        if res is not None:
            return res
    return None


fd.get = polite_get


def plain(v):
    t = html.unescape(re.sub(r"<[^>]+>", " ", str(v)))
    return re.sub(r"\s+", " ", t).strip()


def english(t):
    low = f" {t.lower()} "
    return sum(low.count(f" {w} ") for w in ("the", "and", "of", "is", "in", "its")) >= 3


def summary(t):
    """The first sentences that say what the company does, up to MAX_CHARS; headings and note numbers cut."""
    t = re.sub(r"^(\d+(\.\d+)*\.?\s+)?(note\s+\d+\s*)?(general information|corporate information|reporting entity|"
               r"nature of operations|basis of preparation|the company|about the group|organisation|organization)[:.\s-]*",
               "", t, flags=re.I)
    t = re.sub(r"^\d+(\.\d+)*\.?\s+", "", t)
    out = ""
    for s in re.split(r"(?<=[a-z0-9\)][.])\s+(?=[A-Z])", t):
        if len(out) + len(s) > MAX_CHARS:
            break
        out += (" " if out else "") + s
    return out if len(out) >= 40 else ""


REVENUE = ("ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers")
AXES = {"ifrs-full:SegmentsAxis": "segments", "ifrs-full:ProductsAndServicesAxis": "products",
        "ifrs-full:GeographicalAreasAxis": "regions"}
NOT_PARTS = r"(Operating|Reportable|Total|All|Other)?Segments?Member$|Elimination|Intersegment|Unallocated|Reconciling|" \
            r"Consolidat|Adjustment|Corporate(AndOther)?Member$|OtherSegmentsMember|AggregatedMember|GroupMember$|^ifrs-full:.*Member$"
COUNTRY = {"DK": "Denmark", "SE": "Sweden", "NO": "Norway", "FI": "Finland", "DE": "Germany", "FR": "France", "NL": "Netherlands",
           "BE": "Belgium", "IT": "Italy", "ES": "Spain", "PT": "Portugal", "GB": "United Kingdom", "IE": "Ireland", "US": "United States",
           "CH": "Switzerland", "AT": "Austria", "PL": "Poland", "CN": "China", "JP": "Japan", "IN": "India", "CA": "Canada",
           "BR": "Brazil", "AU": "Australia", "LU": "Luxembourg", "IS": "Iceland", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
           "GR": "Greece", "CZ": "Czechia", "HU": "Hungary", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia", "HR": "Croatia",
           "BG": "Bulgaria", "MX": "Mexico", "KR": "South Korea", "SG": "Singapore", "IL": "Israel", "TR": "Turkey", "ZA": "South Africa"}


def label(member):
    """'nn:DiabetesAndObesityCareMember' -> 'Diabetes and obesity care'; 'country:DK' -> 'Denmark'."""
    prefix, _, name = member.partition(":")
    if prefix == "country":
        return COUNTRY.get(name, name)
    name = re.sub(r"(Member|Segment|Domain)$", "", name)
    t = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)", " ", name).replace("_", " ").strip()
    t = re.sub(r"\b(And|Of|The|For|In|To|With|Other)\b", lambda m: m.group(1).lower(), t)
    return t[:1].upper() + t[1:]


DEBUG = {}


def revenue_split(facts):
    """Revenue by segment, product line and region for the newest full year, as % of group revenue. A split is
    kept only when its parts add up to the group total (95-105%): otherwise parts are missing or overlap."""
    total, parts = {}, {}
    for fact in facts:
        d = fact.get("dimensions", {})
        if d.get("concept") not in REVENUE or "/" not in d.get("period", ""):
            continue
        try:
            start, stop = fd.parse_period(d["period"])
            v = float(fact.get("value"))
        except (TypeError, ValueError):
            continue
        if not 330 <= (stop - start).days <= 400:
            continue
        extra = set(d) - {"concept", "entity", "period", "unit", "language"}
        DEBUG.setdefault(tuple(sorted(extra)), d.get(next(iter(extra)), "") if extra else "")
        if d.get("ifrs-full:ConsolidationItemsAxis", "ifrs-full:OperatingSegmentsMember") in (
                "ifrs-full:OperatingSegmentsMember", "ifrs-full:ReportableSegmentsMember"):
            extra.discard("ifrs-full:ConsolidationItemsAxis")   # the segment note's own column, not an adjustment
        else:
            continue
        if not extra:
            total[(stop, d["concept"])] = v
        elif len(extra) == 1:
            axis = extra.pop()
            if axis in AXES and not re.search(NOT_PARTS, d[axis]):
                parts.setdefault((stop, d["concept"], AXES[axis]), {})[d[axis]] = v
    out = {}
    for (stop, concept, kind), members in parts.items():
        tot = total.get((stop, concept)) or total.get((stop, REVENUE[0])) or total.get((stop, REVENUE[1]))
        if not tot or tot <= 0 or len(members) < 2 or any(v < 0 for v in members.values()):
            continue
        if not 0.95 <= sum(members.values()) / tot <= 1.05:
            continue
        latest = out.get(kind)
        if latest and latest[0] >= stop:
            continue
        rows = sorted(((label(m), round(100 * v / tot)) for m, v in members.items()), key=lambda x: -x[1])
        rows = [(n, p) for n, p in rows if n and p >= 1]
        if len(rows) >= 2:
            out[kind] = (stop, rows[:6])
    return {k: v[1] for k, v in out.items()}


def read_company(lei, cached):
    filings = fd.latest_annual(lei)
    if not filings:
        return None
    for f in filings[:4]:   # newest annual report first; a half-year report may lack the note
        fid = f["fxo_id"]
        if cached.get("fxo_id") == fid:
            return cached
        xj = fd.get(fd.BASE + f["json_url"]) if f.get("json_url") else None
        if not xj:
            continue
        found = {}
        facts = list((xj.get("facts") or {}).values())
        split = revenue_split(facts)
        for fact in facts:
            c = fact.get("dimensions", {}).get("concept")
            if c in CONCEPTS and fact.get("value"):
                t = plain(fact["value"])
                if english(t):
                    found.setdefault(c, t)
        text = summary(found.get(CONCEPTS[0], ""))
        if text or split:
            return {"fxo_id": fid, "text": text or None, "split": split, "period": f["period_end"],
                    "source": fd.BASE + f["viewer_url"] if f.get("viewer_url") else None}
    return {"fxo_id": filings[0]["fxo_id"], "text": None}


def main():
    comp = json.loads((ROOT / "js/companies.json").read_text())
    rows = [dict(zip(comp["fields"], r)) for r in comp["rows"]]
    leis = sorted({r["lei"] for r in rows if r.get("origin") is None and "filings.xbrl.org" in (r.get("source") or "")})
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("_v") != CACHE_VERSION:
        cache = {"_v": CACHE_VERSION}
    today = date.today().isoformat()
    week = date.fromordinal(date.today().toordinal() - 7).isoformat()
    todo = [l for l in leis if cache.get(l, {}).get("checked", "") < week]   # new annual reports come once a year
    size = {r["lei"]: r.get("revenueEur") or 0 for r in rows}
    todo.sort(key=lambda l: -size.get(l, 0))
    if os.environ.get("FINILA_LIMIT"):
        todo = todo[:int(os.environ["FINILA_LIMIT"])]
    print(f"{len(leis)} companies with ESEF reports, {len(todo)} to check", flush=True)
    done = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = {pool.submit(lambda l=l: read_company(l, cache.get(l, {})) if time.time() - START < BUDGET else None): l for l in todo}
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except Exception as e:
                print(f"  ! {futs[fut]}: {e}", flush=True)
                continue
            if res is None:
                continue
            cache[futs[fut]] = {**res, "checked": today}
            done += 1
            if done % 50 == 0:
                print(f"  {done} companies read", flush=True)
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cache, separators=(",", ":"), ensure_ascii=False))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, separators=(",", ":"), ensure_ascii=False))
    out = {l: {k: v.get(k) for k in ("text", "split", "period", "source")} for l, v in cache.items()
           if l != "_v" and (v.get("text") or v.get("split"))}
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print("revenue dimension combinations seen:", sorted(DEBUG.items(), key=lambda x: len(x[0]))[:60], flush=True)
    print(f"descriptions for {sum(1 for v in out.values() if v['text'])}, revenue splits for "
          f"{sum(1 for v in out.values() if v.get('split'))} of {len(leis)} companies; read {done} this run", flush=True)
    names = {r["lei"]: r["name"] for r in rows}
    for l in sorted(out, key=lambda l: -size.get(l, 0))[:60]:
        print(f"== {names.get(l)}: {out[l]['text']}\n   {out[l].get('split')}")


if __name__ == "__main__":
    main()
