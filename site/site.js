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
