// FINILA groups: every European company with a real price and market value (same data and same
// estimate as the market map), summed up by sector or by country. Totals are weighted by market
// value like an index; ratios are medians, so one odd company cannot swing a group.

let GR = [], GR_ALL = [], GR_SLUGS = {}, groupBy = 'sector', groupSort = 'mcap', groupSortDir = -1;
const GR_OPEN = new Set();

const isNum = v => typeof v === 'number' && isFinite(v);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 1) => isNum(v) ? v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
const signed = (v, d = 1) => isNum(v) ? `${v > 0 ? '+' : ''}${num(v, d)}%` : '—';
const money = v => !isNum(v) ? '—' : v >= 1000 ? `€${num(v / 1000, v >= 10000 ? 0 : 1)}B` : `€${num(v, 0)}M`;
const tone = v => !isNum(v) || v === 0 ? '' : v > 0 ? 'pos' : 'neg';
const countryNames = (() => { try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; } })();
const countryName = c => (c && countryNames ? countryNames.of(c) : c) || 'Other';
const OTHER = 'Sector not known yet';

function median(arr) {
  const v = arr.filter(isNum).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = v.length >> 1;
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// Market value exactly as on the market map: shares in issue where known, else net income / EPS;
// absurd P/E or price/sales means mismatched units, so such a company gets no market value.
function buildCompanies(cos, prices) {
  const ix = f => prices.fields.indexOf(f);
  const [iPrice, iChange] = ['price', 'change'].map(ix);
  return cos.rows.map(r => {
    const c = Object.fromEntries(cos.fields.map((f, i) => [f, r[i]]));
    c.group = { sector: c.sector || OTHER, country: countryName(c.country) };
    const q = prices.rows[c.isin];
    if (!q || !isNum(q[iPrice])) return c;
    c.price = q[iPrice]; c.change = q[iChange];
    const fx = isNum(c.revenueEur) && isNum(c.revenue) && c.revenue ? c.revenueEur / c.revenue : c.currency === 'EUR' ? 1 : null;
    const shares = isNum(c.shares) && c.shares > 0 ? c.shares : null;   // from data/shares.json or the annual report (universe.py)
    if (!shares) return c;
    const mcap = c.price * shares, pe = fx && c.eps > 0 ? c.price / (c.eps * fx) : null;
    const ps = isNum(c.revenueEur) && c.revenueEur > 0 ? mcap / c.revenueEur : null;
    if (ps !== null ? ps < 0.02 || ps > 60 : pe !== null && (pe < 1 || pe > 150)) return c;
    c.mcap = mcap;
    c.pe = pe !== null && pe >= 1 && pe <= 1000 ? pe : null;
    c.dividendsEur = fx && c.dividendsPaid > 0 ? c.dividendsPaid * fx : fx ? 0 : null;
    return c;
  });
}

function aggregate() {
  const map = {};
  for (const c of GR_ALL) {
    const key = c.group[groupBy];
    const g = map[key] || (map[key] = { name: key, listed: 0, cos: [] });
    g.listed++;
    if (isNum(c.mcap)) g.cos.push(c);
  }
  return Object.values(map).filter(g => g.cos.length).map(g => {
    const cos = g.cos.sort((a, b) => b.mcap - a.mcap);
    const mcap = cos.reduce((s, c) => s + c.mcap, 0);
    const moved = cos.filter(c => isNum(c.change));
    const movedCap = moved.reduce((s, c) => s + c.mcap, 0);
    const paying = cos.filter(c => isNum(c.dividendsEur));
    const payCap = paying.reduce((s, c) => s + c.mcap, 0);
    return {
      name: g.name, listed: g.listed, count: cos.length, cos, mcap,
      change: movedCap ? moved.reduce((s, c) => s + c.mcap * c.change, 0) / movedCap : null,
      pe: median(cos.map(c => c.pe)),
      divYield: payCap ? paying.reduce((s, c) => s + c.dividendsEur, 0) / payCap * 100 : null,
      roe: median(cos.map(c => c.roe)),
      revenueGrowth: median(cos.map(c => c.revenueGrowth)),
      netMargin: median(cos.map(c => c.netMargin)),
      up: moved.filter(c => c.change > 0).length,
      down: moved.filter(c => c.change < 0).length,
      biggest: cos[0],
    };
  });
}

function stockLink(c) {
  return GR_SLUGS[c.lei] ? `/stock/${GR_SLUGS[c.lei]}` : `/screener?lei=${encodeURIComponent(c.lei || '')}`;
}

function renderGroups() {
  GR = aggregate().sort((a, b) => {
    // Companies with no sector yet always go last, so the known sectors read first.
    if ((a.name === OTHER) !== (b.name === OTHER)) return a.name === OTHER ? 1 : -1;
    let va = a[groupSort], vb = b[groupSort];
    if (typeof va === 'string') return va.localeCompare(vb) * groupSortDir;
    if (!isNum(va)) va = -Infinity * groupSortDir;
    if (!isNum(vb)) vb = -Infinity * groupSortDir;
    return (va - vb) * groupSortDir;
  });
  const total = GR.reduce((s, g) => s + g.count, 0);
  document.getElementById('groups-note').textContent =
    `${total.toLocaleString('en-GB')} companies with a price and a market value, in ${GR.length} ${groupBy === 'sector' ? 'sectors' : 'countries'}. ` +
    'Click a row to see its companies.';
  document.getElementById('groups-tbody').innerHTML = GR.map((g, i) => {
    const open = GR_OPEN.has(g.name), n = g.up + g.down;
    return `
    <tr class="group-row" data-group="${esc(g.name)}" aria-expanded="${open}">
      <td>${i + 1}</td>
      <td><strong>${esc(g.name)}</strong><div class="group-top">Largest: ${esc(g.biggest.name)}</div></td>
      <td title="${g.listed} listed, ${g.count} with a market value">${g.count}<span class="group-of"> / ${g.listed}</span></td>
      <td>${money(g.mcap)}</td>
      <td><span class="${tone(g.change)}">${signed(g.change, 2)}</span></td>
      <td>${num(g.pe)}</td>
      <td>${isNum(g.divYield) ? num(g.divYield) + '%' : '—'}</td>
      <td><span class="${tone(g.roe)}">${isNum(g.roe) ? num(g.roe) + '%' : '—'}</span></td>
      <td><span class="${tone(g.revenueGrowth)}">${signed(g.revenueGrowth)}</span></td>
      <td><span class="${tone(g.netMargin)}">${isNum(g.netMargin) ? num(g.netMargin) + '%' : '—'}</span></td>
      <td>
        <div class="perf-bar">
          <div class="perf-up" style="width:${n ? Math.round(g.up / n * 100) : 0}%"></div>
          <div class="perf-down" style="width:${n ? Math.round(g.down / n * 100) : 0}%"></div>
        </div>
        <span class="perf-label">${g.up}↑ ${g.down}↓</span>
      </td>
    </tr>
    <tr class="group-detail"${open ? '' : ' hidden'}><td colspan="11">${open ? renderGroupStocks(g) : ''}</td></tr>`;
  }).join('') || '<tr><td colspan="11">No companies to show.</td></tr>';
  document.querySelectorAll('#groups-table th[data-sort]').forEach(th =>
    th.setAttribute('aria-sort', th.dataset.sort === groupSort ? (groupSortDir > 0 ? 'ascending' : 'descending') : 'none'));
}

function renderGroupStocks(g) {
  const shown = g.cos.slice(0, 60);
  return `<div class="group-stocks-grid">${shown.map(c => `
    <a class="mini-card" href="${stockLink(c)}">
      <div class="mini-ticker">${esc(c.ticker || c.name.split(/\s+/)[0])}</div>
      <div class="mini-name" title="${esc(c.name)}">${esc(c.name)}</div>
      <div class="mini-price">€${num(c.price, 2)}</div>
      <div class="mini-change ${tone(c.change)}">${signed(c.change, 2)}</div>
      <div class="mini-cap">${money(c.mcap)}</div>
    </a>`).join('')}</div>` +
    (g.cos.length > shown.length ? `<p class="group-more">The ${shown.length} largest of ${g.cos.length}. All of them are on the <a href="/screener">screener</a>.</p>` : '');
}

async function initGroups() {
  const load = async url => { try { return await (await fetch(url, { cache: 'no-cache' })).json(); } catch { return null; } };
  const [cos, prices, slugs] = await Promise.all([load('js/companies.json'), load('js/company-prices.json'), load('js/stock-slugs.json')]);
  GR_SLUGS = slugs || {};
  if (!cos || !prices) { document.getElementById('groups-note').textContent = 'Could not load company data.'; return; }
  GR_ALL = buildCompanies(cos, prices);
  renderGroups();

  document.getElementById('groups-tbody').addEventListener('click', e => {
    const row = e.target.closest('.group-row');
    if (!row) return;
    const name = row.dataset.group;
    GR_OPEN.has(name) ? GR_OPEN.delete(name) : GR_OPEN.add(name);
    renderGroups();
  });
  document.querySelectorAll('#groups-table th[data-sort]').forEach(th => th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (groupSort === key) groupSortDir *= -1;
    else { groupSort = key; groupSortDir = key === 'name' ? 1 : -1; }
    renderGroups();
  }));
  document.querySelectorAll('[data-group-by]').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('[data-group-by]').forEach(b => b.classList.toggle('active', b === btn));
    groupBy = btn.dataset.groupBy;
    GR_OPEN.clear();
    renderGroups();
  }));
}

document.addEventListener('DOMContentLoaded', initGroups);
