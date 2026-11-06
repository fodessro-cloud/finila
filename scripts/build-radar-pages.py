"""Builds the Market Radar pages from js/radar.json, as plain HTML that Google reads without scripts:

  <out>/radar.html                      the overview: every indicator with its light (served as /radar)
  <out>/radar-pages/<indicator>.html    one page per indicator with its history (served as /radar/<indicator>)
  <out>/radar-pages/election-cycle.html the US election cycle (served as /radar/election-cycle)

The header and footer are copied from rates.html, so menu changes there carry over.
Usage: python3 scripts/build-radar-pages.py <site source dir> <out dir>
"""
import html, json, re, sys
from datetime import datetime
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else 'build')
SITE = 'https://www.finila.com'
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
MONTHLY = {'sahm-rule', 'm2-money-supply', 'margin-debt', 'shiller-cape', 'bank-lending-standards', 'euro-bank-lending'}
LIGHT = {'red': 'Red', 'amber': 'Amber', 'green': 'Green'}
COLOR = {'red': '#D23B3B', 'amber': '#B97A0A', 'green': '#0F8A57'}
BLUE = '#1F6FEB'


def esc(v):
    return html.escape(str(v if v is not None else ''), quote=True)


def nice_date(d, monthly=False):
    if not d:
        return ''
    if monthly or len(d) == 7:
        return f'{MONTHS[int(d[5:7]) - 1]} {d[:4]}'
    return f'{int(d[8:10])} {MONTHS[int(d[5:7]) - 1]} {d[:4]}'


def shell(rates):
    head = re.search(r'<header class="fh">.*?</script>', rates, re.S).group(0)
    head = head.replace(' aria-current="page"', '')
    if 'href="/radar"' not in head:
        head = head.replace('<a href="/rates">Rates</a>', '<a href="/rates">Rates</a>\n      <a href="/radar">Radar</a>')
    head = head.replace('<a href="/radar">', '<a href="/radar" aria-current="page">')
    foot = re.search(r'<footer class="h-footer">.*?</footer>', rates, re.S).group(0)
    return head, foot


def page(title, desc, path, body, head, foot, og='rates'):
    url = f'{SITE}/{path}'
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <link rel="canonical" href="{url}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="FINILA">
  <meta property="og:url" content="{url}">
  <meta property="og:title" content="{esc(title)}">
  <meta property="og:description" content="{esc(desc)}">
  <meta property="og:image" content="{SITE}/css/og-image.png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="description" content="{esc(desc)}">
  <link rel="icon" href="/css/logo.svg" type="image/svg+xml">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/css/home.css">
  <link rel="stylesheet" href="/css/invest.css">
  <link rel="stylesheet" href="/css/header.css">
  <link rel="stylesheet" href="/css/radar.css">
</head>
<body>

{head}
<div class="i-banner">Public data from the Federal Reserve, the ECB and Robert Shiller, refreshed every weekday. Not investment advice.</div>

<main class="h-wrap i-main">
{body}
</main>

{foot}

