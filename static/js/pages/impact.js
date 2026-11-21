import { api } from '../api.js';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export async function renderImpact(view, setMsg) {
  const nodes = await api('/api/nodes');
  const jobs = await api('/api/impact/jobs');
  const fate = await api('/api/fate/status');
  const gate = fate.ok;
  const opts = (nodes.items || []).map((n) => `<option value="${n.id}">${esc(n.node_code)}</option>`).join('');
  const fmt = (j) => {
    const mix = j.fate_mixed ? '<span class="tag warn">混合命运</span>' : '';
    const fm = j.fate_mode ? `<span class="tag fate">同源·${esc(j.fate_mode)}</span>` : '<span class="tag">未见命运边</span>';
    return `<span class="tag ok">同源下游 ${j.pinned_count}</span>${fm}${mix}`;
  };
  view.innerHTML = `
    <section class="card">
      <h2>影响分析</h2>
      <p class="muted">
        已确认命运的边读确认瞬间钉住的下游集合；未确认的边按现行关系展开。
        <br>闸口：${gate
          ? '<span class="tag ok">节点目录指纹与闭包清单同源</span>'
          : `<span class="tag warn">漂移，已拦开跑</span>`}
        目录指纹 ${esc(fate.catalog_fingerprint || '')}
      </p>
      ${gate ? '' : `<p class="err">${fate.issues.map(esc).join('<br>')}</p>`}
      <div class="row">
        <label>根节点<select id="root">${opts}</select></label>
        <button class="primary" id="go" type="button" ${gate ? '' : 'disabled'}>启动影响分析</button>
      </div>
    </section>
    <section class="wall" style="margin-top:1rem">
      ${(jobs.items || []).map((j) => `<article class="card">
        <h3>${esc(j.job_code)}</h3>
        <span class="tag">${esc(j.status)}</span>
        <p>根 ${esc(j.node_code)} · 命中 ${j.affected_count}</p>
        <p>${fmt(j)}</p>
        <p class="muted">${esc(j.message)}</p>
        <button data-d="${j.id}" type="button">详情</button>
        <pre class="muted" id="j${j.id}"></pre>
      </article>`).join('') || '<p class="muted">尚无影响作业</p>'}
    </section>`;
  const go = document.getElementById('go');
  go.onclick = async () => {
    try {
      const r = await api('/api/impact/start', {
        method: 'POST',
        body: JSON.stringify({ root_node_id: Number(document.getElementById('root').value) }),
      });
      setMsg('影响作业 ' + r.job.job_code + ' · ' + r.job.status + ' · 同源下游 ' + r.job.pinned_count);
      await renderImpact(view, setMsg);
    } catch (e) { setMsg(e.message, true); }
  };
  view.querySelectorAll('[data-d]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const r = await api('/api/impact/jobs/' + btn.dataset.d);
        document.getElementById('j' + btn.dataset.d).textContent = JSON.stringify(r.hits, null, 2);
      } catch (e) { setMsg(e.message, true); }
    };
  });
}
