// FINILA homepage: every block shows real data from the subpages' data files
// (js/companies.json + js/company-prices.json, js/invest.json, js/insiders-home.json, js/shorts.json).

const fmt = (n, d = 2) => Number(n).toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
const isNum = v => typeof v === 'number' && isFinite(v);
const pct = n => isNum(n) ? (n >= 0 ? '+' : '−') + fmt(Math.abs(n)) + '%' : '—';
const pct1 = n => isNum(n) ? (n >= 0 ? '+' : '−') + fmt(Math.abs(n), 1) + '%' : '—';
const dir = n => (!isNum(n) ? '' : n >= 0 ? 'up' : 'down');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const bnEur = v => isNum(v) ? `€${fmt(v / 1000, v >= 100000 ? 0 : 1)}B` : '—';
// Each company has its own page (/stock/<name>, built by scripts/build-stock-pages.py); the screener is the fallback.
let SLUGS = {};
const coLink = c => SLUGS[c.lei] ? `/stock/${SLUGS[c.lei]}` : `/screener?lei=${encodeURIComponent(c.lei)}`;
const load = async url => { try { return await (await fetch(url, { cache: 'no-cache' })).json(); } catch { return null; } };

let COS = [];

// Companies with their last price. Market value, P/E and dividend yield are estimated the same
// way as on /screener (js/companies.js): shares in issue where known, else net income / EPS,
// implausible values dropped.
function buildCompanies(cos, prices) {
  const list = cos.rows.map(r => Object.fromEntries(cos.fields.map((f, i) => [f, r[i]])));
  if (!prices) return list;
  const [iP, iC] = ['price', 'change'].map(f => prices.fields.indexOf(f));
  list.forEach(c => {
    if (isNum(c.fscore)) c.quality = c.fscore / ((c.fscoreTests || '').replace(/-/g, '').length || 9);
    const q = prices.rows[c.isin];
    if (!q) return;
    c.price = q[iP]; c.change = q[iC];
    const fx = isNum(c.revenueEur) && isNum(c.revenue) && c.revenue ? c.revenueEur / c.revenue : c.currency === 'EUR' ? 1 : null;
    const shares = isNum(c.shares) && c.shares > 0 ? c.shares : null;   // from data/shares.json or the annual report (universe.py)
    if (!shares) return;
    const mcap = c.price * shares, pe = fx && c.eps > 0 ? c.price / (c.eps * fx) : null;
    const ps = isNum(c.revenueEur) && c.revenueEur > 0 ? mcap / c.revenueEur : null;
    if (ps !== null ? ps < 0.02 || ps > 60 : pe !== null && (pe < 1 || pe > 150)) return;
    c.mcap = mcap;
    if (pe !== null && pe >= 1 && pe <= 1000) c.pe = pe;
    if (fx && c.dividendsPaid > 0) { const y = c.dividendsPaid * fx / mcap * 100; if (y < 25) c.divYield = y; }
  });
  return list;
}

