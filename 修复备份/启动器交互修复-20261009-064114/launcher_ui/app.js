'use strict';

/* Local launcher UI. All writes go through the authenticated Python API. */
const params = new URLSearchParams(location.search);
const demo = params.get('demo') === 'true';
const token = params.get('token') || sessionStorage.getItem('pvz-launcher-token') || '';
if (token) sessionStorage.setItem('pvz-launcher-token', token);
const $ = (s, root = document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name, cls = '') => `<svg${cls ? ` class="${esc(cls)}"` : ''} aria-hidden="true"><use href="#i-${name}"/></svg>`;
const pages = {home:'首页',accounts:'账号与资料',gm:'GM 管理',services:'服务与日志',backups:'存档备份',settings:'设置'};
let page = (location.hash || '#home').slice(1);
if (!pages[page]) page = 'home';
let appState = {running:false,busy:false,services:[],currentAccount:null};
let accounts = [];
let selectedId = '';
let profile = null;
let settings = {theme:'light',scale:100};
let entries = [];
let lastLogId = 0;
let backups = [];
let logLevel = 'all';
let logQuery = '';
let logPaused = false;
let accountQuery = '';
let adminUrl = '';
let adminLoading = false;
let adminError = '';
let connecting = true;
let connectionError = '';
let localBusy = false;
let stagedAvatar = 'keep';
let stagedImage = '';
let profileDirty = false;
let profileDraft = null;
let profileSaving = false;
let cropImage = null;
let crop = {zoom:1,x:0,y:0};
let stateTimer = null;
let logTimer = null;
let booting = false;

const demoAccounts = [
  {id:'10001',uin:'10001',name:'向日葵的午后',signature:'把每一缕阳光，都种进花园里。',level:35,gold:128650,face_id:0},
  {id:'10002',uin:'10002',name:'豌豆守护者',signature:'今天也要守护花园。',level:21,gold:6800,face_id:1},
  {id:'10003',uin:'10003',name:'小院新朋友',signature:'',level:8,gold:1200,face_id:2}
];
const demoServices = [
  {id:'web',name:'游戏资源',status:'stopped',detail:'游戏页面与静态资源',port:8080},
  {id:'admin',name:'GM 后台',status:'stopped',detail:'角色与游戏管理工作台',port:8081},
  {id:'game',name:'游戏服务',status:'stopped',detail:'角色数据与游戏逻辑',port:9001},
  {id:'policy',name:'连接策略',status:'stopped',detail:'等待启动',port:843}
];
const demoLogs = [
  {id:1,time:'10:32:18',level:'INFO',message:'花园启动器已准备就绪'},
  {id:2,time:'10:32:18',level:'INFO',message:'已发现 3 个本地角色，等待选择角色'},
  {id:3,time:'10:32:19',level:'INFO',message:'四项游戏服务未启动，点击「开始游戏」进入花园'},
  {id:4,time:'10:32:19',level:'INFO',message:'只读演示模式：展示界面，不连接或修改游戏存档'}
];

