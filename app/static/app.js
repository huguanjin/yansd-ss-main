// ─── 工具 ──────────────────────────────────────────

function showToast(msg, ms = 2000) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), ms);
}

async function api(url, opts = {}) {
  opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) {
    showToast(data.error || "请求失败");
    throw new Error(data.error);
  }
  return data;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ─── 端口列表 ────────────────────────────────────────

async function loadPorts() {
  const data = await api("/api/ports");
  const tbody = document.getElementById("tbody");
  const empty = document.getElementById("emptyMsg");

  if (data.length === 0) {
    tbody.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  // 更新默认端口值
  const maxPort = Math.max(...data.map(p => p.port));
  document.getElementById("f_port").value = Math.max(15001, maxPort + 1);

  tbody.innerHTML = data.map(p => {
    const expTag = p.expired
      ? `<span class="badge badge-expired">已过期</span>`
      : (p.expires_at ? `<span class="badge badge-active">${escapeHtml(p.expires_at)}</span>` : `<span class="badge badge-perm">永久</span>`);
    const rowClass = p.expired ? ' class="row-expired"' : '';
    const subUrl = `${location.protocol}//${location.host}/sub/${encodeURIComponent(p.password)}`;
    return `
    <tr${rowClass}>
      <td><strong>${p.port}</strong></td>
      <td class="pwd-cell">
        ${escapeHtml(p.password)}
        <button class="copy-btn" onclick="copyText('${escapeHtml(p.password)}')">复制</button>
      </td>
      <td>${escapeHtml(p.remark || "—")}</td>
      <td>${expTag}</td>
      <td>${escapeHtml(p.created)}</td>
      <td>
        <button class="btn btn-sm" onclick="editExpiry(${p.port}, '${p.expires_at || ''}')">${p.expires_at ? '改期' : '设期'}</button>
        <button class="btn btn-sm btn-sub" onclick="copyText('${escapeHtml(subUrl)}')">复制订阅</button>
        <button class="btn btn-sm btn-danger" onclick="delPort(${p.port})">删除</button>
        ${!window.isSecureContext ? `<div class="sub-url-show"><input type="text" readonly class="sub-url-input" value="${escapeHtml(subUrl)}" onclick="this.select()" title="选择后复制"></div>` : ''}
      </td>
    </tr>`;
  }).join("");
}

// ─── 添加端口 ────────────────────────────────────────

document.getElementById("addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const port = parseInt(document.getElementById("f_port").value);
  const password = document.getElementById("f_pwd").value.trim();
  const remark = document.getElementById("f_remark").value.trim();
  const expires = document.getElementById("f_expires").value || null;

  await api("/api/ports", {
    method: "POST",
    body: JSON.stringify({ port, password, remark, expires_at: expires })
  });

  showToast("添加成功");
  document.getElementById("addForm").reset();
  loadPorts();
});

// ─── 删除端口 ────────────────────────────────────────

async function delPort(port) {
  if (!confirm(`确认删除端口 ${port}？`)) return;
  await api(`/api/ports/${port}`, { method: "DELETE" });
  showToast("已删除");
  loadPorts();
}

// ─── 生成随机密码 ─────────────────────────────────────

async function genPwd() {
  const data = await api("/api/generate-password");
  document.getElementById("f_pwd").value = data.password;
}

// ─── 修改有效期 ──────────────────────────────────────

async function editExpiry(port, current) {
  const val = prompt(`设置端口 ${port} 的有效期（YYYY-MM-DD），留空为永久:`, current);
  if (val === null) return; // 取消
  await api(`/api/ports/${port}`, {
    method: "PUT",
    body: JSON.stringify({ expires_at: val })
  });
  showToast(val ? `有效期已设为 ${val}` : "已设为永久");
  loadPorts();
}

// ─── 复制 ────────────────────────────────────────────

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => showToast("已复制")).catch(() => showCopyFallback(text));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      if (document.execCommand('copy')) { showToast("已复制"); } else { showCopyFallback(text); }
    } catch (e) { showCopyFallback(text); }
    document.body.removeChild(ta);
  }
}

function showCopyFallback(text) {
  const overlay = document.getElementById('copyOverlay');
  const ta = document.getElementById('copyDialogText');
  ta.value = text;
  overlay.style.display = 'flex';
  setTimeout(() => ta.select(), 50);
}

function closeCopyDialog(e) {
  if (e && e.target !== document.getElementById('copyOverlay')) return;
  document.getElementById('copyOverlay').style.display = 'none';
}

// ─── 退出 ────────────────────────────────────────────

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
}

// ─── 规则管理 ────────────────────────────────────────

let currentRules = [];
let proxyGroupName = '';

async function loadProxyGroupName() {
  const data = await api('/api/proxy-group-name');
  proxyGroupName = data.name || '';
  document.getElementById('proxyGroupName').value = proxyGroupName;
  // 更新 rule_action 下拉框
  const sel = document.getElementById('rule_action');
  // 移除旧的代理组选项（非 DIRECT/REJECT）
  for (let i = sel.options.length - 1; i >= 0; i--) {
    if (sel.options[i].value !== 'DIRECT' && sel.options[i].value !== 'REJECT') sel.remove(i);
  }
  if (proxyGroupName) {
    const opt = document.createElement('option');
    opt.value = proxyGroupName;
    opt.textContent = proxyGroupName;
    sel.insertBefore(opt, sel.firstChild);
    sel.value = proxyGroupName;
  }
}

