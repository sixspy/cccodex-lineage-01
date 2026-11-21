import { api } from '../api.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export async function renderNodes(view, setMsg) {
  const j = await api('/api/nodes');
  const gate = j.gate || {};
  view.innerHTML = `
    <section class="card">
      <h2>节点目录</h2>
      <p class="muted">目录指纹 ${esc(j.catalog_fingerprint || '')}
        · 命运口径 <span class="tag fate">${esc(j.fate_mode || '未定')}</span>
        ${gate.ok ? '<span class="tag ok">与闭包清单同源</span>' : `<span class="tag warn">漂移 ${(gate.issues || []).length} 处</span>`}</p>
      ${gate.ok ? '' : `<p class="err">${(gate.issues || []).map(esc).join('<br>')}</p>`}
      <div class="row">
        <label>编号<input id="code" value="N-NEW-1" /></label>
        <label>表名<input id="table" value="dwd_sample" /></label>
        <label>层级<select id="layer"><option>ODS</option><option selected>DWD</option><option>DWS</option><option>ADS</option></select></label>
        <label>责任组<input id="team" value="仓建组" /></label>
        <button class="primary" id="create" type="button">登记节点</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(j.items || []).map((n) => `<article class="card">
        <h3>${esc(n.node_code)}</h3>
        <span class="tag">${esc(n.status)}</span>
        <span class="tag">${esc(n.layer)}</span>
        <p>${esc(n.table_name)}</p>
        <p class="muted">上游 ${n.upstream_count} · 下游 ${n.downstream_count} · ${esc(n.owner_team)}</p>
        <div class="row">
          <button data-act="${n.id}" type="button">启用</button>
          <button data-ret="${n.id}" type="button">停用</button>
        </div>
      </article>`).join('') || '<p class="muted">暂无节点</p>'}
    </section>`;
  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/nodes', {
        method: 'POST',
        body: JSON.stringify({
          node_code: document.getElementById('code').value,
          table_name: document.getElementById('table').value,
          layer: document.getElementById('layer').value,
          owner_team: document.getElementById('team').value,
        }),
      });
      setMsg('节点已登记');
      await renderNodes(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-act]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/nodes/' + btn.dataset.act + '/activate', { method: 'POST', body: '{}' });
        setMsg('已启用');
        await renderNodes(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-ret]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/nodes/' + btn.dataset.ret + '/retire', { method: 'POST', body: '{}' });
        setMsg('已停用');
        await renderNodes(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
