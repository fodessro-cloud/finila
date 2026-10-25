// FINILA market map: every European company with a real price (js/company-prices.json) and a
// market value (js/companies.json: annual report, or shares in issue for German companies). Box size = estimated market value, color = the chosen figure.

let HM = [], hmGroupBy = 'sector', hmColor = 'change', hmCountry = '', hmTiles = [], hmPinned = null;

const isNum = v => typeof v === 'number' && isFinite(v);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const num = (v, d = 1) => isNum(v) ? v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d }) : '—';
const signed = (v, d = 1, unit = '%') => isNum(v) ? `${v > 0 ? '+' : ''}${num(v, d)}${unit}` : '—';
const money = v => !isNum(v) ? '—' : v >= 1000 ? `€${num(v / 1000, v >= 10000 ? 0 : 1)}B` : `€${num(v, 0)}M`;
const countryNames = (() => { try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; } })();
const countryName = c => (c && countryNames ? countryNames.of(c) : c) || 'Other';

// Each color: how to read the figure, where it is neutral and how far it goes to full red or green.
// `low` means a low value is the good one (green), as with P/E.
const COLORS = [
  { key: 'change', label: 'Day change', mid: 0, span: 3, fmt: v => signed(v, 2) },
  { key: 'revenueGrowth', label: 'Revenue growth', mid: 0, span: 25, fmt: v => signed(v) },
  { key: 'netMargin', label: 'Net margin', mid: 0, span: 20, fmt: v => signed(v) },
  { key: 'quality', label: 'Quality score', mid: 0.5, span: 0.5, fmt: (v, c) => `${c.fscore}/${c.fscoreKnown}` },
  { key: 'pe', label: 'P/E', mid: 18, span: 14, low: true, fmt: v => num(v) },
  { key: 'divYield', label: 'Dividend yield', mid: 1.5, span: 4, fmt: v => `${num(v)}%` },
];
// Most issuers have no sector yet: they go last, so the known sectors read first.
const OTHER = 'Sector not known yet';
const colorDef = () => COLORS.find(c => c.key === hmColor);

// Finviz-style scale: red, dark grey at the neutral point, green.
const RED = [214, 52, 56], MID = [66, 72, 88], GREEN = [34, 160, 84], NONE = '#C9D0DB';
function tileColor(c) {
  const d = colorDef(), v = c[d.key];
  if (!isNum(v)) return NONE;
  let t = Math.max(-1, Math.min(1, (v - d.mid) / d.span));
  if (d.low) t = -t;
  const to = t >= 0 ? GREEN : RED, a = Math.abs(t);
  return `rgb(${MID.map((m, i) => Math.round(m + (to[i] - m) * a)).join(',')})`;
}

// Squarified treemap (Bruls et al.): items sorted by value, laid out in rows along the shorter side.
function squarify(items, rect) {
  const out = [], total = items.reduce((s, i) => s + i.value, 0);
  if (!total || rect.w <= 0 || rect.h <= 0) return out;
  const scale = rect.w * rect.h / total;
  let { x, y, w, h } = rect, i = 0;
  while (i < items.length) {
    const side = Math.min(w, h);
    let sum = 0, j = i, best = Infinity;
    while (j < items.length) {
      const a = items[j].value * scale, s2 = sum + a;
      const rowMax = items[i].value * scale, rowMin = a;
      const worst = Math.max(side * side * rowMax / (s2 * s2), s2 * s2 / (side * side * rowMin));
      if (j > i && worst > best) break;
      best = worst; sum = s2; j++;
    }
    const len = sum / side;
    let pos = w >= h ? y : x;
    for (let k = i; k < j; k++) {
      const size = items[k].value * scale / len;
      out.push({ item: items[k], x: w >= h ? x : pos, y: w >= h ? pos : y, w: w >= h ? len : size, h: w >= h ? size : len });
      pos += size;
    }
    if (w >= h) { x += len; w -= len; } else { y += len; h -= len; }
    i = j;
  }
  return out;
}

