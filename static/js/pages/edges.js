import { api } from '../api.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function fateChip(e) {
  if (!e.fate_mode) return '<span class="tag">未定命运</span>';
  const sync = e.fate_synced
    ? '<span class="tag ok">同源</span>'
    : '<span class="tag warn">漂移</span>';
  return `<span class="tag fate">命运·${esc(e.fate_mode)} · ${e.fate_node_count} 个下游</span>${sync}`;
}

export async function renderEdges(view, setMsg) {
  const nodes = await api('/api/nodes');
  const edges = await api('/api/edges');
  const fate = await api('/api/fate/status');
  const lockedMode = fate.fate_locked ? fate.fate_mode : '';
  const opts = (nodes.items || []).map((n) => `<option value="${n.id}">${esc(n.node_code)}</option>`).join('');

  const modeSelect = (e) => {
    const modes = ['凭证', '归档'];
    const sel = lockedMode || modes[0];
    return `<select data-mode="${e.id}" ${lockedMode ? 'disabled' : ''}>
      ${modes.map((m) => `<option value="${m}" ${m === sel ? 'selected' : ''}>${m}</option>`).join('')}
    </select>`;
  };

  view.innerHTML = `
    <section class="card">
      <h2>边关系</h2>
      <p class="muted">命运口径：
        ${fate.fate_locked
          ? `<span class="tag fate">${esc(fate.fate_mode)}</span> 已锁定，两条命运不可并开`
          : '未定（首次确认时选定，之后不可改）'}
        · 钉住 ${fate.pinned_edges} 条边
        ${fate.ok ? '<span class="tag ok">闸口同源</span>' : `<span class="tag warn">闸口漂移 ${fate.issues.length} 处</span>`}
      </p>
      <div class="row">
        <label>边号<input id="code" value="E-NEW-1" /></label>
        <label>上游<select id="up">${opts}</select></label>
        <label>下游<select id="down">${opts}</select></label>
        <label>变换<input id="kind" value="直抽" /></label>
        <button class="primary" id="create" type="button">创建边</button>
      </div>
      ${fate.ok ? '' : `<p class="err">${fate.issues.map(esc).join('<br>')}</p>`}
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(edges.items || []).map((e) => `<article class="card">
        <h3>${esc(e.edge_code)}</h3>
        <div class="row tight">
          <span class="tag">${esc(e.status)}</span>
          ${fateChip(e)}
        </div>
        <p>${esc(e.upstream_code)} → ${esc(e.downstream_code)}</p>
        <p class="muted">${esc(e.transform_kind)} · ${esc(e.upstream_table)} → ${esc(e.downstream_table)}</p>
        ${e.fate_mode
          ? `<p class="muted">命运由 ${esc(e.fate_confirmed_by)} 于 ${esc(e.fate_confirmed_at)} 确认 · 闭包指纹 ${esc(e.fate_fingerprint)}</p>`
          : `<p class="muted">未确认命运：影响分析将按现行关系临时计算</p>`}
        <div class="row">
          ${e.fate_mode
            ? '<span class="muted">命运已钉死，拒再改</span>'
            : `<label>命运${modeSelect(e)}</label>
               <button class="primary" data-confirm="${e.id}" type="button">确认成正式关系</button>`}
          <button data-pub="${e.id}" class="primary" type="button">发布</button>
          <button data-void="${e.id}" type="button">作废</button>
        </div>
      </article>`).join('') || '<p class="muted">暂无边</p>'}
    </section>`;

  document.getElementById('create').onclick = async () => {
    try {
      await api('/api/edges', {
        method: 'POST',
        body: JSON.stringify({
          edge_code: document.getElementById('code').value,
          upstream_id: Number(document.getElementById('up').value),
          downstream_id: Number(document.getElementById('down').value),
          transform_kind: document.getElementById('kind').value,
        }),
      });
      setMsg('边已创建');
      await renderEdges(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };

  view.querySelectorAll('[data-confirm]').forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.confirm;
      const sel = view.querySelector('[data-mode="' + id + '"]');
      const mode = sel ? sel.value : (lockedMode || '凭证');
      try {
        const r = await api('/api/edges/' + id + '/confirm', {
          method: 'POST',
          body: JSON.stringify({ fate_mode: mode }),
        });
        setMsg(`命运已确认：${r.mode} · ${r.node_count} 个下游`);
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });

  view.querySelectorAll('[data-pub]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.pub + '/publish', { method: 'POST', body: '{}' });
        setMsg('边已发布');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
  view.querySelectorAll('[data-void]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api('/api/edges/' + btn.dataset.void + '/void', { method: 'POST', body: '{}' });
        setMsg('边已作废');
        await renderEdges(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
