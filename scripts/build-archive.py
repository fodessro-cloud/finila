#!/usr/bin/env python3
"""Rebuilds the old finila.com WordPress pages in the FINILA design.

Reads the static WordPress copy (the archiv/ folder exported from the server,
see .github/workflows/archive-export.yml) and writes clean pages to
archive-site/, which the deploy uploads over /archiv/ on the server. Images stay
where they are on the server (/archiv/wp-content/...).

    python3 scripts/build-archive.py <path to exported archiv/> [archive-site]

Needs beautifulsoup4.
"""
import html
import os
import posixpath
import re
import sys
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment

SITE_HOSTS = {"finila.com", "www.finila.com"}
TITLE_SUFFIX = re.compile(r"\s*[-–]\s*Best trading tools and information.*$")
# WooCommerce, login and embed leftovers that were never real content.
SKIP = re.compile(r"^(index\.html|trading-software\.html|wp-login\.php.*|(checkout|my-account|wishlist)/.*|.*/embed/index\.html)$")
SECTIONS = [
    ("market-reports/", "Market reports"),
    ("forex-futures-stock-charts/", "Charts"),
    ("trading-school/", "Trading school"),
]
KEEP_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height", "title"},
    "iframe": {"src", "width", "height", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
DROP_TAGS = ["script", "style", "noscript", "form", "button", "input", "select", "textarea", "link", "meta"]
DROP_SELECTORS = [".entry-title", ".entry-posted", "#respond", ".comments-area", "#comments",
                  ".sharedaddy", ".addtoany_share_save_container", ".post-navigation", ".nav-links"]

LOGO = ('<svg width="{s}" height="{s}" viewBox="0 0 64 64" aria-hidden="true">'
        '<rect x="8" y="6" width="12" height="52" rx="2" fill="#0F1B33"/>'
        '<rect x="24" y="6" width="32" height="12" rx="2" fill="#F2B33D"/>'
        '<rect x="24" y="24" width="22" height="12" rx="2" fill="#5B9BFF"/>'
        '<rect x="24" y="42" width="12" height="12" rx="2" fill="#2C3A5A"/></svg>')

PAGE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} | FINILA Archive</title>
  <link rel="canonical" href="https://www.finila.com{url}">
  <link rel="icon" href="/css/logo.svg" type="image/svg+xml">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/css/home.css">
  <link rel="stylesheet" href="/css/archive.css">
  <link rel="stylesheet" href="/css/header.css">
</head>
<body>

<header class="fh">
  <div class="fh-row">
    <a class="fh-logo" href="/" aria-label="FINILA home">{logo}FINILA</a>
    <nav class="fh-nav" aria-label="Main menu">
      <a href="/screener">Screener</a>
      <a href="/map">Maps</a>
      <a href="/groups">Groups</a>
      <a href="/insiders">Insiders</a>
      <a href="/shorts">Shorts</a>
      <a href="/radar">Radar</a>
      <a href="/property">Property</a>
      <a href="/rates">Rates</a>
      <a href="/assets">Assets</a>
      <a href="/archiv/" aria-current="page">Archive</a>
    </nav>
  </div>
</header>
<script>(function(){{var n=document.querySelector('.fh-nav'),a=n&&n.querySelector('[aria-current]');if(a)n.scrollLeft=a.offsetLeft-n.offsetLeft-16;}})();</script>
<div class="a-bar">
  <div class="h-wrap a-bar-row">
    <span class="a-badge">Archive 2014–2019</span>
    <nav class="a-subnav" aria-label="Archive">
      <a href="/archiv/">All</a>
      <a href="/market-reports/">Market reports</a>
      <a href="/forex-futures-stock-charts/">Charts</a>
      <a href="/trading-school/">Trading school</a>
      <a href="/blog/">Blog</a>
    </nav>
  </div>
</div>

<main class="h-wrap a-main">
{body}
</main>

<footer class="h-footer">
  <div class="h-wrap h-footer-row">
    <div class="h-footer-brand">
      <div class="h-logo small">{logo_small}FINILA</div>
      <p>Archived content from the original FINILA.com (2014–2019), kept for reference. Prices and opinions reflect the date of publication. Not investment advice.</p>
    </div>
    <nav class="h-footer-nav" aria-label="Footer">
      <a href="/screener">Screener</a>
      <a href="/map">Market map</a>
      <a href="/groups">Sectors &amp; countries</a>
      <a href="/archiv/">Archive</a>
      <a href="https://www.linkedin.com/company/finila" target="_blank" rel="noopener"><svg class="h-li" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zM7.12 20.45H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z"/></svg>LinkedIn</a>
    </nav>
  </div>