// Same estimate as the Stocks page: shares in issue where known, else net income / EPS; absurd P/E or
// price/sales means mismatched units.
function buildCompanies(cos, prices) {
  const ix = f => prices.fields.indexOf(f);
  const [iPrice, iChange, iVenue, iDate] = ['price', 'change', 'venue', 'date'].map(ix);
  const out = [];
  for (const r of cos.rows) {
    const c = Object.fromEntries(cos.fields.map((f, i) => [f, r[i]]));
    const q = prices.rows[c.isin];
    if (!q) continue;
    Object.assign(c, { price: q[iPrice], change: q[iChange], venue: q[iVenue], priceDate: q[iDate] });
    const fx = isNum(c.revenueEur) && isNum(c.revenue) && c.revenue ? c.revenueEur / c.revenue : c.currency === 'EUR' ? 1 : null;
    const shares = isNum(c.shares) && c.shares > 0 ? c.shares : null;   // from data/shares.json or the annual report (universe.py)
    if (!shares || !isNum(c.price)) continue;
    const mcap = c.price * shares, pe = fx && c.eps > 0 ? c.price / (c.eps * fx) : null;
    const ps = isNum(c.revenueEur) && c.revenueEur > 0 ? mcap / c.revenueEur : null;
    if (ps !== null ? ps < 0.02 || ps > 60 : pe !== null && (pe < 1 || pe > 150)) continue;
    c.mcap = mcap;
    if (pe !== null && pe >= 1 && pe <= 1000) c.pe = pe;
    if (fx && c.dividendsPaid > 0) { const y = c.dividendsPaid * fx / mcap * 100; c.divYield = y < 25 ? y : null; } else c.divYield = fx ? 0 : null;
    if (isNum(c.fscore)) { c.fscoreKnown = (c.fscoreTests || '').replace(/-/g, '').length; c.quality = c.fscoreKnown ? c.fscore / c.fscoreKnown : null; }
    c.label = c.ticker || c.name.split(/\s+/)[0];
    c.group = { sector: c.sector || OTHER, country: countryName(c.country) };
    out.push(c);
  }
  return out;
}

function fitText(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  for (let n = text.length - 1; n > 1; n--) { const t = text.slice(0, n) + '…'; if (ctx.measureText(t).width <= maxW) return t; }
  return '';
}

