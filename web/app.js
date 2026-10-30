// Festival-Score: statische Vergleichsseite, Vanilla JS, kein Build.
const NF0 = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 });
const NF1 = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const NFC = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const PCT = new Intl.NumberFormat('de-DE', { style: 'percent', maximumFractionDigits: 0 });
const EUR = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });

const DASH = '–';
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const f1 = (v) => (isNum(v) ? NF1.format(v) : DASH);
const f2 = (v) => (isNum(v) ? NF2.format(v) : DASH);
const fInt = (v) => (isNum(v) ? NF0.format(v) : DASH);
const fEur = (v) => (isNum(v) ? EUR.format(v) : DASH);
const fPct = (v) => (isNum(v) ? PCT.format(v) : DASH);
function fListeners(v) {
  if (!isNum(v)) return DASH;
  if (v >= 1e6) return NFC.format(v / 1e6) + ' Mio.';
  return NF0.format(v);
}
function fDate(s) {
  if (!s) return null;
  const d = new Date(s + 'T00:00:00');
  return Number.isNaN(d.getTime()) ? null : d;
}
function fRange(a, b) {
  const da = fDate(a), db = fDate(b);
  if (!da) return DASH;
  const full = (d) => d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
  if (!db || a === b) return full(da);
  if (da.getMonth() === db.getMonth() && da.getFullYear() === db.getFullYear()) {
    return `${String(da.getDate()).padStart(2, '0')}.–${full(db)}`;
  }
  return `${full(da)} – ${full(db)}`;
}

// DOM-Helfer: ausschließlich textContent, nie innerHTML mit Daten.
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return e;
}

const app = document.getElementById('main');
let DATA = null;
let charts = [];

// ---------- Zustand ----------
const DEFAULT_W = { top30: 50, headliner: 25, depth: 15, per_euro: 10 };
const state = {
  weights: loadWeights(),
  includeEarly: false,
  sortKey: 'total',
  sortDir: 'desc',
  pastKey: 'year',
  pastDir: 'desc',
  histMetric: 'top30',
  hidden: new Set(),
};
function loadWeights() {
  try {
    const raw = localStorage.getItem('fs-weights');
    if (raw) {
      const o = JSON.parse(raw);
      const w = {};
      for (const k of Object.keys(DEFAULT_W)) w[k] = isNum(o[k]) ? Math.min(100, Math.max(0, o[k])) : DEFAULT_W[k];
      return w;
    }
  } catch (e) { /* ignorieren */ }
  return { ...DEFAULT_W };
}
function saveWeights() {
  try { localStorage.setItem('fs-weights', JSON.stringify(state.weights)); } catch (e) { /* ignorieren */ }
}

// ---------- Daten ----------
function flatEditions() {
  const out = [];
  for (const f of DATA.festivals) for (const e of f.editions) out.push({ f, e });
  return out;
}
const sc = (e, k) => (e.scores && isNum(e.scores[k]) ? e.scores[k] : null);
function isEarly(e) {
  return (isNum(e.completeness) && e.completeness < 0.3) || e.act_count < 10;
}

const METRICS = [
  ['top30', 'Top 30'],
  ['headliner', 'Headliner-Stärke'],
  ['depth', 'Breite'],
  ['per_euro', 'pro Euro'],
];

