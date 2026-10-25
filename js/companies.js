// FINILA fundamentals screener: real figures from annual reports (js/companies.json,
// built by scripts/data/universe.py). Amounts are in millions of the report currency.

const PAGE = 50;
let COMPANIES = [], UPDATED = '', PRICES_DAY = '', SLUGS = {};
let sortKey = 'mcap', sortDir = -1, page = 1, query = '';
const filters = {};

const isNum = v => typeof v === 'number' && isFinite(v);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const num = (v, d = 1) => isNum(v) ? v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
const pctCell = v => isNum(v) ? `<span class="${v >= 0 ? 'pos' : 'neg'}">${num(v)}%</span>` : '—';
const bn = (v, cur = '') => isNum(v) ? `${num(v / 1000, 2)}B${cur ? ' ' + cur : ''}` : '—';
const countryNames = (() => {
  try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; }
})();
const countryName = c => (c && countryNames ? countryNames.of(c) : c) || '—';
const ndCell = v => isNum(v) ? (v < 0 ? '<span class="pos">net cash</span>' : `${num(v, 2)}×`) : '—';
const eur = v => !isNum(v) ? '—' : Math.abs(v) >= 1e6 ? `€${num(v / 1e6, 1)}M` : `€${num(v / 1e3, 0)}k`;

// FINILA scores. Quality = Piotroski F-score (0-9) from this and last year's annual report.
const F_TESTS = ['Profitable', 'Positive operating cash flow', 'Return on assets improved', 'Cash flow above profit',
  'Long-term debt / assets not up', 'Current ratio improved', 'No new shares issued', 'Gross margin improved', 'Asset turnover improved'];
// Some reports lack a figure (e.g. no gross profit line), so a score is "tests passed / tests possible".
const scoreCell = c => !isNum(c.fscore) ? '—'
  : `<span class="score score-${c.quality >= 0.75 ? 'hi' : c.quality >= 0.45 ? 'mid' : 'lo'}" title="${c.fscore} of ${c.fscoreKnown} tests passed">${c.fscore}/${c.fscoreKnown}</span>`;
// Dividends as % of free cash flow: under 60 % comfortable, over 100 % not covered.
const coverCell = c => !isNum(c.payoutFcf) ? '—'
  : `<span class="${c.payoutFcf <= 60 ? 'pos' : c.payoutFcf > 100 ? 'neg' : ''}">${num(c.payoutFcf, 0)}%</span>`;
const insiderCell = c => !isNum(c.insNet) ? '—' : c.insNet === 0 && !c.insBuyers ? '0'
  : `<span class="${c.insNet > 0 ? 'pos' : c.insNet < 0 ? 'neg' : ''}">${c.insNet > 0 ? '+' : ''}${eur(c.insNet)}</span>${c.insBuyers ? ` <span class="ins-note">${c.insBuyers}↑</span>` : ''}`;
const shortCell = c => !isNum(c.shortPct) ? '—' : `<span class="${c.shortPct >= 2 ? 'neg' : ''}">${num(c.shortPct, 2)}%</span>`;

const COLUMNS = [
  { key: 'name', label: 'Company', left: true, fmt: c => `<span class="ticker-link">${esc(c.ticker || '')}</span> <span class="company-name">${esc(c.name)}</span>` },
  { key: 'country', label: 'Country', left: true, fmt: c => esc(countryName(c.country)) },
  { key: 'sector', label: 'Sector', left: true, fmt: c => c.sector ? `<span class="badge-sector">${esc(c.sector)}</span>` : '—' },
  { key: 'price', label: 'Price €', fmt: c => isNum(c.price) ? `<span title="${esc(c.priceVenue)} · ${esc(c.priceDate)}">${num(c.price, 2)}</span>` : '—' },
  { key: 'change', label: 'Chg %', fmt: c => pctCell(c.change) },
  { key: 'mcap', label: 'Mkt value €', fmt: c => bn(c.mcap) },
  { key: 'pe', label: 'P/E', fmt: c => isNum(c.pe) ? num(c.pe, 1) : '—' },
  { key: 'divYield', label: 'Div. yield', fmt: c => isNum(c.divYield) ? num(c.divYield, 1) + '%' : '—' },
  { key: 'quality', label: 'Quality', fmt: scoreCell },
  { key: 'insNet', label: 'Insiders 30d', fmt: insiderCell },
  { key: 'shortPct', label: 'Shorted', fmt: shortCell },
  { key: 'payoutFcf', label: 'Div. / FCF', fmt: coverCell },
  { key: 'revenueEur', label: 'Revenue €', fmt: c => bn(c.revenueEur) },
  { key: 'revenueGrowth', label: 'Rev. growth', fmt: c => pctCell(c.revenueGrowth) },
  { key: 'netMargin', label: 'Net margin', fmt: c => pctCell(c.netMargin) },
  { key: 'fcfMargin', label: 'FCF margin', fmt: c => pctCell(c.fcfMargin) },
  { key: 'roe', label: 'ROE', fmt: c => pctCell(c.roe) },
  { key: 'netDebtToEbitda', label: 'Net debt/EBITDA', fmt: c => ndCell(c.netDebtToEbitda) },
  { key: 'periodEnd', label: 'FY end', fmt: c => esc(c.periodEnd) },
];

