// Company page (/stock/<name>): the page is rebuilt once a day, so the newest delayed price
// from js/company-prices.json (refreshed hourly) replaces the one written into the page.
(async function () {
  const box = document.querySelector('.sp-price[data-isin]');
  if (!box) return;
  let data;
  try { data = await (await fetch('/js/company-prices.json', { cache: 'no-cache' })).json(); } catch { return; }
  const q = data.rows[box.dataset.isin];
  if (!q) return;
  const [price, change, venue, day, final] = ['price', 'change', 'venue', 'date', 'final'].map(f => q[data.fields.indexOf(f)]);
  const fmt = (v, d) => v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
  box.querySelector('[data-f=price]').textContent = '€' + fmt(price, 2);
  const ch = box.querySelector('[data-f=change]');
  // Change on the previous close in euros and per cent: +€1.23 (+0.95%).
  const diff = typeof change === 'number' && change > -100 ? price - price / (1 + change / 100) : null;
  ch.textContent = diff === null ? '' : `${diff >= 0 ? '+' : '−'}€${fmt(Math.abs(diff), 2)} (${change >= 0 ? '+' : ''}${fmt(change, 2)}%)`;
  ch.className = 'modal-change ' + (change >= 0 ? 'pos' : 'neg');
  box.querySelector('[data-f=badge]').textContent = `Real price · ${venue} · ${day}${final ? '' : ' · 15 min delayed'}`;
})();