function computeRanking() {
  const rows = [];
  for (const f of DATA.festivals) {
    const e = f.editions.find((x) => x.status === 'upcoming');
    if (!e) continue;
    // neueste upcoming (Liste ist neueste zuerst; bei mehreren das höchste Jahr)
    const best = f.editions.filter((x) => x.status === 'upcoming').sort((a, b) => b.year - a.year)[0];
    rows.push({ f, e: best, early: isEarly(best) });
  }
  const pool = rows.filter((r) => state.includeEarly || !r.early);
  const range = {};
  for (const [k] of METRICS) {
    const vals = pool.map((r) => sc(r.e, k)).filter(isNum);
    range[k] = vals.length ? [Math.min(...vals), Math.max(...vals)] : null;
  }
  for (const r of rows) {
    let sum = 0, wsum = 0;
    for (const [k] of METRICS) {
      const w = state.weights[k];
      const v = sc(r.e, k);
      if (!isNum(v) || !range[k] || w <= 0) continue;
      const [lo, hi] = range[k];
      const norm = hi > lo ? ((v - lo) / (hi - lo)) * 100 : 50;
      sum += Math.min(100, Math.max(0, norm)) * w;
      wsum += w;
    }
    r.total = wsum > 0 ? sum / wsum : null;
    r.counted = state.includeEarly || !r.early;
    if (!r.counted) r.total = null; // frühe Lineups bekommen keine Wertung, solange ausgeschlossen
  }
  return rows;
}

const RANK_COLS = [
  { key: 'rank', label: 'Rang', nosort: true },
  { key: 'name', label: 'Festival', cls: 'l', get: (r) => r.f.name },
  { key: 'year', label: 'Jahr', get: (r) => r.e.year },
  { key: 'date', label: 'Termin', cls: 'l', get: (r) => r.e.start_date || '' },
  { key: 'days', label: 'Tage', get: (r) => r.e.days },
  { key: 'price', label: 'Preis', get: (r) => r.e.price_eur },
  { key: 'acts', label: 'Acts', get: (r) => r.e.act_count },
  { key: 'completeness', label: 'Vollständigkeit', get: (r) => r.e.completeness },
  { key: 'top30', label: 'Top 30', get: (r) => sc(r.e, 'top30') },
  { key: 'headliner', label: 'Headliner', get: (r) => sc(r.e, 'headliner') },
  { key: 'depth', label: 'Breite', get: (r) => sc(r.e, 'depth') },
  { key: 'per_day', label: 'pro Tag', get: (r) => sc(r.e, 'per_day') },
  { key: 'per_euro', label: 'pro Euro', get: (r) => sc(r.e, 'per_euro') },
  { key: 'total', label: 'Gesamtwertung', get: (r) => r.total },
];

function cmpValues(a, b, dir) {
  // fehlende Werte immer ans Ende
  const an = a == null || a === '' || (typeof a === 'number' && !Number.isFinite(a));
  const bn = b == null || b === '' || (typeof b === 'number' && !Number.isFinite(b));
  if (an && bn) return 0;
  if (an) return 1;
  if (bn) return -1;
  const c = typeof a === 'string' ? a.localeCompare(b, 'de') : a - b;
  return dir === 'asc' ? c : -c;
}

// ---------- Charts ----------
function destroyCharts() {
  for (const c of charts) c.destroy();
  charts = [];
}
function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
const LIGHT_PAL = ['#5b3fd6', '#d1495b', '#00798c', '#e08a00', '#2e7d32', '#8d5a97', '#b5651d', '#3a6ea5', '#7a7a2a'];
const DARK_PAL = ['#a995ff', '#ff8a9a', '#4cd0e0', '#ffc24d', '#7fd68a', '#d9a6e8', '#e9a066', '#7db4f0', '#cfcf6a'];
function palette() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? DARK_PAL : LIGHT_PAL;
}
function chartBox(cls, label) {
  const canvas = el('canvas', { role: 'img', 'aria-label': label });
  return { box: el('div', { class: 'chart-box ' + (cls || '') }, canvas), canvas };
}
function noChartNote() {
  return el('p', { class: 'status' }, 'Diagramme konnten nicht geladen werden (Chart.js nicht erreichbar).');
}