// Each filter: options as [label, test]
const FILTERS = [
  { id: 'combo', label: 'FINILA signals', options: () => [
      ['Strong quality and insiders buying', c => c.quality >= 0.75 && c.insNet > 0],
      ['Insiders buying while funds short', c => c.insNet > 0 && c.shortPct >= 0.5],
      ['Strong quality and dividend covered', c => c.quality >= 0.75 && c.payoutFcf > 0 && c.payoutFcf <= 60],
      ['Weak quality and heavily shorted', c => c.quality <= 0.34 && c.shortPct >= 2]] },
  { id: 'pe', label: 'P/E', options: () => [
      ['Under 10', c => c.pe > 0 && c.pe < 10], ['Under 15', c => c.pe > 0 && c.pe < 15], ['Under 25', c => c.pe > 0 && c.pe < 25],
      ['Over 25', c => c.pe >= 25]] },
  { id: 'divYield', label: 'Dividend yield', options: () => [
      ['Over 3%', c => c.divYield > 3], ['Over 5%', c => c.divYield > 5], ['Over 8%', c => c.divYield > 8]] },
  { id: 'mcap', label: 'Market value', options: () => [
      ['Over €50B', c => c.mcap > 50000], ['Over €10B', c => c.mcap > 10000], ['Over €1B', c => c.mcap > 1000],
      ['€100M to €1B', c => c.mcap >= 100 && c.mcap <= 1000], ['Under €100M', c => isNum(c.mcap) && c.mcap < 100]] },
  { id: 'priced', label: 'Price', options: () => [['Has a real price', c => isNum(c.price)], ['No price yet', c => !isNum(c.price)]] },
  { id: 'fscore', label: 'Quality score', options: () => [
      ['Strong (3/4 of tests or more)', c => c.quality >= 0.75], ['Above half', c => c.quality > 0.5],
      ['Weak (1/3 of tests or less)', c => c.quality <= 0.34], ['All tests passed', c => isNum(c.fscore) && c.fscore === c.fscoreKnown]] },
  { id: 'insiders', label: 'Insiders, 30 days', options: () => [
      ['Net buying', c => c.insNet > 0], ['2 or more buyers', c => c.insBuyers >= 2], ['Net selling', c => c.insNet < 0]] },
  { id: 'shorts', label: 'Shorted by funds', options: () => [
      ['Over 5%', c => c.shortPct > 5], ['Over 2%', c => c.shortPct > 2], ['Any public short', c => c.shortPct > 0],
      ['No public short', c => !(c.shortPct > 0)]] },
  { id: 'cover', label: 'Dividend vs free cash flow', options: () => [
      ['Well covered (under 60%)', c => c.payoutFcf > 0 && c.payoutFcf <= 60], ['Covered (under 100%)', c => c.payoutFcf > 0 && c.payoutFcf <= 100],
      ['Not covered (over 100%)', c => c.payoutFcf > 100]] },
  { id: 'country', label: 'Country', options: () => [...new Set(COMPANIES.map(c => c.country).filter(Boolean))]
      .sort((a, b) => countryName(a).localeCompare(countryName(b))).map(k => [countryName(k), c => c.country === k]) },
  { id: 'sector', label: 'Sector', options: () => [...new Set(COMPANIES.map(c => c.sector).filter(Boolean))].sort()
      .map(k => [k, c => c.sector === k]).concat([['Unknown', c => !c.sector]]) },
  { id: 'size', label: 'Revenue', options: () => [
      ['Over €50B', c => c.revenueEur > 50000], ['Over €10B', c => c.revenueEur > 10000],
      ['Over €1B', c => c.revenueEur > 1000], ['€100M to €1B', c => c.revenueEur >= 100 && c.revenueEur <= 1000],
      ['Under €100M', c => isNum(c.revenueEur) && c.revenueEur < 100]] },
  { id: 'roe', label: 'ROE', options: () => [
      ['Over 25%', c => c.roe > 25], ['Over 15%', c => c.roe > 15], ['Over 10%', c => c.roe > 10],
      ['Positive', c => c.roe > 0], ['Negative', c => c.roe < 0]] },
  { id: 'netMargin', label: 'Net margin', options: () => [
      ['Over 20%', c => c.netMargin > 20], ['Over 10%', c => c.netMargin > 10], ['Over 5%', c => c.netMargin > 5],
      ['Positive', c => c.netMargin > 0], ['Loss-making', c => c.netMargin < 0]] },
  { id: 'growth', label: 'Revenue growth', options: () => [
      ['Over 20%', c => c.revenueGrowth > 20], ['Over 10%', c => c.revenueGrowth > 10],
      ['Positive', c => c.revenueGrowth > 0], ['Shrinking', c => c.revenueGrowth < 0]] },
  { id: 'fcf', label: 'Free cash flow', options: () => [
      ['Positive', c => c.fcf > 0], ['FCF margin over 10%', c => c.fcfMargin > 10], ['Negative', c => c.fcf < 0]] },
  { id: 'debt', label: 'Net debt/EBITDA', options: () => [
      ['Net cash', c => c.netDebtToEbitda < 0], ['Under 1×', c => isNum(c.netDebtToEbitda) && c.netDebtToEbitda < 1],
      ['Under 2×', c => isNum(c.netDebtToEbitda) && c.netDebtToEbitda < 2], ['Over 3×', c => c.netDebtToEbitda > 3]] },
  { id: 'dividend', label: 'Dividends', options: () => [['Pays dividends', c => c.dividendsPaid > 0], ['No dividend', c => !(c.dividendsPaid > 0)]] },
  { id: 'type', label: 'Company type', options: () => [['Banks & insurers', c => c.financial], ['Excluding banks & insurers', c => !c.financial]] },
];