<script src="/js/analytics.js" defer></script>
</body>
</html>
'''


def card(r, link=True):
    lt = r.get('light', 'green')
    tag = 'a' if link else 'div'
    href = f' href="/radar/{esc(r["id"])}"' if link else ''
    stale = ' <span class="rd-stale">· source failed, last known value</span>' if r.get('stale') else ''
    by = ' · checked by hand' if r.get('manual') else ''
    return f'''<{tag} class="rd-card rd-{lt}"{href}>
      <div class="rd-card-top"><span class="rd-card-name">{esc(r["name"])}</span><span class="rd-pill">{LIGHT[lt]}</span></div>
      <div class="rd-val">{esc(r["display"])}</div>
      <div class="rd-rule">{esc(r["rule"])}</div>
      {f'<div class="rd-note">{esc(r["note"])}</div>' if r.get("note") else ''}
      <div class="rd-meta">As of {nice_date(r["date"], r["id"] in MONTHLY)}{by}{stale}</div>
    </{tag}>'''


def line_chart(hist, amber, red, label):
    """Inline SVG line chart of [[YYYY-MM, value]] with the amber and red limits as dashed lines."""
    pts = [(int(m[:4]) + (int(m[5:7]) - 1) / 12, v) for m, v in hist if v is not None]
    if len(pts) < 3:
        return ''
    W, H, L, R, T, B = 760, 300, 52, 16, 16, 30
    x0, x1 = pts[0][0], pts[-1][0] + 1 / 12
    vals = [v for _, v in pts] + [amber, red]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    if min(v for _, v in pts) >= 0 and lo < 0 and min(amber, red) >= 0:
        lo = 0
    X = lambda t: L + (t - x0) / (x1 - x0) * (W - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    step = 10 ** int(f'{(hi - lo) / 5:e}'.split('e')[1])
    for f in (1, 2, 5, 10):
        if (hi - lo) / (step * f) <= 6:
            step *= f
            break
    out = [f'<svg class="rd-chart" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(label)}">']
    v = (lo // step + 1) * step
    while v < hi:
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#DCE2EC"/>'
                   f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
        v += step
    span = x1 - x0
    ys = 5 if span < 30 else 10 if span < 60 else 20
    y = int(x0 // ys + 1) * ys
    while y < x1:
        out.append(f'<text x="{X(y):.1f}" y="{H - 8}" text-anchor="middle">{y}</text>')
        y += ys
    for lim, c in ((amber, COLOR['amber']), (red, COLOR['red'])):
        if lo <= lim <= hi:
            out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(lim):.1f}" y2="{Y(lim):.1f}" stroke="{c}" stroke-dasharray="5 4" stroke-width="1.5"/>')
    d = ' '.join(f'{"M" if i == 0 else "L"}{X(t):.1f},{Y(v):.1f}' for i, (t, v) in enumerate(pts))
    out.append(f'<path d="{d}" fill="none" stroke="{BLUE}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    lt, lv = pts[-1]
    out.append(f'<circle cx="{X(lt):.1f}" cy="{Y(lv):.1f}" r="4.5" fill="{BLUE}"/>')
    out.append('</svg>')
    return ''.join(out)


MONEY = [('fed-balance-sheet', 'Fed balance sheet', '#1F6FEB'), ('ecb-balance-sheet', 'ECB balance sheet', '#7C3AED'),
         ('m2-money-supply', 'US money supply M2', '#0E9FB3')]


def money_chart(by_id, start='2008-01'):
    """Fed and ECB balance sheets and M2, year-on-year, on one chart (Lub, 2026-11-06)."""
    series = [(lab, col, [(int(m[:4]) + (int(m[5:7]) - 1) / 12, v) for m, v in by_id[i]['history'] if m >= start and v is not None])
              for i, lab, col in MONEY if i in by_id and by_id[i].get('history')]
    series = [x for x in series if len(x[2]) > 3]
    if len(series) < 2:
        return ''
    W, H, L, R, T, B = 760, 320, 52, 16, 16, 30
    allp = [p for _, _, ps in series for p in ps]
    x0, x1 = min(t for t, _ in allp), max(t for t, _ in allp) + 1 / 12
    lo, hi = min(min(v for _, v in allp), 0), max(v for _, v in allp)
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    X = lambda t: L + (t - x0) / (x1 - x0) * (W - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    step = next(st for st in (5, 10, 20, 25, 50) if (hi - lo) / st <= 7)
    out = [f'<svg class="rd-chart" viewBox="0 0 {W} {H}" role="img" aria-label="Fed and ECB balance sheets and M2, change on a year earlier">']
    v = (lo // step + 1) * step
    while v < hi:
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{"#8A94A6" if v == 0 else "#DCE2EC"}"/>'
                   f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:+g}%</text>')
        v += step
    y = int(x0 // 2 + 1) * 2
    while y < x1:
        out.append(f'<text x="{X(y):.1f}" y="{H - 8}" text-anchor="middle">{y}</text>')
        y += 2
    for lab, col, ps in series:
        d = ' '.join(f'{"M" if k == 0 else "L"}{X(t):.1f},{Y(v):.1f}' for k, (t, v) in enumerate(ps))
        out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>')
        out.append(f'<circle cx="{X(ps[-1][0]):.1f}" cy="{Y(ps[-1][1]):.1f}" r="4" fill="{col}"/>')
    out.append('</svg>')
    legend = ''.join(f'<span><i class="rd-sw" style="background:{col}"></i>{esc(lab)} {esc(by_id[i]["display"])}</span>'
                     for i, lab, col in MONEY if i in by_id)
    return f'''<div class="h-card rd-money">
      <h3>Money in the system: Fed, ECB and M2 together</h3>
      <div class="rd-legend">{legend}</div>
      {''.join(out)}
      <p class="h-note">Change on a year earlier, month-end values since 2008. Sources: Federal Reserve H.4.1 and H.6, European Central Bank, via FRED.</p>
    </div>'''


def cycle_chart(cycle):
    W, H, L, R, T, B = 760, 320, 44, 8, 30, 52
    mx = max(20, max(max(c['average'], c['actual'] or 0) for c in cycle))
    n = len(cycle)
    gw = (W - L - R) / n
    bw = min(32, gw / 2 - 6)
    Y = lambda v: T + (mx - v) / mx * (H - T - B)
    out = [f'<svg class="rd-chart" viewBox="0 0 {W} {H}" role="img" aria-label="S&amp;P 500 return by phase of the US election cycle">']
    for v in range(0, mx + 1, 5):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#DCE2EC"/><text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">+{v}%</text>')
    now = next((i for i, c in enumerate(cycle) if c['actual'] is None), None)
    for i, c in enumerate(cycle):
        cx = L + gw * i + gw / 2
        has = c['actual'] is not None
        if i == now:
            out.append(f'<rect x="{L + gw * i + 3:.1f}" y="{T - 24}" width="{gw - 6:.1f}" height="{H - B - T + 24}" rx="6" fill="{COLOR["amber"]}" opacity=".1"/>'
                       f'<text x="{cx:.1f}" y="{T - 10}" text-anchor="middle" class="lbl" style="fill:{COLOR["amber"]}">We are here</text>')
        hx = cx - bw - 2 if has else cx - bw / 2
        fill = COLOR['amber'] if i == now else '#A9B6CC'
        out.append(f'<rect x="{hx:.1f}" y="{Y(c["average"]):.1f}" width="{bw:.1f}" height="{Y(0) - Y(c["average"]):.1f}" rx="3" fill="{fill}"/>'
                   f'<text x="{hx + bw / 2:.1f}" y="{Y(c["average"]) - 5:.1f}" text-anchor="middle" class="lbl">+{c["average"]}%</text>')
        if has:
            out.append(f'<rect x="{cx + 2:.1f}" y="{Y(c["actual"]):.1f}" width="{bw:.1f}" height="{Y(0) - Y(c["actual"]):.1f}" rx="3" fill="{BLUE}"/>'
                       f'<text x="{cx + 2 + bw / 2:.1f}" y="{Y(c["actual"]) - 5:.1f}" text-anchor="middle" class="lbl" style="fill:{BLUE}">+{c["actual"]}%</text>')
        per = c['period'].replace('Nov ', '').replace(' – ', '–')
        out.append(f'<text x="{cx:.1f}" y="{H - B + 18}" text-anchor="middle" class="lbl">Nov {esc(per)}</text>'
                   f'<text x="{cx:.1f}" y="{H - B + 34}" text-anchor="middle">{esc(c["phase"].replace(" year", ""))}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="#C3CCDB"/></svg>')
    return ''.join(out)


CYCLE_TEXT = '''
    <div class="rd-about">
      <p>US stocks have followed a rough four-year rhythm tied to the presidential term. The two years before the midterm elections are usually the weakest, and the year after the midterms has been the strongest: since 1950 the S&amp;P 500 rose in the twelve months after almost every midterm election, by about 15% on average.</p>
      <p>Market bottoms cluster in midterm years, often in September or October: 1962, 1966, 1970, 1974, 1982, 1990, 1998, 2002, 2018 and 2022. Peaks show no such pattern. The worst falls, 1929, 2000–02 and 2008, came after high valuations in different phases of the cycle.</p>
      <p>A good average does not rule out a bad year. In 1987 the market crashed in October of the post-midterm year, and the 2007 peak came less than a year after the 2006 midterms. The cycle can tell you which way the odds have leaned, not what will happen.</p>
    </div>'''


def main():
    data = json.loads((SRC / 'js' / 'radar.json').read_text())
    rates = (SRC / 'rates.html').read_text()
    head, foot = shell(rates)
    inds = data['indicators']
    by_group = {g[0]: [r for r in inds if r['group'] == g[0]] for g in data['groups']}
    by_id = {r['id']: r for r in inds}
    c = data['counts']
    upd = data['updated'][:10]
    (OUT / 'radar-pages').mkdir(parents=True, exist_ok=True)

    # Overview
    groups_html = []
    for gid, gname, gtext in data['groups']:
        cards = '\n'.join(card(r) for r in by_group[gid])
        extra = money_chart(by_id) if gid == 'liquidity' else ''
        groups_html.append(f'''  <section class="h-section" id="{gid}">
    <div class="rd-group-head"><h2>{esc(gname)}</h2><p>{esc(gtext)}</p></div>
    <div class="rd-grid">
{cards}
    </div>
    {extra}
  </section>''')
    reds = [r['short'] for r in inds if r.get('light') == 'red']
    body = f'''  <section class="i-intro">
    <div class="h-eyebrow">MARKET RADAR</div>
    <h1>Stock market signals today</h1>
    <p>{len(inds)} indicators of where the stock market stands in its cycle, each with a traffic light: valuation, recession signals, credit, market breadth, the euro area and central bank liquidity. Red means an indicator is past a level that came before trouble in the past. No indicator tells you when the market will turn.</p>
    <span class="i-updated">Updated {nice_date(upd)}</span>
  </section>

  <section class="h-section">
    <div class="rd-counts">
      <div class="rd-count rd-red"><b>{c["red"]}</b><span><i class="rd-dot"></i>Red, past the limit{": " + esc(", ".join(reds)) if reds else ""}</span></div>
      <div class="rd-count rd-amber"><b>{c["amber"]}</b><span><i class="rd-dot"></i>Amber, worth watching</span></div>
      <div class="rd-count rd-green"><b>{c["green"]}</b><span><i class="rd-dot"></i>Green, normal range</span></div>
    </div>
    <nav class="rd-jump" aria-label="Sections">{"".join(f'<a href="#{g[0]}">{esc(g[1])}</a>' for g in data["groups"])}<a href="#election-cycle">Election cycle</a></nav>
  </section>