// Price chart: 4H and 1D use the minute prices of the last trading day and 1W the half-hourly prices
// of the last 5 (/js/intraday/<ISIN>.json, times shown in Frankfurt time), the longer ranges the daily
// closes embedded in the page.
// 1D is shown first when the minute prices load; moving over the chart shows that price.
(async function () {
  const fig = document.querySelector('.sp-chart');
  if (!fig) return;
  const daily = JSON.parse(fig.querySelector('.sp-hist').textContent);
  const svg = fig.querySelector('svg'), line = svg.querySelector('polyline'), area = svg.querySelector('polygon');
  const cap = fig.querySelectorAll('figcaption span'), axis = fig.querySelectorAll('.sp-chart-axis span');
  const W = 600, H = 150, P = 4;
  const fmt = (v, d = 2) => v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
  let intra = null, pts = [], xy = [];
  try {
    const r = await fetch(`/js/intraday/${fig.dataset.isin}.json`, { cache: 'no-cache' });
    if (r.ok) intra = await r.json();
  } catch { /* no minute prices: daily ranges only */ }
  // Minute of the day (UTC) -> "15:42" in Frankfurt time.
  const clock = (m, d = intra.date) => new Date(Date.UTC(...d.split('-').map((v, i) => i === 1 ? v - 1 : +v), 0, m))
    .toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' });
  const weekday = d => new Date(d + 'T12:00:00Z').toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
  const day = d => new Date(d + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });

  // Trading hours of the venue in minutes of the day (UTC): Xetra 9:00-17:30, Börse Frankfurt 8:00-22:00 Frankfurt time.
  function session() {
    const [y, mo, d] = intra.date.split('-').map(Number);
    const noon = new Date(Date.UTC(y, mo - 1, d, 12));
    const berlinHour = +noon.toLocaleString('en-GB', { hour: '2-digit', hour12: false, timeZone: 'Europe/Berlin' });
    const off = (berlinHour - 12) * 60;   // 60 in winter, 120 in summer
    const [a, b] = /frankfurt/i.test(intra.venue || '') ? [8 * 60, 22 * 60] : [9 * 60, 17 * 60 + 30];
    const bars = intra.bars;
    // Never cut off a price: widen to the first and last bar if the venue traded outside these hours.
    return [Math.min(a - off, bars[0][0]), Math.max(b - off, bars[bars.length - 1][0])];
  }

  function draw(range) {
    if (range === 'w') {
      pts = intra.week.map(([d, m, p]) => [`${weekday(d)} ${clock(m, d)}`, p]);
      cap[0].textContent = `Last ${new Set(intra.week.map(w => w[0])).size} trading days · ${intra.venue}`;
    } else if (range[0] === 'i') {
      const bars = intra.bars, mins = +range.slice(1), end = bars[bars.length - 1][0];
      pts = bars.filter(b => !mins || b[0] > end - mins).map(b => [clock(b[0]), b[1]]);
      if (pts.length < 2) pts = bars.slice(-2).map(b => [clock(b[0]), b[1]]);
      cap[0].textContent = `${range === 'i0' ? 'Trading day' : range === 'i60' ? 'Last hour' : 'Last 4 hours'} · ${day(intra.date)} · ${intra.venue}`;
    } else {
      const days = +range.slice(1);
      const from = days ? new Date(new Date(daily[daily.length - 1][0]).getTime() - days * 864e5).toISOString().slice(0, 10) : '';
      pts = daily.filter(p => p[0] >= from);
      if (pts.length < 2) pts = daily.slice(-2);
      cap[0].textContent = 'Price since ' + day(pts[0][0]);
    }
    const vals = pts.map(p => p[1]), lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    // 1D: the axis is the whole trading day, so the line ends where the day is now.
    const sess = range === 'i0' ? session() : null;
    const xAt = sess ? (m => P + Math.max(0, Math.min(1, (m - sess[0]) / (sess[1] - sess[0]))) * (W - 2 * P))
      : null;
    const dayBars = sess ? intra.bars : null;
    xy = vals.map((v, i) => [sess ? xAt(dayBars[i][0]) : P + i * (W - 2 * P) / Math.max(1, vals.length - 1),
      P + (hi - v) * (H - 2 * P) / span]);
    const pl = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const chg = (vals[vals.length - 1] / vals[0] - 1) * 100, cls = chg >= 0 ? 'pos' : 'neg';
    line.setAttribute('points', pl); line.setAttribute('class', 'sp-chart-line ' + cls);
    area.setAttribute('points', `${xy[0][0].toFixed(1)},${H} ${pl} ${xy[xy.length - 1][0].toFixed(1)},${H}`); area.setAttribute('class', 'sp-chart-area ' + cls);
    cap[1].textContent = (chg >= 0 ? '+' : '') + fmt(chg, 2) + '%'; cap[1].className = cls;
    axis[0].textContent = pts[0][0].length === 10 ? day(pts[0][0]) : pts[0][0];
    axis[1].textContent = `low €${fmt(lo)} · high €${fmt(hi)}`;
    const last = pts[pts.length - 1][0];
    axis[2].textContent = last.length === 10 ? day(last) : last;
    if (sess) { axis[0].textContent = clock(sess[0]); axis[2].textContent = clock(sess[1]); }
    fig.querySelectorAll('[data-range]').forEach(b => b.classList.toggle('active', b.dataset.range === range));
  }

  // The page holds the closes up to when it was built; today's price so far ends the daily line.
  if (intra && intra.bars && intra.bars.length && (!daily.length || intra.date > daily[daily.length - 1][0]))
    daily.push([intra.date, intra.bars[intra.bars.length - 1][1]]);
  const usable = intra && intra.bars && intra.bars.length >= 2;
  if (usable) fig.querySelectorAll('[data-range^="i"]').forEach(b => { b.hidden = false; });
  // 1W from half-hourly prices once two trading days are in; it replaces the 1W of daily closes.
  if (usable && intra.week && new Set(intra.week.map(w => w[0])).size >= 2) {
    fig.querySelectorAll('[data-range="w"]').forEach(b => { b.hidden = false; });
    fig.querySelectorAll('[data-range="d7"]').forEach(b => b.remove());
  }
  if (!usable && daily.length < 5) {
    // Nothing to draw yet: a single close, no minute prices.
    fig.querySelector('.sp-chart-ranges').remove(); svg.remove(); fig.querySelector('.sp-chart-axis').remove();
    cap[0].textContent = `Price chart: FINILA started recording daily closing prices on ${day(daily[0] ? daily[0][0] : new Date().toISOString().slice(0, 10))}; the chart fills in day by day.`;
    return;
  }
  fig.querySelectorAll('[data-range]').forEach(b => b.addEventListener('click', () => draw(b.dataset.range)));
  const cursor = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  cursor.setAttribute('class', 'sp-chart-cursor'); cursor.setAttribute('y1', 0); cursor.setAttribute('y2', H); cursor.style.display = 'none';
  svg.appendChild(cursor);
  const tip = document.createElement('div'); tip.className = 'sp-chart-tip'; fig.appendChild(tip);
  svg.addEventListener('pointermove', e => {
    const r = svg.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * W;
    let i = 0;
    for (let k = 1; k < xy.length; k++) if (Math.abs(xy[k][0] - x) < Math.abs(xy[i][0] - x)) i = k;
    cursor.setAttribute('x1', xy[i][0]); cursor.setAttribute('x2', xy[i][0]); cursor.style.display = '';
    const when = pts[i][0].length === 10 ? day(pts[i][0]) : pts[i][0];
    tip.textContent = `${when} · €${fmt(pts[i][1])}`; tip.style.display = 'block';
    tip.style.left = Math.min(r.width - tip.offsetWidth, Math.max(0, xy[i][0] / W * r.width - tip.offsetWidth / 2)) + 'px';
  });
  svg.addEventListener('pointerleave', () => { cursor.style.display = 'none'; tip.style.display = 'none'; });
  draw(usable ? 'i0' : 'd0');
})();