function renderFilters() {
  document.getElementById('co-filters').innerHTML = FILTERS.map(f => {
    f.opts = f.options();
    return `<div class="filter-item"><label for="f-${f.id}">${f.label}</label>
      <select id="f-${f.id}" data-filter="${f.id}"><option value="">Any</option>
      ${f.opts.map(([label], i) => `<option value="${i}">${esc(label)}</option>`).join('')}</select></div>`;
  }).join('');
  document.querySelectorAll('[data-filter]').forEach(sel => sel.addEventListener('change', () => {
    if (sel.value === '') delete filters[sel.dataset.filter]; else filters[sel.dataset.filter] = Number(sel.value);
    page = 1; render();
  }));
}

function results() {
  const q = query.trim().toLowerCase();
  const active = FILTERS.filter(f => f.id in filters).map(f => f.opts[filters[f.id]][1]);
  const list = COMPANIES.filter(c => active.every(t => t(c)) &&
    (!q || `${c.name} ${c.ticker || ''} ${countryName(c.country)} ${c.industry || ''}`.toLowerCase().includes(q)));
  return list.sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * sortDir;
  });
}

function render() {
  urlWriteFilters(FILTERS, filters, f => f.opts, { q: query.trim() });
  const list = results();
  const pages = Math.max(1, Math.ceil(list.length / PAGE));
  page = Math.min(page, pages);
  const start = (page - 1) * PAGE;
  document.getElementById('co-count').textContent =
    `${list.length.toLocaleString('en-GB')} companies · annual reports read ${UPDATED}` +
    (PRICES_DAY ? ` · prices ${PRICES_DAY} from Deutsche Börse (Xetra, Börse Frankfurt)` : '');
  const head = `<thead><tr><th class="col-no">#</th>${COLUMNS.map(c =>
    `<th class="sortable ${c.left ? 'l' : ''} ${sortKey === c.key ? 'sort-active' : ''}" data-sort="${c.key}">${c.label}${sortKey === c.key ? (sortDir < 0 ? ' ▼' : ' ▲') : ''}</th>`).join('')}</tr></thead>`;
  const body = '<tbody>' + list.slice(start, start + PAGE).map((c, i) =>
    `<tr class="stock-row" data-lei="${esc(c.lei)}"><td class="col-no">${start + i + 1}</td>
      ${COLUMNS.map(col => `<td class="${col.left ? 'l' : ''}">${col.fmt(c)}</td>`).join('')}</tr>`).join('') + '</tbody>';
  document.getElementById('screener-table').innerHTML = head + body;
  document.getElementById('pagination').innerHTML = `
    <button data-page="1" ${page <= 1 ? 'disabled' : ''}>«</button>
    <button data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>‹</button>
    <span>Page ${page} of ${pages}</span>
    <button data-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>›</button>
    <button data-page="${pages}" ${page >= pages ? 'disabled' : ''}>»</button>`;
}