// ---------- Übersicht ----------
function renderOverview() {
  const sc0 = DATA.scoring || {};
  const gen = new Date(DATA.generated_at);
  const genTxt = Number.isNaN(gen.getTime())
    ? DATA.generated_at
    : gen.toLocaleString('de-DE', { dateStyle: 'long', timeStyle: 'short' });
  const root = el('div');
  root.append(
    el('h1', null, 'Festival-Score'),
    el('p', null,
      'Wir bewerten die Lineups großer deutscher Festivals anhand der monatlichen Hörer ihrer Acts (Spotify, ersatzweise geschätzt aus Last.fm). ',
      'Punkte pro Act: 10.000 Hörer = 0, 100.000 = 1, 1 Mio. = 2, 10 Mio. = 3, 100 Mio. = 4.'),
    el('p', null,
      `Top 30 summiert die Punkte der ${sc0.top_depth || 30} besten Acts, Headliner-Stärke ist der Mittelwert der ${sc0.top_n || 5} besten, Breite der Median der übrigen. `,
      'Kommende Lineups sind oft noch unvollständig, vergleiche sie mit Vorsicht.'),
    el('p', { class: 'muted small' }, 'Datenstand: ', el('time', { datetime: DATA.generated_at }, genTxt)),
  );
  root.append(rankingSection(), historySection(), pastSection());
  app.replaceChildren(root);
  drawHistory();
}

function rankingSection() {
  const sec = el('section', { 'aria-labelledby': 'h-rank' });
  sec.append(el('h2', { id: 'h-rank' }, 'Ranking der kommenden Ausgaben'));

  // Gewichte
  const wbox = el('div', { class: 'card' });
  wbox.append(el('h3', null, 'Gesamtwertung gewichten'));
  const grid = el('div', { class: 'weights' });
  const outputs = {};
  for (const [k, label] of METRICS) {
    const id = 'w-' + k;
    const out = el('output', { for: id }, state.weights[k] + ' %');
    outputs[k] = out;
    const input = el('input', {
      type: 'range', id, min: 0, max: 100, step: 1, value: state.weights[k],
      oninput: (ev) => {
        state.weights[k] = Number(ev.target.value);
        out.textContent = state.weights[k] + ' %';
        saveWeights();
        refreshRanking();
      },
    });
    grid.append(el('div', { class: 'weight' }, el('label', { for: id }, el('span', null, label), out), input));
  }
  wbox.append(grid,
    el('p', { class: 'muted small', style: 'margin-top:10px' },
      'Jede Kennzahl wird über die eingereihten Ausgaben auf 0–100 normiert (Min-Max). Fehlt ein Wert, wird die Kennzahl für diese Ausgabe ausgelassen und die übrigen Gewichte werden neu skaliert. ',
      el('button', { type: 'button', onclick: () => {
        state.weights = { ...DEFAULT_W };
        saveWeights();
        for (const [k] of METRICS) {
          document.getElementById('w-' + k).value = state.weights[k];
          outputs[k].textContent = state.weights[k] + ' %';
        }
        refreshRanking();
      } }, 'Zurücksetzen')));
  sec.append(wbox);

  // Sortierung
  const sortSel = el('select', { id: 'sort-sel', onchange: (ev) => {
    state.sortKey = ev.target.value;
    state.sortDir = ev.target.value === 'name' ? 'asc' : 'desc';
    refreshRanking();
  } }, RANK_COLS.filter((c) => !c.nosort).map((c) => el('option', { value: c.key }, c.label)));
  sortSel.value = state.sortKey;
  const earlyChk = el('input', { type: 'checkbox', id: 'early-chk', onchange: (ev) => {
    state.includeEarly = ev.target.checked;
    refreshRanking();
  } });
  earlyChk.checked = state.includeEarly;
  sec.append(el('div', { class: 'controls' },
    el('label', { for: 'sort-sel' }, 'Sortieren nach', sortSel),
    el('label', { for: 'early-chk' }, earlyChk, 'auch frühe Lineups einreihen')));

  sec.append(el('div', { id: 'rank-holder' }));
  const holder = sec.querySelector('#rank-holder');
  holder.append(rankingTable());
  return sec;
}

function refreshRanking() {
  const holder = document.getElementById('rank-holder');
  if (!holder) return;
  const sel = document.getElementById('sort-sel');
  if (sel) sel.value = state.sortKey;
  holder.replaceChildren(rankingTable());
}