// "What if the company grows?": the visitor's assumptions -> revenue, EBITDA, company value and price
// per share after N years. Amounts in EUR millions, shares in millions. Choices go into the address
// (?growth=6&margin=40&multiple=12&years=5) so a scenario can be shared.
(function () {
  const box = document.getElementById('whatif');
  if (!box) return;
  const d = JSON.parse(box.dataset.whatif);
  const inp = k => box.querySelector(`[data-i="${k}"]`), out = k => box.querySelector(`[data-o="${k}"]`);
  const keys = { g: 'growth', m: 'margin', x: 'multiple', y: 'years' };
  Object.entries(keys).forEach(([k, q]) => {
    const v = typeof urlParam === 'function' ? parseFloat(urlParam(q)) : NaN;
    if (!isNaN(v)) inp(k).value = v;
  });
  const fmt = (v, dg = 1) => v.toLocaleString('en-GB', { minimumFractionDigits: dg, maximumFractionDigits: dg });
  const money = v => Math.abs(v) >= 1000 ? '€' + fmt(v / 1000, 2) + 'B' : '€' + fmt(v, 0) + 'M';
  function calc(write) {
    const g = +inp('g').value, m = +inp('m').value, x = +inp('x').value, y = +inp('y').value;
    out('g').textContent = (g >= 0 ? '+' : '') + fmt(g) + '%';
    out('m').textContent = fmt(m) + '%';
    out('x').textContent = fmt(x) + '×';
    out('y').textContent = y;
    const rev = d.rev * Math.pow(1 + g / 100, y), ebitda = rev * m / 100, ev = ebitda * x;
    const pps = (ev - d.nd) / d.shares;
    out('rev').textContent = money(rev);
    out('ebitda').textContent = money(ebitda);
    out('ev').textContent = money(ev);
    if (pps <= 0) {
      out('pps').textContent = '€0';
      out('ret').textContent = 'debt would be larger than the company value';
    } else {
      out('pps').textContent = '€' + fmt(pps, 2);
      const r = (Math.pow(pps / d.price, 1 / y) - 1) * 100, tot = (pps / d.price - 1) * 100;
      out('ret').textContent = `${tot >= 0 ? '+' : ''}${fmt(tot, 0)}% vs today’s €${fmt(d.price, 2)} · ${r >= 0 ? '+' : ''}${fmt(r)}% a year`;
      out('ret').className = r >= 0 ? 'pos' : 'neg';
    }
    if (write && typeof urlSet === 'function') {
      const start = { g: d.g, m: d.m, x: d.x, y: d.y || 3 };
      urlSet(Object.fromEntries(Object.entries(keys).map(([k, q]) => [q, +inp(k).value === start[k] ? null : inp(k).value])));
    }
  }
  box.querySelectorAll('input[type=range]').forEach(i => i.addEventListener('input', () => calc(true)));
  calc(false);
})();
