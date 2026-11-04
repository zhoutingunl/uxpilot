const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
const collator = new Intl.Collator('zh-CN', {numeric: true, sensitivity: 'base'});
const tables = {
  models: {
    label: '模型用量', empty: '暂无模型调用记录', sort: {key: 'calls', direction: 'desc'},
    columns: [
      {key: 'model', label: '模型', type: 'text', value: row => row.model},
      {key: 'calls', label: '调用', value: row => number(row.calls)},
      {key: 'errors', label: '失败', value: row => number(row.errors)},
      {key: 'total_tokens', label: 'token', value: row => row.known_usage_calls === 0 ? null : number(row.total_tokens)},
      {key: 'duration_ms', label: '耗时 ms', value: row => number(row.duration_ms)},
    ],
  },
  daily: {
    label: '每日提交', empty: '暂无提交记录', sort: {key: 'date', direction: 'desc'},
    columns: [
      {key: 'date', label: '日期', type: 'date', value: row => row.date},
      {key: 'submitted', label: '提交', value: row => number(row.submitted)},
      {key: 'finished', label: '完成', value: row => number(row.finished)},
      {key: 'total_tokens', label: 'token', value: row => number(row.total_tokens)},
    ],
  },
};
let data;

function dist(d) {
  return `<p>样本 ${esc(d.count)} · 均值 ${esc(d.mean ?? '—')} 秒 · P50 ${esc(d.p50 ?? '—')} · P90 ${esc(d.p90 ?? '—')} · P95 ${esc(d.p95 ?? '—')}</p><div class="chart">${(d.buckets || []).map(([label,n]) => `<div class="chart-row"><span>${esc(label)}</span><span class="bar"><i style="width:${d.count ? Math.round(n/d.count*100) : 0}%"></i></span><span>${esc(n)}</span></div>`).join('')}</div>`;
}

function table(name) {
  const config = tables[name];
  return `<div class="table-wrap"><table id="${name}-table" class="sortable-table" aria-label="${config.label}"><thead><tr>${config.columns.map(c => `<th scope="col" data-sort="${c.key}" aria-sort="none"><button class="sort-button" type="button" title="点击切换升序或降序">${c.label}<span class="sort-arrow" aria-hidden="true">↕</span></button></th>`).join('')}</tr></thead><tbody></tbody></table></div>`;
}

function renderTable(name) {
  const config = tables[name], state = config.sort;
  const column = config.columns.find(c => c.key === state.key);
  const rows = (data[name] || []).map((row, index) => ({row, index, value: column.value(row)}));
  rows.sort((a, b) => {
    // Unknown values stay last in either direction; zero remains a real value.
    if (a.value == null || b.value == null) {
      if (a.value == null && b.value == null) return a.index - b.index;
      return a.value == null ? 1 : -1;
    }
    const cmp = column.type === 'text' ? collator.compare(a.value, b.value)
      : column.type === 'date' ? String(a.value).localeCompare(String(b.value))
      : a.value - b.value;
    return cmp ? (state.direction === 'asc' ? cmp : -cmp) : a.index - b.index;
  });
  const element = document.getElementById(`${name}-table`);
  element.querySelector('tbody').innerHTML = rows.length ? rows.map(({row}) => `<tr>${config.columns.map(c => `<td>${esc(c.value(row) ?? '—')}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${config.columns.length}" class="muted">${config.empty}</td></tr>`;
  for (const header of element.querySelectorAll('th[data-sort]')) {
    const active = header.dataset.sort === state.key;
    const direction = state.direction === 'asc' ? 'ascending' : 'descending';
    header.setAttribute('aria-sort', active ? direction : 'none');
    header.querySelector('.sort-arrow').textContent = active ? (state.direction === 'asc' ? '↑' : '↓') : '↕';
    const label = config.columns.find(c => c.key === header.dataset.sort).label;
    header.querySelector('button').setAttribute('aria-label', `按${label}排序${active ? `，当前${state.direction === 'asc' ? '升序' : '降序'}` : ''}`);
  }
}

function bindTable(name) {
  const config = tables[name];
  document.querySelector(`#${name}-table thead`).addEventListener('click', event => {
    const header = event.target.closest('th[data-sort]');
    if (!header) return;
    const column = config.columns.find(c => c.key === header.dataset.sort);
    if (!column) return;
    config.sort = {key: column.key, direction: config.sort.key === column.key
      ? (config.sort.direction === 'desc' ? 'asc' : 'desc') : (column.type === 'text' ? 'asc' : 'desc')};
    renderTable(name);
  });
  renderTable(name);
}

async function load() {
  const app = document.getElementById('app');
  try {
    const response = await fetch('/api/stats?days=30', {cache: 'no-store'});
    if (!response.ok) throw new Error('stats unavailable');
    data = await response.json();
    const range = data.start ? `统计范围：${data.start} — ${data.end} · ${data.timezone}` : '暂无评测记录';
    app.innerHTML = `<p class="muted" id="stats-range">${esc(range)}。最多显示最近 30 个自然日，从首次记录起展示；点击列头可排序。</p>
      <section class="grid"><article class="card"><h3>评测提交</h3><div class="metric">${esc(data.submitted)}</div></article><article class="card"><h3>已知 token</h3><div class="metric">${esc(data.known_total_tokens)}</div><p>未知用量调用 ${esc(data.unknown_usage_calls)}</p></article><article class="card"><h3>状态</h3><p>${Object.entries(data.states).map(([k,v]) => `${esc(k)}：${esc(v)}`).join(' · ') || '—'}</p></article></section>
      <section class="grid-2"><article class="panel"><h2>总耗时</h2>${dist(data.total_seconds)}</article><article class="panel"><h2>执行耗时</h2>${dist(data.execution_seconds)}</article><article class="panel"><h2>排队耗时</h2>${dist(data.queue_seconds)}</article><article class="panel"><h2>模型用量</h2>${table('models')}</article></section>
      <section class="panel"><h2>每日提交</h2><p class="muted">默认按日期从新到旧排列；首次记录之后没有提交的日期保留为 0。</p>${table('daily')}<p class="muted">${data.notes.map(esc).join(' ')}</p></section>`;
    bindTable('models');
    bindTable('daily');
  } catch {
    app.innerHTML = '<p role="alert" class="status failed">统计数据暂时无法读取，请刷新重试。</p>';
  }
}
load();