// ── Ticker bar + market cards (ECB, Eurostat, World Bank via /invest) ─
function renderMarket(inv) {
  const n = inv?.now?.c?.U2 || {}, dfr = inv?.now?.dfr;
  const cards = [
    ['Stocks · EURO STOXX 50', n.stocks_12m, 'Last 12 months, incl. dividends', true],
    ['Gold', n.gold_12m, 'Last 12 months, in EUR', true],
    ['Property · euro area', n.house_12m, 'House prices, last 12 months', true],
    ['Inflation · euro area', n.inflation, 'Prices, last 12 months', false],
    ['ECB deposit rate', dfr, 'Per year', false],
    ['10-year govt bond', n.bond10, 'Euro area yield, per year', false],
  ].filter(c => isNum(c[1]?.value));
  const val = (o, change) => change ? pct(o.value) : `${fmt(o.value)}%`;
  const month = m => { const d = /^(\d{4})-(\d{2})/.exec(m || ''); return d ? new Date(Date.UTC(+d[1], +d[2] - 1)).toLocaleString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' }) : ''; };
  const cardsEl = document.getElementById('index-cards');
  if (cardsEl) cardsEl.innerHTML = cards.map(([name, o, sub, change]) =>
    `<a href="/assets"><span class="name">${name}</span>
      <span class="val ${change ? dir(o.value) : ''}">${val(o, change)}</span><span class="sub">${sub}</span><span class="when">${month(o.month)}</span></a>`).join('');
  // Ticker: only what moves today, like the market map: Europe as a whole, then the largest companies.
  const priced = COS.filter(c => isNum(c.mcap) && isNum(c.change));
  const cap = priced.reduce((a, c) => a + c.mcap, 0);
  const europe = cap ? priced.reduce((a, c) => a + c.mcap * c.change, 0) / cap : null;
  const big = priced.sort((a, b) => b.mcap - a.mcap).slice(0, 20);
  const items = (isNum(europe) ? `<span><span class="name">Europe today</span><span class="${dir(europe)}">${pct(europe)}</span></span>` : '') +
    big.map(c => `<a href="${coLink(c)}"><span class="name">${esc(c.ticker || c.name)}</span>€${fmt(c.price)}<span class="chg ${dir(c.change)}">${pct(c.change)}</span></a>`).join('');
  const row = document.getElementById('ticker');
  // The row is written twice so the scrolling loop has no gap; the copy is hidden from screen readers.
  row.innerHTML = items ? `<div class="h-ticker-set">${items}</div><div class="h-ticker-set" aria-hidden="true">${items}</div>` : '';
}

// ── Sector map: daily change weighted by market value ─
function tileColor(c) {
  if (!isNum(c)) return '#E3E9F3';
  if (c >= 1)    return '#34C38F';
  if (c >= 0.4)  return '#7EDDB4';
  if (c >= 0)    return '#B9EBD3';
  if (c > -0.4)  return '#FFB3B3';
  if (c > -1)    return '#FF8A8A';
  return '#FF6060';
}
function renderSectorMap() {
  const bySector = {};
  COS.filter(c => c.sector && isNum(c.mcap)).forEach(c => {
    const g = bySector[c.sector] || (bySector[c.sector] = { cap: 0, w: 0, wc: 0 });
    g.cap += c.mcap;
    if (isNum(c.change)) { g.w += c.mcap * c.change; g.wc += c.mcap; }
  });
  const sectors = Object.entries(bySector)
    .map(([name, g]) => ({ name, cap: g.cap, change: g.wc ? g.w / g.wc : null }))
    .sort((a, b) => b.cap - a.cap).slice(0, 14);
  const sizes = ['big', 'big', 'mid', 'mid', 'mid'];
  document.getElementById('sector-map').innerHTML = sectors.map((s, i) =>
    `<a class="${sizes[i] || 'sm'} ${s.change < 0 ? 'neg' : ''}" href="/screener" style="background:${tileColor(s.change)}">
      ${esc(s.name)}<span>${isNum(s.change) ? pct(s.change) : bnEur(s.cap)}</span></a>`).join('');
  if (!sectors.some(s => isNum(s.change)))
    document.getElementById('map-note').textContent = 'Market value by sector. The daily change appears after the next trading day.';
}

// ── Value-investor screens on real annual reports and prices ─
const PRESETS = [
  { id: 'quality', title: 'Quality at a fair price', rule: 'Strong quality score (3/4 of tests or more), P/E below 15 and positive free cash flow',
    test: c => c.quality >= 0.75 && c.pe > 0 && c.pe < 15 && c.fcf > 0, sort: (a, b) => b.mcap - a.mcap,
    metric: c => `P/E ${fmt(c.pe, 1)}` },
  { id: 'dividend', title: 'Covered dividends', rule: 'Dividend yield above 4%, paid out of less than 60% of free cash flow',
    test: c => c.divYield > 4 && c.payoutFcf > 0 && c.payoutFcf <= 60, sort: (a, b) => b.mcap - a.mcap,
    metric: c => `Div. ${fmt(c.divYield, 1)}%` },
  { id: 'cash', title: 'Cash machines', rule: 'Free cash flow above 15% of revenue, ROE above 15% and net debt under 1× EBITDA',
    test: c => c.fcfMargin > 15 && c.roe > 15 && isNum(c.netDebtToEbitda) && c.netDebtToEbitda < 1 && isNum(c.mcap), sort: (a, b) => b.mcap - a.mcap,
    metric: c => `FCF ${fmt(c.fcfMargin, 0)}%` },
];
function renderPresets() {
  document.getElementById('presets').innerHTML = PRESETS.map(p => {
    const hits = COS.filter(p.test).sort(p.sort);
    const rows = hits.slice(0, 5).map(c =>
      `<li><span><a href="${coLink(c)}">${esc(c.ticker || '')}</a><span class="nm">${esc(c.name)}</span></span><span class="metric">${p.metric(c)}</span></li>`
    ).join('');
    return `<article class="h-card h-preset" id="preset-${p.id}">
      <div><h3>${p.title}</h3><p class="rule">${p.rule}</p></div>
      <ul>${rows}</ul>
      <a class="more" href="/screener">All ${hits.length} results in the screener</a>
    </article>`;
  }).join('');
  document.querySelectorAll('[data-preset]').forEach(chip => chip.addEventListener('click', () => {
    const card = document.getElementById('preset-' + chip.dataset.preset);
    card.classList.add('flash');
    setTimeout(() => card.classList.remove('flash'), 1500);
  }));
}

// ── Total and yearly return over 5/10/20 years, euro area (same data as /invest) ─
const ASSETS = [['stocks', 'Stocks (EURO STOXX 50)'], ['us', 'US stocks (S&P 500)'], ['gold', 'Gold'], ['house', 'Property'], ['bonds', 'Government bonds'],
  ['deposit', 'Term deposit'], ['savings', 'Savings account'], ['cash', 'Cash after inflation']];

const mIdx = m => +m.slice(0, 4) * 12 + +m.slice(5, 7) - 1;
let INV = null;
function series(key) {
  const a = INV?.assets?.U2?.[key] || INV?.assets?.EUR?.[key];
  if (!a) return null;
  const [start, vals] = a, s0 = mIdx(start);
  return { first: s0, last: s0 + vals.length - 1, at: i => vals[i - s0] };
}
function renderCompare(years) {
  const grow = s => { const from = s.last - years * 12; return s.first <= from ? s.at(s.last) / s.at(from) : null; };
  const prices = series('prices'), infl = prices && grow(prices);
  const rows = ASSETS.map(([k, label], i) => {
    const g = k === 'cash' ? (infl ? 1 / infl : null) : (series(k) && grow(series(k)));
    return g ? { label, total: (g - 1) * 100, year: (Math.pow(g, 1 / years) - 1) * 100, color: null } : null;
  }).filter(Boolean).sort((a, b) => b.total - a.total);
  // Bars start at the largest loss, so a negative return still shows as a (short) bar.
  const lo = Math.min(0, ...rows.map(r => r.total)), hi = Math.max(...rows.map(r => r.total));
  document.getElementById('compare').innerHTML = rows.map(r =>
    `<div class="h-bar"><span class="label">${r.label}</span>
      <div class="track"><div class="fill" style="width:${Math.max(4, Math.round((r.total - lo) / (hi - lo || 1) * 100))}%;background:${r.total >= 0 ? 'var(--blue)' : 'var(--down)'}"></div></div>
      <span class="val ${dir(r.total)}">${pct1(r.total)}<small>${pct1(r.year)} a year</small></span></div>`
  ).join('') || '<p class="h-note">Could not load the comparison.</p>';
  document.querySelectorAll('#periods button').forEach(b => b.classList.toggle('on', Number(b.dataset.years) === years));
}

// ── Movers table ──────────────────────────────────
function renderMovers(tab) {
  const priced = COS.filter(c => isNum(c.price) && isNum(c.mcap) && c.mcap >= 1000);   // €1B+ so tiny stocks do not dominate
  const withChange = priced.filter(c => isNum(c.change));
  let list = tab === 'big' || !withChange.length ? [...priced].sort((a, b) => b.mcap - a.mcap)
    : tab === 'up' ? withChange.sort((a, b) => b.change - a.change) : withChange.sort((a, b) => a.change - b.change);
  const note = tab !== 'big' && !withChange.length
    ? '<tr><td colspan="6" class="c">Daily changes appear after the next trading day. Largest companies for now:</td></tr>' : '';
  document.getElementById('movers').innerHTML = note + list.slice(0, 8).map(c =>
    `<tr><td><a href="${coLink(c)}">${esc(c.ticker || '—')}</a></td><td>${esc(c.name)}</td>
      <td class="c">${esc(c.country)}</td>
      <td class="r m">${fmt(c.price)}</td><td class="r m ${dir(c.change)}">${pct(c.change)}</td>
      <td class="r m c">${isNum(c.pe) ? fmt(c.pe, 1) : '—'}</td></tr>`
  ).join('');
  document.querySelectorAll('#tabs button').forEach(b => {
    const on = b.dataset.tab === tab;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', on);
  });
}

// ── FINILA Data: insider buying and fund short positions ─
function renderData(ins, sh) {
  const eur = v => v >= 1e6 ? `€${fmt(v / 1e6, 1)}M` : `€${fmt(v / 1e3, 0)}k`;
  // js/insiders-home.json: the largest buys, already added up per company by scripts/data/insiders_se.py.
  document.getElementById('insider-list').innerHTML = (ins?.buys || []).slice(0, 4)
    .map(b => `<li><span><a href="/insiders?q=${encodeURIComponent(b.issuer)}">${esc(b.issuer)}</a> · ${b.people} ${b.people === 1 ? 'person' : 'people'}</span><span class="v">${eur(b.v)}</span></li>`).join('')
    || '<li><span>No data right now</span></li>';
  const shorts = {};
  (sh?.positions || []).forEach(p => { const s = shorts[p.isin] || (shorts[p.isin] = { name: p.issuer, pct: 0 }); s.pct += p.pct; });
  document.getElementById('short-list').innerHTML = Object.entries(shorts).sort((a, b) => b[1].pct - a[1].pct).slice(0, 4)
    .map(([isin, s]) => `<li><span><a href="/shorts?q=${encodeURIComponent(isin)}">${esc(s.name)}</a></span><span class="v">${fmt(s.pct)}%</span></li>`).join('')
    || '<li><span>No data right now</span></li>';
}

// ── Property prices by country (js/housing.json) ─
function renderProperty(h) {
  const box = document.getElementById('property');
  if (!h || !h.countries) { box.hidden = true; return; }
  const all = Object.entries(h.countries).filter(([, c]) => !c.agg);
  const sign = v => `${v > 0 ? '+' : ''}${fmt(v, 0)}%`;
  const li = (k, c, v) => `<li><span><a href="/property?c=${k}">${esc(c.name)}</a></span><span class="v">${sign(v)}</span></li>`;
  const val = all.filter(([, c]) => isNum(c.valuation)).sort((a, b) => a[1].valuation - b[1].valuation);
  document.getElementById('home-cheap').innerHTML = val.slice(0, 3).map(([k, c]) => li(k, c, c.valuation)).join('');
  document.getElementById('home-dear').innerHTML = val.slice(-3).reverse().map(([k, c]) => li(k, c, c.valuation)).join('');
  const chg = all.filter(([, c]) => isNum(c.nominal['5y'])).sort((a, b) => b[1].nominal['5y'] - a[1].nominal['5y']);
  document.getElementById('home-up').innerHTML = chg.slice(0, 3).map(([k, c]) => li(k, c, c.nominal['5y'])).join('');
  document.getElementById('home-down').innerHTML = chg.slice(-3).reverse().map(([k, c]) => li(k, c, c.nominal['5y'])).join('');
}

// ── Interest rates (js/rates.json) ─
function renderRates(r) {
  const box = document.getElementById('rates');
  if (!r || !r.series || !r.series.length) { box.hidden = true; return; }
  const by = Object.fromEntries(r.series.map(s => [s.key, s]));
  const li = k => {
    const s = by[k]; if (!s) return '';
    const vals = s.values, last = vals.length - 1, before = vals[last - 12];
    const chg = isNum(vals[last]) && isNum(before) ? vals[last] - before : null;
    const ch = isNum(chg) ? ` <small class="${chg > 0 ? 'up' : chg < 0 ? 'down' : ''}">${chg > 0 ? '+' : ''}${fmt(chg, 2)} pp</small>` : '';
    return `<li><span><a href="/rates">${esc(s.label)}</a></span><span class="v">${fmt(s.latest.value, 2)}%${ch}</span></li>`;
  };
  document.getElementById('rates-money').innerHTML = ['ecb', 'eur1m', 'eur3m', 'eur6m', 'eur12m', 'dep1y'].map(li).join('');
  document.getElementById('rates-bonds').innerHTML = ['gov2y', 'gov5y', 'gov10y', 'aaa10y', 'corp'].map(li).join('');
}

// ── Search: opens the company in the screener ─────
function setupSearch() {
  document.getElementById('stock-list').innerHTML = COS.filter(c => isNum(c.mcap)).sort((a, b) => b.mcap - a.mcap)
    .map(c => `<option value="${esc(c.name)}">${esc(c.ticker || '')}</option>`).join('');
  document.querySelector('.h-search').addEventListener('submit', e => {
    e.preventDefault();
    const q = document.getElementById('q').value.trim().toLowerCase();
    if (!q) { location.href = '/screener'; return; }
    const hit = COS.find(c => (c.ticker || '').toLowerCase() === q || c.name.toLowerCase() === q);
    location.href = hit ? coLink(hit) : `/screener?q=${encodeURIComponent(q)}`;
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  const [cos, prices, inv, ins, sh, slugs, housing, rates] = await Promise.all(
    ['js/companies.json', 'js/company-prices.json', 'js/invest.json', 'js/insiders-home.json', 'js/shorts.json', 'js/stock-slugs.json', 'js/housing.json', 'js/rates.json'].map(load));
  SLUGS = slugs || {};
  COS = cos ? buildCompanies(cos, prices) : [];
  INV = inv;
  renderMarket(inv);
  renderSectorMap();
  renderPresets();
  renderCompare(15);
  renderMovers('up');
  renderData(ins, sh);
  renderProperty(housing);
  renderRates(rates);
  setupSearch();
  document.querySelectorAll('#periods button').forEach(b => b.addEventListener('click', () => renderCompare(Number(b.dataset.years))));
  document.querySelectorAll('#tabs button').forEach(b => b.addEventListener('click', () => renderMovers(b.dataset.tab)));
});
