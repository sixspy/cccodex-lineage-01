import { api } from '../api.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export async function renderSettings(view, setMsg) {
  const j = await api('/api/settings');
  const f = j.fate || {};
  view.innerHTML = `
    <section class="card">
      <h2>台参数</h2>
      <p class="muted">命运口径在「边关系」确认时一次定下，设置页只读当前命运口径，不可改。</p>
      <div class="wall">
        <article class="card">
          <h3>当前命运口径（只读）</h3>
          <p><span class="tag fate">${esc(f.fate_mode || '未定')}</span>
            ${f.fate_locked ? '<span class="tag">已锁定</span>' : '<span class="tag">首次确认时选定</span>'}</p>
          <p class="muted">钉住边 ${f.pinned_edges} · 目录指纹 ${esc(f.catalog_fingerprint || '')}</p>
          <p>${f.gate_ok
            ? '<span class="tag ok">节点目录与闭包清单同源</span>'
            : `<span class="tag warn">漂移 ${(f.gate_issues || []).length} 处，影响与入库编排已拦</span>`}</p>
          ${(f.gate_issues || []).length
            ? `<p class="err">${f.gate_issues.map(esc).join('<br>')}</p>` : ''}
        </article>
        ${(j.items || []).map((s) => `<article class="card">
          <h3>${esc(s.label)}</h3>
          <p class="muted">${esc(s.key)}</p>
          <div class="row">
            <input data-k="${esc(s.key)}" value="${esc(s.value)}" />
            <button data-save="${esc(s.key)}" class="primary" type="button">保存</button>
          </div>
        </article>`).join('')}
      </div>
    </section>`;
  view.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const input = view.querySelector('input[data-k="' + btn.dataset.save + '"]');
        await api('/api/settings', {
          method: 'POST',
          body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
        });
        setMsg('参数已保存');
        await renderSettings(view, setMsg);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
