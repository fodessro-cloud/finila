"""Test (GitHub Actions only), round 7: press-release RSS feeds of the DAX 40 companies. For each company page,
look for an RSS link the page itself advertises (rel=alternate) and check robots.txt allows reading it."""
import re, urllib.parse, urllib.request, urllib.robotparser

UA = "FINILA data refresh (finila.com)"
PAGES = {
    "adidas": ["https://www.adidas-group.com/en/media/press-releases/"], "Airbus": ["https://www.airbus.com/en/newsroom/press-releases"],
    "Allianz": ["https://www.allianz.com/en/press/news.html"], "BASF": ["https://www.basf.com/global/en/media/news-releases.html"],
    "Bayer": ["https://www.bayer.com/media/en-us/press-releases/"], "Beiersdorf": ["https://www.beiersdorf.com/newsroom/press-releases"],
    "BMW": ["https://www.press.bmwgroup.com/global"], "Brenntag": ["https://corporate.brenntag.com/en/media/news/"],
    "Commerzbank": ["https://www.commerzbank.com/en/hauptnavigation/presse/pressemitteilungen/"],
    "Continental": ["https://www.continental.com/en/press/press-releases/"], "Daimler Truck": ["https://www.daimlertruck.com/en/newsroom"],
    "Deutsche Bank": ["https://www.db.com/news"], "Deutsche Boerse": ["https://www.deutsche-boerse.com/dbg-en/media/press-releases"],
    "DHL": ["https://group.dhl.com/en/media-relations/press-releases.html"], "Deutsche Telekom": ["https://www.telekom.com/en/media/media-information"],
    "E.ON": ["https://www.eon.com/en/about-us/media/press-release.html"], "Fresenius": ["https://www.fresenius.com/media/press-releases"],
    "FMC": ["https://www.freseniusmedicalcare.com/en/news"], "Hannover Re": ["https://www.hannover-re.com/1557880/news"],
    "Heidelberg Materials": ["https://www.heidelbergmaterials.com/en/press-releases"], "Henkel": ["https://www.henkel.com/press-and-media/press-releases-and-kits"],
    "Infineon": ["https://www.infineon.com/cms/en/about-infineon/press/press-releases/"], "Mercedes-Benz": ["https://group.mercedes-benz.com/company/news/"],
    "Merck": ["https://www.merckgroup.com/en/news.html"], "MTU": ["https://www.mtu.de/newsroom/press/"],
    "Munich Re": ["https://www.munichre.com/en/company/media-relations/media-information-and-corporate-news.html"],
    "Porsche AG": ["https://newsroom.porsche.com/en.html"], "Qiagen": ["https://corporate.qiagen.com/newsroom/press-releases"],
    "Rheinmetall": ["https://www.rheinmetall.com/en/media/news-watch"], "RWE": ["https://www.rwe.com/en/press/"],
    "SAP": ["https://news.sap.com/", "https://news.sap.com/feed/"], "Sartorius": ["https://www.sartorius.com/en/company/newsroom"],
    "Siemens": ["https://press.siemens.com/global/en"], "Siemens Energy": ["https://www.siemens-energy.com/global/en/home/press-releases.html"],
    "Siemens Healthineers": ["https://www.siemens-healthineers.com/press"], "Symrise": ["https://www.symrise.com/newsroom/"],
    "Volkswagen": ["https://www.volkswagen-group.com/en/press-releases"], "Vonovia": ["https://www.vonovia.com/en/news"],
    "Zalando": ["https://corporate.zalando.com/en/newsroom"], "Scout24": ["https://www.scout24.com/en/media/press-releases"],
}


def get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html, application/rss+xml, */*"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except Exception as e:
        return "ERR " + str(e)[:60], "", b""


def allowed(url):
    p = urllib.parse.urlparse(url)
    rp = urllib.robotparser.RobotFileParser(f"{p.scheme}://{p.netloc}/robots.txt")
    try:
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return None


for name, pages in PAGES.items():
    found = []
    for page in pages:
        st, ct, b = get(page)
        if "xml" in ct or b[:200].lstrip().startswith(b"<?xml") or b"<rss" in b[:500]:
            found.append(page)
            continue
        t = b.decode("utf-8", "replace")
        for m in re.finditer(r'<link[^>]+type="application/(rss|atom)\+xml"[^>]*>', t, re.I):
            href = re.search(r'href="([^"]+)"', m.group(0))
            if href:
                found.append(urllib.parse.urljoin(page, href.group(1)))
        found += [urllib.parse.urljoin(page, h) for h in re.findall(r'href="([^"]*(?:rss|feed)[^"]*)"', t, re.I)][:5]
        print(f"   {name}: {page} -> {st} {ct[:30]} {len(b)}")
    seen = []
    for f in dict.fromkeys(found):
        st, ct, b = get(f)
        ok = b"<rss" in b[:2000] or b"<feed" in b[:2000]
        items = len(re.findall(rb"<item[ >]|<entry[ >]", b))
        seen.append((f, st, ok, items, allowed(f)))
    good = [s for s in seen if s[2] and s[3]]
    print(f"== {name}: {'FEED ' + good[0][0] + f' items={good[0][3]} robots_ok={good[0][4]}' if good else 'no feed'}"
          + ("" if good else f"  tried {[(s[0][:80], s[1]) for s in seen][:4]}"))