function rankingTable() {
  const rows = computeRanking();
  const col = RANK_COLS.find((c) => c.key === state.sortKey) || RANK_COLS[RANK_COLS.length - 1];
  // Rang laut Gesamtwertung
  const byTotal = rows.filter((r) => r.counted && isNum(r.total)).sort((a, b) => b.total - a.total);
  byTotal.forEach((r, i) => { r.rank = i + 1; });
  rows.sort((a, b) => {
    if (!state.includeEarly && a.early !== b.early) return a.early ? 1 : -1;
    return cmpValues(col.get(a), col.get(b), state.sortDir) || a.f.name.localeCompare(b.f.name, 'de');
  });
  if (!rows.length) return el('p', { class: 'status' }, 'Keine kommenden Ausgaben in den Daten.');

  const thead = el('tr', null, RANK_COLS.map((c) => {
    const th = el('th', { scope: 'col', class: c.cls || '' });
    if (c.nosort) { th.textContent = c.label; return th; }
    const active = state.sortKey === c.key;
    th.setAttribute('aria-sort', active ? (state.sortDir === 'asc' ? 'ascending' : 'descending') : 'none');
    th.append(el('button', { type: 'button', onclick: () => {
      if (state.sortKey === c.key) state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
      else { state.sortKey = c.key; state.sortDir = c.cls === 'l' && c.key === 'name' ? 'asc' : 'desc'; }
      refreshRanking();
    } }, c.label, el('span', { class: 'arrow', 'aria-hidden': 'true' }, active ? (state.sortDir === 'asc' ? '▲' : '▼') : '')));
    return th;
  }));

  const body = rows.map((r) => {
    const e = r.e;
    const nameCell = el('td', { class: 'l' },
      el('a', { href: `#/festival/${encodeURIComponent(r.f.slug)}/${e.year}`, class: 'fname' }, r.f.name),
      r.early ? el('span', { class: 'badge' }, 'Lineup noch früh') : null);
    const comp = el('td', { class: 'num' });
    if (isNum(e.completeness)) {
      comp.append(el('span', { class: 'meter', role: 'img', 'aria-label': fPct(e.completeness) },
        el('span', { style: `width:${Math.min(100, e.completeness * 100)}%` })), fPct(e.completeness));
    } else comp.textContent = DASH;
    return el('tr', { class: r.early ? 'early' : '' },
      el('td', { class: 'num' }, r.rank ? r.rank : DASH),
      nameCell,
      el('td', { class: 'num' }, e.year),
      el('td', { class: 'l' }, fRange(e.start_date, e.end_date)),
      el('td', { class: 'num' }, fInt(e.days)),
      el('td', { class: 'num' }, fEur(e.price_eur)),
      el('td', { class: 'num' }, fInt(e.act_count)),
      comp,
      el('td', { class: 'num' }, f1(sc(e, 'top30'))),
      el('td', { class: 'num' }, f2(sc(e, 'headliner'))),
      el('td', { class: 'num' }, f2(sc(e, 'depth'))),
      el('td', { class: 'num' }, f1(sc(e, 'per_day'))),
      el('td', { class: 'num' }, f2(sc(e, 'per_euro'))),
      el('td', { class: 'num score-cell' }, isNum(r.total) ? f1(r.total) : DASH));
  });

  return el('div', { class: 'table-scroll', tabindex: '0', role: 'region', 'aria-label': 'Ranking der kommenden Ausgaben (scrollbar)' },
    el('table', null,
      el('caption', { class: 'small muted', style: 'text-align:left;padding:8px 10px;caption-side:bottom' },
        'Gesamtwertung 0–100, relativ zu den eingereihten Ausgaben.'),
      el('thead', null, thead), el('tbody', null, body)));
}

