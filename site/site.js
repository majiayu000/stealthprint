function selectCandidate(button, updateUrl = true) {
  const payload = JSON.parse(document.getElementById('probe-data').textContent);
  const candidate = payload[button.dataset.candidate];
  if (!candidate) return;
  document.querySelectorAll('[data-candidate]').forEach(item => {
    item.setAttribute('aria-pressed', String(item === button));
  });
  const tbody = document.getElementById('probe-rows');
  tbody.replaceChildren();
  for (const row of candidate) {
    const tr = document.createElement('tr');
    for (const value of [row.probe, row.api, row.local, row.api - row.local]) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.append(td);
    }
    tr.classList.toggle('probe-match', row.api === row.local);
    tbody.append(tr);
  }
  document.getElementById('probe-selection').textContent = `${button.dataset.candidate} · ${candidate.length} 条探针`;
  const details = document.getElementById('probe-details');
  revealDetails(details);
  if (updateUrl) {
    const url = new URL(location.href);
    url.searchParams.set('candidate', button.dataset.candidate);
    url.hash = 'probe-details';
    history.replaceState(null, '', url);
  }
  details.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'nearest' });
}
document.querySelectorAll('[data-candidate]').forEach(button => {
  button.addEventListener('click', () => selectCandidate(button));
});

document.querySelectorAll('pre > code').forEach(code => {
  const pre = code.parentElement;
  const wrapper = document.createElement('div');
  wrapper.className = 'code-block';
  pre.before(wrapper);
  wrapper.append(pre);
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'copy-button';
  button.textContent = '复制';
  button.setAttribute('aria-label', '复制这段代码');
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = '已复制';
      button.setAttribute('aria-label', '代码已复制');
    } catch (error) {
      console.error('Unable to copy code:', error);
      button.textContent = '请选中文本复制';
      button.setAttribute('aria-label', '复制失败，请选中文本复制');
    }
    setTimeout(() => {
      button.textContent = '复制';
      button.setAttribute('aria-label', '复制这段代码');
    }, 2500);
  });
  wrapper.append(button);
});

function revealDetails(target) {
  let fold = target?.closest('details');
  while (fold) {
    fold.open = true;
    fold = fold.parentElement.closest('details');
  }
}
document.querySelectorAll('a[href^="#"]').forEach(link => {
  link.addEventListener('click', () => {
    revealDetails(document.getElementById(hashId(link.hash)));
  });
});
function hashId(hash) {
  try { return decodeURIComponent(hash.slice(1)); }
  catch { return hash.slice(1); }
}
function revealHash() {
  if (!location.hash) return;
  const target = document.getElementById(hashId(location.hash));
  revealDetails(target);
  target?.scrollIntoView({ block: 'start', behavior: 'instant' });
}
window.addEventListener('hashchange', revealHash);
revealHash();

const selectedCandidate = new URL(location.href).searchParams.get('candidate');
const selectedButton = [...document.querySelectorAll('[data-candidate]')].find(button => button.dataset.candidate === selectedCandidate);
if (selectedButton) selectCandidate(selectedButton, false);

const updateSearch = document.getElementById('update-search');
if (updateSearch) {
  const kind = document.getElementById('update-kind');
  const signal = document.getElementById('update-signal');
  const rows = [...document.querySelectorAll('[data-update]')];
  function filterUpdates() {
    const query = updateSearch.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {
      row.hidden = !row.dataset.search.toLocaleLowerCase().includes(query) ||
        (kind.value !== '' && row.dataset.kind !== kind.value) ||
        (signal.value !== '' && !row.dataset.signals.split(' ').includes(signal.value));
      if (!row.hidden) visible++;
    }
    document.getElementById('update-count').textContent = `${visible} / ${rows.length} 组记录`;
    document.getElementById('update-empty').hidden = visible !== 0;
  }
  updateSearch.addEventListener('input', filterUpdates);
  kind.addEventListener('change', filterUpdates);
  signal.addEventListener('change', filterUpdates);
}

function updateFreshness() {
  const now = Date.now();
  document.querySelectorAll('[data-source-health]').forEach(card => {
    const last = Date.parse(card.dataset.lastSuccess);
    const stale = Number.isFinite(last) && now - last > Number(card.dataset.staleAfter) * 1000;
    card.dataset.stale = String(stale);
    card.querySelector('[data-health-label]').textContent = card.dataset.error === 'true' ? '采集失败' :
      !Number.isFinite(last) ? '等待首次采集' : stale ? '数据已过期' : '采集正常';
  });
  const healthSummary = document.getElementById('source-health-summary');
  if (healthSummary) {
    const cards = [...document.querySelectorAll('[data-source-health]')];
    const healthy = cards.filter(card => card.querySelector('[data-health-label]').textContent === '采集正常').length;
    healthSummary.textContent = `${healthy} / ${cards.length} 个来源最近采集成功${healthy < cards.length ? ' · 部分来源异常或过期' : ''}`;
  }
  const snapshot = document.getElementById('monitor-freshness');
  if (snapshot) {
    const checked = Date.parse(snapshot.dataset.checkedAt);
    const stale = Number.isFinite(checked) && now - checked > 30 * 60 * 1000;
    snapshot.dataset.stale = String(stale);
    if (stale) snapshot.textContent = `监测快照已超过 30 分钟未更新 · 请核对来源状态（最近检查：${new Date(checked).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false})} 北京时间）`;
  }
}
updateFreshness();
if (document.getElementById('monitor-freshness')) setInterval(updateFreshness, 60000);
