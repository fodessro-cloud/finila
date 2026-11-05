"""Test (GitHub Actions only): take report sections by their headings (Highlights, Significant events, Outlook)
instead of picking sentences. Prints old quotes and new sections side by side for 20 recent reports."""
import html, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import news

ROOT = Path(__file__).resolve().parents[2]
SECTIONS = {
    "highlights": r"^(highlights|key (figures|highlights)|financial highlights|summary|(the )?(third|second|first|fourth) quarter( 20\d\d)?( in brief)?|q[1-4]( 20\d\d)?( in brief| highlights)?|"
                  r"(july|april|january|october)\s*[-–]\s*(september|june|march|december)( 20\d\d)?( in brief)?|the period in brief)$",
    "events": r"^(significant|important|key|material) events.*|events (during|in) the (quarter|period).*|events after.*|"
              r"(business|operational|operating|commercial|strategic) (highlights|progress|update|developments?)|business review|"
              r"other (significant )?events.*|highlights (during|in) the (quarter|period)$",
    "outlook": r"^(outlook|guidance|financial (outlook|guidance|targets)|(20\d\d )?outlook( for 20\d\d| 20\d\d)?|prospects|outlook and guidance|market outlook)$",
}


BLOCK = re.compile(r"(?i)<(/?)(h[1-6]|p|li|div|tr|td|ul|ol|table|br)\b[^>]*>")


def blocks_of(raw, ctype):
    """HTML -> list of (is_heading, text, tag). The text between two block tags is one block; a heading is an
    h1-h6, or a short block that is entirely bold or italic (<p><strong>Outlook</strong></p>)."""
    if b"%PDF" in raw[:1024] or "pdf" in (ctype or "").lower():
        return None
    src = raw.decode("utf-8", "replace")
    src = re.sub(r"(?is)<(script|style|head|nav|footer)\b.*?</\1>", " ", src)
    out, last, tag = [], 0, ""
    marks = list(BLOCK.finditer(src)) + [None]
    for m in marks:
        end = m.start() if m else len(src)
        inner = src[last:end]
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).replace("\xa0", " ").strip()
        if text and tag != "td":
            styled = re.fullmatch(r"(?is)\s*(<(strong|b|em|i|span|u)\b[^>]*>\s*)+[^<]+(</(strong|b|em|i|span|u)>\s*)+:?\s*", inner) is not None \
                and re.search(r"(?i)<(strong|b|em|i)\b", inner) is not None
            head = (tag.startswith("h") or styled or text.isupper()) and len(text) < 90 and not text.endswith(".")
            out.append((head, text, tag))
        if m:
            last, tag = m.end(), (m.group(2).lower() if not m.group(1) else "")
    return out


def sections(blocks):
    found, cur = {}, None
    for head, text, tag in blocks:
        t = text.strip(" :").lower()
        if head:
            cur = next((k for k, rx in SECTIONS.items() if re.search(rx, t)), None)
            if cur and cur in found:
                cur = None   # only the first section of each kind
            continue
        if cur:
            items = found.setdefault(cur, [])
            if sum(len(x) for x in items) < 900 and len(items) < 8:
                items.append(text)
    return found


cache = json.loads((ROOT / "data/cache/news.json").read_text())
anns = [a for a in cache.get("ann", {}).values() if a.get("results") and a.get("src") == "Nasdaq Nordic"]
anns.sort(key=lambda a: -a.get("chars", 0))
picked = anns[:12] + [a for a in cache.get("ann", {}).values() if a.get("src") == "AMF France" and a.get("results")][:3]
stats = {"html": 0, "pdf": 0, **{k: 0 for k in SECTIONS}}
for a in picked:
    r = news.get(a["url"], as_json=False)
    print(f"\n######## {a['company']} | {a['title'][:90]}\n{a['url']}", flush=True)
    if not r:
        print("  (no page)"); continue
    raw = r[0].decode("utf-8", "replace")
    bl = blocks_of(*r)
    if bl is None:
        stats["pdf"] += 1; print("  (PDF: no headings)"); continue
    stats["html"] += 1
    print("  headings seen:", [t for h, t, _ in bl if h][:25])
    sec = sections(bl)
    for k in SECTIONS:
        if sec.get(k):
            stats[k] += 1
            print(f"  NEW [{k}]"); [print("     -", x[:260]) for x in sec[k]]
    print("  OLD results:"); [print("     -", q["text"][:200] if isinstance(q, dict) else q) for q in a.get("results", [])]
    print("  OLD outlook:"); [print("     -", q["text"][:200]) for q in a.get("outlook", [])]
print("\n== stats", stats)