function openCompany(lei) {
  const c = COMPANIES.find(x => x.lei === lei);
  if (!c) return;
  const cur = c.currency || '';
  const row = (k, v) => `<tr><td>${k}</td><td>${v}</td></tr>`;
  const fin = c.financial ? '<p class="modal-report-src">Bank or insurer: EBITDA, free cash flow and net debt are not shown because they do not describe these businesses well.</p>' : '';
  document.getElementById('modal-content').innerHTML = `
    <div class="modal-header">
      <div>
        <h2>${esc(c.name)} ${c.ticker ? `<span class="modal-name">${esc(c.ticker)}${c.exchange ? ' · ' + esc(c.exchange) : ''}</span>` : ''}</h2>
        <div class="modal-meta">${esc(countryName(c.country))}${c.sector ? ' · ' + esc(c.sector) : ''}${c.industry ? ' · ' + esc(c.industry) : ''}</div>
      </div>
      <span class="modal-actions">${SLUGS[c.lei] ? `<a class="modal-page" href="/stock/${SLUGS[c.lei]}">Company page</a>` : ''}
      <button class="modal-close" type="button" data-close>✕</button></span>
    </div>
    ${isNum(c.price) ? `<div class="modal-price-row">
      <span class="modal-price">€${num(c.price, 2)}</span>
      ${isNum(c.change) ? `<span class="modal-change ${c.change >= 0 ? 'pos' : 'neg'}">${c.change >= 0 ? '+' : ''}${num(c.change, 2)}%</span>` : ''}
      <span class="real-badge">Real price · ${esc(c.priceVenue)} · ${esc(c.priceDate)}</span>
      <span class="modal-vol">${isNum(c.mcap) ? `Market value ≈ ${bn(c.mcap)} €` : ''}${isNum(c.pe) ? ` · P/E ${num(c.pe, 1)}` : ''}${isNum(c.divYield) ? ` · Dividend yield ${num(c.divYield, 1)}%` : ''}</span>
    </div>` : ''}
    ${c.origin === 'listing' ? `<div class="modal-report"><p class="modal-report-src">No annual report figures yet: this company’s ESEF report is not on filings.xbrl.org (German issuers, for example, are not published there). The price, insider trades and short positions are real.</p></div>` : `
    <div class="modal-report">
      <div class="modal-report-head">
        <h4>Latest annual report · FY ending ${esc(c.periodEnd)} · ${esc(cur)}</h4>
        <span class="real-badge">Real data</span>
      </div>
      <div class="modal-grid">
        <div class="modal-section"><h4>Income</h4><table class="kv-table">
          ${row('Revenue', bn(c.revenue, cur))}${row('EBITDA', bn(c.ebitda, cur))}${row('Operating profit', bn(c.operatingProfit, cur))}${row('Net income', bn(c.netIncome, cur))}${row('EPS', isNum(c.eps) ? num(c.eps, 2) + ' ' + esc(cur) : '—')}
        </table></div>
        <div class="modal-section"><h4>Cash flow</h4><table class="kv-table">
          ${row('Operating cash flow', bn(c.operatingCashFlow, cur))}${row('Capex', bn(c.capex, cur))}${row('Free cash flow', bn(c.fcf, cur))}${row('Dividends paid', bn(c.dividendsPaid, cur))}
        </table></div>
        <div class="modal-section"><h4>Balance sheet</h4><table class="kv-table">
          ${row('Total assets', bn(c.assets, cur))}${row('Equity', bn(c.equity, cur))}${row('Cash', bn(c.cash, cur))}${row('Borrowings', bn(c.debt, cur))}${row('Net debt', bn(c.netDebt, cur))}
        </table></div>
        <div class="modal-section"><h4>Ratios</h4><table class="kv-table">
          ${row('ROE', pctCell(c.roe))}${row('EBITDA margin', pctCell(c.ebitdaMargin))}${row('Operating margin', pctCell(c.operatingMargin))}${row('Net margin', pctCell(c.netMargin))}${row('FCF margin', pctCell(c.fcfMargin))}${row('Net debt / EBITDA', ndCell(c.netDebtToEbitda))}${row('Revenue growth', pctCell(c.revenueGrowth))}${row('Net income growth', pctCell(c.netIncomeGrowth))}
        </table></div>
      </div>
      ${scoresHTML(c)}
      ${fin}
      <div class="modal-report-src">${c.origin === 'hand'
        ? `Source: the company’s published annual accounts, entered by FINILA (no ESEF report on filings.xbrl.org)${c.source ? ` · <a href="${esc(c.source)}" target="_blank" rel="noopener">view figures</a>` : ''}. Check the annual report before relying on them.`
        : `Source: the company’s ESEF annual report via filings.xbrl.org${c.source ? ` · <a href="${esc(c.source)}" target="_blank" rel="noopener">view report</a>` : ''}. Figures are read automatically and can contain errors; check the report before relying on them.`}</div>
    </div>`}`;
  document.getElementById('stock-modal').style.display = 'flex';
}