function accountId(a) { return String(a?.id ?? a?.uin ?? a?.account_id ?? ''); }
function accountName(a) { return a?.name || a?.nickname || (a ? `角色 ${accountId(a)}` : '尚未选择角色'); }
function currentAccount() { return accounts.find(a => accountId(a) === selectedId) || appState.currentAccount || accounts[0] || null; }
function busy() { return localBusy || appState.busy || appState.starting || appState.stopping; }
function running() { return Boolean(appState.running); }
function avatarSource(a) { return a?.avatar_url || a?.avatarUrl || a?.qq_face_url || a?.image_data || ''; }
function avatar(a, size = '') {
  const src = avatarSource(a);
  return `<span class="avatar${size ? ` avatar-${size}` : ''}">${src ? `<img src="${esc(src)}" alt="${esc(accountName(a))}的头像" loading="lazy">` : esc((accountName(a) || 'P').slice(0,1))}</span>`;
}
function previewAccount(a) {
  const choice = typeof stagedAvatar === 'number' ? (a.avatarChoices || []).find(v=>Number(v.id)===stagedAvatar) : null;
  return stagedImage ? {...a,avatar_url:stagedImage} : choice ? {...a,avatar_url:choice.url} : a;
}
function numeric(n) { return Number.isFinite(Number(n)) ? Number(n).toLocaleString('zh-CN') : '—'; }
function humanSize(n) { if (typeof n === 'string' && !/^\d+$/.test(n)) return n; const b = Number(n || 0); return b > 1048576 ? `${(b/1048576).toFixed(1)} MB` : b > 1024 ? `${(b/1024).toFixed(1)} KB` : `${b} B`; }
function shortTime(v) { if (!v) return ''; if (/^\d\d:\d\d/.test(v)) return String(v).slice(0,8); const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}); }
function dateTime(v) { if (!v) return '—'; const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}); }
function statusTag(status) {
  const v = {running:['green','运行中'],starting:['yellow','启动中'],stopping:['yellow','停止中'],error:['red','异常'],stopped:['','未启动'],ready:['green','已就绪']}[status] || ['', '未启动'];
  return `<span class="tag ${v[0]}"><i></i>${v[1]}</span>`;
}
function toast(message, kind = 'success') {
  const div = document.createElement('div'); div.className = `toast ${kind}`;
  div.innerHTML = `${icon(kind === 'error' ? 'close' : kind === 'warn' ? 'info' : 'check')}<span>${esc(message)}</span>`;
  $('#toastRegion').append(div); setTimeout(() => div.remove(), 5000);
}
async function api(path, options = {}) {
  if (demo) {
    if (options.method && options.method !== 'GET') throw new Error('当前为只读演示，请在本地启动器中执行此操作。');
    if (path === '/api/state') return {running:false,busy:false,currentAccount:demoAccounts[0],services:demoServices,statusMessage:'服务已停止'};
    if (path.startsWith('/api/accounts/')) { const a = demoAccounts.find(a => accountId(a) === path.split('/').pop()); return {...a,avatarChoices:[],profile_expected:{name:a.name,signature:a.signature,face_id:a.face_id,qq_face_url:''}}; }
    if (path.startsWith('/api/accounts')) return demoAccounts;
    if (path.startsWith('/api/logs')) return {entries:demoLogs,lastId:4};
    if (path.startsWith('/api/backups')) return [{id:'demo-backup-1',name:'花园存档 · 手动备份',created:'2026-10-09T10:28:00+08:00',size:3489102,reason:'manual',files:['玩家存档','公会数据']},{id:'demo-backup-2',name:'操作前自动备份',created:'2026-10-08T20:41:00+08:00',size:3470176,reason:'before-reset',files:['玩家存档','公会数据']}];
    if (path.startsWith('/api/settings')) return settings;
    if (path.startsWith('/api/admin-info')) return {url:''};
    return null;
  }
  const headers = {'X-PVZ-Token':token,...options.headers};
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path,{...options,headers,body:options.body === undefined ? undefined : JSON.stringify(options.body),cache:'no-store'});
  let result;
  try { result = await response.json(); } catch { throw new Error(`服务返回了无法读取的内容（HTTP ${response.status}）。`); }
  if (!response.ok || result.ok === false) throw new Error(result.error?.message || result.error || result.message || `操作失败（HTTP ${response.status}）`);
  return result.data === undefined ? result : result.data;
}
function normalizeAccounts(result) { return Array.isArray(result) ? result : result?.accounts || result?.items || []; }
function normalizeBackups(result) { return Array.isArray(result) ? result : result?.backups || result?.items || []; }
function applyTheme() {
  const dark = settings.theme === 'dark' || (settings.theme === 'auto' && matchMedia('(prefers-color-scheme:dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.style.setProperty('--scale', String(Number(settings.scale || 100)/100));
  $('#themeToggle').innerHTML = icon(dark ? 'moon' : 'sun');
}
function updateChrome() {
  document.body.dataset.page = page;
  $('#breadcrumbPage').textContent = pages[page];
  document.querySelectorAll('[data-page]').forEach(el => {el.classList.toggle('active',el.dataset.page === page);el.setAttribute('aria-current',el.dataset.page === page ? 'page' : 'false');});
  $('#demoBadge').hidden = !demo;
  const connection = $('#connectionBadge');
  connection.className = `connection-badge ${connectionError ? 'error' : connecting ? '' : 'connected'}`;
  connection.innerHTML = `<i></i>${demo ? '演示模式' : connectionError ? '连接异常' : connecting ? '正在连接' : '本地已连接'}`;
}
function heading(title, subtitle, actions = '') {
  return `<div class="page-heading" aria-label="${esc(title)}"><p>${subtitle}</p>${actions}</div>`;
}
function connectionPanel() { return connectionError ? `<div class="connection-error"><strong>暂时无法连接启动器服务</strong><p>${esc(connectionError)}</p><button class="button" data-action="reconnect">${icon('refresh')}重新连接</button></div>` : ''; }
function readonlyBanner() { return demo ? `<div class="readonly-banner">${icon('info')}当前为只读界面演示，启动、修改角色、管理存档等操作不会执行。</div>` : running() ? `<div class="readonly-banner">${icon('shield')}游戏服务正在运行。角色资料、GM 数据和存档恢复已锁定，请先停止游戏服务再修改。</div>` : ''; }
function progressPanel() {
  if (!busy()) return '';
  const p = typeof appState.progress === 'object' ? Number(appState.progress?.percent || 0) : Number(appState.progress || 0);
  return `<div class="panel progress-panel"><span class="spinner"></span><div class="progress-content"><div class="progress-caption"><span>${esc(appState.statusMessage || '正在准备游戏服务…')}</span><small>${Math.max(0,Math.min(100,p))}%</small></div><div class="progress-track"><span style="width:${Math.max(3,Math.min(100,p))}%"></span></div></div><button class="button small" data-action="stop">取消</button></div>`;
}
function serviceCards(detail = false) {
  const services = appState.services?.length ? appState.services : demoServices;
  const array = Array.isArray(services) ? services : Object.entries(services).map(([id,v])=> typeof v === 'object' ? {id,...v} : {id,name:id,status:v});
  return `<div class="service-grid ${detail ? 'service-detail' : ''}">${array.map((s,i) => `<article class="panel service-card"><div class="service-top"><span class="service-icon">${icon(['shield','grid','server','refresh'][i%4])}</span>${statusTag(s.status)}</div><h3>${esc(s.name || s.id)}</h3><p title="${esc(s.detail || '')}">${esc(s.detail || (s.status === 'running' ? '服务运行正常' : '等待启动'))}</p>${detail ? `<div class="port">${s.port ? `LOCAL · 127.0.0.1:${esc(s.port)}` : 'LOCAL SERVICE'}</div>` : ''}</article>`).join('')}</div>`;
}
function renderHome() {
  const a = currentAccount();
  const state = connectionError
    ? {kind:'error',icon:'info',title:'连接异常',text:'请重新连接本地启动器服务。'}
    : busy()
      ? {kind:'pending',icon:'refresh',title:'正在处理',text:'请稍候，正在处理游戏服务。'}
      : appState.error
        ? {kind:'error',icon:'info',title:'启动遇到问题',text:String(appState.error)}
        : running()
          ? {kind:'running',icon:'check',title:'游戏运行中',text:'退出后可点击「停止游戏」，关闭本次游戏服务。'}
          : {kind:'ready',icon:'play',title:'准备就绪',text:'选好角色，点击「启动游戏」即可进入游戏。'};
  const locked = busy() || running();
  return `${connectionPanel()}${progressPanel()}
    <div class="home-layout">
      <section class="panel launch-card" aria-labelledby="launchTitle">
        <h2 id="launchTitle">游戏角色</h2>
        <div class="launch-identity">
          ${avatar(a,'lg')}
          <h3>${esc(a ? accountName(a) : '暂无角色')}</h3>
          <p>${a ? `UID ${esc(accountId(a))}` : '新建一个角色，开始游戏。'}</p>
        </div>
        <div class="launch-controls">
          <label for="homeAccount">选择角色</label>
          <select class="field-control" id="homeAccount" ${locked || !accounts.length ? 'disabled' : ''}>
            ${accounts.length ? accounts.map(x => `<option value="${esc(accountId(x))}" ${accountId(x) === accountId(a) ? 'selected' : ''}>${esc(accountName(x))} · ${esc(accountId(x))}</option>`).join('') : '<option value="">暂无角色</option>'}
          </select>
          ${a || running() ? `<button class="button primary launch-button" data-action="${running() ? 'stop' : 'start'}" ${busy() || connectionError ? 'disabled' : ''}>${icon(running() ? 'stop' : 'play')}${busy() ? '请稍候…' : running() ? '停止游戏' : '启动游戏'}</button>` : `<button class="button primary launch-button" data-action="new-account" ${busy() || connectionError ? 'disabled' : ''}>${icon('plus')}新建角色</button>`}
          <a class="launch-profile-link" href="#accounts">账号与个人资料 ${icon('chevron')}</a>
        </div>
      </section>
      <section class="panel session-card" aria-labelledby="sessionTitle">
        <h2 id="sessionTitle">游戏状态</h2>
        <div class="session-summary ${state.kind}" role="status" aria-live="polite">
          <span class="session-icon">${icon(state.icon)}</span>
          <div><h3>${esc(state.title)}</h3><p>${esc(state.text)}</p></div>
        </div>
        <a class="subtle-link" href="#services">查看服务与日志 ${icon('chevron')}</a>
      </section>
    </div>`;
}
function renderAccounts() {
  const filtered = accounts.filter(a => `${accountName(a)} ${accountId(a)}`.toLowerCase().includes(accountQuery.toLowerCase()));
  const actions = `<div class="page-actions"><button class="button primary" data-action="new-account" ${running() || busy() ? 'disabled' : ''}>${icon('plus')}新建角色</button></div>`;
  return `${heading('角色管理','管理角色账号、昵称、签名与头像。',actions)}${connectionPanel()}${readonlyBanner()}<div class="account-layout"><section class="panel account-list-panel"><div class="panel-head"><h2>我的角色</h2><span class="tag">${accounts.length} 个角色</span></div><div class="account-search">${icon('search')}<input class="field-control" id="accountSearch" placeholder="搜索角色名或 UID" value="${esc(accountQuery)}" aria-label="搜索角色"></div><div class="account-list">${filtered.length ? filtered.map(a => `<button class="account-row ${accountId(a) === selectedId ? 'selected' : ''}" data-action="select-account" data-id="${esc(accountId(a))}">${avatar(a)}<span><strong>${esc(accountName(a))}</strong><small>UID ${esc(accountId(a))}${a.level !== undefined ? ` · Lv.${esc(a.level)}` : ''}</small></span>${accountId(a) === selectedId ? icon('check') : ''}</button>`).join('') : `<div class="empty-state">${icon('search')}<strong>${accounts.length ? '没有找到角色' : '花园里还没有角色'}</strong>${accounts.length ? '试试另一个名字或 UID。' : '点击右上方新建角色。'}</div>`}</div><div class="account-list-footer"><span>本地角色档案</span><span>点击角色编辑资料</span></div></section><section id="profilePanel" class="panel">${renderProfile()}</section></div>`;
}
function renderProfile() {
  if (!selectedId || !accounts.length) return `<div class="empty-state">${icon('user')}<strong>创建属于你的角色</strong>新建角色后，可以在这里设置昵称、签名与头像。</div>`;
  if (!profile || accountId(profile) !== selectedId) return `<div class="loading"><span class="spinner"></span>正在读取角色资料…</div>`;
  const a = profile; const disabled = running() || busy() || demo || profileSaving;
  const choices = a.avatarChoices || [];
  return `<div class="profile-banner">${avatar(a,'lg')}<div><h2>${esc(accountName(a))}</h2><p>UID ${esc(accountId(a))} · 你的花园身份</p></div><span class="tag green">本地角色</span></div><form id="profileForm" class="profile-form"><div class="profile-avatar"><div id="profileAvatarPreview">${avatar(previewAccount(a))}</div><div class="avatar-action"><strong>角色头像</strong><button type="button" class="button" data-action="upload-avatar" ${disabled ? 'disabled' : ''}>${icon('upload')}上传并裁剪</button><p class="hint">支持 PNG / JPG / WebP，保存为正方形头像</p></div></div>${choices.length ? `<div class="avatar-choices" role="group" aria-label="选择内置头像">${choices.map(v => `<button type="button" class="avatar-choice ${(stagedAvatar === 'keep' && !a.qq_face_url && Number(a.face_id) === Number(v.id)) || stagedAvatar === Number(v.id) ? 'selected' : ''}" data-action="choose-avatar" data-id="${esc(v.id)}" data-url="${esc(v.url)}" title="${esc(v.label)}" ${disabled ? 'disabled' : ''}><img src="${esc(v.url)}" alt="${esc(v.label)}"></button>`).join('')}</div>` : ''}<div class="form-grid"><label class="field"><span>角色昵称</span><input class="field-control" name="name" value="${esc(profileDraft?.name ?? a.name ?? a.nickname ?? '')}" maxlength="7" required ${disabled ? 'disabled' : ''}><small>昵称为 2–7 个字，沿用游戏内限制</small></label><label class="field"><span>账号 UID</span><input class="field-control" value="${esc(accountId(a))}" disabled><small>角色账号创建后保持固定</small></label></div><label class="field"><span>个性签名</span><input class="field-control" name="signature" maxlength="30" placeholder="说说你的花园故事…" value="${esc(profileDraft?.signature ?? a.signature ?? '')}" ${disabled ? 'disabled' : ''}><small>最多 30 个字</small></label><div class="form-actions"><button type="button" class="button" data-action="reload-profile" ${disabled ? 'disabled' : ''}>重置修改</button><button type="submit" class="button primary" ${disabled ? 'disabled' : ''}>${icon('check')}保存资料</button></div></form><div class="profile-details"><div><small>角色等级</small><strong>${numeric(a.level)} 级</strong></div><div><small>金币余额</small><strong>${numeric(a.gold ?? a.money ?? a.coin)}</strong></div><div><small>当前状态</small><strong>${running() && accountId(appState.currentAccount) === selectedId ? '游戏运行中' : '已保存'}</strong></div></div><div class="profile-footer"><span>资料保存前会校验存档，避免覆盖其他页面的新修改。</span><button class="button danger small" data-action="delete-account" ${disabled ? 'disabled' : ''}>${icon('trash')}删除角色</button></div>`;
}
function renderGM() {
  return `${heading('GM 管理工作台','角色属性、植物、背包与冒险进度，在启动器里统一管理。')}${connectionPanel()}${readonlyBanner()}<section class="panel gm-panel"><div class="gm-toolbar"><span class="tag green">本地后台</span><select class="field-control gm-account-select" id="gmAccount" aria-label="GM 当前角色">${accounts.map(a=>`<option value="${esc(accountId(a))}" ${accountId(a)===selectedId?'selected':''}>${esc(accountName(a))}</option>`).join('')}</select><span class="url">${adminLoading ? '正在连接工作台…' : adminUrl ? '工作台已连接' : '等待连接工作台'}</span><button class="icon-button" data-action="reload-admin" title="重新加载后台" aria-label="重新加载后台">${icon('refresh')}</button>${adminUrl ? `<a class="button small" href="${esc(adminFrameUrl())}" target="_blank" rel="noopener">${icon('external')}独立窗口</a>` : ''}</div>${adminUrl && !demo ? `<iframe class="gm-frame" id="gmFrame" src="${esc(adminFrameUrl())}" title="GM 管理工作台"></iframe>` : `<div class="gm-empty">${adminLoading ? '<span class="spinner"></span>' : icon('grid')}<h3>${demo ? 'GM 工作台，已融入启动器' : adminLoading ? '正在连接本地后台' : '连接 GM 管理工作台'}</h3><p>${esc(demo ? '正式运行时，此处将直接显示完整的本地后台，可管理角色、道具、植物卡、塔防进度与会员特权。' : adminError || '后台将在本地启动。游戏运行期间，所有数据修改入口由服务端锁定。')}</p>${!demo && !adminLoading ? `<button class="button primary" data-action="open-admin">${icon('grid')}打开工作台</button>` : demo ? `<span class="tag yellow">只读界面演示</span>` : ''}</div>`}</section>`;
}
function adminFrameUrl() {
  if (!adminUrl) return '';
  const url = new URL(adminUrl,location.origin);
  if (selectedId) {url.pathname = '/player';url.searchParams.set('uin',selectedId);url.searchParams.set('section','attributes');}
  url.searchParams.set('embedded','1');
  url.searchParams.set('theme',document.documentElement.dataset.theme || 'light');
  return url.toString();
}
function filteredLogs() { return entries.filter(e => (logLevel === 'all' || String(e.level).toLowerCase() === logLevel || (logLevel === 'warn' && String(e.level).toLowerCase() === 'warning')) && `${e.message} ${e.level} ${e.time}`.toLowerCase().includes(logQuery.toLowerCase())); }
function logLines() { const lines = filteredLogs(); return lines.length ? lines.map(e=> `<div class="log-line ${String(e.level).toLowerCase()}"><span class="log-time">${esc(shortTime(e.time))}</span><span class="log-level">${esc(String(e.level || 'INFO').toUpperCase())}</span><span class="log-text">${esc(e.message)}</span></div>`).join('') : '<div class="log-line"><span class="log-text muted">暂无匹配的日志。等待服务输出，或调整筛选条件。</span></div>'; }
function renderServices() {
  return `${heading('服务与日志','查看运行状态与实时日志。',`<div class="page-actions"><button class="button ${running() ? 'danger' : 'primary'}" data-action="${running() ? 'stop' : 'start'}" ${busy() || !currentAccount() ? 'disabled' : ''}>${icon(running() ? 'stop' : 'play')}${running() ? '停止全部服务' : '启动游戏服务'}</button><button class="button" data-action="refresh-state">${icon('refresh')}刷新</button></div>`)}${connectionPanel()}${progressPanel()}${serviceCards(true)}<section class="panel log-panel"><div class="panel-head"><div><h2>运行日志</h2><p>来自启动器与本地游戏服务的实时记录</p></div><span class="tag ${logPaused ? 'yellow' : 'green'}" id="logLiveBadge"><i></i>${logPaused ? '已暂停显示' : '实时更新'}</span></div><div class="log-toolbar"><div class="log-levels">${[['all','全部'],['info','信息'],['warn','警告'],['error','错误']].map(([v,label])=> `<button data-action="log-level" data-level="${v}" class="${logLevel === v ? 'active' : ''}">${label}</button>`).join('')}</div><div class="log-search">${icon('search')}<input class="field-control" id="logSearch" value="${esc(logQuery)}" placeholder="搜索日志内容" aria-label="搜索日志"></div><button class="button small" data-action="pause-logs" id="pauseLogs">${icon(logPaused ? 'play' : 'pause')}${logPaused ? '继续' : '暂停'}</button><button class="button small" data-action="copy-logs">${icon('copy')}复制</button><button class="button small" data-action="export-logs">${icon('download')}导出</button></div><div class="log-view" id="logView" role="log" aria-live="off">${logLines()}</div><div class="log-status"><span id="logCount">${filteredLogs().length} 条记录 · 最多保留最近 2,000 条</span><span>${demo ? '只读演示记录' : '本地时间 · 自动滚动至最新记录'}</span></div></section>`;
}
function renderBackups() {
  return `${heading('存档备份','创建和恢复玩家存档与公会数据快照。')}${connectionPanel()}${readonlyBanner()}<section class="panel backup-summary"><span class="backup-summary-icon">${icon('shield')}</span><div><h2>存档备份</h2><p>备份包含玩家存档与公会数据。恢复前会自动保留当前存档。</p></div><button class="button primary" data-action="create-backup" ${busy() || running() ? 'disabled' : ''}>${icon('plus')}创建备份</button></section><section class="panel"><div class="panel-head"><div><h2>备份记录</h2><p>共 ${backups.length} 份快照，按创建时间排列</p></div><button class="subtle-link" data-action="refresh-backups">${icon('refresh')}刷新列表</button></div>${backups.length ? `<div style="overflow:auto"><table class="backup-table"><thead><tr><th>存档快照</th><th>创建时间</th><th>大小</th><th>操作</th></tr></thead><tbody>${backups.map(b => `<tr><td><div class="backup-name">${icon('archive')}<span><strong>${esc(b.name || b.id)}</strong><small>${b.reason === 'manual' ? '手动备份' : b.reason === 'before-restore' ? '恢复前自动备份' : '操作前自动备份'}${b.files?.length ? ` · ${b.files.length} 个数据文件` : ''}</small></span></div></td><td>${esc(dateTime(b.created))}</td><td>${esc(humanSize(b.size))}</td><td><button class="button" data-action="restore-backup" data-id="${esc(b.id)}" ${running() || busy() ? 'disabled' : ''}>${icon('refresh')}恢复</button></td></tr>`).join('')}</tbody></table></div>` : `<div class="empty-state">${icon('archive')}<strong>你的第一份存档快照</strong>点击「创建备份」，保存现在的花园。</div>`}</section><p class="hint">恢复会覆盖当前玩家与公会数据，请在游戏服务停止后操作。</p>`;
}
function renderSettings() {
  return `${heading('设置','调整主题与显示大小，查看本地环境。')}${connectionPanel()}${demo ? readonlyBanner() : ''}<div class="settings-layout"><section class="panel settings-group"><div class="panel-head"><h2>外观与显示</h2><span class="tag">个性化</span></div><div class="setting-row"><div><h3>界面主题</h3><p>浅色、深色或跟随系统。</p></div><div class="theme-options">${[['light','sun','浅色'],['dark','moon','深色'],['auto','settings','跟随系统']].map(([v,i,label]) => `<button class="theme-option ${settings.theme === v ? 'active' : ''}" data-action="set-theme" data-theme="${v}">${icon(i)}${label}</button>`).join('')}</div></div><div class="setting-row"><div><h3>界面缩放</h3><p>调整文字与控件的尺寸，适配你的屏幕。</p></div><select id="settingScale" class="field-control scale-select" aria-label="界面缩放">${[85,90,100,110,125,150].map(n=> `<option value="${n}" ${Number(settings.scale) === n ? 'selected' : ''}>${n}%${n===100 ? ' · 默认' : ''}</option>`).join('')}</select></div></section><section class="panel settings-group"><div class="panel-head"><h2>本地环境</h2><span class="tag green">离线可用</span></div><div class="setting-row"><div><h3>游戏与存档目录</h3><p>${esc(settings.game_path || appState.gamePath || '由启动器自动定位游戏所在目录')}</p></div>${icon('archive')}</div><div class="setting-row"><div><h3>存档写入保护</h3><p>游戏运行时锁定修改；保存资料前校验最新存档。</p></div><span class="tag green">${icon('shield')}已启用</span></div><div class="setting-row"><div><h3>服务停止与恢复</h3><p>停止后清理启动器创建的游戏进程，支持连续启动。</p></div><span class="tag green">自动处理</span></div></section><section class="panel about-panel"><span class="brand-mark"><i></i><i></i><i></i><i></i></span><div><h3>植物大战僵尸 OL · 启动器</h3><p>PVZ OL 本地版</p></div><span class="tag">${esc(appState.version || '2.0')}</span></section></div>`;
}
function render() {
  updateChrome();
  const views = {home:renderHome,accounts:renderAccounts,gm:renderGM,services:renderServices,backups:renderBackups,settings:renderSettings};
  $('#main').innerHTML = views[page]();
  if (page === 'services') scrollLogs();
}
function scrollLogs() { const view = $('#logView'); if (view) view.scrollTop = view.scrollHeight; }
function renderLogsOnly() {
  const view = $('#logView'); if (!view) return;
  const nearBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 50;
  view.innerHTML = logLines(); $('#logCount').textContent = `${filteredLogs().length} 条记录 · 最多保留最近 2,000 条`;
  if (nearBottom) scrollLogs();
}
async function loadProfile(id = selectedId) {
  if (!id) {profile = null; return;}
  try {
    const result = await api(`/api/accounts/${encodeURIComponent(id)}`);
    if (id !== selectedId) return;
    profile = {...result,id:String(result.id ?? result.uin ?? id)};
    stagedAvatar = 'keep'; stagedImage = ''; profileDirty = false;profileDraft=null;
    if (page === 'accounts') $('#profilePanel').innerHTML = renderProfile();
  } catch (e) {
    if (page === 'accounts') $('#profilePanel').innerHTML = `<div class="empty-state">${icon('info')}<strong>角色资料读取失败</strong>${esc(e.message)}<br><button class="button small" data-action="reload-profile" style="margin-top:15px">重试</button></div>`;
    toast(e.message,'error');
  }
}
async function syncState(force = false) {
  try {
    const next = await api('/api/state');
    const changed = JSON.stringify({running:next.running,busy:next.busy,starting:next.starting,stopping:next.stopping,services:next.services,progress:next.progress,statusMessage:next.statusMessage,currentAccount:next.currentAccount}) !== JSON.stringify({running:appState.running,busy:appState.busy,starting:appState.starting,stopping:appState.stopping,services:appState.services,progress:appState.progress,statusMessage:appState.statusMessage,currentAccount:appState.currentAccount});
    const wasRunning = running(); appState = next; connecting = false; connectionError = '';
    if (changed || force) {updateChrome();if (page === 'home' || page === 'services') render();if (page === 'accounts' && wasRunning !== running() && !profileDirty) render();if (page === 'gm' && wasRunning !== running()) render();if (page === 'backups' && wasRunning !== running()) render();}
    else updateChrome();
  } catch (e) { connectionError = e.message; connecting = false; updateChrome(); if (force) render(); }
}
async function syncLogs() {
  if (logPaused || demo) return;
  try {
    const data = await api(`/api/logs?after=${encodeURIComponent(lastLogId)}`);
    const incoming = data.entries || [];
    if (incoming.length) {
      const known = new Set(entries.map(e => String(e.id)));
      entries.push(...incoming.filter(e => !known.has(String(e.id))));
      entries = entries.slice(-2000);lastLogId = data.lastId ?? incoming.at(-1)?.id ?? lastLogId;
      if (page === 'services') renderLogsOnly();
    }
  } catch { /* State polling reports connection failures without a repeating toast. */ }
}
async function refreshAccounts() {
  accounts = normalizeAccounts(await api('/api/accounts'));
  if (!accounts.some(a => accountId(a) === selectedId)) selectedId = accountId(appState.currentAccount) || accountId(accounts[0]);
  updateChrome();
}
async function refreshBackups() { backups = normalizeBackups(await api('/api/backups')); if(page === 'backups') render(); }
async function startGame() {
  if (busy() || running()) return;
  if (!selectedId) {toast('请先创建或选择一个角色。','warn');location.hash = '#accounts';return;}
  localBusy = true; render();
  try {await api('/api/start',{method:'POST',body:{account_id:selectedId}});await syncState(true);toast('启动请求已提交，正在准备游戏服务。');}
  catch(e) {toast(e.message,'error');}
  finally {localBusy = false;render();}
}
async function stopGame() {
  localBusy = true; render();
  try {await api('/api/stop',{method:'POST',body:{}});await syncState(true);toast('停止请求已提交。');}
  catch(e) {toast(e.message,'error');}
  finally {localBusy = false;render();}
}
async function openAdmin(force = false) {
  if (demo || adminLoading || (adminUrl && !force)) return;
  adminLoading = true;adminError = '';if(page === 'gm') render();
  try { const result = await api('/api/admin/start',{method:'POST',body:{}}); adminUrl = result.url || '';if(!adminUrl) throw new Error('后台未返回访问地址，请检查服务日志。');}
  catch(e) {adminError = e.message;toast(e.message,'error');}
  finally {adminLoading = false;if(page === 'gm') render();}
}
function dialog(title, content, actions) {
  $('#dialogBody').innerHTML = `<div class="dialog-heading"><h2>${title}</h2><button class="icon-button" data-action="close-dialog" aria-label="关闭弹窗">${icon('close')}</button></div><div class="dialog-content">${content}</div><div class="dialog-actions"><button class="button" data-action="close-dialog">取消</button>${actions}</div>`;
  $('#appDialog').showModal();
}
function closeDialog() { $('#appDialog').close(); }
function newAccountDialog() {
  dialog('创建一个新角色',`<p>为你的新伙伴设置一个账号 UID。昵称和头像可在创建后修改。</p><label class="field"><span>账号 UID</span><input class="field-control" id="newUin" inputmode="numeric" pattern="[0-9]+" maxlength="20" placeholder="请输入数字账号" autofocus></label><p class="hint">每个角色使用独立账号，不覆盖已有角色。</p>`,`<button class="button primary" data-action="confirm-create">${icon('plus')}创建角色</button>`);
}
async function createAccount(button) {
  const uin = $('#newUin').value.trim();if (!/^\d+$/.test(uin)) {toast('UID 请使用数字。','warn');$('#newUin').focus();return;}
  button.disabled = true;
  try {await api('/api/accounts',{method:'POST',body:{uin}});closeDialog();await refreshAccounts();selectedId = uin;profile = null;render();await loadProfile();toast('新角色已创建。');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
function deleteAccountDialog() {
  const a = currentAccount();
  dialog('删除角色',`<p>将删除角色「${esc(accountName(a))}」及其角色存档。此操作会先保留一份备份。</p><p class="confirm-note">请输入 UID <strong>${esc(selectedId)}</strong>，确认要删除的角色。</p><label class="field"><span>确认角色 UID</span><input class="field-control" id="confirmDeleteUin" autocomplete="off" placeholder="${esc(selectedId)}"></label>`,`<button class="button danger" data-action="confirm-delete">${icon('trash')}删除角色</button>`);
}
async function deleteAccount(button) {
  const value = $('#confirmDeleteUin').value.trim();if (value !== selectedId) {toast('输入的 UID 与当前角色不一致。','warn');return;}
  button.disabled = true;
  try {await api(`/api/accounts/${encodeURIComponent(selectedId)}`,{method:'DELETE',body:{confirm_uin:value}});closeDialog();profile = null;await refreshAccounts();render();await loadProfile();await refreshBackups();toast('角色已删除。');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
async function saveProfile(form) {
  if (profileSaving) return;
  if (running() || busy() || demo) {toast(demo ? '当前为只读演示，不能修改角色资料。' : '请先停止游戏服务再修改资料。','warn');return;}
  const button = $('button[type=submit]',form);button.disabled = true;
  const body = {name:form.elements.name.value.trim(),signature:form.elements.signature.value,avatar:stagedAvatar,profile_expected:profile.profile_expected};
  if (stagedImage) body.image_data = stagedImage;
  if (!body.name) {toast('请填写角色昵称。','warn');button.disabled = false;return;}
  if(body.name !== (profile.name || profile.nickname || '') && (body.name.length<2 || body.name.length>7)) {toast('昵称需要 2–7 个字。','warn');button.disabled=false;return;}
  profileSaving = true;
  form.querySelectorAll('input,textarea,button').forEach(control=>control.disabled=true);
  try {await api(`/api/accounts/${encodeURIComponent(selectedId)}`,{method:'PATCH',body});await refreshAccounts();await loadProfile();toast('角色资料已保存。');}
  catch(e) {toast(e.message,'error');}
  finally {profileSaving=false;if(page==='accounts')render();}
}
function beginCrop(file) {
  if (!file) return;
  if (file.size > 15*1024*1024) {toast('请使用小于 15 MB 的头像图片。','warn');return;}
  const reader = new FileReader();
  reader.onerror = ()=>toast('图片读取失败，请换一张图片。','error');
  reader.onload = ()=>{const image = new Image();image.onerror = ()=>toast('无法识别这张图片。','error');image.onload = ()=>{cropImage = image;crop = {zoom:1,x:0,y:0};dialog('裁剪你的头像',`<p>拖动图片调整位置，使用滑块放大。头像将保存为正方形图片。</p><canvas id="cropCanvas" class="crop-preview" width="480" height="480" aria-label="头像裁剪预览"></canvas><label class="crop-control"><span>缩放</span><input type="range" id="cropZoom" min="1" max="3" value="1" step="0.01"></label><p class="hint" style="text-align:center">拖动图片移动裁剪区域</p>`,`<button class="button primary" data-action="confirm-crop">${icon('check')}使用头像</button>`);drawCrop();installCropDrag();};image.src = reader.result;};
  reader.readAsDataURL(file);
}
function drawCrop() {
  const canvas = $('#cropCanvas');if (!canvas || !cropImage) return;
  const scale = Math.max(canvas.width/cropImage.width,canvas.height/cropImage.height)*crop.zoom;
  const w = cropImage.width*scale,h=cropImage.height*scale;
  crop.x = Math.max(-(w-canvas.width)/2,Math.min((w-canvas.width)/2,crop.x));
  crop.y = Math.max(-(h-canvas.height)/2,Math.min((h-canvas.height)/2,crop.y));
  const ctx = canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(cropImage,(canvas.width-w)/2+crop.x,(canvas.height-h)/2+crop.y,w,h);
}
function installCropDrag() {
  const canvas = $('#cropCanvas');let origin = null;
  canvas.addEventListener('pointerdown',e=>{origin={px:e.clientX,py:e.clientY,x:crop.x,y:crop.y};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!origin)return;const ratio=canvas.width/canvas.getBoundingClientRect().width;crop.x=origin.x+(e.clientX-origin.px)*ratio;crop.y=origin.y+(e.clientY-origin.py)*ratio;drawCrop();});
  canvas.addEventListener('pointerup',()=>origin=null);canvas.addEventListener('pointercancel',()=>origin=null);
}
function finishCrop() {
  const source = $('#cropCanvas');const output = document.createElement('canvas');output.width=output.height=256;output.getContext('2d').drawImage(source,0,0,256,256);
  stagedImage = output.toDataURL('image/png');stagedAvatar='custom';profileDirty=true;closeDialog();
  $('#profileAvatarPreview').innerHTML = avatar({name:accountName(profile),avatar_url:stagedImage});document.querySelectorAll('.avatar-choice').forEach(el=>el.classList.remove('selected'));
}
async function createBackup(button) {
  button.disabled = true;
  try {await api('/api/backups',{method:'POST',body:{reason:'manual'}});await refreshBackups();toast('完整存档备份已创建。');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
function restoreDialog(id) {
  const b = backups.find(x => String(x.id) === id);if(!b)return;
  dialog('恢复存档快照',`<p>将恢复「${esc(b.name || b.id)}」，覆盖当前的玩家存档与公会数据。</p><p class="confirm-note">恢复前会自动备份当前存档。请确认这是你想回到的花园。</p><p>快照时间：${esc(dateTime(b.created))}</p>`,`<button class="button primary" data-action="confirm-restore" data-id="${esc(id)}">${icon('refresh')}确认恢复</button>`);
}
async function restoreBackup(id,button) {
  button.disabled = true;
  try {await api(`/api/backups/${encodeURIComponent(id)}/restore`,{method:'POST',body:{confirm:true}});closeDialog();await refreshBackups();await refreshAccounts();profile=null;toast('存档已恢复，当前存档已自动备份。');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
async function saveSettings(patch) {
  const previous = {...settings};settings={...settings,...patch};applyTheme();if(page==='settings' || page==='gm')render();
  if (demo) {toast('演示外观已切换，仅在当前预览中生效。');return;}
  try {const next=await api('/api/settings',{method:'PATCH',body:patch});if(next)settings={...settings,...next};applyTheme();}
  catch(e) {settings=previous;applyTheme();if(page==='settings')render();toast(e.message,'error');}
}
async function copyLogs() {
  const text=filteredLogs().map(e=>`[${shortTime(e.time)}] [${String(e.level||'INFO').toUpperCase()}] ${e.message}`).join('\n');
  try {await navigator.clipboard.writeText(text);toast('筛选后的日志已复制。');}
  catch {const textarea=document.createElement('textarea');textarea.value=text;document.body.append(textarea);textarea.select();const ok=document.execCommand('copy');textarea.remove();toast(ok?'日志已复制。':'复制失败，请使用导出日志。',ok?'success':'error');}
}
async function exportLogs() {
  if(demo) {toast('只读演示不导出实际运行日志。','warn');return;}
  try {const response=await fetch('/api/logs/export',{headers:{'X-PVZ-Token':token},cache:'no-store'});if(!response.ok)throw new Error('日志导出失败。');const blob=await response.blob();const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=`PVZ-运行日志-${new Date().toISOString().slice(0,10)}.txt`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('运行日志已导出。');}
  catch(e){toast(e.message,'error');}
}
async function navigate() {
  const next = location.hash.slice(1); page=pages[next]?next:'home';render();
  if(page==='accounts' && (!profile || accountId(profile)!==selectedId)) await loadProfile();
  if(page==='gm') await openAdmin();
  if(page==='backups') {try{await refreshBackups();}catch(e){toast(e.message,'error');}}
}
document.addEventListener('click', async event => {
  const target=event.target.closest('[data-action]');if(!target || target.disabled)return;
  const action=target.dataset.action;
  try {
    if(action==='start') await startGame();
    else if(action==='stop') await stopGame();
    else if(action==='reconnect') await boot();
    else if(action==='refresh-state') {await syncState(true);await syncLogs();}
    else if(action==='select-account') {if(profileSaving){toast('资料正在保存，请稍候。','warn');return;}if(target.dataset.id===selectedId&&profile)return;if(profileDirty && target.dataset.id!==selectedId){dialog('切换角色',`<p>当前资料尚未保存。切换到另一个角色将放弃这些修改。</p>`,`<button class="button primary" data-action="confirm-switch" data-id="${esc(target.dataset.id)}">切换角色</button>`);return;}selectedId=target.dataset.id;profile=null;render();await loadProfile();}
    else if(action==='confirm-switch') {closeDialog();selectedId=target.dataset.id;profile=null;profileDirty=false;render();await loadProfile();}
    else if(action==='new-account') newAccountDialog();
    else if(action==='confirm-create') await createAccount(target);
    else if(action==='delete-account') deleteAccountDialog();
    else if(action==='confirm-delete') await deleteAccount(target);
    else if(action==='reload-profile') await loadProfile();
    else if(action==='upload-avatar') $('#avatarUpload').click();
    else if(action==='choose-avatar') {stagedAvatar=Number(target.dataset.id);stagedImage='';profileDirty=true;document.querySelectorAll('.avatar-choice').forEach(el=>el.classList.toggle('selected',el===target));$('#profileAvatarPreview').innerHTML=avatar({name:accountName(profile),avatar_url:target.dataset.url});}
    else if(action==='confirm-crop') finishCrop();
    else if(action==='open-admin' || action==='reload-admin') await openAdmin(true);
    else if(action==='log-level') {logLevel=target.dataset.level;document.querySelectorAll('[data-action=log-level]').forEach(el=>el.classList.toggle('active',el===target));renderLogsOnly();}
    else if(action==='pause-logs') {logPaused=!logPaused;target.innerHTML=`${icon(logPaused?'play':'pause')}${logPaused?'继续':'暂停'}`;$('#logLiveBadge').className=`tag ${logPaused?'yellow':'green'}`;$('#logLiveBadge').innerHTML=`<i></i>${logPaused?'已暂停显示':'实时更新'}`;if(!logPaused)await syncLogs();}
    else if(action==='copy-logs') await copyLogs();
    else if(action==='export-logs') await exportLogs();
    else if(action==='create-backup') await createBackup(target);
    else if(action==='refresh-backups') await refreshBackups();
    else if(action==='restore-backup') restoreDialog(target.dataset.id);
    else if(action==='confirm-restore') await restoreBackup(target.dataset.id,target);
    else if(action==='set-theme') await saveSettings({theme:target.dataset.theme});
    else if(action==='close-dialog') closeDialog();
  } catch(e) {toast(e.message,'error');}
});
document.addEventListener('input',event=>{
  if(event.target.id==='accountSearch') {accountQuery=event.target.value;const selection=event.target.selectionStart;const panel=$('.account-list-panel');const holder=document.createElement('div');holder.innerHTML=renderAccounts();panel.replaceWith($('.account-list-panel',holder));const input=$('#accountSearch');input.focus();input.setSelectionRange(selection,selection);}
  if(event.target.id==='logSearch') {logQuery=event.target.value;renderLogsOnly();}
  if(event.target.id==='cropZoom') {crop.zoom=Number(event.target.value);drawCrop();}
  if(event.target.closest('#profileForm')) {profileDirty=true;const form=$('#profileForm');profileDraft={name:form.elements.name.value,signature:form.elements.signature.value};}
});
document.addEventListener('change',async event=>{
  if(event.target.id==='gmAccount') {selectedId=event.target.value;profile=null;render();}
  if(event.target.id==='homeAccount') {selectedId=event.target.value;profile=null;render();}
  if(event.target.id==='settingScale') await saveSettings({scale:Number(event.target.value)});
  if(event.target.id==='avatarUpload') {beginCrop(event.target.files[0]);event.target.value='';}
});
document.addEventListener('submit',async event=>{if(event.target.id==='profileForm'){event.preventDefault();await saveProfile(event.target);}});
$('#themeToggle').addEventListener('click',()=>saveSettings({theme:document.documentElement.dataset.theme==='dark'?'light':'dark'}));
$('#appDialog').addEventListener('click',event=>{if(event.target===$('#appDialog')){const r=$('#appDialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
window.addEventListener('hashchange',navigate);
matchMedia('(prefers-color-scheme:dark)').addEventListener('change',()=>{if(settings.theme==='auto')applyTheme();});
window.addEventListener('beforeunload',event=>{if(profileDirty){event.preventDefault();event.returnValue='';}});

async function boot() {
  if(booting)return;booting=true;connecting=true;connectionError='';updateChrome();
  const results=await Promise.allSettled([api('/api/state'),api('/api/accounts'),api('/api/settings'),api('/api/logs?after=0'),api('/api/backups')]);
  if(results[0].status==='fulfilled')appState=results[0].value;
  else connectionError=results[0].reason.message;
  if(results[1].status==='fulfilled')accounts=normalizeAccounts(results[1].value);
  if(results[2].status==='fulfilled')settings={...settings,...results[2].value};
  if(results[3].status==='fulfilled'){entries=results[3].value.entries||[];lastLogId=results[3].value.lastId||0;}
  if(results[4].status==='fulfilled')backups=normalizeBackups(results[4].value);
  selectedId=selectedId && accounts.some(a=>accountId(a)===selectedId)?selectedId:accountId(appState.currentAccount)||accountId(accounts[0]);
  connecting=false;applyTheme();render();booting=false;
  if(page==='accounts')await loadProfile();
  if(page==='gm')await openAdmin();
  clearInterval(stateTimer);clearInterval(logTimer);
  if(!demo){stateTimer=setInterval(()=>syncState(),1500);logTimer=setInterval(()=>syncLogs(),1800);}
}
render();boot();
