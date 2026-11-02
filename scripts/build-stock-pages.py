#!/usr/bin/env python3
"""Builds one static page per company (/stock/<name>) so search engines can find every FINILA company.

Reads js/companies.json, js/company-prices.json, js/insiders.json and js/shorts.json and writes
  <out>/stock/<slug>.html   one page per company, the figures written into the HTML
  <out>/stock/index.html    an A-Z list of all companies, linked from every page
  <out>/sitemap.xml         every FINILA page for Google and Bing
  <out>/js/stock-slugs.json {lei: slug}, used by /screener and the homepage to link the pages
Prices on a page are those of the build; js/stock-page.js refreshes them from company-prices.json.

    python3 scripts/build-stock-pages.py [source dir] [output dir]   (both default to the repo root)
"""
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SITE = 'https://www.finila.com'
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else SRC)
# Pages listed in the sitemap besides the company pages.
PAGES = ['', 'screener', 'map', 'groups', 'insiders', 'shorts', 'property', 'rates', 'assets', 'stock/']

COUNTRIES = {
    'AT': 'Austria', 'BE': 'Belgium', 'BG': 'Bulgaria', 'CH': 'Switzerland', 'CY': 'Cyprus', 'CZ': 'Czechia',
    'DE': 'Germany', 'DK': 'Denmark', 'EE': 'Estonia', 'ES': 'Spain', 'FI': 'Finland', 'FR': 'France',
    'GB': 'United Kingdom', 'GR': 'Greece', 'HR': 'Croatia', 'HU': 'Hungary', 'IE': 'Ireland', 'IS': 'Iceland',
    'IT': 'Italy', 'LI': 'Liechtenstein', 'LT': 'Lithuania', 'LU': 'Luxembourg', 'LV': 'Latvia', 'MT': 'Malta',
    'NL': 'Netherlands', 'NO': 'Norway', 'PL': 'Poland', 'PT': 'Portugal', 'RO': 'Romania', 'SE': 'Sweden',
    'SI': 'Slovenia', 'SK': 'Slovakia',
}
F_TESTS = ['Profitable', 'Positive operating cash flow', 'Return on assets improved', 'Cash flow above profit',
           'Long-term debt / assets not up', 'Current ratio improved', 'No new shares issued', 'Gross margin improved',
           'Asset turnover improved']
# Legal forms dropped from the address: /stock/asml-holding, not /stock/asml-holding-n-v
LEGAL = r'\b(publ|ab|asa|as|a s|plc|p l c|ag|se|sa|s a|nv|n v|spa|s p a|oyj|abp|oy|ltd|limited|hf|dd|d d|d o o|nyrt|sgps|socimi|bv|kgaa|co kgaa|ab publ|anonymos etaireia|ae)\b'


def esc(v):
    return html.escape(str(v if v is not None else ''), quote=True)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def num(v, d=1):
    return f'{v:,.{d}f}' if is_num(v) else '—'


def bn(v, cur=''):
    return f'{num(v / 1000, 2)}B{" " + cur if cur else ""}' if is_num(v) else '—'


def pct(v):
    return f'<span class="{"pos" if v >= 0 else "neg"}">{num(v)}%</span>' if is_num(v) else '—'


def eur(v):
    if not is_num(v):
        return '—'
    return f'€{num(v / 1e6, 1)}M' if abs(v) >= 1e6 else f'€{num(v / 1e3, 0)}k'


# Greek names (Athens listings) would otherwise lose every letter.
GREEK = dict(zip('αβγδεζηθικλμνξοπρσςτυφχψω', ['a', 'v', 'g', 'd', 'e', 'z', 'i', 'th', 'i', 'k', 'l', 'm', 'n', 'x', 'o', 'p', 'r', 's', 's', 't', 'y', 'f', 'ch', 'ps', 'o']))


def slugify(name):
    s = unicodedata.normalize('NFKD', name.lower())
    s = ''.join(GREEK.get(ch, ch) for ch in s.replace('ου', 'ou')).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^a-z0-9]+', ' ', s.replace('&', ' and '))
    base = re.sub(LEGAL + r'\s*$', '', s.strip()).strip()
    base = re.sub(LEGAL + r'\s*$', '', base).strip() or s.strip()
    # Long official names are cut at a word, e.g. the Greek "... anonymi etaireia ..." forms.
    words, out = base.split(), ''
    for w in words:
        if out and len(out) + len(w) + 1 > 50:
            break
        out = f'{out}-{w}' if out else w
    return out or 'company'