{chr(10).join(groups_html)}

  <section class="h-section" id="election-cycle">
    <div class="rd-group-head"><h2>US election cycle</h2><p>S&amp;P 500 return from November to November by phase between elections. <a href="/radar/election-cycle">More on the election cycle</a></p></div>
    <div class="h-card">
      <div class="rd-legend"><span><i class="rd-sw" style="background:#A9B6CC"></i>Average since 1950</span><span><i class="rd-sw" style="background:{BLUE}"></i>Actual</span><span><i class="rd-sw" style="background:{COLOR["amber"]}"></i>We are here</span></div>
      {cycle_chart(data["cycle"])}
      <p class="h-note">Averages are approximate, from the Stock Trader's Almanac and LPL Research. Future periods show the historical average, not a forecast.</p>
    </div>
  </section>

  <section class="h-section i-split">
    <div class="h-card i-method">
      <h2>How the lights work</h2>
      <ul>
        <li><strong>Green:</strong> the indicator is in its normal range.</li>
        <li><strong>Amber:</strong> it is nearing levels that came before falls in the past.</li>
        <li><strong>Red:</strong> it is past a level after which trouble followed in the past.</li>
        <li>The limits are a simplification based on history, not official rules. Several reds at once matter more than any single one.</li>
      </ul>
    </div>
    <div class="h-card i-method">
      <h2>Sources</h2>
      <ul>
        <li>FRED, Federal Reserve Bank of St. Louis: Treasury yields, Sahm rule, St. Louis Fed financial stress index, Chicago Fed financial conditions index, M2, Fed and ECB balance sheets, bank reserves, Fed Z.1 equities and GDP.</li>
        <li>Robert Shiller's own data (Yale, shillerdata.com): cyclically adjusted P/E, monthly.</li>
        <li>Checked by hand: forward P/E and earnings estimates (FactSet), margin debt (FINRA), breadth and equal weight.</li>
      </ul>
      {f'<p class="h-note">Not updated today, last known value shown: {esc(", ".join(data["failed"]))}.</p>' if data.get("failed") else ''}
    </div>
  </section>'''
    lights = f'{c["red"]} red, {c["amber"]} amber, {c["green"]} green'
    (OUT / 'radar.html').write_text(page(
        'Market Radar: stock market indicators today | FINILA',
        f'Where does the stock market stand? {len(inds)} indicators with traffic lights, updated daily: Shiller CAPE, Buffett indicator, yield curve, Sahm rule, financial stress, Fed balance sheet. Today: {lights}.',
        'radar', body, head, foot))

    # One page per indicator
    for r in inds:
        group = next(g for g in data['groups'] if g[0] == r['group'])
        related = ''.join(f'<a href="/radar/{esc(o["id"])}">{esc(o["short"])}</a>' for o in by_group[r['group']] if o['id'] != r['id'])
        chart = line_chart(r.get('history') or [], r['amber'], r['red'], f'{r["name"]} over time')
        chart_html = f'''  <section class="h-section">
    <div class="h-card">
      <div class="rd-legend"><span><i class="rd-sw" style="background:{BLUE}"></i>{esc(r["short"])}</span><span><i class="rd-sw" style="background:{COLOR["amber"]}"></i>Amber limit</span><span><i class="rd-sw" style="background:{COLOR["red"]}"></i>Red limit</span></div>
      {chart}
      <p class="h-note">Month-end values. Source: {esc(r["source"])}.</p>
    </div>
  </section>''' if chart else ''
        about = ''.join(f'<p>{esc(p)}</p>' for p in r.get('about', []))
        lt = r.get('light', 'green')
        body = f'''  <section class="i-intro">
    <a class="rd-back" href="/radar#{esc(r["group"])}">← Market Radar · {esc(group[1])}</a>
    <h1>{esc(r["name"])} today</h1>
    <div class="rd-hero rd-{lt}"><span class="rd-val">{esc(r["display"])}</span><span class="rd-pill">{LIGHT[lt]}</span></div>
    <p>{esc(r["rule"])} {esc(r.get("note") or "")}</p>
    <span class="i-updated">As of {nice_date(r["date"], r["id"] in MONTHLY)} · {esc(r["source"])}{" · checked by hand" if r.get("manual") else ""}</span>
  </section>

