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
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SITE = 'https://www.finila.com'
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else SRC)
# Pages listed in the sitemap besides the company pages.
PAGES = ['', 'screener', 'map', 'groups', 'insiders', 'shorts', 'property', 'rates', 'assets', 'stock/', 'stock/methodology']
# Market Radar: the overview, the election cycle and one page per indicator in js/radar.json.
try:
    PAGES += ['radar', 'radar/election-cycle', 'radar/money-in-the-system'] + ['radar/' + r['id'] for r in json.loads((SRC / 'js' / 'radar.json').read_text())['indicators']]
except (OSError, ValueError, KeyError):
    pass

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


# US test companies (scripts/data/sec_preview.py, Lub 2026-11-05): a page each from their SEC 10-K figures,
# not public: noindex, out of the sitemap, the stock list, the slugs file and other companies' peers and medians.
try:
    PREVIEW = json.loads((SRC / 'data' / 'preview' / 'companies.json').read_text(encoding='utf-8'))
except (OSError, ValueError):
    PREVIEW = {}


def companies():
    data = load('companies.json')
    rows = [dict(zip(data['fields'], r)) for r in data['rows']] + [dict(r, preview=True) for r in PREVIEW.get('rows', [])]
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



def add_history_figures(rows):
    """data/history.json (scripts/data/history.py): five years of annual figures per LEI. Adds the
    multi-year figures, and the ratios that need lines the daily data does not carry (gross profit,
    current assets, interest), only from the same report the page's other figures come from."""
    try:
        hist = json.loads((SRC / 'data' / 'history.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    hist.update(PREVIEW.get('history') or {})
    # Companies without ESEF reports that file 10-K / 20-F reports with the SEC (scripts/data/sec_history.py).
    try:
        for lei, h in json.loads((SRC / 'data' / 'sec-history.json').read_text(encoding='utf-8')).items():
            if len((hist.get(lei) or {}).get('years', {})) < len(h['years']):
                hist[lei] = h
    except (OSError, ValueError):
        pass
    for c in rows:
        h = hist.get(c['lei'])
        if not h or c.get('origin') not in (None, 'hand') or h['currency'] != c.get('currency'):
            continue
        years = {y: dict(v) for y, v in h['years'].items()}
        # Figures copied by hand from a newer annual report than the ESEF archive has (LVMH, bpost ...):
        # that year joins the official years from the ESEF reports (Lub 2026-11-06: same page as Novo).
        py = (c.get('periodEnd') or '')[:4]
        if c.get('origin') == 'hand' and py and py not in years and py > max(years):
            # Only lines defined the same way everywhere: debt, cash and EBITDA are drawn differently by
            # data sites than in the report (LVMH: net debt 2.5bn in ESEF 2024, 23bn with leases by hand).
            years[py] = {k: c[k] for k in ('revenue', 'operatingProfit', 'netIncome', 'operatingCashFlow',
                                           'dividendsPaid', 'equity', 'assets', 'eps') if is_num(c.get(k))}
            years[py].update(end=c['periodEnd'], source=c.get('source'), hand=True)
        # Share splits: an old report states EPS on the old share count (Novo Nordisk 2:1 in 2023).
        # Where profit / EPS gives a share count about 2, 3, 4, 5, 10 or 20 times off the newest
        # year, the old EPS is put on today's share basis, as companies restate it themselves.
        ny = years[max(years)]
        base = ny['netIncome'] / ny['eps'] if is_num(ny.get('netIncome')) and is_num(ny.get('eps')) and ny['eps'] and ny['netIncome'] * ny['eps'] > 0 else None
        for y, v in years.items():
            if base and is_num(v.get('netIncome')) and is_num(v.get('eps')) and v['eps'] and v['netIncome'] * v['eps'] > 0:
                r = base / (v['netIncome'] / v['eps'])
                for f in (2, 3, 4, 5, 8, 10, 20, 25, 50, 100):
                    for ff in (f, 1 / f):
                        if abs(r / ff - 1) < 0.06:
                            v['eps'] = v['eps'] / ff
                            v['epsSplit'] = round(ff, 3)
                            if is_num(v.get('sharesTagged')):
                                v['sharesTagged'] = v['sharesTagged'] * ff
        last = years.get((c.get('periodEnd') or '')[:4])
        if not last or last.get('end') != c.get('periodEnd'):
            last = None
        c['years'] = years
        ys = sorted(years)
        # Growth is measured from FY2021 when possible: 2020 was a covid year, a low base for most companies.
        start = next((y for y in ys if y >= '2021'), ys[0]) if int(ys[-1]) - int(next((y for y in ys if y >= '2021'), ys[0])) >= 3 else ys[0]
        first, newest = years[start], years[ys[-1]]
        n = int(ys[-1]) - int(start)
        c['cagrFrom'] = start

        def cagr(k):
            a, b = first.get(k), newest.get(k)
            if n >= 3 and is_num(a) and is_num(b) and a > 0 and b > 0:
                return ((b / a) ** (1 / n) - 1) * 100
        c['revenueCagr'], c['epsCagr'], c['cagrYears'] = cagr('revenue'), cagr('eps'), n
        sh = [years[y].get('sharesTagged') or (years[y]['netIncome'] / years[y]['eps']
              if is_num(years[y].get('netIncome')) and is_num(years[y].get('eps')) and abs(years[y]['eps']) > 1e-6
              and years[y]['netIncome'] * years[y]['eps'] > 0 else None) for y in ys]
        s0 = sh[ys.index(start)]
        if n >= 3 and is_num(s0) and is_num(sh[-1]) and s0 > 0:
            ch = (sh[-1] / s0 - 1) * 100
            if -60 < ch < 300:
                c['sharesChange'] = ch
        div_years = 0
        for y in reversed(ys):
            if (years[y].get('dividendsPaid') or 0) > 0:
                div_years += 1
            else:
                break
        c['divYears'], c['yearsKnown'] = div_years, len(ys)
        if is_num(c.get('pe')) and is_num(c.get('epsCagr')) and c['epsCagr'] >= 1:
            c['pegPast'] = c['pe'] / c['epsCagr']
        if not last:
            continue
        rev = last.get('revenue')
        if is_num(last.get('gross')) and is_num(rev) and rev > 0 and 0 < last['gross'] <= rev:
            c['grossMargin'] = last['gross'] / rev * 100
        if is_num(last.get('curAssets')) and is_num(last.get('curLiab')) and last['curLiab'] > 0:
            c['currentRatio'] = last['curAssets'] / last['curLiab']
        op = last.get('operatingProfit')
        if not c.get('financial') and is_num(op) and is_num(last.get('interest')) and last['interest'] > 0:
            c['interestCover'] = op / last['interest']
        # ROIC: operating profit after tax / (equity + net debt), tax rate from the report (15-35%).
        pbt, tax = last.get('pbt'), last.get('tax')
        rate = tax / pbt if is_num(pbt) and is_num(tax) and pbt > 0 else None
        rate = min(max(rate, 0.15), 0.35) if rate is not None else 0.25
        cap = (c.get('equity') or 0) + (c.get('netDebt') or 0)
        if not c.get('financial') and is_num(op) and is_num(c.get('equity')) and c['equity'] > 0 and cap > 0:
            c['roic'] = op * (1 - rate) / cap * 100
        if is_num(last.get('leases')) and last['leases'] > 0:
            c['leases'] = last['leases']
        if is_num(last.get('minorities')) and last['minorities'] > 0:
            c['minorities'] = last['minorities']
        for k, base in (('bvps', 'equity'), ('fcfps', 'fcf')):
            shares = c.get('shares')
            if is_num(c.get(base)) and is_num(shares) and shares > 0:
                c[k] = c[base] / shares


RESTATED = '<sup title="Restated by the next annual report">R</sup>'



def summary_html(c, med, intro):
    """In short: what a buyer would want to know first, in plain sentences built from the figures."""
    out = [intro]
    sec = esc(med.get('label') or (c.get('sector') or '').lower())
    yrs = c.get('cagrYears')
    if is_num(c.get('revenueCagr')) and yrs and not c.get('financial'):
        r = c['revenueCagr']
        out.append(f'Revenue grew {num(r)}% a year over the last {yrs} years.' if r >= 0.5 else
                   f'Revenue shrank {num(-r)}% a year over the last {yrs} years.' if r <= -0.5 else
                   f'Revenue was flat over the last {yrs} years.')
    ve, vm = c.get('evEbitda'), med.get('evEbitda')
    pe, pm = c.get('pe'), med.get('pe')
    if is_num(ve) and is_num(vm):
        word = 'below' if ve <= vm * 0.85 else 'above' if ve >= vm * 1.15 else 'close to'
        out.append(f'At today’s price the whole company is valued at {num(ve)}× EBITDA, {word} the European {sec} median of {num(vm)}×.')
    elif is_num(pe) and is_num(pm):
        word = 'below' if pe <= pm * 0.85 else 'above' if pe >= pm * 1.15 else 'close to'
        out.append(f'The share price is {num(pe)}× last year’s profit per share (P/E), {word} the European {sec} median of {num(pm)}.')
    em, mm = c.get('ebitdaMargin'), med.get('ebitdaMargin')
    nd = c.get('netDebtToEbitda')
    bits = []
    if is_num(em) and is_num(mm) and not c.get('financial'):
        bits.append(f'the EBITDA margin of {num(em)}% is {"above" if em > mm * 1.1 else "below" if em < mm * 0.9 else "close to"} the peer median of {num(mm)}%')
    if is_num(nd) and not c.get('financial'):
        pm = med.get('netDebtToEbitda')
        bits.append('it has more cash than debt' if nd < 0 else f'net debt is {num(nd, 1)}× EBITDA' + (f' (peer median {num(pm, 1)}×)' if is_num(pm) else ''))
    if bits:
        s = ', '.join(bits)
        out.append(s[0].upper() + s[1:] + '.')
    if len(out) < 2:
        return about_html(c) + f'<p class="sp-intro">{intro}</p>'
    return f'''<section class="sp-card sp-summary" id="summary"><h2>In short</h2>
      {about_html(c)}
      <p>{" ".join(out)}</p>
      <p class="sp-sub sp-after">Written by FINILA from the annual report{"s" if yrs else ""} and today’s price {chip("c")}</p></section>'''


HORIZON = 3   # years ahead in the scenarios and the calculator (Lub, 2026-11-04)


def scenarios_html(c, med, g_past, x_med):
    """Three scenarios from real ranges (the company's own margins of the last years, the sector's spread of
    multiples) and what today's price assumes. EUR millions, shares in millions, HORIZON years."""
    rev, nd, sh, price, m_now = c['revenueEur'], c['claimsEur'], c['shares'], c['price'], c['ebitdaMargin']
    years = c.get('years') or {}
    margins = [v['ebitda'] / v['revenue'] * 100 for v in years.values()
               if is_num(v.get('ebitda')) and is_num(v.get('revenue')) and v['revenue'] > 0 and v['ebitda'] > 0]
    lo_m, hi_m = (min(margins), max(margins)) if len(margins) >= 3 else (max(m_now - 3, 1), m_now)
    p25, p75 = med.get('evEbitdaP25'), med.get('evEbitdaP75')
    if not (is_num(p25) and is_num(p75)):
        return ''
    n = HORIZON
    val = lambda g, m, x: (rev * (1 + g / 100) ** n * m / 100 * x - nd) / sh
    g_sec = med.get('revenueGrowth') if is_num(med.get('revenueGrowth')) else 0
    rows = [('Weak', min(g_past, 0), lo_m, p25, 'no growth (or the past decline), the lowest margin of the last years, a multiple like the cheapest quarter of the sector'),
            ('Middle', g_sec, m_now, x_med, 'growth like the typical company of the sector last year, today’s margin, the sector median multiple'),
            ('Strong', max(g_past, g_sec), hi_m, p75, 'growth like the company’s own past, the best margin of the last years, a multiple like the dearest quarter of the sector')]
    trs = ''
    for name, g, m, x, what in rows:
        v = val(g, m, x)
        ch = f'<span class="{"pos" if v >= price else "neg"}">{sign((v / price - 1) * 100)}%</span>' if v > 0 else '<span class="neg">debt above value</span>'
        trs += (f'<tr><td><b>{name}</b><small>{what}</small></td><td>{sign(g)}%</td><td>{num(m)}%</td><td>{num(x)}×</td>'
                f'<td><b>{"€" + num(v, 2) if v > 0 else "€0"}</b></td><td>{ch}</td></tr>')
    # What today's price assumes: growth needed for today's company value at the sector median multiple, today's margin.
    ev_now = c['mcap'] + nd
    need = ((ev_now / (rev * m_now / 100 * x_med)) ** (1 / n) - 1) * 100 if ev_now > 0 else None
    implied = ''
    if is_num(need) and -30 < need < 60:
        past = f'; the last {c["cagrYears"]} years it grew {sign(c["revenueCagr"])}% a year' if is_num(c.get('revenueCagr')) else ''
        implied = (f'<p class="sp-wi-implied"><b>What today’s price assumes:</b> revenue {"growing" if need >= 0 else "shrinking"} {num(abs(need))}% a year '
                   f'for {n} years at today’s margin, if the company is then valued at the sector median of {num(x_med)}× EBITDA{past}.</p>')
    return f'''{implied}
      <div class="sp-scroll"><table class="sp-table sp-scen"><tr><th>In {n} years</th><th>Growth a year</th><th>EBITDA margin</th><th>EV/EBITDA</th><th>Price per share</th><th>vs today €{num(price, 2)}</th></tr>{trs}</table></div>'''


def guided_growth(c):
    """Revenue growth the company itself expects for the current year, from its outlook sentence
    ("sales growth for 2026 is expected to be 0% to -6%"): the midpoint, and the sentence."""
    q = c.get('quotes') or {}
    items = ((c.get('latestReport') or {}).get('outlook') or []) + q.get('items', [])   # the newest report first
    pat = re.compile(r'\b(sales|revenue|turnover)\b[^.%]{0,60}?\bgrowth\b[^.%]{0,80}?(?<![\d.])(-?\+?\d{1,2}(?:\.\d)?)\s*%'
                     r'(?:\s*(?:to|and|-|–)\s*(-?\+?\d{1,2}(?:\.\d)?)\s*%)?', re.I)
    for it in items:
        if it['kind'] != 'outlook':
            continue
        m = pat.search(it['text'])
        if m:
            a = float(m.group(2))
            b = float(m.group(3)) if m.group(3) else a
            if -40 <= a <= 60 and -40 <= b <= 60:
                return round((a + b) / 2, 1), it['text']
    return None


def whatif_html(c, med):
    """What if the company grows: the visitor's own assumptions -> price per share. js/stock-page.js computes it."""
    need = ('revenueEur', 'claimsEur', 'mcap', 'price', 'shares', 'ebitdaMargin')
    if c.get('financial') or not all(is_num(c.get(k)) for k in need) or c['revenueEur'] <= 0 or c['ebitdaMargin'] <= 0 or c['shares'] <= 0:
        return ''
    g_past = c.get('revenueCagr') if is_num(c.get('revenueCagr')) else c.get('revenueGrowth')
    g_past = round(min(max(g_past, -10), 30), 1) if is_num(g_past) else 0
    # The sliders start at the company's own record in the years-of-results table: its revenue growth a
    # year (CAGR) and its average EBITDA margin over those years, 3 years ahead (Lub, 2026-11-04).
    years = c.get('years') or {}
    ms = [v['ebitda'] / v['revenue'] * 100 for v in years.values()
          if is_num(v.get('ebitda')) and is_num(v.get('revenue')) and v['revenue'] > 0 and v['ebitda'] > 0]
    # Growth over the 3 years the way analysts build a base case: year 1 from the company's own outlook
    # (else its growth last year), years 2-3 the sector's typical growth, since growth far from the
    # sector rarely lasts. The slider starts at the average of those 3 years.
    guided = guided_growth(c)
    g_sec = med.get('revenueGrowth') if is_num(med.get('revenueGrowth')) else None
    g1, g1_src = (guided[0], 'the company’s own outlook') if guided else \
        (c['revenueGrowth'], 'its growth last year') if is_num(c.get('revenueGrowth')) else (g_past, 'its past growth')
    g1 = min(max(g1, -30), 60)
    if g_sec is not None:
        avg = (((1 + g1 / 100) * (1 + g_sec / 100) ** (HORIZON - 1)) ** (1 / HORIZON) - 1) * 100
        g = round(min(max(avg, -10), 30), 1)
        g_src = f'year 1 {g1_src} ({sign(g1)}%), then the sector median ({sign(g_sec)}%)'
    else:
        g, g_src = round(min(max(g1, -10), 30), 1), g1_src
    # Margin: the latest year, which already holds the trend (Novo 52% -> 48%). Cyclical sectors (energy,
    # materials) swing with prices, so their start is the average over the years shown (mid-cycle margin).
    cyclical = (c.get('sector') or '') in ('Energy', 'Materials')
    if cyclical and len(ms) >= 3:
        m0, m_src = round(sum(ms) / len(ms), 1), f'average of {len(ms)} years, a cyclical sector (results table)'
    else:
        m0, m_src = round(c['ebitdaMargin'], 1), 'last year'
    # What the company actually did in the years shown, so a visitor sees how far the start is from its record.
    ys_ = sorted(years)
    gr = [(years[b]['revenue'] / years[a]['revenue'] - 1) * 100 for a, b in zip(ys_, ys_[1:])
          if is_num(years[a].get('revenue')) and is_num(years[b].get('revenue')) and years[a]['revenue'] > 0]
    g_rng = f'. FY {ys_[0]}–{ys_[-1]}: from {sign(min(gr))}% to {sign(max(gr))}% a year' if len(gr) >= 3 and not c.get('financial') else ''
    m_rng = f'. FY {ys_[0]}–{ys_[-1]}: {num(min(ms))}% to {num(max(ms))}%' if len(ms) >= 3 else ''
    x = med.get('evEbitda') if is_num(med.get('evEbitda')) else c.get('evEbitda')
    x_src = f'median of {esc(med.get("label") or (c.get("sector") or "").lower())} companies' if is_num(med.get('evEbitda')) else 'today’s multiple'
    if not is_num(x):
        return ''
    scen = scenarios_html(c, med, g_past, x)
    data = {'rev': round(c['revenueEur'], 1), 'nd': round(c['claimsEur'], 1), 'shares': c['shares'], 'price': c['price'],
            'g': g, 'm': m0, 'x': round(x, 1), 'y': HORIZON}
    return f'''<section class="sp-card sp-whatif" id="whatif" data-whatif='{json.dumps(data)}'>
      <h2>What could the share be worth?</h2>
      <p class="sp-sub">Three scenarios from real ranges: the company’s own margins of the last years and the spread of multiples in its sector.</p>
      {scen}
      <h3 class="sp-wi-h">Try your own</h3>
      <p class="sp-wi-shared" hidden>Values set by the person who shared this link, not by FINILA. <button type="button" data-a="reset">Reset to FINILA start</button></p>
      <div class="sp-wi-inputs">
        <label><span>Revenue growth a year <b data-o="g"></b></span><input type="range" data-i="g" min="-10" max="30" step="0.5" value="{g}"><small>Start: {g_src} ({sign(g)}%){g_rng}</small></label>
        <label><span>EBITDA margin <b data-o="m"></b></span><input type="range" data-i="m" min="1" max="70" step="0.5" value="{data["m"]}"><small>Start: {m_src} ({num(data["m"])}%){m_rng}</small></label>
        <label><span>EV / EBITDA at the end <b data-o="x"></b></span><input type="range" data-i="x" min="2" max="40" step="0.5" value="{data["x"]}"><small>Start: {x_src} ({num(x)}×)</small></label>
        <label><span>Years <b data-o="y"></b></span><input type="range" data-i="y" min="1" max="10" step="1" value="{HORIZON}"><small>Start: {HORIZON} years ahead</small></label>
      </div>
      <div class="sp-wi-out">
        <div><span>Revenue then</span><b data-o="rev"></b></div>
        <div><span>EBITDA then</span><b data-o="ebitda"></b></div>
        <div><span>Company value (EV)</span><b data-o="ev"></b></div>
        <div class="sp-wi-main"><span>Price per share</span><b data-o="pps"></b><small data-o="ret"></small></div>
      </div>
      <p class="sp-wi-share"><button type="button" data-a="copy">Copy link to this calculation</button> <span data-o="copied" class="ins-note"></span></p>
      <p class="sp-sub sp-after">Price per share = company value minus today’s net debt, leases and minorities (€{bn(c["claimsEur"])}), divided by today’s {num(c["shares"], 1)}M shares; dividends not counted. A calculation from your assumptions, not a forecast or advice from FINILA.</p></section>'''


def history_html(c):
    """Five years of results: one column per financial year, each from its own (or the restating) report,
    with the change on the year before and small bar charts of the main lines."""
    years = c.get('years')
    if not years or len(years) < 2:
        return ''
    ys = sorted(years)
    cur = esc(c.get('currency') or '')
    big = max((abs(years[y].get('revenue') or years[y].get('assets') or 0) for y in ys), default=0) >= 5000
    unit = f'billions of {cur}' if big else f'millions of {cur}'
    amt = lambda v: '—' if not is_num(v) else num(v / 1000, 2) if big else num(v, 1 if abs(v) < 100 else 0)
    ratio = lambda a, b: a / b * 100 if is_num(a) and is_num(b) and b > 0 else None
    wc = lambda y: y['inventories'] + y['receivables'] - y['payables'] if all(is_num(y.get(k)) for k in ('inventories', 'receivables', 'payables')) else None
    # (label, value of a year, shown as, change on the year before: 'pct' for amounts, 'pp' for margins, None)
    lines = [
        ('Revenue', lambda y: y.get('revenue'), amt, 'pct'),
        ('EBITDA', lambda y: y.get('ebitda'), amt, 'pct'),
        ('EBITDA margin', lambda y: ratio(y.get('ebitda'), y.get('revenue')), pc, 'pp'),
        ('Operating profit', lambda y: y.get('operatingProfit'), amt, 'pct'),
        ('Net income', lambda y: y.get('netIncome'), amt, 'pct'),
        ('Net margin', lambda y: ratio(y.get('netIncome'), y.get('revenue')), pc, 'pp'),
        ('EPS', lambda y: y.get('eps'), lambda v: num(v, 2), 'pct'),
        ('Operating cash flow', lambda y: y.get('operatingCashFlow'), amt, 'pct'),
        ('Capex', lambda y: y.get('capex'), amt, 'pctn'),
        ('Free cash flow', lambda y: y.get('fcf'), amt, 'pct'),
        ('Dividends paid', lambda y: y.get('dividendsPaid'), amt, 'pct'),
        ('Net debt', lambda y: y.get('netDebt'), amt, None),
        ('Equity', lambda y: y.get('equity'), amt, 'pct'),
        ('Working capital', wc, amt, None),
    ]
    if c.get('financial'):   # a bank's 'revenue' is not comparable from year to year
        lines = [l for l in lines if l[0] in ('Net income', 'EPS', 'Dividends paid', 'Equity')]

    def change(a, b, kind):
        if kind in ('pct', 'pctn') and is_num(a) and is_num(b) and a > 0 and b > 0:
            d = (b / a - 1) * 100
            if abs(d) < 1000:   # capex up or down is neither good nor bad: grey
                return f'<small class="{"" if kind == "pctn" else "pos" if d >= 0 else "neg"}">{sign(d)}%</small>'
        if kind == 'pp' and is_num(a) and is_num(b):
            d = b - a
            return f'<small class="{"pos" if d >= 0 else "neg"}">{sign(d)} pp</small>'
        return ''
    body = ''
    for label, get, fmt, kind in lines:
        vals = [get(years[y]) for y in ys]
        if sum(is_num(v) for v in vals) < 2:
            continue
        cells = ''.join(f'<td>{fmt(v) if is_num(v) else "—"}{change(vals[i - 1], v, kind) if i else ""}</td>' for i, v in enumerate(vals))
        body += f'<tr><td>{label}</td>{cells}</tr>'
    if not body:
        return ''
    head_ = ''.join(
        f'<th><a href="{esc(years[y]["source"])}" target="_blank" rel="noopener" title="Open the annual report">FY {y}</a>'
        f'{RESTATED if years[y].get("restated") else ""}</th>'
        if years[y].get('source') else f'<th>FY {y}</th>' for y in ys)

    def bars(label, key):
        vals = [years[y].get(key) for y in ys]
        if sum(is_num(v) for v in vals) < 3:
            return ''
        top = max(abs(v) for v in vals if is_num(v)) or 1
        neg = any(is_num(v) and v < 0 for v in vals)
        w, h, gap = 200, 70, 6
        bw = (w - gap * (len(ys) - 1)) / len(ys)
        base = h / 2 if neg else h
        scale = (h / 2 if neg else h) / top
        rects = ''
        for i, v in enumerate(vals):
            x = i * (bw + gap)
            if not is_num(v):
                continue
            bh = max(1, abs(v) * scale)
            y0 = base - bh if v >= 0 else base
            cls = 'sp-bar-neg' if v < 0 else 'sp-bar-last' if i == len(vals) - 1 else ''
            rects += f'<rect x="{x:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" class="{cls}"><title>FY {ys[i]}: {amt(v)}</title></rect>'
        li = next((i for i in range(len(vals) - 1, -1, -1) if is_num(vals[i])), None)
        last = vals[li] if li is not None else None
        # The newest value shown is labelled with its year when the newest year lacks this line.
        when = f' <small>FY {ys[li]}</small>' if li is not None and li != len(vals) - 1 else ''
        return (f'<figure class="sp-mini"><figcaption><span>{label}</span><b>{amt(last)}{when}</b></figcaption>'
                f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" aria-label="{label}, FY {ys[0]} to {ys[-1]}">{rects}</svg>'
                f'<div class="sp-mini-axis"><span>FY {ys[0]}</span><span>FY {ys[-1]}</span></div></figure>')
    keys = [('Net income', 'netIncome'), ('Equity', 'equity'), ('Dividends paid', 'dividendsPaid')] if c.get('financial') else \
        [('Revenue', 'revenue'), ('EBITDA', 'ebitda'), ('Net income', 'netIncome'), ('Free cash flow', 'fcf')]
    minis = ''.join(bars(l, k) for l, k in keys)
    tiles = []
    if is_num(c.get('revenueCagr')) and not c.get('financial'):
        tiles.append(('Revenue growth a year', f'{sign(c["revenueCagr"])}%', f'FY {c["cagrFrom"]}–{ys[-1]} (CAGR)'))
    if is_num(c.get('epsCagr')):
        tiles.append(('EPS growth a year', f'{sign(c["epsCagr"])}%', f'FY {c["cagrFrom"]}–{ys[-1]} (CAGR)'))
    if is_num(c.get('sharesChange')):
        tiles.append(('Shares in issue', f'{sign(c["sharesChange"])}%', f'since FY {c["cagrFrom"]}' + (' (dilution)' if c['sharesChange'] > 2 else ' (buybacks)' if c['sharesChange'] < -2 else '')))
    if c.get('divYears'):
        tiles.append(('Dividend paid', f'{c["divYears"]} of {c["yearsKnown"]} years', 'in a row, newest first'))
    tiles_html = ''.join(f'<div><span>{a}</span><b>{b}</b><small>{d}</small></div>' for a, b, d in tiles)
    restated = any(years[y].get('restated') for y in ys)
    return f'''<section class="sp-card" id="history"><h2>{len(ys)} years of results</h2>
      <p class="sp-sub">Each year from the company’s {"10-K annual report filed with the SEC" if c.get("sec") else "annual report filed with the SEC (10-K or 20-F)" if all("sec.gov" in (v.get("source") or "") for v in c["years"].values() if not v.get("hand")) else "ESEF annual report"}{"; FY " + ", FY ".join(y for y, v in sorted(c["years"].items()) if v.get("hand")) + " copied by hand from its published accounts, as ESEF does not have it yet" if any(v.get("hand") for v in c["years"].values()) else ""}; click a year to open it {chip("r", "Reports")}. Amounts in {unit}; small figures: change on the year before.</p>
      {f'<div class="sp-tiles">{tiles_html}</div>' if tiles_html else ''}
      {f'<div class="sp-minis">{minis}</div>' if minis else ''}
      <div class="sp-scroll"><table class="sp-table sp-hist-table"><tr><th></th>{head_}</tr>{body}</table></div>
      <p class="sp-sub sp-after">{"R = restated: the next annual report changed this year’s figures (e.g. a business sold), the newer figures are shown. " if restated else ""}pp = percentage points. Working capital = inventories + trade receivables − trade payables.</p></section>'''

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


# Valuation figures a buyer of the whole company looks at, in EUR millions (report figures are
# converted with the same rate as revenueEur). Only for companies with a market value.
VAL_KEYS = ['evEbitda', 'evEbit', 'evSales', 'pe', 'ps', 'pb', 'pfcf', 'fcfYield', 'divYield', 'ebitdaMargin',
            'operatingMargin', 'netMargin', 'revenueGrowth', 'netDebtToEbitda', 'roe', 'roa', 'fcfMargin', 'debtEquity']


def add_valuation(c):
    rev, rev_eur = c.get('revenue'), c.get('revenueEur')
    fx = rev_eur / rev if is_num(rev_eur) and is_num(rev) and rev else 1 if c.get('currency') == 'EUR' else None
    c['fx'] = fx
    if is_num(c.get('netIncome')) and is_num(c.get('assets')) and c['assets'] > 0:
        c['roa'] = c['netIncome'] / c['assets'] * 100
    if is_num(c.get('debt')) and is_num(c.get('equity')) and c['equity'] > 0 and not c.get('financial'):
        c['debtEquity'] = c['debt'] / c['equity']
    if not (fx and is_num(c.get('mcap'))):
        return
    mcap = c['mcap']
    if is_num(rev_eur) and rev_eur > 0:
        c['ps'] = mcap / rev_eur
    if is_num(c.get('equity')) and c['equity'] > 0:
        pb = mcap / (c['equity'] * fx)
        if 0.05 <= pb <= 100:
            c['pb'] = pb
    if is_num(c.get('fcf')):
        y = c['fcf'] * fx / mcap * 100
        if -50 <= y <= 50:
            c['fcfYield'] = y
            if c['fcf'] > 0 and y > 0.2:
                c['pfcf'] = 100 / y
    if c.get('financial'):
        return   # banks and insurers: debt is their raw material, EV multiples say nothing
    if is_num(c.get('netDebt')):
        c['netDebtEur'] = c['netDebt'] * fx
        # Enterprise value as analysts count it: shares + net debt + lease liabilities (IFRS 16: EBITDA is
        # before lease costs) + minority interests (EBITDA includes 100% of subsidiaries).
        c['leasesEur'] = (c.get('leases') or 0) * fx
        c['minoritiesEur'] = (c.get('minorities') or 0) * fx
        c['claimsEur'] = c['netDebtEur'] + c['leasesEur'] + c['minoritiesEur']
        c['ev'] = mcap + c['claimsEur']
    ev = c.get('ev')
    if is_num(ev) and ev > 0:
        for key, base, hi in (('evEbitda', 'ebitda', 200), ('evEbit', 'operatingProfit', 300), ('evSales', 'revenue', 60)):
            if is_num(c.get(base)) and c[base] > 0:
                m = ev / (c[base] * fx)
                if 0.02 <= m <= hi:
                    c[key] = m


def sector_medians(rows, key=lambda c: ('s', c.get('sector')) if c.get('sector') else None):
    """Median of each valuation figure per peer group, only where at least 5 companies have it."""
    out, by = {}, defaultdict(list)
    for c in rows:
        k = key(c)
        if k and is_num(c.get('mcap')):
            by[k].append(c)
    for sec, cs in by.items():
        m = {'n': len(cs), 'label': sec[1].lower().replace(' industry', '')}
        for k in VAL_KEYS:
            v = sorted(c[k] for c in cs if is_num(c.get(k)))
            if len(v) >= 5:
                m[k] = v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
                if k == 'evEbitda' and len(v) >= 8:   # spread of the sector, for the scenarios
                    m['evEbitdaP25'], m['evEbitdaP75'] = v[len(v) // 4], v[(3 * len(v)) // 4]
        out[sec] = m
    return out


SRC_LABEL = {'r': 'Report', 'p': 'Price', 'c': 'FINILA calc'}


def chip(kind, label=None):
    """Where a figure comes from: r = the annual report, p = the share price, c = FINILA's calculation."""
    return f'<span class="sp-src sp-src-{kind}">{label or SRC_LABEL[kind]}</span>'


def report_chip(c):
    return chip('r', 'Accounts' if c.get('origin') == 'hand' else 'Wikidata' if c.get('origin') == 'wikidata' else 'Report')


def mult(v, d=1):
    return f'{num(v, d)}×' if is_num(v) else '—'


def pc(v, d=1):
    return f'{num(v, d)}%' if is_num(v) else '—'


def stats_html(c):
    """Finviz-style key statistics, grouped by the questions a buyer asks; only figures FINILA has."""
    cur = esc(c.get('currency') or '')
    ins = c.get('insNet')
    yrs = c.get('cagrYears')
    nd = c.get('netDebtToEbitda')
    groups = [
        ('Valuation', [
            ('Market value', f'€{bn(c["mcap"])}' if is_num(c.get('mcap')) else '—'),
            ('Enterprise value', f'€{bn(c["ev"])}' if is_num(c.get('ev')) else '—'),
            ('P/E', num(c.get('pe'), 1)), ('P/S', num(c.get('ps'), 2)), ('P/B', num(c.get('pb'), 2)),
            ('P/FCF', num(c.get('pfcf'), 1)), ('EV/EBITDA', mult(c.get('evEbitda'))), ('EV/EBIT', mult(c.get('evEbit'))),
            (f'PEG ({yrs}y past growth)' if yrs else 'PEG', num(c.get('pegPast'), 2)),
            ('FCF yield', pc(c.get('fcfYield')))]),
        ('Profitability', [
            ('Gross margin', pc(c.get('grossMargin'))), ('EBITDA margin', pc(c.get('ebitdaMargin'))),
            ('Operating margin', pc(c.get('operatingMargin'))), ('Net margin', pc(c.get('netMargin'))),
            ('FCF margin', pc(c.get('fcfMargin'))), ('ROE', pc(c.get('roe'))), ('ROA', pc(c.get('roa'))),
            ('ROIC', pc(c.get('roic')))]),
        ('Growth', [
            ('Revenue, 1 year', pc(c.get('revenueGrowth'))), ('Net income, 1 year', pc(c.get('netIncomeGrowth'))),
            (f'Revenue, {yrs}y a year' if yrs else 'Revenue CAGR', pc(c.get('revenueCagr'))),
            (f'EPS, {yrs}y a year' if yrs else 'EPS CAGR', pc(c.get('epsCagr')))]),
        ('Financial health', [
            ('Net debt / EBITDA', ('net cash' if nd < 0 else mult(nd, 2)) if is_num(nd) and not c.get('financial') else '—'),
            ('Debt / equity', num(c.get('debtEquity'), 2)), ('Current ratio', num(c.get('currentRatio'), 2)),
            ('Interest cover', mult(c.get('interestCover'))), ('Cash', bn(c.get('cash'), cur)),
            ('Quality score', f'{c["fscore"]}/{c["fscoreKnown"]}' if is_num(c.get('fscore')) else '—')]),
        ('Dividend', [
            ('Dividend yield', pc(c.get('divYield'))), ('Payout of profit', pc(c.get('payoutNet'), 0)),
            ('Payout of free cash flow', pc(c.get('payoutFcf'), 0)),
            ('Paid in a row', f'{c["divYears"]} of {c["yearsKnown"]} years' if c.get('divYears') else '—')]),
        ('Per share', [
            ('EPS', f'{num(c["eps"], 2)} {cur}' if is_num(c.get('eps')) else '—'),
            ('Book value / share', f'{num(c["bvps"], 2)} {cur}' if is_num(c.get('bvps')) else '—'),
            ('FCF / share', f'{num(c["fcfps"], 2)} {cur}' if is_num(c.get('fcfps')) else '—'),
            ('Shares in issue', f'{num(c["shares"], 1)}M' if is_num(c.get('shares')) else '—'),
            (f'Shares, {yrs}y change' if yrs else 'Shares change', pc(c.get('sharesChange')))]),
        ('Signals', [
            ('Shorted by funds', pc(c.get('shortPct'), 2)),
            ('Managers, 30 days', (('+' if ins > 0 else '') + eur(ins)) if is_num(ins) and ins else '—')]),
    ]
    html_ = ''
    for title, cells in groups:
        cells = [x for x in cells if x[1] != '—']
        if cells:
            html_ += f'<div class="sp-stat-group"><h3>{title}</h3>' + ''.join(f'<div><span>{k}</span><b>{v}</b></div>' for k, v in cells) + '</div>'
    if html_.count('<span>') < 4:
        return ''
    fy = (c.get('periodEnd') or '')[:4]
    return f'''<section class="sp-stats" id="stats" aria-label="Key statistics"><div class="sp-stats-grid">{html_}</div>
      <p class="sp-sub">From the FY {esc(fy)} annual report{"s of the last " + str(c["yearsKnown"]) + " years" if c.get("yearsKnown") else ""} and today’s price; ratios calculated by FINILA. Not available for free, so not shown: analysts’ forecasts (forward P/E).</p></section>'''


def buyer_html(c, med):
    """If you bought the whole company: shares + net debt = enterprise value, and the price tags."""
    if not is_num(c.get('mcap')):
        return ''
    sec = med.get('label') or (c.get('sector') or '').lower()
    rep = report_chip(c)
    ev = ''
    if is_num(c.get('ev')):
        nd = c['netDebtEur']
        parts = f'<div><span>All shares {chip("p")}</span><b>€{bn(c["mcap"])}</b></div>'
        parts += f'<i>{"+" if nd >= 0 else "−"}</i><div><span>{"Net debt" if nd >= 0 else "Net cash"} {rep}</span><b>€{bn(abs(nd))}</b></div>'
        if c.get('leasesEur'):
            parts += f'<i>+</i><div><span>Lease liabilities {rep}</span><b>€{bn(c["leasesEur"])}</b></div>'
        if c.get('minoritiesEur'):
            parts += f'<i>+</i><div><span>Minority interests {rep}</span><b>€{bn(c["minoritiesEur"])}</b></div>'
        ev = f'<div class="sp-ev">{parts}<i>=</i><div class="sp-ev-tot"><span>Enterprise value</span><b>€{bn(c["ev"])}</b></div></div>'
    elif c.get('captive'):
        ev = (f'<p class="sp-sub">The group includes its own bank ({esc(c["captive"])}): its borrowings fund customer loans and '
              'leasing, so group net debt, enterprise value and free cash flow do not describe the business and are not shown. '
              'Analysts use the figures of the industrial division alone, which the annual report shows only in its notes.</p>')
    rows = [('EV / EBITDA', mult(c.get('evEbitda')), mult(med.get('evEbitda'))),
            ('EV / EBIT', mult(c.get('evEbit')), mult(med.get('evEbit'))),
            ('P/E', num(c.get('pe'), 1), num(med.get('pe'), 1)),
            ('P/B (price / book value)', num(c.get('pb'), 1), num(med.get('pb'), 1)),
            ('Free cash flow yield', pc(c.get('fcfYield')), pc(med.get('fcfYield'))),
            ('Dividend yield', pc(c.get('divYield')), pc(med.get('divYield'))),
            ('Net debt / EBITDA', ('net cash' if c['netDebtToEbitda'] < 0 else mult(c['netDebtToEbitda'], 2))
             if is_num(c.get('netDebtToEbitda')) and not c.get('financial') else '—', mult(med.get('netDebtToEbitda'), 2))]
    rows = [r for r in rows if r[1] != '—']
    if not rows:
        return ''
    trs = ''.join(f'<tr><td>{k}</td><td><b>{v}</b></td><td class="sp-med">{m}</td></tr>' for k, v, m in rows)
    note = f'Median of {med["n"]} European {esc(sec)} companies on FINILA. ' if med.get('n') else ''
    if is_num(c.get('evEbitda')):
        note += f'A buyer of the whole firm would pay about {num(c["evEbitda"], 1)} years of this year’s EBITDA. '
    if c.get('financial'):
        note += 'Bank or insurer: enterprise value and EBITDA multiples are not shown, debt is part of the business. '
    return f'''<section class="sp-card" id="buyer"><h2>If you bought the whole company</h2>
      <p class="sp-sub">What a buyer pays: all the shares at today’s price{"" if c.get("financial") else " plus the debt, leases and minority stakes that come with the company"} {chip("c")}</p>
      {ev}
      <table class="sp-table"><tr><th>Price tag</th><th>{esc(c["name"])}</th><th>Peer median</th></tr>{trs}</table>
      <p class="sp-sub sp-after">{note}</p></section>'''


def price_change(price, ch):
    """Change on the previous close in euros and per cent: +€1.23 (+0.95%)."""
    if not (is_num(price) and is_num(ch)) or ch <= -100:
        return ''
    d = price - price / (1 + ch / 100)
    return f'{"+" if d >= 0 else "−"}€{num(abs(d), 2)} ({"+" if ch >= 0 else ""}{num(ch, 2)}%)'


def sign(v):
    return ('+' if v >= 0 else '') + num(v)


def findings(c, med):
    """Plain-language observations FINILA draws from the figures: (good: True/False/None, what, what it means)."""
    f, cur = [], esc(c.get('currency') or '')
    g = lambda k: c.get(k) if is_num(c.get(k)) else None
    sec = esc(med.get('label') or (c.get('sector') or 'its sector').lower())
    fin = c.get('financial')
    em, mm = g('ebitdaMargin'), med.get('ebitdaMargin')
    if em is not None and mm is not None and not fin and abs(em - mm) >= 5:
        f.append((em > mm, f'EBITDA margin {num(em)}% vs a median of {num(mm)}% for {sec} companies.',
                  'More of every euro of sales stays as operating profit before depreciation than at the typical company of the sector.' if em > mm
                  else 'Less of every euro of sales stays as operating profit before depreciation than at the typical company of the sector.'))
    ve, vm = g('evEbitda'), med.get('evEbitda')
    if ve is not None and vm is not None:
        if ve <= vm * 0.75:
            f.append((None, f'Priced at {num(ve)}× EBITDA vs {num(vm)}× for the sector median.',
                      'A multiple below the sector median usually reflects lower expected growth or higher risk.'))
        elif ve >= vm * 1.33:
            f.append((None, f'Priced at {num(ve)}× EBITDA vs {num(vm)}× for the sector median.',
                      'A multiple above the sector median usually reflects higher expected growth.'))
    elif fin and g('pb') is not None and med.get('pb'):
        pb, pm = c['pb'], med['pb']
        if pb <= pm * 0.75 or pb >= pm * 1.33:
            f.append((None, f'Priced at {num(pb, 2)}× book value vs {num(pm, 2)}× for the sector median.',
                      'Below the sector median on book value.' if pb < pm else 'Above the sector median on book value.'))
    rg, ng = g('revenueGrowth'), g('netIncomeGrowth')
    # Growth from a tiny or negative base (+900%) says little, so only moderate changes are explained.
    if rg is not None and ng is not None and (g('netIncome') or 0) > 0 and -80 <= ng <= 300 and -60 <= rg <= 200:
        if rg > 0 and ng < rg - 5:
            f.append((False, f'Revenue {sign(rg)}% but net income {sign(ng)}% vs the year before.', 'Costs grew faster than sales: margins narrowed.'))
        elif ng > rg + 5 and rg > -5:
            f.append((True, f'Net income {sign(ng)}% on revenue {sign(rg)}% vs the year before.', 'Profit grew faster than sales: margins widened.'))
        elif rg >= 10:
            f.append((True, f'Revenue up {num(rg)}% on the year before.', 'Growth well above the typical listed company.'))
        elif rg <= -5:
            f.append((False, f'Revenue down {num(-rg)}% on the year before.', 'Lower sales than the year before.'))
    if ng is not None and ng <= -50 and (g('netIncome') or 0) > 0 and (rg is None or rg > -30):
        f.append((None, f'Net income down {num(-ng, 0)}% on the year before{"" if rg is None else f", revenue {sign(rg)}%"}.',
                  'A profit drop this large with stable sales usually comes from a one-off item (write-off, sale, '
                  'restructuring). The P/E then looks high: check the report for the cause.'))
    rev, capex, ocf, fcf = g('revenue'), g('capex'), g('operatingCashFlow'), g('fcf')
    if not fin and rev and rev > 0 and capex is not None and ocf and ocf > 0 and capex / rev >= 0.15:
        left = max(fcf or 0, 0) / ocf * 100
        f.append((None, f'Capex {cur} {bn(capex)} = {num(capex / rev * 100, 0)}% of revenue; {num(left, 0)}% of operating cash flow is left as free cash flow.',
                  'Heavy investment: if the new capacity sells, free cash flow should rise; if not, the money is tied up.'))
    pf = g('payoutFcf')
    if pf is not None and pf > 100 and (g('dividendsPaid') or 0) > 0 and (fcf or 0) > 0:
        nd = g('netDebtToEbitda')
        room = '' if fin or nd is None else ' The company has net cash.' if nd < 0 else f' Net debt is {num(nd, 1)}× EBITDA.'
        f.append((False, f'Dividends paid {cur} {bn(c["dividendsPaid"])} = {num(pf, 0)}% of free cash flow.',
                  'Part of the dividend came from cash or new debt, which cannot go on for years.' + room))
    nd = g('netDebtToEbitda')
    if nd is not None and not fin:
        if nd < 0:
            f.append((True, 'More cash than debt (net cash).', 'Net cash leaves room for dividends, buybacks or acquisitions.'))
        elif nd >= 3.5:
            f.append((False, f'Net debt {num(nd, 1)}× EBITDA.', 'At this level a weaker year or higher interest rates weigh more on profit.'))
    roe = g('roe')
    if roe is not None and (g('equity') or 0) > 0:
        if 20 <= roe <= 150:
            f.append((True, f'Return on equity {num(roe)}%.', 'Every 100 of shareholders’ equity earned more than 20 in the year.'))
        elif roe < 0:
            f.append((False, f'Return on equity {num(roe)}%.', 'Loss-making in the last financial year.'))
    sp = g('shortPct')
    if sp is not None and sp >= 2:
        f.append((False, f'Funds hold net short positions of {num(sp, 2)}% of the shares.', 'A net short position is a bet on a falling price, as reported to the regulator.'))
    ins = g('insNet')
    if ins is not None and ins >= 100000:
        f.append((True, f'Managers bought shares worth {eur(ins)} net in the last 30 days.', 'Managers’ own purchases, as reported to the regulator.'))
    return f


def analysis_html(c):
    """What a due diligence looks at, year by year from the reports: cash conversion, working capital in
    days, returns on capital, investment against depreciation, and where the cash went. Definitions only,
    no comment: every figure is a ratio of lines the company itself reports."""
    years = c.get('years')
    if c.get('financial') or c.get('captive') or not years or len(years) < 2:
        return ''
    ys = sorted(years)
    cur = esc(c.get('currency') or '')
    ok = lambda *v: all(is_num(x) for x in v)

    def ratio(a, b, k=100, lo=None, hi=None):
        if not ok(a, b) or b <= 0:
            return None
        r = a / b * k
        return None if (lo is not None and r < lo) or (hi is not None and r > hi) else r

    def cogs(y):
        if is_num(y.get('cogs')) and y['cogs'] > 0:
            return y['cogs']
        if ok(y.get('revenue'), y.get('gross')) and 0 < y['gross'] < y['revenue']:
            return y['revenue'] - y['gross']

    def nopat(y):
        op = y.get('operatingProfit')
        if not is_num(op):
            return None
        pbt, tax = y.get('pbt'), y.get('tax')
        rate = tax / pbt if ok(pbt, tax) and pbt > 0 else 0.25
        return op * (1 - min(max(rate, 0.15), 0.35))

    def capital(y):
        eq, nd = y.get('equity'), y.get('netDebt')
        if not ok(eq, nd):
            return None
        cap = eq + nd + (y.get('leases') or 0)
        return cap if cap > 0 else None

    days = lambda a, b: ratio(a, b, 365, 0, 730)
    groups = [
        ('Cash conversion', 'How much of the profit arrives as cash.', [
            ('Operating cash flow / EBITDA', lambda y: ratio(y.get('operatingCashFlow'), y.get('ebitda'), lo=-100, hi=250), 'pct'),
            ('Free cash flow / EBITDA', lambda y: ratio(y.get('fcf'), y.get('ebitda'), lo=-300, hi=200), 'pct'),
            ('Operating cash flow / net income', lambda y: ratio(y.get('operatingCashFlow'), y.get('netIncome'), 1, -20, 20), 'x'),
        ]),
        ('Working capital in days', 'Days of sales or purchases tied up at the year end.', [
            ('Inventory days (DIO)', lambda y: days(y.get('inventories'), cogs(y)), 'd'),
            ('Receivable days (DSO)', lambda y: days(y.get('receivables'), y.get('revenue')), 'd'),
            ('Payable days (DPO)', lambda y: days(y.get('payables'), cogs(y)), 'd'),
            ('Cash cycle (DIO + DSO − DPO)', lambda y: (lambda a, b, p: a + b - p if ok(a, b, p) else None)(
                days(y.get('inventories'), cogs(y)), days(y.get('receivables'), y.get('revenue')), days(y.get('payables'), cogs(y))), 'd'),
        ]),
        ('Returns and investment', 'What the money in the business earns, and how much is reinvested.', [
            ('ROIC (operating profit after tax / capital)', lambda y: ratio(nopat(y), capital(y), lo=-100, hi=200), 'pct'),
            ('Capex / depreciation and amortisation', lambda y: ratio(y.get('capex'), y.get('da'), 1, 0, 20), 'x'),
            ('Capex / revenue', lambda y: ratio(y.get('capex'), y.get('revenue'), lo=0, hi=100), 'pct'),
        ]),
    ]
    fmt = {'pct': lambda v: f'{num(v, 0)}%', 'x': lambda v: f'{num(v, 1)}×', 'd': lambda v: num(v, 0)}
    head_ = ''.join(f'<th>FY {y}</th>' for y in ys)
    body = ''
    for title, sub, lines in groups:
        rows = ''
        for label, fn, f in lines:
            vals = [fn(years[y]) for y in ys]
            if sum(v is not None for v in vals) >= 2:
                rows += f'<tr><td>{label}</td>' + ''.join(f'<td>{fmt[f](v) if v is not None else "—"}</td>' for v in vals) + '</tr>'
        if rows:
            body += f'<tr class="sp-an-group"><th colspan="{len(ys) + 1}">{title} <small>{sub}</small></th></tr>{rows}'
    # Incremental ROIC: extra operating profit after tax per extra unit of capital, first to last year.
    tiles = []
    a, b = years[ys[0]], years[ys[-1]]
    n0, n1, k0, k1 = nopat(a), nopat(b), capital(a), capital(b)
    if ok(n0, n1, k0, k1) and k1 - k0 > 0.1 * k0 and len(ys) >= 4:
        tiles.append(('Incremental ROIC', f'{num((n1 - n0) / (k1 - k0) * 100, 0)}%',
                      f'extra profit after tax on the extra capital, FY {ys[0]}–{ys[-1]}'))
    # Where the cash went, all years together.
    tot = lambda k: sum(years[y][k] for y in ys if is_num(years[y].get(k)))
    has = lambda k: sum(is_num(years[y].get(k)) for y in ys)
    ocf = tot('operatingCashFlow') if has('operatingCashFlow') == len(ys) else None
    uses = []
    if ocf and ocf > 0:
        for label, k in (('Capex', 'capex'), ('Acquisitions', 'acquisitions'), ('Dividends', 'dividendsPaid'),
                         ('Share buybacks', 'buybacks'), ('Lease payments', 'leasePaid')):
            if has(k) == len(ys) or (k in ('acquisitions', 'buybacks') and has(k) >= len(ys) - 1):
                uses.append((label, tot(k)))
        if has('borrowed') == len(ys) and has('repaid') == len(ys):
            net = tot('repaid') - tot('borrowed')
            uses.append(('Debt repaid, net' if net >= 0 else 'New debt, net', net))
    flow = ''
    if len(uses) >= 3:
        flow = ''.join(f'<tr><td>{l}</td><td>{bn(v)}</td><td>{num(v / ocf * 100, 0)}%</td></tr>' for l, v in uses)
        flow = f'''<h3 class="sp-an-h">Where the cash went, FY {ys[0]}–{ys[-1]}</h3>
      <div class="sp-scroll"><table class="sp-table sp-an-flow"><tr><th></th><th>{cur}</th><th>of operating cash flow</th></tr>
      <tr class="sp-an-total"><td>Operating cash flow, {len(ys)} years</td><td>{bn(ocf)}</td><td>100%</td></tr>{flow}</table></div>
      <p class="sp-sub sp-after">From the cash flow statements. New debt counts against repayments; the rest is the change in cash, disposals and other items.</p>'''
    if not body and not flow:
        return ''
    tiles_html = ''.join(f'<div><span>{a}</span><b>{b}</b><small>{d}</small></div>' for a, b, d in tiles)
    return f'''<section class="sp-card" id="analysis"><h2>Analysis</h2>
      <p class="sp-sub">The ratios a due diligence looks at, each year from that year’s annual report {chip("r", "Reports")} {chip("c")}. Definitions: <a href="/stock/methodology#analysis">methodology</a>.</p>
      {f'<div class="sp-tiles">{tiles_html}</div>' if tiles_html else ''}
      {f'<div class="sp-scroll"><table class="sp-table sp-hist-table sp-an"><tr><th></th>{head_}</tr>{body}</table></div>' if body else ''}
      {flow}</section>'''


def add_report_quotes(rows):
    """data/outlook.json (scripts/data/outlook.py): sentences quoted word for word from the newest report."""
    try:
        quotes = json.loads((SRC / 'data' / 'outlook.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    for c in rows:
        q = quotes.get(c['lei'])
        # An outlook from a report older than the page's figures is out of date (hand-copied newer year).
        if q and q.get('items') and (q.get('period') or '') >= (c.get('periodEnd') or ''):
            c['quotes'] = q


def add_about(rows):
    """data/about.json (scripts/data/about.py): what the company does, quoted from its annual report."""
    try:
        about = json.loads((SRC / 'data' / 'about.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    for c in rows:
        a = about.get(c['lei'])
        if a and a.get('text'):
            c['about'] = a


def about_short(t, limit=170):
    """The first sentence, at most about `limit` characters: a longer one is cut after a comma or 'and' with '…'
    (the words stay the company's own; Lub, 2026-11-04: keep it short)."""
    first = re.split(r'(?<=[a-z0-9\)][.])\s+(?=[A-Z])', t)[0]
    if len(first) <= limit:
        return first
    cut = max(first.rfind(', ', 0, limit), first.rfind(' and ', 0, limit))
    return first[:cut].rstrip(' ,') + '…' if cut > 60 else ''


def about_clean(t):
    """Line-break hyphens ("user-friend- ly"), glued sentences ("specialist.Through") and capital headings
    ("OUR PURPOSE") the PDF-to-ESEF conversion leaves; a lost company name at the start becomes '…'."""
    t = re.sub(r"(?<=[a-z])- (?=[a-z])", "", t)
    t = re.sub(r"(?<=[a-z]{3})\.(?=[A-Z][a-z])", ". ", t)
    t = re.sub(r"^(?:[A-Z]{2,}\s){1,4}(?=[A-Z][a-z])", "", t)
    if re.match(r"['’]s\b", t):
        t = "…" + t
    return t.rstrip(" ,;")


def about_html(c):
    """What the company does (its own words) and where its revenue comes from (segments, products, regions)."""
    a = c.get('about')
    if not a:
        return ''
    full = about_clean(a['text']) if a.get('text') else ''
    t = about_short(full) if full else ''
    link = f' <a href="{esc(a["source"])}" target="_blank" rel="noopener">annual report {esc(a["period"][:4])}</a>' if a.get('source') else ''
    more = ''
    if t and len(full) > len(t) + 20:   # the whole paragraph from the report, opened on request
        rest = full[len(t):].strip() if full.startswith(t) else full   # only what the first sentence did not say
        more = f'<details class="sp-more"><summary>More</summary>“{esc(rest)}”</details>'
    out = f'<div class="sp-about"><p><b>What it does:</b> “{esc(t)}” {chip("r")}{link}</p>{more}</div>' if t else ''
    split = a.get('split') or {}
    rows = []
    for kind, name in (('segments', 'By business'), ('products', 'By product'), ('regions', 'By region')):
        if kind == 'products' and split.get('segments'):
            continue   # one business split is enough
        if split.get(kind):
            rows.append(f'<span class="sp-split"><b>{name}:</b> ' + ' · '.join(f'{esc(n)} {p}%' for n, p in split[kind]) + '</span>')
    if rows:
        out += (f'<p class="sp-about sp-rev">Revenue {a["period"][:4]}: ' + ' '.join(rows) + (f' {chip("r")}' if not t else '') + '</p>')
    return out


def add_news(rows):
    """data/news.json (scripts/data/news.py): official exchange announcements and the newest report."""
    try:
        news = json.loads((SRC / 'data' / 'news.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return
    for c in rows:
        n = news.get(c['lei'])
        if n:
            c['news'] = n.get('news') or []
            if n.get('report'):
                c['latestReport'] = n['report']


def nice_date(d):
    try:
        return date.fromisoformat((d or '')[:10]).strftime('%-d %b %Y')
    except ValueError:
        return d or ''


def latest_report_html(c):
    """The newest quarterly / half-year / annual report announced on the exchange, quoted word for word."""
    r = c.get('latestReport')
    if not r:
        return ''
    res = [x if isinstance(x, dict) else {'text': x, 'period': None} for x in r.get('results') or []]
    res = [x for x in res if not re.match(r'(In|For|During) the (comparison|corresponding) period', x['text'])]   # last year's figures
    n, yr = r.get('quarter'), (r.get('date') or '')[:4]
    if n == 4 and (r.get('date') or '')[5:7] in ('01', '02', '03'):
        yr = str(int(yr) - 1)
    months = {1: 'January–March', 2: 'January–June', 3: 'January–September', 4: 'January–December'}
    labels = {'q': (f'Quarter: Q{n} {yr}' if n else 'The quarter', 'What the company reports for the three months.'),
              'ytd': (f'Year so far: {months[n]} {yr}' if n else 'Year so far', 'From the start of the financial year to the report date.'),
              None: ('Results', 'The release does not say which period these sentences cover: see the announcement.')}
    if n == 1:   # the first quarter is also the year so far
        labels['ytd'] = labels['q']
    sec = r.get('sections') or {}
    ul = lambda items: '<ul>' + ''.join(f'<li>“{esc(t)}”</li>' for t in items) + '</ul>'
    head = lambda title, sub='', extra='': f'<h3>{title}{extra} <small>{sub}</small></h3>'
    # 1. How the quarter went: the company's own highlights, else the sentences with a figure (quarter first).
    hl = quote_unique([quote_clean(t) for t in sec.get('highlights') or []])[:3]
    rest = []
    if not hl:
        ordered = [x for x in res if x['period'] == 'q'] + [x for x in res if x['period'] is None] + [x for x in res if x['period'] == 'ytd']
        allq = quote_unique([quote_clean(x['text']) for x in ordered])
        hl, rest = allq[:3], allq[3:]
    body = ''
    if hl:
        body += f'<div class="sp-q">{head("How the quarter went", "Key figures as the company states them.")}{ul(hl)}</div>'
    # 2. What happened in the business: contracts, launches, acquisitions, approvals.
    ev = quote_unique([quote_clean(t) for t in sec.get('events') or []])[:2]
    if ev:
        body += f'<div class="sp-q">{head("What happened in the business", "Orders, launches, deals and approvals from the report.")}{ul(ev)}</div>'
    # 3. What the company expects, and whether it raised, lowered or confirmed it.
    change = sec.get('change') or r.get('change')
    tag = {'raised': ('pos', 'Raised'), 'lowered': ('neg', 'Lowered'), 'confirmed': ('neu', 'Confirmed'), 'updated': ('neu', 'Updated')}.get(change)
    tag_html = f' <span class="sp-chg sp-chg-{tag[0]}">{tag[1]}</span>' if tag else ''
    exp = [quote_clean(sec['outlook'])] if sec.get('outlook') else \
        quote_unique([quote_clean(i['text']) for i in r.get('outlook') or [] if kind_of(i) == 'outlook' and len(i['text']) <= 320 and not re.search(BROKEN, i['text'])])[:2]
    if exp:
        body += f'<div class="sp-q">{head("What the company expects", "Its outlook in this report" + (", compared with its previous one" if tag else "") + ".", tag_html)}{ul(exp)}</div>'
    for kind, title, sub in (('target', 'Mid-term targets', 'Goals the company has set for the years ahead.'),
                             ('backlog', 'Order backlog', 'Orders signed and not yet delivered.')):
        out = quote_unique([quote_clean(i['text']) for i in r.get('outlook') or [] if kind_of(i) == kind and len(i['text']) <= 320 and not re.search(BROKEN, i['text'])])[:2]
        if out:
            body += f'<div class="sp-q">{head(title, sub)}{ul(out)}</div>'
    if rest:
        body += f'<details class="sp-more"><summary>More from the report</summary>{ul(rest)}</details>'
    period = f'Q{n} {yr}' if n in (1, 2, 3) else (f'Full year {yr}' if n == 4 else '')
    return (f'<div class="sp-latest"><p class="sp-q-date"><b>Latest report{": " + period if period else ""}</b> · {esc(r["title"])}, published {esc(nice_date(r["date"]))} '
            f'{chip("r", "Exchange")} <a href="{esc(r["url"])}" target="_blank" rel="noopener">read the report</a></p>{body}</div>')

INVEST_BACKLOG = r"\binvestments?\b(?! decisions?)"
BROKEN = (r"[a-z] -[a-z]|[a-z]- [a-z]|[a-z] - [a-z]|(?<![\w'’.])[b-hj-z] [a-z]{2,}|\b20\d \d\b|[a-z]20\d\d\b|"
          r"(\b\w ){4,}|(?i:page \d+ of \d+|ceo comments|president and ceo|^business update)")


def kind_of(i):
    """A backlog of approved investment projects (Air Liquide's plants) is future spending, not orders: not shown."""
    return 'projects' if i['kind'] == 'backlog' and re.search(INVEST_BACKLOG, i['text'], re.I) \
        and not re.search(r'\border(s| book| backlog| intake)\b', i['text'], re.I) else i['kind']


REPORT_BLOCKS = [('outlook', 'Outlook', 'What the company expects for the current year.'),
                 ('target', 'Mid-term targets', 'Goals the company has set for the years ahead.'),
                 ('backlog', 'Order backlog', 'Orders signed and not yet delivered.'),
                 ('event', 'What happened', 'Deals, restructuring, approvals, prices and management changes.')]
REPORT_SHOWN = {'outlook', 'target', 'backlog'}   # 'event' joins once its selection is checked


def quote_clean(t):
    """Footnote marks and symbols the PDF-to-ESEF conversion leaves in sentences («, ◊, *)."""
    return re.sub(r'\s+([.,;:])', r'\1', re.sub(r'\s*[«»◊*†‡]\s*', ' ', t)).strip()


def quote_unique(items):
    """The same statement often appears two or three times in a report (highlights, outlook, CEO letter):
    sentences with the same figures are one statement, the shortest wording is kept."""
    sig = lambda t: frozenset(re.sub(r'\.0\b', '', x) for x in re.findall(r'\d+(?:[.,]\d+)?', t))
    out = []
    for t in sorted(items, key=len):
        k = sig(t)
        if len(k) >= 2 and any(k <= sig(o) or sig(o) <= k for o in out):
            continue
        out.append(t)
    return [t for t in items if t in out]


TOPICS = {'sales': r'sales|revenue|turnover', 'profit': r'operating (profit|income|result|margin)|ebit|ebita|ebitda|margin',
          'capex': r'capex|capital expenditure|investments?', 'cash': r'free cash flow|cash flow', 'eps': r'earnings per share|eps|net (profit|income)',
          'dividend': r'dividend'}


def topics(t):
    low = t.lower()
    return {k for k, rx in TOPICS.items() if re.search(fr'\b({rx})\b', low)}


def report_html(c):
    q = c.get('quotes') or {'items': []}
    latest = latest_report_html(c)
    # Lub (2026-11-05): the outlook comes from the newest quarter. When the newest exchange report states an
    # outlook, the older annual report's outlook lines are not shown; its other lines (targets, backlog) stay.
    lr = c.get('latestReport') or {}
    has_new = bool((lr.get('sections') or {}).get('outlook')) or any(i['kind'] == 'outlook' for i in lr.get('outlook') or [])
    if has_new and (lr.get('date') or '')[:10] > (q.get('period') or ''):
        q = {**q, 'items': [i for i in q['items'] if i['kind'] != 'outlook']}
    blocks = ''
    for kind, title, sub in REPORT_BLOCKS:
        items = quote_unique([quote_clean(i['text']) for i in q['items'] if kind_of(i) == kind and len(i['text']) <= 320 and not re.search(BROKEN, i['text'])
                              and not re.search(r'materiality|ceteris paribus|annual report( and accounts)? 20\d\d|financial report for the period',
                                                i['text'], re.I)])
        if kind in REPORT_SHOWN and items:
            blocks += (f'<div class="sp-q"><h3>{title} <small>{sub}</small></h3><ul>' +
                       ''.join(f'<li>“{esc(t)}”</li>' for t in items) + '</ul></div>')
    if not blocks and not latest:
        return ''
    esef = ''
    if blocks:
        period = esc(q.get('period') or '')
        pub = f', in the ESEF register since {esc(nice_date(q["published"]))}' if q.get('published') else ''
        esef = (f'<p class="sp-q-date">{"Earlier: " if latest else ""}report for the period ending <b>{period}</b>{pub} '
                f'{chip("r", "Report")} <a href="{esc(q["source"])}" target="_blank" rel="noopener">open the report</a></p>{blocks}')
    return f'''<section class="sp-card" id="report"><h2>From the company’s report</h2>
      <p class="sp-sub">Sentences quoted word for word. The company’s own statements, not FINILA’s: read the full context in the report.</p>
      {latest}{esef}</section>'''


def findings_html(c, med):
    f = findings(c, med)
    if not f:
        return ''
    icon = {True: '<span class="sp-ic pos">▲</span>', False: '<span class="sp-ic neg">▼</span>', None: '<span class="sp-ic">●</span>'}
    items = ''.join(f'<li>{icon[good]}<div>{what}<em>{means}</em></div></li>' for good, what, means in f)
    good, bad = sum(1 for x in f if x[0] is True), sum(1 for x in f if x[0] is False)
    fy = (c.get('periodEnd') or '')[:4]
    return f'''<section class="sp-card" id="findings"><h2>What the numbers say</h2>
      <p class="sp-sub">Found automatically in the {esc(fy)} annual figures and today’s price {chip("c")}</p>
      <ul class="sp-find">{items}</ul>
      <p class="sp-sub sp-after">▲ favourable, ▼ unfavourable, ● neutral compared with the sector median or the year before. Facts from the figures, not investment advice.</p></section>'''


def peers_html(c, peers, med):
    """Competitors table: same sector, largest first, with the sector median row."""
    if not peers:
        return ''
    cols = [('Market value', lambda x: f'€{bn(x["mcap"])}' if is_num(x.get('mcap')) else '—'),
            ('EV/EBITDA', lambda x: mult(x.get('evEbitda'))), ('P/E', lambda x: num(x.get('pe'), 1)),
            ('P/B', lambda x: num(x.get('pb'), 1)), ('EBITDA margin', lambda x: pc(x.get('ebitdaMargin'))),
            ('Revenue growth', lambda x: pc(x.get('revenueGrowth'))), ('ROE', lambda x: pc(x.get('roe')))]
    if c.get('financial'):   # banks and insurers: no EBITDA or enterprise value
        cols = [x for x in cols if x[0] not in ('EV/EBITDA', 'EBITDA margin')]
    th = ''.join(f'<th>{h}</th>' for h, _ in cols)
    tds = lambda x: ''.join(f'<td>{fn(x)}</td>' for _, fn in cols)
    me = f'<tr class="sp-me"><td>{esc(c["name"])}</td>{tds(c)}</tr>'
    body = ''.join(f'<tr><td><a href="/stock/{p["slug"]}">{esc(p["name"])}</a></td>{tds(p)}</tr>' for p in peers)
    medrow = (f'<tr class="sp-medrow"><td>Median of {med["n"]} companies</td><td></td>' +
              ''.join(f'<td>{fn(med)}</td>' for _, fn in cols[1:]) + '</tr>') if med.get('n') else ''
    title = f'Versus other European {esc(med["label"])} companies' if med.get('label') else f'Other companies from {esc(COUNTRIES.get(c["country"], c["country"] or ""))}'
    return f'''<section class="sp-card" id="peers"><h2>{title}</h2>
      <p class="sp-sub">Largest European companies of the same industry group (closest industry first), latest annual figures and prices {chip("r", "Reports")}{chip("p", "Prices")}{chip("c")}</p>
      <div class="sp-scroll"><table class="sp-table sp-peer-table"><tr><th>Company</th>{th}</tr>{me}{body}{medrow}</table></div></section>'''


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
    used['methodology'] = None   # /stock/methodology is the methodology page
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
      <a href="/radar">Radar</a>
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
  <p><a href="/stock/">All companies A-Z</a> · <a href="/stock/methodology">Methodology</a> · <a href="/screener">Stock screener</a> · <a href="/insiders">Insider trades</a> · <a href="/shorts">Short positions</a> · <a href="/assets">Where to put money</a> · <a href="/property">Property prices</a> · <a href="https://www.linkedin.com/company/finila" target="_blank" rel="noopener">LinkedIn</a></p>
  <p>Contact: <a href="mailto:info@finila.com">info@finila.com</a></p>
</footer>
'''


def events_html(c):
    """Company events FINILA records itself: managers' trades, short positions, the latest annual report."""
    ev = sorted(c.get('events', []), key=lambda e: e[0], reverse=True)[:12]
    nice = lambda d: date.fromisoformat(d).strftime('%-d %b %Y') if re.fullmatch(r'\d{4}-\d\d-\d\d', d or '') else d
    ev += [(n['date'][:10], esc(n['title']) + ({'fr': ' <small class="ins-note">(in French)</small>', 'es': ' <small class="ins-note">(in Spanish)</small>',
                                                     'nl': ' <small class="ins-note">(in Dutch)</small>'}.get(n.get('lang'), '')), n['url'], True)
           for n in c.get('news', [])]
    ev = sorted(ev, key=lambda e: e[0], reverse=True)[:12]
    items = ''.join(f'<li><time datetime="{esc(e[0])}">{esc(nice(e[0]))}</time><span>{e[1]}</span>'
                    + (f'<a href="{esc(e[2])}" target="_blank" rel="noopener">Read</a></li>' if len(e) > 3 else f'<a href="{e[2]}">Details</a></li>') for e in ev)
    if c.get('source') and c.get('origin') not in ('listing', 'wikidata', 'hand'):
        items += (f'<li><time>FY {esc((c.get("periodEnd") or "")[:4])}</time><span>Annual report for the year ending {esc(c["periodEnd"])}</span>'
                  f'<a href="{esc(c["source"])}" target="_blank" rel="noopener">Read</a></li>')
    if not items:
        return ''
    return f'''<section class="sp-events"><h2>Latest about {esc(c["name"])}</h2>
    <ul>{items}</ul>
    <p class="ins-note">From official sources: company announcements filed with the exchange or regulator (so far Nasdaq Nordic, Oslo Børs, the French AMF and the Spanish CNMV), managers’ trades (so far Sweden), net short positions of funds (NL, IT, SE, NO, UK) and annual reports.</p></section>'''


def page(c, peers, updated, med=None):
    med = med or {}
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
    src = (f'Source: the company’s 10-K annual report filed with the US SEC (XBRL, data.sec.gov){link}' if c.get('sec')
           else f'Source: headline figures from Wikidata (CC0), the company has no ESEF report on filings.xbrl.org yet{link}' if wd
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
        ', '.join(x for x in [country, c.get('subindustry') or c.get('group') or c.get('sector')] if x) + '.'   # the most specific line of business (bpost: Logistics & postal, not Industrials)
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
      <span class="modal-change {"pos" if is_num(ch) and ch >= 0 else "neg"}" data-f="change">{price_change(c["price"], ch)}</span>
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
      </div>'''
    stats = stats_html(c)
    if stats and c.get('years') and not listed_only:
        # The figures of the last annual report are already in the key statistics and the results table,
        # and payout, insiders and shorts in the statistics: only the quality-score tests stay here.
        figs = ''
        signals = f'<p class="ins-note">{" · ".join(l for l in links if l.startswith("<a"))}</p>' if any(l.startswith('<a') for l in links) else ''
        qual = qual.replace('<div class="modal-section">', '<div class="modal-section sp-fscore">', 1)
    if listed_only or c.get('preview'):   # no report figures yet / US test page: search engines keep out
        extra = '  <meta name="robots" content="noindex, follow">\n' + extra
    return head(title, desc, url, extra) + f'''
<main class="sp-wrap">
  <nav class="sp-crumbs" aria-label="Breadcrumb"><a href="/stock/">Stocks</a> › {esc(COUNTRIES.get(c["country"], c["country"]))} › {esc(c["name"])}</nav>
  <article class="modal-box sp-box">
    <div class="modal-header">
      <div>
        <h1 class="sp-title">{esc(c["name"])} {f'<span class="modal-name">{esc(c["ticker"])}{" · " + esc(c["exchange"]) if c.get("exchange") else ""}</span>' if c.get("ticker") else ""}</h1>
        <div class="modal-meta">{esc(country)}{" · " + esc(" · ".join(x for x in (c.get("subindustry"), c.get("group")) if x) or c["industry"]) if c.get("group") or c.get("industry") else ""}{" · " + ("sector " if c.get("group") or c.get("industry") else "") + esc(c["sector"]) if c.get("sector") else ""} · ISIN {esc(c["isin"])} · LEI {esc(c["lei"])}</div>
      </div>
    </div>
    {price}
    {chart(c)}
    {summary_html(c, med, intro)}
    {stats or f'<div class="sp-keys">{keyhtml}</div>'}
    {buyer_html(c, med)}
    {report_html(c)}
    {history_html(c)}
    {analysis_html(c)}
    <div class="modal-report">
{figs}
      <div class="modal-grid modal-scores">{qual}{signals}</div>
      {fin}
      <div class="modal-report-src">{"" if listed_only else src + ". "}Prices: Deutsche Börse delayed data. Market value is estimated from net income / EPS. Figures are read automatically from the report; every year links to it as proof. Page updated {esc(updated)}.</div>
    </div>
    {whatif_html(c, med)}
    {"" if c.get("preview") else f'<p class="sp-cta"><a class="btn btn-primary" href="/screener?lei={esc(c["lei"])}">Compare in the stock screener</a></p>'}
  </article>
  {peers_html(c, peers, med)}
  {events_html(c)}
  <p class="sp-legend">Where each number comes from: {chip('r')} the company’s annual report or an official register · {chip('p')} Deutsche Börse delayed share price · {chip('c')} calculated by FINILA from those two. <a href="/stock/methodology">How FINILA calculates</a>.</p>
</main>
{FOOTER}<script src="/js/url-state.js" defer></script>
<script src="/js/stock-page.js" defer></script>
<script src="/js/analytics.js" defer></script>
</body>
</html>
'''


METHOD = [
    ('Sources', [
        ('Annual figures', 'Each company’s ESEF annual report (the machine-readable report every EU-listed company files under the Transparency Directive), read from filings.xbrl.org. Group totals only, no segments. Where a company has no ESEF report, figures are copied by hand from its published annual accounts and marked “Accounts”.'),
        ('Restated years', 'A later report sometimes restates an earlier year (a business sold, new accounting rules). The newest version of each year is shown and marked R, so the years compare.'),
        ('Share prices', 'Deutsche Börse delayed prices (Xetra, else Börse Frankfurt), converted to euros by the exchange. Market value = price × shares in issue from the report.'),
        ('Exchange rates', 'ECB reference rates. Ratios mixing price and report figures convert the report figures at the rate implied by revenue in euros.'),
        ('Insiders and shorts', 'Managers’ trades from the Swedish regulator (FI); net short positions from the regulators of the Netherlands, Italy, Sweden, Norway and the UK.'),
    ]),
    ('Definitions', [
        ('EBITDA', 'Operating profit + depreciation and amortisation (incl. right-of-use assets), as tagged in the report. Not the company’s “adjusted” EBITDA: one-off items stay in.'),
        ('Net debt', 'Borrowings (current + non-current) − cash and cash equivalents. Lease liabilities are shown separately.'),
        ('Enterprise value (EV)', 'Market value + net debt + lease liabilities (IFRS 16) + minority interests. Not shown for banks and insurers.'),
        ('Free cash flow (FCF)', 'Cash flow from operating activities − purchases of property, plant, equipment and intangible assets.'),
        ('Capex', 'Purchases of property, plant and equipment and of intangible assets, from the cash flow statement.'),
        ('Working capital', 'Inventories + trade receivables − trade payables, at the year end.'),
        ('P/E', 'Share price / basic earnings per share of the last financial year. P/S, P/B, P/FCF: market value / revenue, equity (owners of the parent), free cash flow.'),
        ('EV/EBITDA, EV/EBIT', 'Enterprise value / EBITDA or operating profit of the last financial year.'),
        ('ROE, ROA', 'Net income (owners of the parent) / equity or total assets at the year end.'),
        ('ROIC', 'Operating profit × (1 − tax rate) / (equity + net debt). Tax rate = income tax / profit before tax, held between 15% and 35%.'),
        ('Interest cover', 'Operating profit / finance costs.'),
        ('Growth a year (CAGR)', 'Compound annual growth from FY2021 (2020 was a covid year) to the latest year; from the first year available when fewer years exist.'),
        ('PEG (past growth)', 'P/E / EPS growth a year over the same years. Uses past growth, not analysts’ forecasts.'),
        ('EPS history', 'Earnings per share of older years are put on today’s share basis after share splits, as companies restate them.'),
        ('Peer median', 'Median of the companies of the same industry group on FINILA (GICS-style groups checked by hand, such as Transportation & logistics, when the group has at least 8 companies), else of the same sector; at least 5 values.'),
        ('Quality score', 'Piotroski F-score: nine tests of this year’s report against last year’s; shown as passed / tests possible.'),
    ]),
    ('Analysis', [
        ('Cash conversion', 'Operating cash flow ÷ EBITDA, free cash flow ÷ EBITDA, and operating cash flow ÷ net income, each year from its own report.'),
        ('Working capital in days', 'Inventory days = inventories ÷ cost of sales × 365; receivable days = trade receivables ÷ revenue × 365; payable days = trade payables ÷ cost of sales × 365; cash cycle = inventory + receivable − payable days. Year-end balances. Cost of sales as reported, else revenue − gross profit.'),
        ('ROIC', 'Operating profit × (1 − tax rate) ÷ (equity + net debt + lease liabilities). Tax rate = income tax ÷ profit before tax from the same report, limited to 15–35% (25% when not reported).'),
        ('Incremental ROIC', 'Change in operating profit after tax ÷ change in capital (as in ROIC) between the first and the last year shown, when capital grew by more than 10%.'),
        ('Capex ÷ depreciation', 'Purchases of property, plant, equipment and intangibles ÷ depreciation and amortisation. Above 1× the company invests more than its assets wear out.'),
        ('Where the cash went', 'Sums of the cash flow statements over the years shown: operating cash flow, and the payments for capex, acquisitions of businesses, dividends, share buybacks and lease liabilities, and borrowings repaid minus new borrowings. A line is left out when a year does not report it.'),
        ('From the company’s report', 'Sentences quoted word for word from the newest ESEF report (annual, or half-year where the company files it in ESEF). Kept only when a sentence names a financial line (revenue, margin, capex, dividend …), an expectation, a year ahead and a number; backlog sentences need a figure. Sustainability, pay, audit and market-forecast sentences are left out. Nothing is rewritten.'),
        ('Not shown', 'For banks and insurers, and for vehicle groups with their own bank (their borrowings fund customer loans).'),
    ]),
    ('Scenarios', [
        ('Calculator start', 'Revenue growth: year 1 from the company’s own revenue outlook (midpoint) when its report gives one, else its growth last year; years 2 and 3 the sector median growth; the slider starts at the average of the 3 years. EBITDA margin: the latest year; for energy and materials companies, whose margins swing with prices, the average over the results table (mid-cycle). EV/EBITDA: the sector median. 3 years ahead.'),
        ('Three scenarios', 'Revenue grows for 3 years at the stated rate, EBITDA = revenue × margin, company value = EBITDA × EV/EBITDA, minus today’s net debt, leases and minorities, divided by today’s shares. Weak: no growth, the lowest margin of the last years, the multiple of the cheapest quarter of peers. Middle: peer median growth of the last year, today’s margin, peer median multiple. Strong: the company’s own past growth, its best margin, the multiple of the dearest quarter of peers. Dividends, new shares and debt changes are not counted.'),
        ('What today’s price assumes', 'The revenue growth a year needed for today’s enterprise value to equal, in 3 years, EBITDA at today’s margin × the peer median multiple.'),
    ]),
    ('Checks', [
        ('Unit and currency checks', 'A year whose revenue or assets are about 1,000× off the other years is left out (a report tagged in thousands). Years in another currency are left out. Ratios outside plausible ranges (e.g. P/S above 60) are not shown.'),
        ('When in doubt', 'A figure that fails a check is shown as “—”, never estimated.'),
        ('Not advice', 'FINILA shows figures from official reports and calculations from them. It is not investment advice.'),
    ]),
]


def method_page(updated):
    body = ''.join(f'<section class="sp-card" id="{esc(t.lower())}"><h2>{esc(t)}</h2><table class="sp-table sp-method">' +
                   ''.join(f'<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>' for k, v in items) + '</table></section>' for t, items in METHOD)
    title = 'How FINILA calculates company figures | FINILA'
    desc = 'Sources, definitions (EBITDA, EV, free cash flow, ROIC, CAGR, peer median) and checks behind the FINILA company pages.'
    return head(title, desc, f'{SITE}/stock/methodology') + f'''
<main class="sp-wrap">
  <nav class="sp-crumbs" aria-label="Breadcrumb"><a href="/stock/">Stocks</a> › Methodology</nav>
  <h1 class="sp-title">How FINILA calculates company figures</h1>
  <p class="sp-intro">Every number on a company page comes from an official report, a share price or a calculation from those two, done the same way for every company. Updated {esc(updated)}.</p>
  {body}
</main>
{FOOTER}<script src="/js/analytics.js" defer></script>
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


# Vehicle and machinery groups with their own bank (car loans, leasing): the group's borrowings fund
# customer loans, so group net debt, EV and free cash flow say nothing about the industrial business.
# Analysts use the industrial division's figures, which ESEF does not tag separately: shown as a dash.
CAPTIVE_FINANCE = {
    '529900NNUPAGGOMPXZ31': 'Volkswagen Financial Services', 'YEH5ZCD6E441RHVHD759': 'BMW Financial Services',
    '529900R27DL06UVNT076': 'Mercedes-Benz Mobility', '529900PW78JIYOUBSR24': 'Daimler Truck Financial Services',
    '529900UIMQRKPFN6SV80': 'Traton Financial Services', '549300WGC2HZ5J67V817': 'CNH Capital',
    '529900EWEX125AULXI58': 'Porsche Financial Services', '549300ZWF2ZJDD9EOR96': 'Iveco Capital',
    '5299005KY91C4C6U9H17': 'KION Financial Services', '529900PHZORTU6FSXE73': 'Jungheinrich Finance',
    '549300LKT9PW7ZIBDF31': 'Stellantis Financial Services', '969500F7JLTX36OUI695': 'Mobilize Financial Services',
}


# Where an official filing lives: ESEF reports (filings.xbrl.org), SEC filings, or a company's own report link set by hand.
OFFICIAL = ('filings.xbrl.org', 'sec.gov')
COVER = [('price', 'Price', lambda c: is_num(c.get('price'))),
         ('mcap', 'Market value', lambda c: is_num(c.get('mcap'))),
         ('sector', 'Sector', lambda c: bool(c.get('sector'))),
         ('figures', 'Annual figures', lambda c: is_num(c.get('revenue')) or is_num(c.get('netIncome'))),
         ('official', 'Figures from an official report', lambda c: any(d in (c.get('source') or '') or d in ((c.get('years') or {}).get(max(c.get('years') or {'': 0}), {}).get('source') or '') for d in OFFICIAL)),
         ('history', '3+ years of history', lambda c: len(c.get('years') or {}) >= 3),
         ('about', 'What the company does', lambda c: bool(c.get('about'))),
         ('outlook', 'Outlook from a report', lambda c: bool(c.get('quotes') or (c.get('latestReport') or {}).get('outlook'))),
         ('report', 'Newest quarterly report', lambda c: bool(c.get('latestReport'))),
         ('news', 'Company announcements', lambda c: bool(c.get('news')))]


def coverage(rows):
    """Which parts of the company page template each company fills (Lub 2026-11-06: check regularly
    that every page has what it should). Writes data/page-coverage.json and data/page-coverage.md."""
    pub = [c for c in rows if c.get('origin') != 'listing']
    per = {c['lei']: [k for k, _, f in COVER if not f(c)] for c in pub}
    by_c = defaultdict(list)
    for c in pub:
        by_c[c['country']].append(c)
    pct = lambda cs, f: round(100 * sum(1 for c in cs if f(c)) / len(cs)) if cs else 0
    lines = [f'# Company page coverage, {date.today().isoformat()}', '',
             f'{len(pub)} public company pages. Share of pages that show each part of the template.', '',
             '| Country | Pages | ' + ' | '.join(n for _, n, _ in COVER) + ' |', '|' + '---|' * (len(COVER) + 2)]
    for ctry, cs in sorted(by_c.items(), key=lambda x: -len(x[1])) + [('All', pub)]:
        lines.append(f'| {ctry} | {len(cs)} | ' + ' | '.join(f'{pct(cs, f)}%' for _, _, f in COVER) + ' |')
    big = sorted(pub, key=lambda c: -(c.get('mcap') or c.get('revenueEur') or 0))[:300]
    gaps = [c for c in big if set(per[c['lei']]) & {'price', 'mcap', 'sector', 'figures', 'official', 'history', 'about'}]
    lines += ['', '## Largest companies with gaps (top 300 by size)', '']
    lines += [f'- {c["name"]} ({c["country"]}): missing ' + ', '.join(n for k, n, _ in COVER if k in per[c['lei']]) for c in gaps[:80]]
    (SRC / 'data').mkdir(exist_ok=True)
    (SRC / 'data' / 'page-coverage.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (SRC / 'data' / 'page-coverage.json').write_text(json.dumps({'updated': date.today().isoformat(), 'missing': per},
                                                               separators=(',', ':')), encoding='utf-8')
    print(f'Coverage: {pct(pub, COVER[5][2])}% of pages with history, {len(gaps)} of the 300 largest have gaps (data/page-coverage.md).')


def main():
    updated, price_day, rows = companies()
    for c in rows:
        if c['lei'] in CAPTIVE_FINANCE:
            c['captive'] = CAPTIVE_FINANCE[c['lei']]
            for k in ('netDebt', 'netDebtToEbitda', 'fcf', 'fcfMargin', 'payoutFcf'):
                c[k] = None
    add_signals(rows)
    add_history(rows)
    add_history_figures(rows)
    add_report_quotes(rows)
    add_news(rows)
    add_about(rows)
    for c in rows:
        if c.get('captive') and c.get('years'):
            c['years'] = {y: {k: v for k, v in r.items() if k not in ('netDebt', 'fcf', 'debt')} for y, r in c['years'].items()}
        add_valuation(c)
    # Peers and medians by industry group (data/groups.json, GICS-style: bpost next to PostNL and
    # Österreichische Post, not Siemens), by sector where the group has fewer than 8 companies.
    in_group = Counter(c.get('group') for c in rows if not c.get('preview'))
    group = lambda c: (('g', c['group']) if c.get('group') and in_group[c['group']] >= 8
                       else ('s', c['sector']) if c.get('sector') else None)
    tests = [c for c in rows if c.get('preview')]
    rows = [c for c in rows if not c.get('preview')]
    medians = sector_medians(rows, group)
    slugs = assign_slugs(rows, published_slugs())
    for c in tests:
        c['slug'] = re.sub(r'[^a-z0-9]+', '-', c['name'].lower()).strip('-') + '-us'
    out = OUT / 'stock'
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('*.html'):
        old.unlink()
    by_sector = defaultdict(list)
    for c in rows:
        by_sector[group(c)].append(c)
    for v in by_sector.values():
        v.sort(key=lambda c: -(c.get('mcap') or c.get('revenueEur') or 0))
    stamp = f'{updated}' + (f', prices of {price_day}' if price_day else '')
    for c in rows:
        same = by_sector[group(c)] if group(c) else [p for p in rows if p['country'] == c['country']]
        # the same finer industry first (bpost: PostNL, Österreichische Post before airlines and airports)
        same = sorted(same, key=lambda p: not (c.get('subindustry') and p.get('subindustry') == c['subindustry']))
        peers = [p for p in same if p is not c][:8]
        (out / f'{c["slug"]}.html').write_text(page(c, peers, stamp, medians.get(group(c), {})), encoding='utf-8')
    for c in tests:
        peers = [p for p in by_sector[group(c)]][:8] if group(c) else []
        (out / f'{c["slug"]}.html').write_text(page(c, peers, stamp, medians.get(group(c), {})), encoding='utf-8')
        print(f'Test page (not public): /stock/{c["slug"]}')
    (out / 'index.html').write_text(index_page(rows, updated), encoding='utf-8')
    (out / 'methodology.html').write_text(method_page(updated), encoding='utf-8')
    (OUT / 'js').mkdir(exist_ok=True)
    (OUT / 'js' / 'stock-slugs.json').write_text(json.dumps(slugs, separators=(',', ':')), encoding='utf-8')
    today = date.today().isoformat()
    # The old articles (archive-site/, served under their original addresses) keep their search ranking.
    old = sorted(f'{SITE}/{p.parent.relative_to(SRC / "archive-site").as_posix()}/' for p in (SRC / 'archive-site').glob('*/**/index.html'))
    # Property pages by country (scripts/build-property-pages.py names them the same way).
    housing = json.loads((SRC / 'js' / 'housing.json').read_text()) if (SRC / 'js' / 'housing.json').exists() else {'countries': {}}
    prop = ['property/undervalued'] + sorted(f'property/{re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")}'
                                            for c in housing['countries'].values() if not c.get('agg'))
    urls = [f'{SITE}/{p}' for p in PAGES + prop] + [f'{SITE}/stock/{c["slug"]}' for c in sorted(rows, key=lambda c: c['slug']) if c.get('origin') != 'listing'] + old
    (OUT / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
                                     ''.join(f'  <url><loc>{esc(u)}</loc><lastmod>{today}</lastmod></url>\n' for u in urls) + '</urlset>\n', encoding='utf-8')
    print(f'Built {len(rows)} company pages in {out}, sitemap with {len(urls)} addresses.')
    coverage(rows)


if __name__ == '__main__':
    main()