// ---------- Verlauf ----------
function historySection() {
  const sec = el('section', { 'aria-labelledby': 'h-hist' });
  sec.append(el('h2', { id: 'h-hist' }, 'Verlauf'));
  const sel = el('select', { id: 'hist-sel', onchange: (ev) => { state.histMetric = ev.target.value; drawHistory(); } },
    [['top30', 'Top 30'], ['headliner', 'Headliner-Stärke'], ['depth', 'Breite'], ['acts', 'Anzahl Acts']]
      .map(([v, l]) => el('option', { value: v }, l)));
  sel.value = state.histMetric;
  sec.append(el('div', { class: 'controls' }, el('label', { for: 'hist-sel' }, 'Kennzahl', sel)),
    el('p', { class: 'muted small' },
      'Gestrichelte Linien und hohle Punkte: kommende, noch unvollständige Ausgaben. Legende anklicken blendet Festivals aus.'));
  const { box, canvas } = chartBox('', 'Liniendiagramm der Kennzahl je Festival über die Jahre');
  canvas.id = 'hist-canvas';
  sec.append(typeof Chart === 'undefined' ? noChartNote() : box);
  return sec;
}

function drawHistory() {
  const canvas = document.getElementById('hist-canvas');
  if (!canvas || typeof Chart === 'undefined') return;
  const old = charts.find((c) => c.canvas === canvas);
  if (old) { old.destroy(); charts = charts.filter((c) => c !== old); }
  const years = [...new Set(flatEditions().map(({ e }) => e.year))].sort((a, b) => a - b);
  const pal = palette();
  const m = state.histMetric;
  const text = cssVar('--text'), grid = cssVar('--border'), surface = cssVar('--surface');
  const datasets = DATA.festivals.map((f, i) => {
    const color = pal[i % pal.length];
    const up = years.map((y) => f.editions.some((e) => e.year === y && e.status === 'upcoming'));
    const data = years.map((y) => {
      const e = f.editions.find((x) => x.year === y);
      if (!e) return null;
      const v = m === 'acts' ? e.act_count : sc(e, m);
      return isNum(v) ? v : null;
    });
    return {
      label: f.name, data, borderColor: color, backgroundColor: color, spanGaps: true,
      hidden: state.hidden.has(f.slug), slug: f.slug, borderWidth: 2, tension: 0.15,
      pointRadius: 5, pointHoverRadius: 7,
      pointBackgroundColor: (ctx) => (up[ctx.dataIndex] ? surface : color),
      pointBorderColor: color, pointBorderWidth: 2,
      segment: { borderDash: (ctx) => (up[ctx.p1DataIndex] ? [6, 4] : undefined) },
    };
  });
  const chart = new Chart(canvas, {
    type: 'line',
    data: { labels: years.map(String), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: {
          labels: { color: text, usePointStyle: true },
          onClick: (ev, item, legend) => {
            const ds = legend.chart.data.datasets[item.datasetIndex];
            if (state.hidden.has(ds.slug)) state.hidden.delete(ds.slug); else state.hidden.add(ds.slug);
            Chart.defaults.plugins.legend.onClick.call(legend, ev, item, legend);
          },
        },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${m === 'acts' ? fInt(c.parsed.y) : f2(c.parsed.y)}` } },
      },
      scales: {
        x: { ticks: { color: text }, grid: { color: grid } },
        y: { beginAtZero: true, ticks: { color: text }, grid: { color: grid } },
      },
    },
  });
  charts.push(chart);
}

// ---------- Vergangene Ausgaben ----------
const PAST_COLS = [
  { key: 'name', label: 'Festival', cls: 'l', get: (r) => r.f.name },
  { key: 'year', label: 'Jahr', get: (r) => r.e.year },
  { key: 'date', label: 'Termin', cls: 'l', get: (r) => r.e.start_date || '' },
  { key: 'price', label: 'Preis', get: (r) => r.e.price_eur },
  { key: 'acts', label: 'Acts', get: (r) => r.e.act_count },
  { key: 'top30', label: 'Top 30', get: (r) => sc(r.e, 'top30') },
  { key: 'headliner', label: 'Headliner', get: (r) => sc(r.e, 'headliner') },
  { key: 'depth', label: 'Breite', get: (r) => sc(r.e, 'depth') },
  { key: 'per_euro', label: 'pro Euro', get: (r) => sc(r.e, 'per_euro') },
];
function pastSection() {
  const det = el('details', { open: true });
  det.append(el('summary', null, 'Vergangene Ausgaben'), el('div', { id: 'past-holder' }, pastTable()));
  return det;
}
function refreshPast() {
  const h = document.getElementById('past-holder');
  if (h) h.replaceChildren(pastTable());
}
function pastTable() {
  const rows = flatEditions().filter(({ e }) => e.status === 'past');
  const col = PAST_COLS.find((c) => c.key === state.pastKey) || PAST_COLS[1];
  rows.sort((a, b) => cmpValues(col.get(a), col.get(b), state.pastDir) || a.f.name.localeCompare(b.f.name, 'de') || b.e.year - a.e.year);
  const head = el('tr', null, PAST_COLS.map((c) => {
    const active = state.pastKey === c.key;
    return el('th', { scope: 'col', class: c.cls || '', 'aria-sort': active ? (state.pastDir === 'asc' ? 'ascending' : 'descending') : 'none' },
      el('button', { type: 'button', onclick: () => {
        if (active) state.pastDir = state.pastDir === 'asc' ? 'desc' : 'asc';
        else { state.pastKey = c.key; state.pastDir = c.key === 'name' ? 'asc' : 'desc'; }
        refreshPast();
      } }, c.label, el('span', { class: 'arrow', 'aria-hidden': 'true' }, active ? (state.pastDir === 'asc' ? '▲' : '▼') : '')));
  }));
  const body = rows.map(({ f, e }) => el('tr', null,
    el('td', { class: 'l' }, el('a', { href: `#/festival/${encodeURIComponent(f.slug)}/${e.year}` }, f.name)),
    el('td', { class: 'num' }, e.year),
    el('td', { class: 'l' }, fRange(e.start_date, e.end_date)),
    el('td', { class: 'num' }, fEur(e.price_eur)),
    el('td', { class: 'num' }, fInt(e.act_count)),
    el('td', { class: 'num' }, f1(sc(e, 'top30'))),
    el('td', { class: 'num' }, f2(sc(e, 'headliner'))),
    el('td', { class: 'num' }, f2(sc(e, 'depth'))),
    el('td', { class: 'num' }, f2(sc(e, 'per_euro')))));
  return el('div', { class: 'table-scroll', tabindex: '0', role: 'region', 'aria-label': 'Vergangene Ausgaben (scrollbar)' },
    el('table', null, el('thead', null, head), el('tbody', null, body)));
}

// ---------- Detail ----------
const actState = { sort: 'position', q: '' };

function renderDetail(slug, yearStr) {
  const f = DATA.festivals.find((x) => x.slug === slug);
  if (!f) return renderNotFound();
  let e = f.editions.find((x) => String(x.year) === yearStr) || f.editions[0];
  if (!e) return renderNotFound();
  const root = el('div');
  root.append(el('p', { class: 'crumb' }, el('a', { href: '#/' }, '← Übersicht')),
    el('h1', null, f.name),
    el('p', { class: 'muted' }, f.location || ''));

  const tabs = el('div', { class: 'tabs', role: 'group', 'aria-label': 'Jahr wählen' },
    f.editions.map((x) => el('button', {
      type: 'button', 'aria-pressed': x === e ? 'true' : 'false',
      onclick: () => { location.hash = `#/festival/${encodeURIComponent(f.slug)}/${x.year}`; },
    }, String(x.year) + (x.status === 'upcoming' ? ' (kommend)' : ''))));
  root.append(tabs);

  // Eckdaten
  const facts = el('dl', { class: 'facts' });
  const add = (k, v) => { facts.append(el('div', null, el('dt', null, k), el('dd', null, v))); };
  add('Termin', fRange(e.start_date, e.end_date));
  add('Tage', fInt(e.days));
  add('Preis', fEur(e.price_eur));
  add('Kapazität', fInt(e.capacity));
  add('Besucher', fInt(e.visitors));
  const srcDd = [];
  srcDd.push(e.source ? String(e.source) : DASH);
  if (typeof e.url === 'string' && /^https?:\/\//i.test(e.url)) {
    srcDd.push(' (', el('a', { href: e.url, rel: 'noopener' }, 'Link'), ')');
  }
  add('Quelle', srcDd);
  root.append(el('div', { class: 'card' }, facts));

  if (e.status === 'upcoming') {
    root.append(el('p', { class: 'status small', style: 'margin-top:10px' },
      'Kommende Ausgabe: Das Lineup ist ' + (isNum(e.completeness) ? `etwa zu ${fPct(e.completeness)} bekannt` : 'möglicherweise noch unvollständig') + '.'));
  }

  // Kacheln
  const tiles = el('div', { class: 'tiles' });
  const tile = (k, v) => tiles.append(el('div', { class: 'tile' }, el('div', { class: 'v' }, v), el('div', { class: 'k' }, k)));
  tile('Top 30', f1(sc(e, 'top30')));
  tile('Headliner-Stärke', f2(sc(e, 'headliner')));
  tile('Breite', f2(sc(e, 'depth')));
  tile('Summe aller Punkte', f1(sc(e, 'total')));
  tile('pro Tag', f1(sc(e, 'per_day')));
  tile('pro Euro', f2(sc(e, 'per_euro')));
  tile('Acts', fInt(e.act_count) + (isNum(e.reported_act_count) ? ` (offiziell ${fInt(e.reported_act_count)})` : ''));
  tile('Vollständigkeit', fPct(e.completeness));
  root.append(tiles);

  // Verteilung
  root.append(el('h2', null, 'Verteilung der Punkte'));
  const dist = [0, 0, 0, 0];
  for (const a of e.acts || []) {
    if (!isNum(a.points)) continue;
    dist[Math.min(3, Math.floor(a.points))]++;
  }
  if (typeof Chart === 'undefined') root.append(noChartNote());
  else {
    const { box, canvas } = chartBox('small', 'Balkendiagramm: Anzahl Acts je Punkte-Stufe');
    canvas.id = 'dist-canvas';
    root.append(box);
    root.dataset.dist = JSON.stringify(dist);
  }

  // Act-Liste
  root.append(el('h2', null, 'Acts'));
  const holder = el('div');
  const draw = () => holder.replaceChildren(actTable(e));
  const sortSel = el('select', { id: 'act-sort', onchange: (ev) => { actState.sort = ev.target.value; draw(); } },
    [['position', 'Position'], ['listeners', 'Hörer'], ['name', 'Name']].map(([v, l]) => el('option', { value: v }, l)));
  sortSel.value = actState.sort;
  const search = el('input', { type: 'search', id: 'act-q', class: 'list-search', placeholder: 'Act suchen …', 'aria-label': 'Act suchen', value: actState.q,
    oninput: (ev) => { actState.q = ev.target.value; draw(); } });
  root.append(el('div', { class: 'controls' }, el('label', { for: 'act-sort' }, 'Sortieren nach', sortSel), search), holder);
  draw();

  app.replaceChildren(root);
  const dc = document.getElementById('dist-canvas');
  if (dc) {
    const text = cssVar('--text'), grid = cssVar('--border');
    charts.push(new Chart(dc, {
      type: 'bar',
      data: { labels: ['0–1', '1–2', '2–3', '3+'], datasets: [{ label: 'Acts', data: dist, backgroundColor: cssVar('--bar') }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { title: { display: true, text: 'Punkte', color: text }, ticks: { color: text }, grid: { color: grid } },
          y: { beginAtZero: true, ticks: { color: text, precision: 0 }, grid: { color: grid }, title: { display: true, text: 'Anzahl Acts', color: text } },
        },
      },
    }));
  }
}

function actTable(e) {
  let acts = [...(e.acts || [])];
  const q = actState.q.trim().toLowerCase();
  if (q) acts = acts.filter((a) => String(a.name).toLowerCase().includes(q));
  if (actState.sort === 'listeners') acts.sort((a, b) => cmpValues(a.monthly_listeners ?? a.lastfm_listeners, b.monthly_listeners ?? b.lastfm_listeners, 'desc'));
  else if (actState.sort === 'name') acts.sort((a, b) => String(a.name).localeCompare(String(b.name), 'de'));
  else acts.sort((a, b) => (a.position ?? 1e9) - (b.position ?? 1e9));
  if (!acts.length) return el('p', { class: 'status' }, 'Keine Acts gefunden.');
  const rows = acts.map((a) => {
    const nameCell = el('td', { class: 'act' },
      a.spotify_id
        ? el('a', { href: `https://open.spotify.com/artist/${encodeURIComponent(a.spotify_id)}`, rel: 'noopener' }, String(a.name))
        : String(a.name),
      a.headliner ? el('span', { class: 'badge hl', style: 'margin-left:6px' }, 'Headliner') : null);
    const l = el('td', { class: 'num' });
    if (isNum(a.monthly_listeners)) {
      if (a.listeners_estimated) l.append(el('span', { class: 'hint', title: 'aus Last.fm geschätzt' }, '≈ ' + fListeners(a.monthly_listeners)));
      else l.textContent = fListeners(a.monthly_listeners);
    } else l.textContent = DASH;
    const p = el('td', { class: 'num' });
    if (isNum(a.points)) {
      p.append(el('span', { class: 'pbar', role: 'img', 'aria-label': `${f2(a.points)} von 4 Punkten` },
        el('span', { style: `width:${Math.min(100, (a.points / 4) * 100)}%` })), f2(a.points));
    } else p.append(el('span', { class: 'muted' }, 'keine Daten'));
    return el('tr', null,
      el('td', { class: 'num' }, isNum(a.position) ? a.position + 1 : DASH),
      nameCell, l,
      el('td', { class: 'num' }, isNum(a.lastfm_listeners) ? fListeners(a.lastfm_listeners) : DASH), p);
  });
  return el('div', { class: 'table-scroll', tabindex: '0', role: 'region', 'aria-label': 'Acts (scrollbar)' },
    el('table', null,
      el('thead', null, el('tr', null,
        el('th', { scope: 'col' }, '#'),
        el('th', { scope: 'col', class: 'l' }, 'Act'),
        el('th', { scope: 'col' }, 'Hörer/Monat (Spotify)'),
        el('th', { scope: 'col' }, 'Last.fm-Hörer'),
        el('th', { scope: 'col' }, 'Punkte'))),
      el('tbody', null, rows)));
}

function renderNotFound() {
  app.replaceChildren(el('p', { class: 'status error' }, 'Festival nicht gefunden. ', el('a', { href: '#/' }, 'Zur Übersicht')));
}

// ---------- Routing ----------
function route() {
  if (!DATA) return;
  destroyCharts();
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map((s) => {
    try { return decodeURIComponent(s); } catch (e) { return s; }
  });
  if (parts[0] === 'festival' && parts[1]) {
    renderDetail(parts[1], parts[2]);
    document.title = 'Festival-Score – ' + (DATA.festivals.find((x) => x.slug === parts[1])?.name || 'Festival');
  } else {
    renderOverview();
    document.title = 'Festival-Score';
  }
  window.scrollTo(0, 0);
}

async function init() {
  try {
    const res = await fetch('data/festivals.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    DATA = await res.json();
    if (!DATA || !Array.isArray(DATA.festivals)) throw new Error('Unerwartetes Datenformat');
  } catch (err) {
    app.replaceChildren(el('p', { class: 'status error', role: 'alert' },
      'Die Daten konnten nicht geladen werden (' + err.message + '). ',
      'Bitte die Seite über einen Webserver öffnen (z. B. „python -m http.server“) statt direkt per file://.'));
    return;
  }
  window.addEventListener('hashchange', route);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', route);
  route();
}
init();