{chart_html}
{f'  <section class="h-section">{money_chart(by_id)}</section>' if r['id'] in [m[0] for m in MONEY] else ''}

  <section class="h-section">
    <div class="rd-group-head"><h2>What it measures</h2></div>
    <div class="rd-about">{about}</div>
  </section>

  <section class="h-section">
    <div class="rd-group-head"><h2>More {esc(group[1].lower())} signals</h2></div>
    <div class="rd-related">{related}<a href="/radar">All {len(inds)} indicators</a></div>
  </section>'''
        desc = f'{r["name"]} today: {r["display"]} ({LIGHT[lt].lower()} light), as of {nice_date(r["date"], r["id"] in MONTHLY)}. {r["rule"]} History chart and what it means for stocks.'
        (OUT / 'radar-pages' / f'{r["id"]}.html').write_text(page(
            f'{r["name"]} today: {r["display"]} | FINILA Market Radar', desc, f'radar/{r["id"]}', body, head, foot))

    # Election cycle
    body = f'''  <section class="i-intro">
    <a class="rd-back" href="/radar#election-cycle">← Market Radar</a>
    <h1>The US election cycle and the stock market</h1>
    <p>S&amp;P 500 return from November to November in each phase between US elections: the historical average since 1950 next to what actually happened. The midterm elections were on 3 November 2026; the next presidential election is on 7 November 2028.</p>
  </section>
  <section class="h-section">
    <div class="h-card">
      <div class="rd-legend"><span><i class="rd-sw" style="background:#A9B6CC"></i>Average since 1950</span><span><i class="rd-sw" style="background:{BLUE}"></i>Actual</span><span><i class="rd-sw" style="background:{COLOR["amber"]}"></i>We are here</span></div>
      {cycle_chart(data["cycle"])}
      <p class="h-note">Averages are approximate, from the Stock Trader's Almanac and LPL Research. Future periods show the historical average, not a forecast.</p>
    </div>
  </section>
  <section class="h-section">
    <div class="rd-group-head"><h2>What history shows</h2></div>
    {CYCLE_TEXT}
  </section>
  <section class="h-section">
    <div class="rd-related"><a href="/radar/shiller-cape">Shiller CAPE</a><a href="/radar/buffett-indicator">Buffett indicator</a><a href="/radar/yield-curve-10y-2y">Yield curve</a><a href="/radar">All indicators</a></div>
  </section>'''
    (OUT / 'radar-pages' / 'election-cycle.html').write_text(page(
        'US election cycle and the stock market: where we are now | FINILA',
        'S&P 500 returns by phase of the US presidential cycle since 1950, with the actual results of 2024–2026. The post-midterm year has been the strongest; we are in it now.',
        'radar/election-cycle', body, head, foot))
    print(f'Built radar.html and {len(inds) + 1} radar pages in {OUT}.')


if __name__ == '__main__':
    main()