async function saveProxyGroupName() {
  const name = document.getElementById('proxyGroupName').value.trim();
  if (!name) { showToast('名称不能为空'); return; }
  if (name === proxyGroupName) { showToast('名称未变更'); return; }
  if (!confirm(`确认将代理组名称从「${proxyGroupName}」改为「${name}」？\n同时会修改所有规则中的引用。`)) return;
  await api('/api/proxy-group-name', {
    method: 'PUT',
    body: JSON.stringify({ name })
  });
  showToast('代理组名称已修改');
  await loadProxyGroupName();
  refreshRules();
}

async function loadRules() {
  currentRules = await api("/api/rules");
  renderRules();
}

function parseRule(ruleStr) {
  const parts = ruleStr.split(",");
  if (parts.length >= 3) {
    return { type: parts[0], value: parts[1], action: parts[2], extra: parts.slice(3).join(",") };
  } else if (parts.length === 2) {
    // MATCH,action or GEOIP,XX,action — handle MATCH specially
    return { type: parts[0], value: "", action: parts[1], extra: "" };
  }
  return { type: ruleStr, value: "", action: "", extra: "" };
}

function ruleToString(r) {
  let s = r.type;
  if (r.value) s += "," + r.value;
  if (r.action) s += "," + r.action;
  if (r.extra) s += "," + r.extra;
  return s;
}

function renderRules() {
  const tbody = document.getElementById("rulesTbody");
  const empty = document.getElementById("rulesEmptyMsg");
  if (currentRules.length === 0) {
    tbody.innerHTML = "";
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  tbody.innerHTML = currentRules.map((rule, i) => {
    const r = parseRule(rule);
    const actionClass = r.action === "DIRECT" ? "badge-perm" : (r.action === "REJECT" ? "badge-expired" : "badge-active");
    return `
    <tr>
      <td>${i + 1}</td>
      <td><code>${escapeHtml(r.type)}</code></td>
      <td>${escapeHtml(r.value || "—")}</td>
      <td><span class="badge ${actionClass}">${escapeHtml(r.action)}</span></td>
      <td>${escapeHtml(r.extra || "")}</td>
      <td><div class="action-btns">
        <button class="btn btn-sm" onclick="editRule(${i})">编辑</button>
        <button class="btn btn-sm btn-danger" onclick="removeRule(${i})">删除</button>
      </div></td>
    </tr>`;
  }).join("");
}

function addRule() {
  const type = document.getElementById("rule_type").value;
  const value = document.getElementById("rule_value").value.trim();
  const action = document.getElementById("rule_action").value;
  const extra = document.getElementById("rule_extra").value.trim();

  if (type !== "MATCH" && !value) {
    showToast("请输入匹配值");
    return;
  }

  const r = { type, value, action, extra };
  currentRules.push(ruleToString(r));
  renderRules();
  document.getElementById("rule_value").value = "";
  document.getElementById("rule_extra").value = "";
  showToast("规则已添加（需点击保存生效）");
}

function removeRule(idx) {
  currentRules.splice(idx, 1);
  renderRules();
  showToast("规则已移除（需点击保存生效）");
}

function editRule(idx) {
  const r = parseRule(currentRules[idx]);
  const newVal = prompt("编辑规则（格式: 类型,匹配值,动作[,额外参数]）：", currentRules[idx]);
  if (newVal === null) return;
  if (!newVal.trim()) {
    showToast("规则不能为空");
    return;
  }
  currentRules[idx] = newVal.trim();
  renderRules();
  showToast("规则已修改（需点击保存生效）");
}

async function saveRules() {
  if (!confirm("确认保存规则到 subfile.yaml？")) return;
  const isRawMode = document.getElementById('rawEditor').style.display !== 'none';
  if (isRawMode) {
    const raw = document.getElementById('rawRulesText').value;
    if (!raw.trim().startsWith('rules:')) {
      showToast('内容必须以 rules: 开头');
      return;
    }
    await api('/api/rules/raw', {
      method: 'PUT',
      body: JSON.stringify({ raw })
    });
    showToast('规则已保存');
    currentRules = await api('/api/rules');
    renderRules();
  } else {
    await api('/api/rules', {
      method: 'PUT',
      body: JSON.stringify(currentRules)
    });
    showToast('规则已保存');
  }
}

// ─── 规则编辑模式切换 ───────────────────────────────────

let ruleTabMode = 'visual';

function switchRuleTab(mode) {
  ruleTabMode = mode;
  document.getElementById('tabVisual').classList.toggle('active', mode === 'visual');
  document.getElementById('tabRaw').classList.toggle('active', mode === 'raw');
  document.getElementById('visualEditor').style.display = mode === 'visual' ? '' : 'none';
  document.getElementById('rawEditor').style.display = mode === 'raw' ? '' : 'none';
  if (mode === 'raw') loadRulesRaw();
  else loadRules();
}

async function loadRulesRaw() {
  const data = await api('/api/rules/raw');
  document.getElementById('rawRulesText').value = data.raw;
}

function refreshRules() {
  if (ruleTabMode === 'raw') loadRulesRaw();
  else loadRules();
}

async function restoreRules() {
  if (!confirm('确认恢复到上次保存前的规则？')) return;
  await api('/api/rules/restore', { method: 'POST' });
  showToast('规则已恢复');
  loadRules();
  if (ruleTabMode === 'raw') loadRulesRaw();
}

// ─── 初始化 ──────────────────────────────────────────

async function init() {
  loadPorts();
  loadProxyGroupName();
  loadRules();
  // 设置默认端口
  try {
    const data = await api("/api/next-port");
    document.getElementById("f_port").value = data.port;
  } catch (e) {}
}

init();
