"""Test (GitHub Actions only): how many companies tag their order backlog as a number in the newest ESEF annual report.
IFRS 15.120 'TransactionPriceAllocatedToRemainingPerformanceObligations' or a company-specific backlog/order book element."""
import json, re, sys
from collections import Counter
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).parent))
import fundamentals as fd

ROOT = Path(__file__).resolve().parents[2]
out = json.load(open(ROOT / "data/outlook.json"))
news = json.load(open(ROOT / "data/news.json"))
quoted = {k for k, v in out.items() if any(i["kind"] == "backlog" for i in v.get("items") or [])}
quoted |= {k for k, v in news.items() if any(i["kind"] == "backlog" for i in ((v.get("report") or {}).get("outlook") or []))}
withfig = sorted(json.load(open(ROOT / "data/history.json")))
sample = sorted(quoted) + [l for l in withfig if l not in quoted][:150]
RX = re.compile(r"RemainingPerformanceObligation|backlog|orderbook|order_book|orderstock|ordersonhand|unfilledorders", re.I)


def one(lei):
    fs = fd.latest_annual(lei) or []
    if not fs:
        return lei, None
    xj = fd.get(fd.BASE + fs[0]["json_url"]) or {}
    hits = Counter()
    for f in (xj.get("facts") or {}).values():
        c = f.get("dimensions", {}).get("concept", "")
        if RX.search(c) and isinstance(f.get("value"), (int, float, str)) and re.fullmatch(r"-?\d+(\.\d+)?", str(f.get("value"))):
            hits[c] += 1
    return lei, dict(hits)


tot = Counter(); n_q = n_q_hit = n_o = n_o_hit = 0
with ThreadPoolExecutor(4) as ex:
    for lei, hits in ex.map(one, sample):
        q = lei in quoted
        if hits is None:
            continue
        if q: n_q += 1; n_q_hit += bool(hits)
        else: n_o += 1; n_o_hit += bool(hits)
        for c in hits: tot[c] += 1
        if hits: print(("Q " if q else "- ") + lei, hits, flush=True)
print(f"== quoted backlog: {n_q_hit}/{n_q} tag a number; others: {n_o_hit}/{n_o}")
print("== concepts:", tot.most_common(25))
