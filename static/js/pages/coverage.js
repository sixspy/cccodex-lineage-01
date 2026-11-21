import { api } from '../api.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export async function renderCoverage(view, setMsg) {
  const reports = await api('/api/coverage/reports');
  const fate = await api('/api/fate/status');
  view.innerHTML = `
    <section class="card">
      <h2>覆盖对账</h2>
      <p class="muted">
        命运口径 <span class="tag fate">${esc(fate.fate_mode || '未定')}</span>
        · 钉住 ${fate.pinned_edges} 条边
        · 目录指纹 ${esc(fate.catalog_fingerprint || '')}
        ${fate.ok ? '<span class="tag ok">同源</span>' : `<span class="tag warn">漂移 ${fate.issues.length} 处</span>`}
      </p>
      ${fate.ok ? '' : `<p class="err">${fate.issues.map(esc).join('<br>')}</p>`}
      <div class="row">
        <button class="primary" id="go" type="button">跑覆盖对账</button>
        <button id="pf" type="button">入库编排预检</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(reports.items || []).map((r) => `<article class="card">
        <h3>报告 #${r.id}</h3>
        <p>${esc(r.summary)}</p>
        <p class="muted">节点 ${r.node_count} · 已发布边 ${r.edge_count} · 孤儿 ${r.orphan_count} · 草稿边 ${r.uncovered_edges} · 命运边 ${r.pinned_edges} · 漂移 ${r.fate_drift}</p>
      </article>`).join('') || '<p class="muted">暂无对账报告</p>'}
    </section>`;
  document.getElementById('go').onclick = async () => {
    try {
      const r = await api('/api/coverage/reports', { method: 'POST', body: '{}' });
      setMsg(r.report.summary);
      await renderCoverage(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  document.getElementById('pf').onclick = async () => {
    try {
      const r = await api('/api/orchestration/preflight');
      setMsg(r.message);
    } catch (e) { setMsg(e.message, true); }
  };
}