</footer>
<script src="/js/analytics.js" defer></script>
</body>
</html>
"""


def public_url(rel):
    """archiv/<rel> file -> the address visitors use (no /archiv/, no index.html)."""
    path = "/" + rel
    if path.endswith("/"):
        path = path[: -len("/")]
    return path


def map_link(value, page_rel, attr):
    """Rewrites a link found on archiv/<page_rel> to its public address."""
    value = value.strip()
    if not value or value.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return value
    absolute = urljoin("https://www.finila.com/archiv/" + page_rel, value)
    u = urlparse(absolute)
    if u.hostname not in SITE_HOSTS:
        return value
    path = posixpath.normpath(u.path) + ("/" if u.path.endswith("/") else "")
    if "wp-login" in path or "replytocom" in u.query or "/feed" in path:
        return None
    if path.startswith("/archiv/wp-content/") or path.startswith("/archiv/wp-includes/"):
        return path
    if path.startswith("/wp-content/"):
        return "/archiv" + path
    if path.startswith("/archiv/"):
        path = path[len("/archiv"):]
    if path.endswith("/"):
        path = path[: -len("/")]
    if attr == "href" and path == "/":
        return "/archiv/"
    return path + (("#" + u.fragment) if u.fragment else "")


def tradingview_iframe(main):
    """Old pages embedded charts with TradingView's retired tv.js; use the iframe widget."""
    for sc in main.find_all("script"):
        m = re.search(r'"symbol"\s*:\s*"([^"]+)"', sc.string or "")
        if m:
            interval = re.search(r'"interval"\s*:\s*"([^"]+)"', sc.string or "")
            src = ("https://s.tradingview.com/widgetembed/?symbol=" + m.group(1)
                   + "&interval=" + (interval.group(1) if interval else "D")
                   + "&theme=dark&style=1&locale=en&withdateranges=1")
            return src
    return None


def clean(main, soup, page_rel):
    chart = tradingview_iframe(main)
    for sel in DROP_SELECTORS:
        for el in main.select(sel):
            el.decompose()
    for tag in main.find_all(DROP_TAGS):
        tag.decompose()
    for c in main.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for el in main.find_all(True):
        if el.name in ("a", "img", "iframe"):
            for attr in ("href", "src"):
                if el.get(attr):
                    new = map_link(el[attr], page_rel, attr)
                    if new is None:
                        el.unwrap() if el.name == "a" else el.decompose()
                        break
                    el[attr] = new
            else:
                if el.name == "img":
                    el["loading"] = "lazy"
                if el.name == "a" and el.get("href", "").startswith("http"):
                    el["rel"] = "noopener"
            if el.parent is None:
                continue
        keep = KEEP_ATTRS.get(el.name, set()) | {"loading", "rel"}
        for attr in list(el.attrs):
            if attr not in keep:
                del el[attr]
    # Drop wrappers left empty by the clean-up.
    for el in reversed(main.find_all(["div", "span", "p", "section"])):
        if not el.get_text(strip=True) and not el.find(["img", "iframe", "table"]):
            el.decompose()
    for el in main.find_all(["div", "section", "article", "span"]):
        el.unwrap()
    if chart:
        frame = soup.new_tag("iframe", src=chart, title="Chart", loading="lazy")
        frame["class"] = "a-chart"
        main.insert(0, frame)
    return main


def page_meta(soup):
    t = soup.select_one(".entry-title")
    title = t.get_text(" ", strip=True) if t else ""
    if not title and soup.title:
        title = TITLE_SUFFIX.sub("", soup.title.get_text(strip=True))
    posted = soup.select_one(".entry-posted")
    date = ""
    if posted:
        m = re.search(r"[A-Z][a-z]+ \d{1,2}, \d{4}", posted.get_text(" ", strip=True))
        date = m.group(0) if m else ""
    return title or "FINILA", date


def is_post(soup):
    art = soup.select_one("main article")
    return bool(art and "type-post" in (art.get("class") or []))


def is_listing(rel):
    return rel.startswith(("blog/", "category/", "author/"))


def render(title, url, body, lang="en"):
    return PAGE.format(title=html.escape(title), url=url, body=body, lang=lang,
                       logo=LOGO.format(s=36), logo_small=LOGO.format(s=26))