def load(name):
    try:
        return json.loads((SRC / 'js' / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def companies():
    data = load('companies.json')
    rows = [dict(zip(data['fields'], r)) for r in data['rows']]
    prices = load('company-prices.json') or {'fields': [], 'rows': {}}
    pi = {f: i for i, f in enumerate(prices['fields'])}
    for c in rows:
        if is_num(c.get('fscore')):
            c['fscoreKnown'] = len((c.get('fscoreTests') or '').replace('-', ''))
            c['quality'] = c['fscore'] / c['fscoreKnown'] if c['fscoreKnown'] else None
        q = prices['rows'].get(c['isin'] or '')
        if not q:
            continue
        c.update(price=q[pi['price']], change=q[pi['change']], priceVenue=q[pi['venue']], priceDate=q[pi['date']])
        # Same estimate as js/companies.js: shares in issue from data/shares.json or the annual
        # report (universe.py), skipped when price-to-sales (else P/E) says the units are mismatched.
        rev, rev_eur, eps, ni = c.get('revenue'), c.get('revenueEur'), c.get('eps'), c.get('netIncome')
        fx = rev_eur / rev if is_num(rev_eur) and is_num(rev) and rev else 1 if c.get('currency') == 'EUR' else None
        shares = c['shares'] if is_num(c.get('shares')) and c['shares'] > 0 else None
        if not shares:
            continue
        mcap = c['price'] * shares
        pe = c['price'] / (eps * fx) if fx and is_num(eps) and eps > 0 else None
        ps = mcap / rev_eur if is_num(rev_eur) and rev_eur > 0 else None
        if (not 0.02 <= ps <= 60) if ps is not None else (pe is not None and not 1 <= pe <= 150):
            continue
        c['mcap'] = mcap
        if pe is not None and 1 <= pe <= 1000:
            c['pe'] = pe
        if fx and is_num(c.get('dividendsPaid')) and c['dividendsPaid'] > 0:
            y = c['dividendsPaid'] * fx / mcap * 100
            if y < 25:
                c['divYield'] = y
    return data['updated'], prices.get('date'), rows


def add_history(rows):
    """Daily closes from data/prices-history.json (ISIN -> {date: close}), kept by scripts/data/prices.py."""
    try:
        hist = json.loads((SRC / 'data' / 'prices-history.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    for c in rows:
        if c.get('isin') in hist:
            c['history'] = sorted(hist[c['isin']].items())


def chart(c):
    """Price chart. Daily closes are embedded; js/stock-page.js adds the minute prices of the last
    trading day (/js/intraday/<ISIN>.json) for 4H and 1D, and 1D is shown first."""
    if not is_num(c.get('price')):
        return ''
    pts = c.get('history') or []
    w, h, pad = 600, 150, 4
    line = area = ''
    cap, chg_html, axis = 'Price', '', ['', '', '']
    if len(pts) >= 5:
        vals = [v for _, v in pts]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        xy = [(pad + i * (w - 2 * pad) / (len(pts) - 1), pad + (hi - v) * (h - 2 * pad) / span) for i, v in enumerate(vals)]
        line = ' '.join(f'{x:.1f},{y:.1f}' for x, y in xy)
        area = f'{pad},{h} {line} {w - pad},{h}'
        chg = (vals[-1] / vals[0] - 1) * 100
        cap = f'Price since {esc(pts[0][0])}'
        chg_html = f'{"+" if chg >= 0 else ""}{num(chg)}%'
        axis = [esc(pts[0][0]), f'low €{num(lo, 2)} · high €{num(hi, 2)}', esc(pts[-1][0])]
    days = (date.fromisoformat(pts[-1][0]) - date.fromisoformat(pts[0][0])).days if len(pts) >= 2 else 0
    buttons = ''.join(f'<button type="button" data-range="i{m}" hidden>{label}</button>' for label, m in (('4H', 240), ('1D', 0)))
    buttons += '<button type="button" data-range="w" hidden>1W</button>'
    buttons += ''.join(f'<button type="button" data-range="d{d}">{label}</button>'
                       for label, d in (('1W', 7), ('1M', 31), ('3M', 92), ('6M', 183), ('1Y', 366)) if days > d)
    if len(pts) >= 5:
        buttons += '<button type="button" data-range="d0" class="active">All</button>'
    return f'''<figure class="sp-chart" data-isin="{esc(c["isin"])}">
      <div class="sp-chart-ranges" role="group" aria-label="Chart period">{buttons}</div>
      <script type="application/json" class="sp-hist">{json.dumps(pts, separators=(',', ':'))}</script>
      <figcaption><span>{cap}</span><span>{chg_html}</span></figcaption>
      <svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" aria-label="Share price chart of {esc(c["name"])}">
        <polygon class="sp-chart-area" points="{area}"/><polyline class="sp-chart-line" points="{line}"/>
      </svg>
      <div class="sp-chart-axis"><span>{axis[0]}</span><span>{axis[1]}</span><span>{axis[2]}</span></div>
    </figure>'''


def add_signals(rows):
    ins, sh = load('insiders.json'), load('shorts.json')
    by_lei, by_isin = defaultdict(lambda: {'net': 0, 'buyers': set()}), defaultdict(lambda: {'pct': 0, 'funds': 0})
    if ins:
        since = datetime.now(timezone.utc) - timedelta(days=30)
        for t in ins['rows']:
            if not (t.get('lei') and t.get('signal')):
                continue
            try:
                when = datetime.fromisoformat(t['published'].replace('Z', '+00:00'))
            except (KeyError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < since:
                continue
            s = by_lei[t['lei']]
            s['name'] = t['issuer']
            if t['nature'] in ('Acquisition', 'Disposal') and t.get('valueEur'):
                pos = (t.get('position') or '').replace(chr(160), ' ').split(',')[0]
                pos = 'board member' if pos.startswith('Other member') else pos
                who = t['person'] + (f', {pos}' if pos else '')
                s.setdefault('events', []).append((t.get('date') or t['published'][:10],
                    f'{esc(who)} {"bought" if t["nature"] == "Acquisition" else "sold"} shares worth {eur(t["valueEur"])}',
                    f'/insiders?q={esc(t["issuer"])}'))
            if t['nature'] == 'Acquisition':
                s['net'] += t.get('valueEur') or 0
                s['buyers'].add(t['person'])
            elif t['nature'] == 'Disposal':
                s['net'] -= t.get('valueEur') or 0
    for p in (sh or {}).get('positions', []):
        s = by_isin[p['isin']]
        s['pct'] += p['pct']
        s['funds'] += p.get('c') != 'UK'
        was = f' (was {num(p["prev"], 2)}%)' if is_num(p.get('prev')) and p['prev'] != p['pct'] else ''
        holder = 'Funds together' if p.get('c') == 'UK' else p['holder'].title() if p['holder'].isupper() else p['holder']
        s.setdefault('events', []).append((p['date'], f'{esc(holder)} holds a short position of {num(p["pct"], 2)}%{was}',
                                           f'/shorts?q={esc(p["isin"])}'))
    for c in rows:
        if c['lei'] in by_lei:
            i = by_lei[c['lei']]
            c.update(insNet=i['net'], insBuyers=len(i['buyers']), insName=i['name'])
            c.setdefault('events', []).extend(i.get('events', []))
        elif ins and c['country'] == 'SE':
            c.update(insNet=0, insBuyers=0, insName=c['name'])
        if c.get('isin') in by_isin:
            c.update(shortPct=round(by_isin[c['isin']]['pct'], 2), shortFunds=by_isin[c['isin']]['funds'])
            c.setdefault('events', []).extend(by_isin[c['isin']].get('events', []))


def published_slugs():
    """The addresses already on the site ({lei: slug}). Lub's rule: an address never changes once
    published, so a company keeps its slug even when a same-named company arrives later."""
    url = f'{SITE}/js/stock-slugs.json?v={int(time.time())}'
    for attempt in range(3):   # the host sometimes times out for a minute (30 Oct 2026)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'FINILA page build'}), timeout=60) as r:
                return json.load(r)
        except Exception as e:
            err = e
            time.sleep(30 * (attempt + 1))
    if os.environ.get('FINILA_NEW_SLUGS') == '1':   # a first build, or a test without the live site
        return {}
    sys.exit(f'::error title=Company addresses::cannot read {url} ({err}); without it addresses could change. '
             'Set FINILA_NEW_SLUGS=1 only for a build that is not uploaded.')


def assign_slugs(rows, known):
    """Published addresses stay; a new company gets its clean name when free, else the name with its
    ticker (or country, then LEI) added. Returns every address ever given, dropped companies included,
    so a later company never takes over an old page."""
    used = {s: lei for lei, s in known.items()}
    groups = defaultdict(list)
    for c in rows:
        if c['lei'] in known:
            c['slug'] = known[c['lei']]
        else:
            groups[slugify(c['name'])].append(c)
    for base, cs in sorted(groups.items()):
        for c in sorted(cs, key=lambda c: c['lei']):
            tries = [base] if len(cs) == 1 else []
            ticker = re.sub(r'[^a-z0-9]+', '-', (c.get('ticker') or '').lower()).strip('-')
            if ticker:
                tries.append(f'{base}-{ticker}')
            tries += [f'{base}-{(c["country"] or "").lower()}', f'{base}-{c["lei"][:8].lower()}', f'{base}-{c["lei"].lower()}']
            c['slug'] = next(s for s in tries if s not in used)
            used[c['slug']] = c['lei']
    return {**known, **{c['lei']: c['slug'] for c in rows}}


HEADER = '''<header class="fh">
  <div class="fh-row">
    <a class="fh-logo" href="/" aria-label="FINILA home"><svg width="36" height="36" viewBox="0 0 64 64" aria-hidden="true"><rect x="8" y="6" width="12" height="52" rx="2" fill="#0F1B33"/><rect x="24" y="6" width="32" height="12" rx="2" fill="#F2B33D"/><rect x="24" y="24" width="22" height="12" rx="2" fill="#5B9BFF"/><rect x="24" y="42" width="12" height="12" rx="2" fill="#2C3A5A"/></svg>FINILA</a>
    <nav class="fh-nav" aria-label="Main menu">
      <a href="/screener" aria-current="page">Screener</a>
      <a href="/map">Maps</a>
      <a href="/groups">Groups</a>
      <a href="/insiders">Insiders</a>
      <a href="/shorts">Shorts</a>
      <a href="/property">Property</a>
      <a href="/rates">Rates</a>
      <a href="/assets">Assets</a>
      <a href="/archiv/">Archive</a>
    </nav>
  </div>
</header>
<script>(function(){var n=document.querySelector('.fh-nav'),a=n&&n.querySelector('[aria-current]');if(a)n.scrollLeft=a.offsetLeft-n.offsetLeft-16;})();</script>'''


def head(title, description, canonical, extra=''):
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(description)}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="FINILA">
  <meta property="og:title" content="{esc(title)}">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:url" content="{canonical}">
  <meta property="og:image" content="https://www.finila.com/css/og-image.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="icon" href="/css/logo.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/css/main.css">
  <link rel="stylesheet" href="/css/header.css">
  <link rel="stylesheet" href="/css/stock.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Sans:wght@500;700&display=swap" rel="stylesheet">
{extra}</head>
<body>

{HEADER}
'''


FOOTER = '''<footer class="sp-foot">
  <p>FINILA shows figures read automatically from official sources. They can contain errors and are not investment advice.</p>
  <p><a href="/stock/">All companies A-Z</a> · <a href="/screener">Stock screener</a> · <a href="/insiders">Insider trades</a> · <a href="/shorts">Short positions</a> · <a href="/assets">Where to put money</a> · <a href="/property">Property prices</a> · <a href="https://www.linkedin.com/company/finila" target="_blank" rel="noopener">LinkedIn</a></p>
  <p>Contact: <a href="mailto:info@finila.com">info@finila.com</a></p>
</footer>
'''


def events_html(c):
    """Company events FINILA records itself: managers' trades, short positions, the latest annual report."""
    ev = sorted(c.get('events', []), key=lambda e: e[0], reverse=True)[:12]
    nice = lambda d: date.fromisoformat(d).strftime('%-d %b %Y') if re.fullmatch(r'\d{4}-\d\d-\d\d', d or '') else d
    items = ''.join(f'<li><time datetime="{esc(d)}">{esc(nice(d))}</time><span>{text}</span><a href="{link}">Details</a></li>' for d, text, link in ev)
    if c.get('source') and c.get('origin') not in ('listing', 'wikidata', 'hand'):
        items += (f'<li><time>FY {esc((c.get("periodEnd") or "")[:4])}</time><span>Annual report for the year ending {esc(c["periodEnd"])}</span>'
                  f'<a href="{esc(c["source"])}" target="_blank" rel="noopener">Read</a></li>')
    if not items:
        return ''
    return f'''<section class="sp-events"><h2>Latest about {esc(c["name"])}</h2>
    <ul>{items}</ul>
    <p class="ins-note">From the official registers: managers’ trades (so far Sweden), net short positions of funds (NL, IT, SE, NO, UK) and annual reports.</p></section>'''


def page(c, peers, updated):
    cur = c.get('currency') or ''
    country = COUNTRIES.get(c['country'], c['country'] or '')
    who = f'{c["name"]} ({c["ticker"]})' if c.get('ticker') else c['name']
    # Issuers with no ESEF report on filings.xbrl.org (e.g. all of Germany) carry headline figures from Wikidata.
    wd = c.get('origin') == 'wikidata'
    # Listed issuers without any usable report: name, ISIN, ticker and sector only.
    listed_only = c.get('origin') == 'listing'
    link = f' · <a href="{esc(c["source"])}" target="_blank" rel="noopener">{"view on Wikidata" if wd else "view report"}</a>' if c.get('source') else ''
    hand = c.get('origin') == 'hand'
    if hand:
        link = f' · <a href="{esc(c["source"])}" target="_blank" rel="noopener">view figures</a>' if c.get('source') else ''
    src = (f'Source: headline figures from Wikidata (CC0), the company has no ESEF report on filings.xbrl.org yet{link}' if wd
           else f'Source: the company’s published annual accounts, entered by FINILA (no ESEF report on filings.xbrl.org){link}' if hand
           else f'Source: the company’s ESEF annual report via filings.xbrl.org{link}')
    fy = (c.get('periodEnd') or '')[:4]
    url = f'{SITE}/stock/{c["slug"]}'
    title = f'{who} stock: price, P/E, dividend and financials | FINILA'
    facts = []
    if is_num(c.get('price')):
        facts.append(f'share price €{num(c["price"], 2)}')
    if is_num(c.get('pe')):
        facts.append(f'P/E {num(c["pe"], 1)}')
    if is_num(c.get('divYield')):
        facts.append(f'dividend yield {num(c["divYield"], 1)}%')
    if is_num(c.get('revenueEur')):
        facts.append(f'revenue €{num(c["revenueEur"] / 1000, 2)}B')
    if is_num(c.get('netMargin')):
        facts.append(f'net margin {num(c["netMargin"])}%')
    desc = (f'{c["name"]}: {", ".join(facts)}. ' if facts else f'{c["name"]}: ') + \
        ('Share price, insider trades and short positions. ' if listed_only else
         f'Headline financials for {fy}, insider trades and short positions. ' if wd else
         f'Financials from the {fy} annual report, quality score, insider trades and short positions. ') + \
        ', '.join(x for x in [country, c.get('sector')] if x) + '.'
    ld = {'@context': 'https://schema.org', '@type': 'Corporation', 'name': c['name'], 'url': url,
          'leiCode': c['lei'], 'address': {'@type': 'PostalAddress', 'addressCountry': c['country']}}
    if c.get('ticker'):
        ld['tickerSymbol'] = c['ticker']
    if c.get('isin'):
        ld['identifier'] = {'@type': 'PropertyValue', 'propertyID': 'ISIN', 'value': c['isin']}
    crumbs = {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': 'Stocks', 'item': f'{SITE}/stock/'},
        {'@type': 'ListItem', 'position': 2, 'name': c['name'], 'item': url}]}
    extra = ''.join(f'  <script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>\n' for x in (ld, crumbs))
    row = lambda k, v: f'<tr><td>{k}</td><td>{v}</td></tr>'

    price = ''
    if is_num(c.get('price')):
        ch = c.get('change')
        price = f'''<div class="modal-price-row sp-price" data-isin="{esc(c["isin"])}">
      <span class="modal-price" data-f="price">€{num(c["price"], 2)}</span>
      <span class="modal-change {"pos" if is_num(ch) and ch >= 0 else "neg"}" data-f="change">{(("+" if ch >= 0 else "") + num(ch, 2) + "%") if is_num(ch) else ""}</span>
      <span class="real-badge" data-f="badge">Real price · {esc(c["priceVenue"])} · {esc(c["priceDate"])}</span>
    </div>'''
    else:
        price = '<p class="ins-note sp-noprice">No share price yet: this share has no trades in the free delayed data of Deutsche Börse (Xetra, Börse Frankfurt).</p>'
    key = [('Market value', f'≈ €{bn(c["mcap"])}' if is_num(c.get('mcap')) else '—'),
           ('P/E', num(c.get('pe'), 1)),
           ('Dividend yield', f'{num(c["divYield"], 1)}%' if is_num(c.get('divYield')) else '—'),
           ('Quality score', f'{c["fscore"]}/{c["fscoreKnown"]}' if is_num(c.get('fscore')) else '—'),
           ('Revenue', f'€{bn(c["revenueEur"])}' if is_num(c.get('revenueEur')) else '—'), ('Net margin', pct(c.get('netMargin')))]
    keyhtml = ''.join(f'<div class="sp-key"><span>{k}</span><b>{v}</b></div>' for k, v in key)

    tests = list(c.get('fscoreTests') or '')
    if is_num(c.get('fscore')):
        qual = f'''<div class="modal-section"><h2 class="sp-h">Quality score {c["fscore"]}/{c["fscoreKnown"]}</h2>
      <p class="ins-note">Piotroski F-score: tests of this year’s annual report against last year’s.</p><table class="kv-table">
      {"".join(row(t, '<span class="pos">✓ yes</span>' if i < len(tests) and tests[i] == '1' else '<span class="neg">✗ no</span>' if i < len(tests) and tests[i] == '0' else '<span class="ins-note">not in report</span>') for i, t in enumerate(F_TESTS))}
    </table></div>'''
    else:
        qual = f'<div class="modal-section"><h2 class="sp-h">Quality score</h2><p class="ins-note">{"Not calculated for banks and insurers." if c.get("financial") else "Needs the full annual report, which FINILA does not have for this company yet." if wd or listed_only else "Not enough figures in the last two annual reports."}</p></div>'

    ins = '—' if not is_num(c.get('insNet')) else '0' if c['insNet'] == 0 and not c['insBuyers'] else \
        f'<span class="{"pos" if c["insNet"] > 0 else "neg" if c["insNet"] < 0 else ""}">{"+" if c["insNet"] > 0 else ""}{eur(c["insNet"])}</span>'
    short = f'<span class="{"neg" if c["shortPct"] >= 2 else ""}">{num(c["shortPct"], 2)}%</span>' if is_num(c.get('shortPct')) else '—'
    links = []
    if is_num(c.get('insNet')):
        links.append(f'<a href="/insiders?q={esc(c["insName"])}">See insider trades</a>')
    else:
        links.append('Insider trades so far only for Sweden.')
    if is_num(c.get('shortPct')):
        links.append(f'<a href="/shorts?q={esc(c["isin"])}">See short positions</a>')
    signals = f'''<div class="modal-section"><h2 class="sp-h">Dividend, insiders, short sellers</h2><table class="kv-table">
      {row('Dividends / free cash flow', f'{num(c["payoutFcf"], 0)}%' if is_num(c.get('payoutFcf')) else '—')}
      {row('Dividends / net profit', f'{num(c["payoutNet"], 0)}%' if is_num(c.get('payoutNet')) else '—')}
      {row('Managers, net buying 30 days', ins)}
      {row('Managers buying, 30 days', c['insBuyers'] if is_num(c.get('insNet')) else '—')}
      {row('Shorted by funds', short + (f' <span class="ins-note">{c["shortFunds"]} {"fund" if c["shortFunds"] == 1 else "funds"}</span>' if c.get('shortFunds') else ''))}
    </table><p class="ins-note">{" · ".join(links)}</p></div>'''

    fin = '<p class="modal-report-src">Bank or insurer: EBITDA, free cash flow and net debt are not shown because they do not describe these businesses well.</p>' if c.get('financial') else ''
    nd = c.get('netDebtToEbitda')
    ndv = ('<span class="pos">net cash</span>' if nd < 0 else f'{num(nd, 2)}×') if is_num(nd) else '—'
    peer_html = ''.join(f'<li><a href="/stock/{p["slug"]}">{esc(p["name"])}</a><span>{"€" + bn(p["mcap"]) if is_num(p.get("mcap")) else esc(COUNTRIES.get(p["country"], p["country"]))}</span></li>' for p in peers)
    peer_title = f'Other {esc(c["sector"].lower())} companies' if c.get('sector') else f'Other companies from {esc(country)}'
    intro = f'{esc(c["name"])} is a{"n" if (c.get("sector") or "x")[0].lower() in "aeiou" else ""} {esc((c.get("sector") or "listed").lower())} company from {esc(country)}' + \
        (f', listed on {esc(c["exchange"])}' + (f' under the ticker {esc(c["ticker"])}' if c.get('ticker') else '') if c.get('exchange') else '') + \
        (f'. In the financial year ending {esc(c["periodEnd"])} it reported revenue of {bn(c["revenue"], cur)}' if is_num(c.get('revenue')) else '') + \
        (f' and net income of {bn(c["netIncome"], cur)}' if is_num(c.get('netIncome')) and is_num(c.get('revenue')) else '') + '.'

    if listed_only:
        figs = '''      <div class="modal-report-head">
        <h2 class="sp-h0">No annual report figures yet</h2>
      </div>
      <p class="ins-note">FINILA reads company figures from ESEF annual reports on filings.xbrl.org, which does not have this company’s report yet. The price, insider trades and short positions are real.</p>'''
    else:
        figs = f'''      <div class="modal-report-head">
        <h2 class="sp-h0">{"Headline figures from Wikidata" if wd else "Latest annual report"} · FY ending {esc(c["periodEnd"])} · {esc(cur)}</h2>
        <span class="real-badge">Real data</span>
      </div>
      <div class="modal-grid">
        <div class="modal-section"><h2 class="sp-h">Income</h2><table class="kv-table">
          {row('Revenue', bn(c.get('revenue'), cur))}{row('EBITDA', bn(c.get('ebitda'), cur))}{row('Operating profit', bn(c.get('operatingProfit'), cur))}{row('Net income', bn(c.get('netIncome'), cur))}{row('EPS', f'{num(c["eps"], 2)} {esc(cur)}' if is_num(c.get('eps')) else '—')}
        </table></div>
        <div class="modal-section"><h2 class="sp-h">Cash flow</h2><table class="kv-table">
          {row('Operating cash flow', bn(c.get('operatingCashFlow'), cur))}{row('Capex', bn(c.get('capex'), cur))}{row('Free cash flow', bn(c.get('fcf'), cur))}{row('Dividends paid', bn(c.get('dividendsPaid'), cur))}
        </table></div>
        <div class="modal-section"><h2 class="sp-h">Balance sheet</h2><table class="kv-table">
          {row('Total assets', bn(c.get('assets'), cur))}{row('Equity', bn(c.get('equity'), cur))}{row('Cash', bn(c.get('cash'), cur))}{row('Borrowings', bn(c.get('debt'), cur))}{row('Net debt', bn(c.get('netDebt'), cur))}
        </table></div>
        <div class="modal-section"><h2 class="sp-h">Ratios</h2><table class="kv-table">
          {row('ROE', pct(c.get('roe')))}{row('EBITDA margin', pct(c.get('ebitdaMargin')))}{row('Operating margin', pct(c.get('operatingMargin')))}{row('Net margin', pct(c.get('netMargin')))}{row('FCF margin', pct(c.get('fcfMargin')))}{row('Net debt / EBITDA', ndv)}{row('Revenue growth', pct(c.get('revenueGrowth')))}{row('Net income growth', pct(c.get('netIncomeGrowth')))}
        </table></div>
      </div>'''
    return head(title, desc, url, extra) + f'''
<main class="sp-wrap">
  <nav class="sp-crumbs" aria-label="Breadcrumb"><a href="/stock/">Stocks</a> › {esc(COUNTRIES.get(c["country"], c["country"]))} › {esc(c["name"])}</nav>
  <article class="modal-box sp-box">
    <div class="modal-header">
      <div>
        <h1 class="sp-title">{esc(c["name"])} {f'<span class="modal-name">{esc(c["ticker"])}{" · " + esc(c["exchange"]) if c.get("exchange") else ""}</span>' if c.get("ticker") else ""}</h1>
        <div class="modal-meta">{esc(country)}{" · " + esc(c["sector"]) if c.get("sector") else ""}{" · " + esc(c["industry"]) if c.get("industry") else ""} · ISIN {esc(c["isin"])} · LEI {esc(c["lei"])}</div>
      </div>
    </div>
    {price}
    {chart(c)}
    <div class="sp-keys">{keyhtml}</div>
    <p class="sp-intro">{intro}</p>
    <div class="modal-report">
{figs}
      <div class="modal-grid modal-scores">{qual}{signals}</div>
      {fin}
      <div class="modal-report-src">{"" if listed_only else src + ". "}Prices: Deutsche Börse delayed data. Market value is estimated from net income / EPS. Figures are read automatically and can contain errors; check the report before relying on them. Page updated {esc(updated)}.</div>
    </div>
    <p class="sp-cta"><a class="btn btn-primary" href="/screener?lei={esc(c["lei"])}">Compare in the stock screener</a></p>
  </article>
  {events_html(c)}
  {f'<section class="sp-peers"><h2>{peer_title}</h2><ul>{peer_html}</ul></section>' if peer_html else ''}
</main>
{FOOTER}<script src="/js/stock-page.js" defer></script>
<script src="/js/analytics.js" defer></script>
</body>
</html>
'''


def index_page(rows, updated):
    by_letter = defaultdict(list)
    for c in sorted(rows, key=lambda c: c['slug']):
        ch = c['slug'][0].upper()
        by_letter[ch if ch.isalpha() else '#'].append(c)
    letters = sorted(by_letter)
    nav = ' '.join(f'<a href="#l-{l}">{l}</a>' for l in letters)
    body = ''.join(
        f'<section id="l-{l}"><h2>{l}</h2><ul class="sp-az">' +
        ''.join(f'<li><a href="/stock/{c["slug"]}">{esc(c["name"])}</a> <span>{esc(c.get("ticker") or "")} · {esc(COUNTRIES.get(c["country"], c["country"]))}</span></li>' for c in by_letter[l]) +
        '</ul></section>' for l in letters)
    title = 'All European listed companies A-Z | FINILA'
    desc = f'{len(rows):,} European listed companies with share price, P/E, dividend, annual report figures, quality score, insider trades and short positions.'
    return head(title, desc, f'{SITE}/stock/') + f'''
<main class="sp-wrap">
  <h1 class="sp-title">All companies A-Z</h1>
  <p class="sp-intro">{len(rows):,} European listed companies. Updated {esc(updated)}. To filter and sort them, use the <a href="/screener">stock screener</a>.</p>
  <p class="sp-letters">{nav}</p>
  {body}
</main>
{FOOTER}<script src="/js/analytics.js" defer></script>
</body>
</html>
'''


def main():
    updated, price_day, rows = companies()
    add_signals(rows)
    add_history(rows)
    slugs = assign_slugs(rows, published_slugs())
    out = OUT / 'stock'
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('*.html'):
        old.unlink()
    by_sector = defaultdict(list)
    for c in rows:
        by_sector[c.get('sector')].append(c)
    for v in by_sector.values():
        v.sort(key=lambda c: -(c.get('mcap') or c.get('revenueEur') or 0))
    stamp = f'{updated}' + (f', prices of {price_day}' if price_day else '')
    for c in rows:
        same = by_sector[c.get('sector')] if c.get('sector') else [p for p in rows if p['country'] == c['country']]
        peers = [p for p in same if p is not c][:8]
        (out / f'{c["slug"]}.html').write_text(page(c, peers, stamp), encoding='utf-8')
    (out / 'index.html').write_text(index_page(rows, updated), encoding='utf-8')
    (OUT / 'js').mkdir(exist_ok=True)
    (OUT / 'js' / 'stock-slugs.json').write_text(json.dumps(slugs, separators=(',', ':')), encoding='utf-8')
    today = date.today().isoformat()
    # The old articles (archive-site/, served under their original addresses) keep their search ranking.
    old = sorted(f'{SITE}/{p.parent.relative_to(SRC / "archive-site").as_posix()}/' for p in (SRC / 'archive-site').glob('*/**/index.html'))
    # Property pages by country (scripts/build-property-pages.py names them the same way).
    housing = json.loads((SRC / 'js' / 'housing.json').read_text()) if (SRC / 'js' / 'housing.json').exists() else {'countries': {}}
    prop = ['property/undervalued'] + sorted(f'property/{re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")}'
                                            for c in housing['countries'].values() if not c.get('agg'))
    urls = [f'{SITE}/{p}' for p in PAGES + prop] + [f'{SITE}/stock/{c["slug"]}' for c in sorted(rows, key=lambda c: c['slug'])] + old
    (OUT / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
                                     ''.join(f'  <url><loc>{esc(u)}</loc><lastmod>{today}</lastmod></url>\n' for u in urls) + '</urlset>\n', encoding='utf-8')
    print(f'Built {len(rows)} company pages in {out}, sitemap with {len(urls)} addresses.')


if __name__ == '__main__':
    main()