function scoresHTML(c) {
  const tests = (c.fscoreTests || '').split('');
  const quality = isNum(c.fscore) ? `
    <div class="modal-section"><h4>Quality score ${scoreCell(c)} <span class="ins-note">Piotroski F-score: this year against last year</span></h4><table class="kv-table">
      ${F_TESTS.map((t, i) => `<tr><td>${t}</td><td>${tests[i] === '1' ? '<span class="pos">✓ yes</span>' : tests[i] === '0' ? '<span class="neg">✗ no</span>' : '<span class="ins-note">not in report</span>'}</td></tr>`).join('')}
    </table></div>` : `<div class="modal-section"><h4>Quality score</h4><p class="ins-note">${c.financial ? 'Not calculated for banks and insurers.' : 'Not enough figures in the last two annual reports.'}</p></div>`;
  const other = `
    <div class="modal-section"><h4>Dividend, insiders, short sellers</h4><table class="kv-table">
      <tr><td>Dividends / free cash flow</td><td>${coverCell(c)}</td></tr>
      <tr><td>Dividends / net profit</td><td>${isNum(c.payoutNet) ? num(c.payoutNet, 0) + '%' : '—'}</td></tr>
      <tr><td>Insiders, net 30 days</td><td>${insiderCell(c)}</td></tr>
      <tr><td>Insiders buying, 30 days</td><td>${isNum(c.insNet) ? c.insBuyers : '—'}</td></tr>
      <tr><td>Shorted by funds</td><td>${shortCell(c)}${c.shortFunds ? ` <span class="ins-note">${c.shortFunds} ${c.shortFunds === 1 ? 'fund' : 'funds'}</span>` : ''}</td></tr>
    </table>
    <p class="ins-note">${isNum(c.insNet) ? `<a href="/insiders?q=${encodeURIComponent(c.insName)}">See insider trades</a>` : 'Insider trades so far only for Sweden.'}
    ${isNum(c.shortPct) ? ` · <a href="/shorts?q=${encodeURIComponent(c.isin)}">See short positions</a>` : ''}</p></div>`;
  return `<div class="modal-grid modal-scores">${quality}${other}</div>`;
}