function renderHeatmap() {
  const wrap = document.getElementById('hm-wrap'), canvas = document.getElementById('heatmap-canvas');
  const W = wrap.clientWidth;
  // Phones get a taller map that scrolls, so the boxes stay big enough to read.
  const avail = window.innerHeight - wrap.getBoundingClientRect().top - window.scrollY - 8;
  const H = W < 700 ? Math.max(avail, Math.round(W * 2.4)) : Math.max(avail, 480);
  wrap.style.height = H + 'px';
  const dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = '#FFFFFF';
  ctx.fillRect(0, 0, W, H);

  const shown = HM.filter(c => !hmCountry || c.country === hmCountry);
  const groups = {};
  shown.forEach(c => (groups[c.group[hmGroupBy]] ||= []).push(c));
  const groupItems = Object.entries(groups).map(([name, list]) => ({
    name, list: list.sort((a, b) => b.mcap - a.mcap), value: list.reduce((s, c) => s + c.mcap, 0),
  })).sort((a, b) => (a.name === OTHER) - (b.name === OTHER) || b.value - a.value);

  hmTiles = [];
  const d = colorDef();
  for (const g of squarify(groupItems, { x: 0, y: 0, w: W, h: H })) {
    const HEAD = g.h > 60 && g.w > 50 ? 18 : 0;
    ctx.fillStyle = '#E3E9F3';
    ctx.fillRect(g.x, g.y, g.w, g.h);
    if (HEAD) {
      ctx.fillStyle = '#34445F';
      ctx.font = '600 11px Inter, sans-serif';
      ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText(fitText(ctx, g.item.name.toUpperCase(), g.w - 10), g.x + 5, g.y + HEAD / 2 + 1);
    }
    const inner = { x: g.x + 1, y: g.y + HEAD + 1, w: g.w - 2, h: g.h - HEAD - 2 };
    for (const t of squarify(g.item.list.map(c => ({ ...c, value: c.mcap })), inner)) {
      const c = t.item;
      hmTiles.push({ c, x: t.x, y: t.y, w: t.w, h: t.h });
      ctx.fillStyle = tileColor(c);
      ctx.fillRect(t.x + 0.5, t.y + 0.5, Math.max(t.w - 1, 0.5), Math.max(t.h - 1, 0.5));
      if (t.w < 26 || t.h < 14) continue;
      const fs = Math.max(9, Math.min(22, t.w / 5, t.h / 2.6));
      const dark = !isNum(c[d.key]);
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = `700 ${fs}px Inter, sans-serif`;
      const name = fitText(ctx, c.label, t.w - 6);
      if (!name) continue;
      ctx.font = `500 ${Math.max(9, fs * 0.62)}px Inter, sans-serif`;
      const value = isNum(c[d.key]) ? d.fmt(c[d.key], c) : '—';
      // The figure goes under the name only when it fits whole: a cut-off number would mislead.
      const two = t.h > fs * 2.6 && ctx.measureText(value).width <= t.w - 6;
      ctx.font = `700 ${fs}px Inter, sans-serif`;
      ctx.fillStyle = dark ? '#34445F' : '#FFFFFF';
      ctx.fillText(name, t.x + t.w / 2, t.y + t.h / 2 - (two ? fs * 0.45 : 0));
      if (two) {
        ctx.font = `500 ${Math.max(9, fs * 0.62)}px Inter, sans-serif`;
        ctx.fillStyle = dark ? '#5A6A86' : 'rgba(255,255,255,0.85)';
        ctx.fillText(value, t.x + t.w / 2, t.y + t.h / 2 + fs * 0.6);
      }
    }
  }
  const total = HM.length, n = shown.length;
  const day = HM[0] ? new Date(HM[0].priceDate + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
  document.getElementById('hm-note').innerHTML =
    `${num(n, 0)} companies${hmCountry ? ` in ${esc(countryName(hmCountry))}` : ''} with a real price (${day}) and a known market value, worth ${money(shown.reduce((s, c) => s + c.mcap, 0))} together. ` +
    (hmColor === 'change' ? (changeShare(shown) < 0.5 ? 'The day change appears after the next trading day; until then the boxes are grey, so try another color. '
      : 'Grey boxes did not trade today. ') : hmColor !== 'pe' && hmColor !== 'divYield'
      ? 'Colors from the newest annual report of each company (mostly fiscal 2024 or 2025); revenue growth compares it with the year before. Grey boxes have no report figures yet (most German companies). ' : '') +
    `Box size is market value. Click a box to see the company.`;
  hideHover();
}

// Part of the market value whose companies have a day change.
function changeShare(list) {
  const all = list.reduce((s, c) => s + c.mcap, 0);
  return all ? list.filter(c => isNum(c.change)).reduce((s, c) => s + c.mcap, 0) / all : 0;
}

function renderLegend() {
  const d = colorDef();
  const lo = d.low ? d.mid + d.span : d.mid - d.span, hi = d.low ? d.mid - d.span : d.mid + d.span;
  const f = v => d.key === 'quality' ? `${Math.round(v * 100)}%` : d.key === 'pe' ? num(v, 0) : d.fmt(v, {});
  document.getElementById('hm-legend').innerHTML = `<span>${f(lo)}</span><div class="hm-legend-bar"></div><span>${f(hi)}</span><span class="hm-legend-none"></span><span>no figure</span>`;
}

function tileAt(e) {
  const r = document.getElementById('heatmap-canvas').getBoundingClientRect();
  const x = e.clientX - r.left, y = e.clientY - r.top;
  return hmTiles.find(t => x >= t.x && x < t.x + t.w && y >= t.y && y < t.y + t.h);
}

function showHover(t, e) {
  const box = document.getElementById('hm-hover'), tip = document.getElementById('hm-tooltip'), c = t.c, d = colorDef();
  Object.assign(box.style, { display: 'block', left: t.x + 'px', top: t.y + 'px', width: t.w + 'px', height: t.h + 'px' });
  const row = (k, v) => `<tr><td>${k}</td><td>${v}</td></tr>`;
  tip.innerHTML = `<div class="hm-tip-name">${esc(c.name)}</div>
    <div class="hm-tip-meta">${esc([c.ticker, countryName(c.country), c.sector].filter(Boolean).join(' · '))}</div>
    <div class="hm-tip-price">€${num(c.price, 2)} <span class="${c.change > 0 ? 'pos' : c.change < 0 ? 'neg' : ''}">${isNum(c.change) ? signed(c.change, 2) : ''}</span></div>
    <table>${row('Market value ≈', money(c.mcap))}${row('P/E', isNum(c.pe) ? num(c.pe) : '—')}${row('Dividend yield', isNum(c.divYield) ? num(c.divYield) + '%' : '—')}
    ${d.key !== 'pe' && d.key !== 'divYield' && d.key !== 'change' ? row(`${d.label}${c.periodEnd ? ` (FY ${c.periodEnd.slice(0, 4)})` : ''}`, isNum(c[d.key]) ? d.fmt(c[d.key], c) : '—') : ''}</table>
    <div class="hm-tip-src">${esc(c.venue)} · ${esc(c.priceDate)}</div>`;
  tip.style.display = 'block';
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = e.clientX + 14, top = e.clientY + 14;
  if (left + tw > window.innerWidth - 8) left = Math.max(8, e.clientX - tw - 14);
  if (top + th > window.innerHeight - 8) top = Math.max(8, e.clientY - th - 14);
  tip.style.left = left + 'px'; tip.style.top = top + 'px';
}

function hideHover() {
  document.getElementById('hm-hover').style.display = 'none';
  document.getElementById('hm-tooltip').style.display = 'none';
  hmPinned = null;
}

function setActive(sel, btn) {
  document.querySelectorAll(sel).forEach(b => b.classList.toggle('active', b === btn));
}

// Company page addresses (/stock/<name>) by LEI, from scripts/build-stock-pages.py.
let HM_SLUGS = {};

async function initHeatmap() {
  const load = async url => { try { return await (await fetch(url, { cache: 'no-cache' })).json(); } catch { return null; } };
  const [cos, prices, slugs] = await Promise.all([load('js/companies.json'), load('js/company-prices.json'), load('js/stock-slugs.json')]);
  HM_SLUGS = slugs || {};
  if (!cos || !prices) { document.getElementById('hm-note').textContent = 'Could not load company data.'; return; }
  HM = buildCompanies(cos, prices);
  // Start on the day change; only without one yet (only one trading day so far) on revenue growth.
  // Measured by market value: small firms often trade on no day, the big ones every day.
  if (changeShare(HM) < 0.5) hmColor = 'revenueGrowth';

  const sel = document.getElementById('hm-country');
  const counts = {};
  HM.forEach(c => { counts[c.country] = (counts[c.country] || 0) + 1; });
  sel.innerHTML += Object.keys(counts).sort((a, b) => countryName(a).localeCompare(countryName(b)))
    .map(k => `<option value="${esc(k)}">${esc(countryName(k))} (${counts[k]})</option>`).join('');
  document.getElementById('hm-colors').innerHTML = COLORS
    .map(c => `<button class="hm-btn${c.key === hmColor ? ' active' : ''}" type="button" data-hm-color="${c.key}">${c.label}</button>`).join('');

  // /map?country=SI (or ?country=Slovenia) opens on one country, and choosing one updates the address, so it can be shared.
  const asked = (new URLSearchParams(location.search).get('country') || '').trim().toLowerCase();
  const code = Object.keys(counts).find(k => k.toLowerCase() === asked || countryName(k).toLowerCase() === asked);
  if (code) { hmCountry = code; sel.value = code; }
  // Country, colour and grouping go into the address (/map?country=SI&color=pe&group=country), so a copied link opens the same map.
  const defColor = hmColor;
  const syncUrl = () => urlSet({ country: hmCountry, color: hmColor === defColor ? null : hmColor, group: hmGroupBy === 'sector' ? null : hmGroupBy });
  sel.addEventListener('change', () => { hmCountry = sel.value; renderHeatmap(); syncUrl(); });
  document.querySelectorAll('[data-hm-group]').forEach(b => b.addEventListener('click', () => {
    hmGroupBy = b.dataset.hmGroup; setActive('[data-hm-group]', b); renderHeatmap(); syncUrl();
  }));
  document.querySelectorAll('[data-hm-color]').forEach(b => b.addEventListener('click', () => {
    hmColor = b.dataset.hmColor; setActive('[data-hm-color]', b); renderLegend(); renderHeatmap(); syncUrl();
  }));
  const askedColor = document.querySelector(`[data-hm-color="${CSS.escape(urlParam('color') || '')}"]`);
  if (askedColor) { hmColor = askedColor.dataset.hmColor; setActive('[data-hm-color]', askedColor); }
  const askedGroup = document.querySelector(`[data-hm-group="${CSS.escape(urlParam('group') || '')}"]`);
  if (askedGroup) { hmGroupBy = askedGroup.dataset.hmGroup; setActive('[data-hm-group]', askedGroup); }

  const canvas = document.getElementById('heatmap-canvas');
  canvas.addEventListener('pointermove', e => {
    if (e.pointerType !== 'mouse') return;
    const t = tileAt(e);
    canvas.style.cursor = t ? 'pointer' : 'default';
    t ? showHover(t, e) : hideHover();
  });
  canvas.addEventListener('mouseleave', hideHover);
  // Mouse: a click opens the company. Touch: the first tap shows the details, a second tap on the same box opens it.
  canvas.addEventListener('click', e => {
    const t = tileAt(e);
    if (!t) return hideHover();
    const touch = e.pointerType ? e.pointerType !== 'mouse' : matchMedia('(hover: none)').matches;
    if (touch && hmPinned !== t.c) { showHover(t, e); hmPinned = t.c; return; }
    location.href = HM_SLUGS[t.c.lei] ? `/stock/${HM_SLUGS[t.c.lei]}` : `/screener?lei=${encodeURIComponent(t.c.lei)}`;
  });
  window.addEventListener('scroll', () => { if (hmPinned) hideHover(); }, { passive: true });
  let timer;
  window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(renderHeatmap, 120); });

  renderLegend();
  renderHeatmap();
}

document.addEventListener('DOMContentLoaded', initHeatmap);