def build_page(src_root, rel):
    soup = BeautifulSoup(open(os.path.join(src_root, rel), encoding="utf-8", errors="replace"), "html.parser")
    main = soup.find("main")
    if main is None:
        return None
    listing = is_listing(rel)
    title, date = page_meta(soup)
    if listing and soup.title:
        title = TITLE_SUFFIX.sub("", soup.title.get_text(strip=True))
    post = is_post(soup) and not listing
    lang = "sk" if re.search(r"[čšžťľýáíéúäô]", title.lower()) else "en"
    if listing:
        items = []
        for art in main.select("article"):
            h = art.select_one(".entry-title a") or art.select_one(".entry-title")
            if not h:
                continue
            href = map_link(h.get("href", ""), rel, "href") if h.name == "a" else ""
            _, d = page_meta(BeautifulSoup(str(art), "html.parser"))
            text = art.select_one(".entry-content")
            excerpt = " ".join(text.get_text(" ").split())[:220] + "…" if text else ""
            items.append(f'<li><a href="{html.escape(href)}">{html.escape(h.get_text(" ", strip=True))}</a>'
                         f'<span class="a-date">{d}</span><p>{html.escape(excerpt)}</p></li>')
        body = f'<h1>{html.escape(title)}</h1>\n<ul class="a-list">\n' + "\n".join(items) + "\n</ul>"
    else:
        article = main.select_one("article")
        content = clean(article.select_one(".entry-content") or article if article else main, soup, rel)
        inner = "".join(str(c) for c in content.contents).strip()
        date_html = f'<p class="a-date">Published {date}</p>' if date else ""
        body = f'<article class="a-article">\n<h1>{html.escape(title)}</h1>\n{date_html}\n<div class="a-content">{inner}</div>\n</article>'
    return title, (date if post else ""), render(title, public_url(rel), body, lang)


def build_index(pages):
    groups = {label: [] for _, label in SECTIONS}
    articles, other = [], []
    for rel, title, date in pages:
        url = public_url(rel)
        for prefix, label in SECTIONS:
            if rel.startswith(prefix) and rel != prefix + "/":
                groups[label].append((title, url, date))
                break
        else:
            if rel.count("/") == 1 and date and not rel.startswith(("category/", "author/")):
                articles.append((title, url, date))
            elif not rel.startswith(("category/", "author/")) and rel.count("/") == 1:
                other.append((title, url, date))

    def ul(items, with_date=False):
        rows = []
        for t, u, d in sorted(items, key=lambda x: x[0].lower()):
            extra = f'<span class="a-date">{d}</span>' if with_date and d else ""
            rows.append(f'<li><a href="{html.escape(u)}">{html.escape(t)}</a>{extra}</li>')
        return '<ul class="a-links">' + "".join(rows) + "</ul>"

    from datetime import datetime
    articles.sort(key=lambda x: datetime.strptime(x[2], "%B %d, %Y"), reverse=True)
    art_rows = "".join(f'<li><a href="{html.escape(u)}">{html.escape(t)}</a><span class="a-date">{d}</span></li>'
                       for t, u, d in articles)
    blocks = [
        '<h1>FINILA Archive</h1>',
        '<p class="a-lead">Market reports, charts, trading lessons and analyses published on FINILA.com between 2014 and 2019.</p>',
        '<div class="a-grid">',
        f'<section class="a-card"><h2>Articles and analyses</h2><ul class="a-links">{art_rows}</ul></section>',
    ]
    for _, label in SECTIONS:
        blocks.append(f'<section class="a-card"><h2>{label}</h2>{ul(groups[label])}</section>')
    blocks.append(f'<section class="a-card"><h2>Other pages</h2>{ul(other)}</section>')
    blocks.append("</div>")
    return render("FINILA Archive", "/archiv/", "\n".join(blocks))


def main():
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "archive-site"
    pages = []
    for dirpath, _, files in os.walk(src):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), src).replace(os.sep, "/")
            if not f.endswith(".html") or SKIP.match(rel):
                continue
            built = build_page(src, rel)
            if not built:
                print("skipped (no content):", rel)
                continue
            title, date, page = built
            dest = os.path.join(out, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(page)
            pages.append((rel, title, date))
    with open(os.path.join(out, "/"), "w", encoding="utf-8") as fh:
        fh.write(build_index(pages))
    print(f"Built {len(pages)} pages + index into {out}/")


if __name__ == "__main__":
    main()