// Join the insider register (by LEI) and the short-selling registers (by ISIN) to each company.
async function addSignals() {
  const load = async url => { try { return await (await fetch(url, { cache: 'no-cache' })).json(); } catch { return null; } };
  const [ins, sh] = await Promise.all([load('js/insiders.json'), load('js/shorts.json')]);
  const byLei = {}, byIsin = {};
  if (ins) {
    const since = Date.now() - 30 * 864e5;
    ins.rows.filter(t => t.lei && t.signal && new Date(t.published) >= since).forEach(t => {
      const s = byLei[t.lei] || (byLei[t.lei] = { net: 0, buyers: new Set(), name: t.issuer });
      if (t.nature === 'Acquisition') { s.net += t.valueEur || 0; s.buyers.add(t.person); }
      if (t.nature === 'Disposal') s.net -= t.valueEur || 0;
    });
  }
  if (sh) sh.positions.forEach(p => {
    const s = byIsin[p.isin] || (byIsin[p.isin] = { pct: 0, funds: 0 });
    s.pct += p.pct; if (p.c !== 'UK') s.funds += 1;
  });
  COMPANIES.forEach(c => {
    const i = byLei[c.lei], s = byIsin[c.isin];
    // Swedish companies are covered by the register, so no trades means zero rather than unknown.
    if (i) Object.assign(c, { insNet: i.net, insBuyers: i.buyers.size, insName: i.name });
    else if (ins && c.country === 'SE') Object.assign(c, { insNet: 0, insBuyers: 0, insName: c.name });
    if (s) Object.assign(c, { shortPct: +s.pct.toFixed(2), shortFunds: s.funds });
  });
}

// Last price from Deutsche Börse's free delayed data (js/company-prices.json, by ISIN, in EUR).
// Market value uses the shares in issue where known (data/shares.json, German companies), else
// shares = net income / EPS from the annual report, so it is an estimate; values that point to
// mismatched units in the report (absurd P/E or price/sales) are left out.
async function addPrices() {
  let data;
  try { data = await (await fetch('js/company-prices.json', { cache: 'no-cache' })).json(); } catch { return; }
  PRICES_DAY = data.date;
  const [iPrice, iChange, iVenue, iDate] = ['price', 'change', 'venue', 'date'].map(f => data.fields.indexOf(f));
  COMPANIES.forEach(c => {
    const q = data.rows[c.isin];
    if (!q) return;
    Object.assign(c, { price: q[iPrice], change: q[iChange], priceVenue: q[iVenue], priceDate: q[iDate] });
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
}

function closeCompany() { document.getElementById('stock-modal').style.display = 'none'; }

document.addEventListener('DOMContentLoaded', async () => {
  try {
    const data = await (await fetch('js/companies.json')).json();
    UPDATED = data.updated;
    COMPANIES = data.rows.map(r => Object.fromEntries(data.fields.map((f, i) => [f, r[i]])));
    COMPANIES.forEach(c => {
      if (!isNum(c.fscore)) return;
      c.fscoreKnown = (c.fscoreTests || '').replace(/-/g, '').length;
      c.quality = c.fscore / c.fscoreKnown;
    });
  } catch (e) {
    document.getElementById('co-count').textContent = 'Could not load company data.';
    return;
  }
  // Addresses of the company pages (/stock/<name>), built with the pages by scripts/build-stock-pages.py.
  const slugs = fetch('js/stock-slugs.json').then(r => r.json()).then(d => { SLUGS = d; }).catch(() => {});
  await Promise.all([addSignals(), addPrices(), slugs]);
  renderFilters();
  const params = new URLSearchParams(location.search);
  query = params.get('q') || '';
  document.getElementById('co-search').value = query;
  urlReadFilters(FILTERS, filters, f => f.opts);
  render();
  if (params.get('lei')) openCompany(params.get('lei'));

  document.getElementById('co-search').addEventListener('input', e => { query = e.target.value; page = 1; render(); });
  document.getElementById('co-reset').addEventListener('click', () => {
    Object.keys(filters).forEach(k => delete filters[k]);
    document.querySelectorAll('[data-filter]').forEach(s => s.value = '');
    query = ''; document.getElementById('co-search').value = ''; page = 1; render();
  });
  document.getElementById('screener-table').addEventListener('click', e => {
    const th = e.target.closest('[data-sort]');
    if (th) {
      const k = th.dataset.sort;
      if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = COLUMNS.find(c => c.key === k).left ? 1 : -1; }
      render(); return;
    }
    const tr = e.target.closest('[data-lei]');
    if (tr) openCompany(tr.dataset.lei);
  });
  document.getElementById('pagination').addEventListener('click', e => {
    const b = e.target.closest('[data-page]');
    if (b && !b.disabled) { page = Number(b.dataset.page); render(); window.scrollTo(0, 0); }
  });
  document.getElementById('stock-modal').addEventListener('click', e => {
    if (e.target.id === 'stock-modal' || e.target.closest('[data-close]')) closeCompany();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeCompany(); });
});
