'use strict';

/* Local launcher UI. All writes go through the authenticated Python API. */
const params = new URLSearchParams(location.search);
const demo = params.get('demo') === 'true';
const token = params.get('token') || sessionStorage.getItem('pvz-launcher-token') || '';
if (token) sessionStorage.setItem('pvz-launcher-token', token);
const $ = (s, root = document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name, cls = '') => `<svg${cls ? ` class="${esc(cls)}"` : ''} aria-hidden="true"><use href="#i-${name}"/></svg>`;
const pages = {home:'运行概览',accounts:'角色资料',gm:'GM 管理',services:'服务与日志',backups:'存档备份',settings:'设置'};
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
let showRobotAccounts = false;
let robotToggleLoading = false;
const collapsedAccountGroups = new Set();
let adminUrl = '';
let gmDashboard = true;
let gmContext = {view:'list',uin:'',section:'attributes'};
let gmCurrentAccount = null;
let localLaunchError = '';
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
let stateRequest = null;
let logRequest = null;
let booting = false;
let settingsSaveQueue = Promise.resolve();

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
function isRobotAccount(a) {
  const uin = Number(accountId(a));
  return a?.is_robot === true || (uin >= 900000 && uin < 1000000);
}
function playerAccounts() { return accounts.filter(a => !isRobotAccount(a)); }
function accountsEndpoint() { return `/api/accounts${showRobotAccounts ? '?include_robots=1' : ''}`; }
function currentAccount() { const players = playerAccounts(); return players.find(a => accountId(a) === selectedId) || players.find(a => accountId(a) === accountId(appState.currentAccount)) || players[0] || null; }
function launchAccount() { return running() ? appState.currentAccount || accounts.find(a => accountId(a) === String(appState.activeAccountId)) || null : currentAccount(); }
function busy() { return Boolean(localBusy || appState.busy || appState.starting || appState.stopping); }
function running() { return Boolean(appState.running); }
function writesLocked() { return running() || busy() || Boolean(connectionError); }
function avatarSource(a) { return a?.avatar_url || a?.avatarUrl || a?.qq_face_url || a?.image_data || ''; }
function avatar(a, size = '') {
  const src = avatarSource(a) || '/avatar/unknow.png';
  return `<span class="avatar${size ? ` avatar-${size}` : ''}"><img src="${esc(src)}" alt="${esc(accountName(a))}的头像" loading="lazy"></span>`;
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
  const v = {running:['green','运行中'],starting:['yellow','启动中'],stopping:['yellow','停止中'],checking:['','检测中'],conflict:['red','被其他程序占用'],error:['red','异常'],stopped:['','未启动'],ready:['green','已就绪']}[status] || ['', '未启动'];
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
    if (path.startsWith('/api/accounts')) return path.includes('include_robots=1') ? [...demoAccounts,{id:'900001',uin:'900001',name:'花园机器人',level:30,is_robot:true,face_id:0}] : demoAccounts;
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
  const frame = $('#gmFrame');
  if (frame && adminUrl && new URL(frame.src).searchParams.get('theme') !== document.documentElement.dataset.theme) frame.src = adminFrameUrl();
}
function updateChrome() {
  document.body.dataset.view = page;
  $('#breadcrumbPage').textContent = pages[page];
  $('#pageContext').textContent = page === 'gm' && !gmDashboard && gmCurrentAccount ? accountName(gmCurrentAccount) : '';
  document.querySelectorAll('a[data-page]').forEach(el => {el.classList.toggle('active',el.dataset.page === page);el.setAttribute('aria-current',el.dataset.page === page ? 'page' : 'false');});
  $('#demoBadge').hidden = !demo;
  const connection = $('#connectionBadge');
  connection.className = `connection-badge ${connectionError ? 'error' : connecting ? '' : 'connected'}`;
  connection.innerHTML = `<i></i>${demo ? '演示模式' : connectionError ? '连接异常' : connecting ? '连接中' : '本地已连接'}`;
}

function heading(title, subtitle, actions = '') {
  return `<div class="page-heading"><p>${esc(subtitle)}</p>${actions}</div>`;
}

function connectionPanel() { return connectionError ? `<div class="connection-error"><strong>暂时无法连接启动器服务</strong><p>${esc(connectionError)}</p><button class="button" data-action="reconnect">${icon('refresh')}重新连接</button></div>` : ''; }
function readonlyBanner() { return demo ? `<div class="readonly-banner">${icon('info')}当前为只读界面演示，启动、修改角色、管理存档等操作不会执行。</div>` : busy() || running() ? `<div class="readonly-banner">${icon('shield')}${busy() ? '游戏服务正在启动或停止，请稍候再修改资料与存档。' : '游戏服务正在运行。角色资料、GM 数据和存档恢复已锁定，请先停止游戏服务再修改。'}</div>` : ''; }
function progressPanel() {
  if (!busy()) return '';
  const p = typeof appState.progress === 'object' ? Number(appState.progress?.percent || 0) : Number(appState.progress || 0);
  return `<div class="panel progress-panel"><span class="spinner"></span><div class="progress-content"><div class="progress-caption"><span>${esc(appState.statusMessage || '正在准备游戏服务…')}</span><small>${Math.max(0,Math.min(100,p))}%</small></div><div class="progress-track"><span style="width:${Math.max(3,Math.min(100,p))}%"></span></div></div><button class="button small" data-action="stop">取消</button></div>`;
}
function serviceCards(detail = false) {
  return `<section class="panel service-summary"><div class="panel-head"><h2>服务连接</h2><button class="button small" data-action="edit-ports">${icon('settings')}端口配置</button></div><div class="port-list">${portRows()}</div></section>`;
}

function renderHome() {
  const a = launchAccount();
  const state = homeStatus();
  return `${connectionPanel()}<section class="launch-overview"><div class="launch-person">${avatar(a,'lg')}<div><span class="eyebrow">当前游戏角色</span><h2>${esc(a ? accountName(a) : '创建你的第一个角色')}</h2><p>${a ? `UID ${esc(accountId(a))} · Lv.${esc(a.level ?? '—')}` : '每个角色拥有独立存档'}</p></div></div><div class="launch-side"><span class="session-state ${state.kind}"><i></i>${esc(state.title)}</span><p>${esc(state.text)}</p><div class="launch-actions"><select id="homeAccount" class="field-control" aria-label="选择游戏角色" ${busy()||running()||!playerAccounts().length?'disabled':''}>${playerAccounts().length ? playerAccounts().map(x=>`<option value="${esc(accountId(x))}" ${accountId(x)===accountId(a)?'selected':''}>${esc(accountName(x))}</option>`).join('') : '<option value="">暂无角色</option>'}</select><button class="button primary launch-button" data-action="${busy()||running()?'stop':a?'start':'new-account'}" ${connectionError?'disabled':''}>${icon(busy()||running()?'stop':a?'play':'plus')}${busy()?'取消启动':running()?'停止游戏':a?'启动游戏':'新建角色'}</button></div></div></section>${progressPanel()}<div id="homeLive">${renderHomeLive()}</div>`;
}

function renderAccountGroup(title, items, group, robots = false) {
  if (!items.length) return '';
  return `<details class="account-group" data-account-group="${group}" ${accountQuery || !collapsedAccountGroups.has(group) ? 'open' : ''}>
    <summary><span>${title}<small>${items.length}</small></span>${icon('chevron')}</summary>
    <div class="account-group-items">${items.map(a => {
      const selected = accountId(a) === selectedId;
      const content = `${avatar(a)}<span class="account-row-content"><strong title="${esc(accountName(a))}">${esc(accountName(a))}</strong><span class="account-row-tags"><small>UID ${esc(accountId(a))}</small>${a.level !== undefined ? `<small>Lv.${esc(a.level)}</small>` : ''}${robots ? '<small class="robot-tag">机器人</small>' : ''}</span></span><span class="account-row-end">${robots ? icon('shield') : selected ? icon('check') : icon('chevron')}</span>`;
      return robots ? `<div class="account-row robot-row" title="系统机器人账号">${content}</div>` : `<button class="account-row ${selected ? 'selected' : ''}" data-action="select-account" data-id="${esc(accountId(a))}" aria-pressed="${selected}">${content}</button>`;
    }).join('')}</div></details>`;
}
function renderAccountListPanel() {
  const filtered = accounts.filter(a => (showRobotAccounts || !isRobotAccount(a)) && `${accountName(a)} ${accountId(a)}`.toLowerCase().includes(accountQuery.trim().toLowerCase()));
  const players = filtered.filter(a => !isRobotAccount(a));
  const robots = filtered.filter(isRobotAccount);
  return `<section class="panel account-list-panel"><div class="panel-head"><h2>账号选择</h2><span class="tag">${playerAccounts().length} 个角色</span></div>
    <div class="account-search">${icon('search')}<input class="field-control" id="accountSearch" placeholder="搜索角色名或 UID" value="${esc(accountQuery)}" aria-label="搜索角色"></div>
    <div class="account-list-options"><label class="robot-visibility"><input id="showRobotAccounts" type="checkbox" role="switch" ${showRobotAccounts ? 'checked' : ''} ${robotToggleLoading ? 'disabled' : ''}><span class="switch-track" aria-hidden="true"></span><span>显示机器人账号</span></label>${robotToggleLoading ? '<span class="spinner small"></span>' : ''}</div>
    <div class="account-list">${renderAccountGroup('我的角色',players,'players')}${showRobotAccounts ? renderAccountGroup('机器人账号',robots,'robots',true) : ''}${!filtered.length ? `<div class="empty-state">${icon('search')}<strong>${accountQuery ? '没有找到匹配账号' : '暂无角色'}</strong>${accountQuery ? '试试另一个名字或 UID。' : '点击右上方新建角色。'}</div>` : ''}</div>
    <div class="account-list-footer"><span>${accountQuery ? `${filtered.length} 个搜索结果` : showRobotAccounts ? `机器人账号 ${robots.length} 个` : '机器人账号已隐藏'}</span><span>选择角色编辑资料</span></div></section>`;
}
function replaceAccountListPanel() {
  if (page !== 'accounts') return;
  const panel = $('.account-list-panel');
  if (!panel) return;
  const holder = document.createElement('div');
  holder.innerHTML = renderAccountListPanel();
  panel.replaceWith(holder.firstElementChild);
}
async function toggleRobotAccounts(enabled) {
  if (robotToggleLoading) return;
  const previous = showRobotAccounts;
  robotToggleLoading = true; showRobotAccounts = enabled; replaceAccountListPanel();
  try { await refreshAccounts(); }
  catch (e) { showRobotAccounts = previous; toast(e.message,'error'); }
  finally { robotToggleLoading = false; replaceAccountListPanel(); $('#showRobotAccounts')?.focus(); }
}
function renderAccounts() {
  const actions = `<div class="page-actions"><button class="button primary" data-action="new-account" ${writesLocked() || profileSaving ? 'disabled' : ''}>${icon('plus')}新建角色</button></div>`;
  return `${heading('角色管理','管理角色账号、昵称、签名与头像。',actions)}${connectionPanel()}${readonlyBanner()}<div class="account-layout">${renderAccountListPanel()}<section id="profilePanel" class="panel">${renderProfile()}</section></div>`;
}
function renderProfile() {
  if (!selectedId || !playerAccounts().length) return `<div class="empty-state">${icon('user')}<strong>还没有游戏角色</strong>新建角色后设置昵称、签名与头像。</div>`;
  if (!profile || accountId(profile)!==selectedId) return `<div class="loading"><span class="spinner"></span>读取角色资料…</div>`;
  const a=profile, disabled=writesLocked()||demo||profileSaving, choices=a.avatarChoices||[];
  return `<div class="profile-banner">${avatar(a,'lg')}<div><h2>${esc(accountName(a))}</h2><p>UID ${esc(accountId(a))}</p></div><span class="tag green">本地角色</span></div><form id="profileForm" class="profile-form"><div class="profile-avatar"><div id="profileAvatarPreview">${avatar(previewAccount(a))}</div><div class="avatar-action"><strong>角色头像</strong><p class="hint">选择游戏头像，或上传自己的图片</p></div><button type="button" class="button small" data-action="upload-avatar" ${disabled?'disabled':''}>${icon('upload')}上传图片</button></div>${choices.length?`<div class="avatar-choices" aria-label="选择内置头像">${choices.map(v=>`<button type="button" class="avatar-choice ${(stagedAvatar==='keep'&&!a.qq_face_url&&Number(a.face_id)===Number(v.id))||stagedAvatar===Number(v.id)?'selected':''}" data-action="choose-avatar" data-id="${esc(v.id)}" data-url="${esc(v.url)}" title="${esc(v.label)}" ${disabled?'disabled':''}><img src="${esc(v.url)}" alt="${esc(v.label)}"></button>`).join('')}</div>`:''}<div class="form-grid"><label class="field"><span>角色昵称</span><input class="field-control" name="name" value="${esc(profileDraft?.name??a.name??'')}" maxlength="7" required ${disabled?'disabled':''}><small>2–7 个字</small></label><label class="field"><span>角色 UID</span><input class="field-control" value="${esc(accountId(a))}" disabled><small>角色创建后不可更改</small></label></div><label class="field"><span>个性签名</span><input class="field-control" name="signature" maxlength="30" placeholder="写下你的花园故事" value="${esc(profileDraft?.signature??a.signature??'')}" ${disabled?'disabled':''}><small>最多 30 个字</small></label><div class="form-actions"><button type="button" class="button" data-action="reload-profile" ${disabled?'disabled':''}>重置修改</button><button type="submit" class="button primary" ${disabled?'disabled':''}>${icon('check')}保存资料</button></div></form><div class="profile-details"><div><small>等级</small><strong>Lv.${numeric(a.level)}</strong></div><div><small>金币</small><strong>${numeric(a.coin??a.gold??a.money)}</strong></div><div><small>状态</small><strong>${running()&&accountId(appState.currentAccount)===selectedId?'游戏运行中':'已保存'}</strong></div></div><div class="profile-footer"><span>资料会同步到游戏中的角色。</span><button class="button danger small" data-action="delete-account" ${disabled?'disabled':''}>${icon('trash')}删除角色</button></div>`;
}

function renderGM() {
  return `${connectionPanel()}${readonlyBanner()}<section class="gm-panel"><div class="gm-toolbar" id="gmToolbar">${gmToolbar()}</div>${adminUrl&&!demo?`<iframe class="gm-frame" id="gmFrame" src="${esc(adminFrameUrl())}" title="GM 管理工作台"></iframe>`:`<div class="gm-empty">${adminLoading?'<span class="spinner"></span>':icon('grid')}<h3>${adminLoading?'正在连接管理工作台':'GM 管理工作台'}</h3><p>${esc(adminError||'管理角色属性、背包、植物和关卡进度。')}</p>${!demo&&!adminLoading?'<button class="button primary" data-action="open-admin">打开工作台</button>':''}</div>`}</section>`;
}

function adminFrameUrl() {
  if(!adminUrl)return '';
  const url=new URL(gmContext.route||adminUrl,new URL(adminUrl,location.origin).origin);
  if(!gmContext.route){
    url.pathname=gmDashboard?'/':'/player';
    if(gmDashboard){url.searchParams.delete('uin');url.searchParams.delete('section');}
    else{url.searchParams.set('uin',gmContext.uin||selectedId);url.searchParams.set('section',gmContext.section||'attributes');}
  }
  url.searchParams.set('embedded','1');url.searchParams.set('theme',document.documentElement.dataset.theme||'light');
  return url.toString();
}

function filteredLogs() { return entries.filter(e => (logLevel === 'all' || String(e.level).toLowerCase() === logLevel || (logLevel === 'warn' && String(e.level).toLowerCase() === 'warning')) && `${e.message} ${e.level} ${e.time}`.toLowerCase().includes(logQuery.toLowerCase())); }
function logLines() { const lines = filteredLogs(); return lines.length ? lines.map(e=> `<div class="log-line ${String(e.level).toLowerCase()}"><span class="log-time">${esc(shortTime(e.time))}</span><span class="log-level">${esc(String(e.level || 'INFO').toUpperCase())}</span><span class="log-text">${esc(e.message)}</span></div>`).join('') : '<div class="log-line"><span class="log-text muted">暂无匹配的日志。等待服务输出，或调整筛选条件。</span></div>'; }
function renderServiceLive() {
  return `<div id="serviceLive">${connectionPanel()}${progressPanel()}<div class="dashboard-columns services-overview">${serviceCards(true)}<section class="panel"><div class="panel-head"><h2>进程与内存</h2><span class="tag">${memorySize(appState.runtime?.memory_bytes)}</span></div><div id="processList">${processRows()}</div></section></div></div>`;
}
function refreshServiceLive() {
  const live=$('#serviceLive');if(live)live.outerHTML=renderServiceLive();
  const stop=$('#main .page-actions [data-action="stop"]');if(stop)stop.disabled=busy();
}
function renderServices() {
  return `${heading('服务与日志','连接状态、进程资源和运行记录。',`<div class="page-actions"><button class="button" data-action="stop" ${busy()?'disabled':''}>${icon('stop')}停止所有服务</button><button class="button" data-action="refresh-state">${icon('refresh')}刷新</button></div>`)}${renderServiceLive()}<section class="panel log-panel"><div class="panel-head"><h2>运行日志</h2><span class="tag ${logPaused?'yellow':'green'}" id="logLiveBadge"><i></i>${logPaused?'暂停显示':'实时更新'}</span></div><div class="log-toolbar"><div class="log-levels">${[['all','全部'],['info','信息'],['warn','警告'],['error','错误']].map(([v,label])=>`<button data-action="log-level" data-level="${v}" class="${logLevel===v?'active':''}">${label}</button>`).join('')}</div><div class="log-search">${icon('search')}<input id="logSearch" class="field-control" value="${esc(logQuery)}" placeholder="搜索日志" aria-label="搜索日志"></div><button class="icon-button" data-action="pause-logs" id="pauseLogs" title="暂停或继续日志" aria-label="暂停或继续日志">${icon(logPaused?'play':'pause')}</button><button class="button small" data-action="copy-logs">复制</button><button class="button small" data-action="export-logs">导出</button></div><div class="log-view" id="logView" role="log" aria-live="off">${logLines()}</div><div class="log-status"><span id="logCount">${filteredLogs().length} 条记录</span><span>最多保留最近 2,000 条</span></div></section>`;
}

function renderBackups() {
  return `${heading('存档备份','保存与恢复玩家及公会存档。',`<div class="page-actions"><button class="button" data-action="refresh-backups">${icon('refresh')}刷新</button><button class="button primary" data-action="create-backup" ${busy()||running()?'disabled':''}>${icon('plus')}创建备份</button></div>`)}${connectionPanel()}${readonlyBanner()}<div class="backup-overview"><div>${icon('shield')}<span>备份快照<strong>${backups.length} 份</strong></span></div><p>恢复前自动保存当前存档，方便回退。</p></div><section class="panel"><div class="panel-head"><h2>备份记录</h2><span class="muted">${backups.length?'按创建时间排列':'等待创建第一份备份'}</span></div>${backups.length?`<div class="table-scroll"><table class="backup-table"><thead><tr><th>存档快照</th><th>创建时间</th><th>大小</th><th></th></tr></thead><tbody>${backups.map(b=>`<tr><td><div class="backup-name">${icon('archive')}<span><strong>${esc(b.name||b.id)}</strong><small>${b.reason==='manual'?'手动备份':'操作前自动备份'}</small></span></div></td><td>${esc(dateTime(b.created))}</td><td>${esc(humanSize(b.size))}</td><td><button class="button small" data-action="restore-backup" data-id="${esc(b.id)}" ${busy()||running()?'disabled':''}>恢复</button></td></tr>`).join('')}</tbody></table></div>`:`<div class="empty-state">${icon('archive')}<strong>创建第一份存档快照</strong>保存现在的角色和公会进度。</div>`}</section>`;
}

function renderSettings() {
  const ports=settings.ports||{web:8080,admin:8081,game:9001,policy:843};
  return `${heading('设置','外观、连接与本地环境。')}${connectionPanel()}<div class="settings-layout"><section class="panel settings-group"><div class="panel-head"><h2>外观</h2></div><div class="setting-row"><div><h3>界面主题</h3><p>选择适合你的明暗外观</p></div><div class="theme-options">${[['light','sun','浅色'],['dark','moon','深色'],['auto','settings','跟随系统']].map(([v,i,label])=>`<button class="theme-option ${settings.theme===v?'active':''}" data-action="set-theme" data-theme="${v}">${icon(i)}${label}</button>`).join('')}</div></div><div class="setting-row"><div><h3>界面缩放</h3><p>调整文字和控件尺寸</p></div><select id="settingScale" class="field-control scale-select" aria-label="界面缩放">${[85,90,100,110,125,150].map(n=>`<option value="${n}" ${Number(settings.scale)===n?'selected':''}>${n}%</option>`).join('')}</select></div></section><section class="panel settings-group"><div class="panel-head"><h2>连接配置</h2><button class="button small" data-action="edit-ports">编辑端口</button></div><div class="setting-port-summary">${[['web','资源'],['admin','GM'],['game','游戏'],['policy','Flash 策略']].map(([key,label])=>`<div><span>${label}</span><strong>${esc(ports[key])}</strong><small>${key==='policy'?'固定端口':'可切换'}</small></div>`).join('')}</div><div class="setting-note">停止游戏和 GM 后可修改端口。新配置在下次服务启动时生效。</div></section><section class="panel settings-group"><div class="panel-head"><h2>本地环境</h2></div><div class="setting-row"><div><h3>游戏目录</h3><p class="path-text">${esc(appState.gamePath||settings.game_path||'由启动器自动定位')}</p></div>${icon('archive')}</div><div class="setting-row"><div><h3>运行环境</h3><p>${esc(appState.player||'Flash')} · PVZ OL 启动器 ${esc(appState.version||'2.0')}</p></div><span class="tag green">离线可用</span></div></section></div>`;
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
    if (id !== selectedId) return;
    if (page === 'accounts') $('#profilePanel').innerHTML = `<div class="empty-state">${icon('info')}<strong>角色资料读取失败</strong>${esc(e.message)}<br><button class="button small" data-action="reload-profile" style="margin-top:15px">重试</button></div>`;
    toast(e.message,'error');
  }
}
async function syncState(force = false) {
  while(stateRequest){await stateRequest;if(!force)return;}
  stateRequest=fetchState(force).finally(()=>{stateRequest=null;});
  await stateRequest;
}
async function fetchState(force = false) {
  try {
    const next = await api('/api/state');
    const changed = JSON.stringify({running:next.running,busy:next.busy,starting:next.starting,stopping:next.stopping,services:next.services,progress:next.progress,statusMessage:next.statusMessage,error:next.error,currentAccount:next.currentAccount}) !== JSON.stringify({running:appState.running,busy:appState.busy,starting:appState.starting,stopping:appState.stopping,services:appState.services,progress:appState.progress,statusMessage:appState.statusMessage,error:appState.error,currentAccount:appState.currentAccount});
    const wasRunning = running(), wasLocked = writesLocked(), reconnected = Boolean(connectionError);
    appState = next; connecting = false; connectionError = '';
    if (wasRunning && !running()) { adminUrl = ''; adminError = ''; }
    const lockChanged = wasLocked !== writesLocked();
    if (changed || force || reconnected) {
      updateChrome();
      if (page === 'home') render();
      if ((page === 'accounts' || page === 'backups') && (lockChanged || wasRunning !== running() || reconnected || force)) render();
      if (page === 'gm' && (wasRunning !== running() || lockChanged || force)) render();
      if (page === 'gm' && reconnected) $('.connection-error')?.remove();
      if (page === 'settings' && reconnected) render();
    }
    else updateChrome();
    if(page==='home' && $('#homeLive'))$('#homeLive').innerHTML=renderHomeLive();
    if(page==='services')refreshServiceLive();
  } catch (e) {
    const changed = connectionError !== e.message;
    connectionError = e.message; connecting = false; updateChrome();
    if (changed || force) {
      if (page === 'gm') { $('.connection-error')?.remove(); $('#main').insertAdjacentHTML('afterbegin',connectionPanel()); }
      else if(page==='services')refreshServiceLive();
      else render();
    }
  }
}
async function syncLogs() {
  if(logRequest)return logRequest;
  logRequest=fetchLogs().finally(()=>{logRequest=null;});
  await logRequest;
}
async function fetchLogs() {
  if (logPaused || demo) return;
  try {
    const data = await api(`/api/logs?after=${encodeURIComponent(lastLogId)}`);
    const incoming = data.entries || [];
    if (incoming.length) {
      const known = new Set(entries.map(e => String(e.id)));
      entries.push(...incoming.filter(e => !known.has(String(e.id))));
      entries = entries.slice(-2000);lastLogId = data.lastId ?? incoming.at(-1)?.id ?? lastLogId;
      if (page === 'services') renderLogsOnly();
      if (page === 'home' && $('#homeLive')) $('#homeLive').innerHTML=renderHomeLive();
    }
  } catch { /* State polling reports connection failures without a repeating toast. */ }
}
async function refreshAccounts() {
  accounts = normalizeAccounts(await api(accountsEndpoint()));
  const players = playerAccounts();
  if (!players.some(a => accountId(a) === selectedId)) selectedId = accountId(players.find(a => accountId(a) === accountId(appState.currentAccount)) || players[0]);
  updateChrome();
}
async function refreshBackups() { backups = normalizeBackups(await api('/api/backups')); if(page === 'backups') render(); }
async function startGame() {
  if (busy() || running()) return;
  if (!selectedId) {toast('请先创建或选择一个角色。','warn');location.hash = '#accounts';return;}
  localLaunchError='';appState.error='';localBusy = true; render();
  try {await api('/api/start',{method:'POST',body:{account_id:selectedId}});await syncState(true);toast('启动请求已提交，正在准备游戏服务。');}
  catch(e) {localLaunchError=e.message;appState.error=e.message;toast(e.message,'error');}
  finally {localBusy = false;render();}
}
async function stopGame() {
  localBusy = true; render();
  try {await api('/api/stop',{method:'POST',body:{}});adminUrl = '';await syncState(true);toast('停止请求已提交。');}
  catch(e) {toast(e.message,'error');}
  finally {localBusy = false;render();}
}
async function openAdmin(force = false) {
  if (demo || adminLoading) return;
  if (adminUrl && !force) {
    try { const info = await api('/api/admin-info'); if (info.ready) return; }
    catch(e) {adminError=e.message;toast(e.message,'error');return;}
    adminUrl = '';
  }
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
function clearProfileDraft() { profile=null;profileDirty=false;profileDraft=null;stagedAvatar='keep';stagedImage=''; }
async function selectAccount(id, discard = false) {
  if (profileSaving) {toast('资料正在保存，请稍候。','warn');return false;}
  if (id === selectedId) {if(page==='accounts' && !profile)await loadProfile();return true;}
  if (!playerAccounts().some(a => accountId(a) === id)) return false;
  if (profileDirty && !discard) {
    dialog('切换角色',`<p>当前资料尚未保存。切换到另一个角色将放弃这些修改。</p>`,`<button class="button primary" data-action="confirm-switch" data-id="${esc(id)}">切换角色</button>`);
    return false;
  }
  clearProfileDraft();selectedId=id;render();
  if(page==='accounts')await loadProfile();
  return true;
}
function newAccountDialog() {
  if(profileSaving){toast('资料正在保存，请稍候。','warn');return;}
  dialog('创建一个新角色',`<p>为你的新伙伴设置一个账号 UID。昵称和头像可在创建后修改。</p>${profileDirty ? '<p class="confirm-note">创建后将切换至新角色，当前未保存的资料修改会放弃。</p>' : ''}<label class="field"><span>账号 UID</span><input class="field-control" id="newUin" inputmode="numeric" pattern="[0-9]+" maxlength="20" placeholder="请输入数字账号" autofocus></label><p class="hint">每个角色使用独立账号，不覆盖已有角色。</p>`,`<button class="button primary" data-action="confirm-create">${icon('plus')}创建角色</button>`);
}
async function createAccount(button) {
  if(profileSaving){toast('资料正在保存，请稍候。','warn');return;}
  const uin = $('#newUin').value.trim();if (!/^\d+$/.test(uin)) {toast('UID 请使用数字。','warn');$('#newUin').focus();return;}
  button.disabled = true;
  try {const created = await api('/api/accounts',{method:'POST',body:{uin}});closeDialog();await refreshAccounts();clearProfileDraft();selectedId = accountId(created);render();await loadProfile();toast('新角色已创建。');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
function deleteAccountDialog() {
  const a = currentAccount();
  dialog('删除角色',`<p>将删除角色「${esc(accountName(a))}」及其角色存档。此操作会先保留一份备份。</p><p class="confirm-note">请输入 UID <strong>${esc(selectedId)}</strong>，确认要删除的角色。</p><label class="field"><span>确认角色 UID</span><input class="field-control" id="confirmDeleteUin" autocomplete="off" placeholder="${esc(selectedId)}"></label>`,`<button class="button danger" data-action="confirm-delete">${icon('trash')}删除角色</button>`);
}
async function deleteAccount(button) {
  const value = $('#confirmDeleteUin').value.trim();if (value !== selectedId) {toast('输入的 UID 与当前角色不一致。','warn');return;}
  button.disabled = true;
  try {await api(`/api/accounts/${encodeURIComponent(selectedId)}`,{method:'DELETE',body:{confirm_uin:value}});closeDialog();clearProfileDraft();await refreshAccounts();render();await loadProfile();await refreshBackups();toast('角色已删除。');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
async function saveProfile(form) {
  if (profileSaving) return;
  if (writesLocked() || demo) {toast(demo ? '当前为只读演示，不能修改角色资料。' : connectionError ? '本地服务尚未连接，请稍候重试。' : '请先停止游戏服务再修改资料。','warn');return;}
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
  try {await api(`/api/backups/${encodeURIComponent(id)}/restore`,{method:'POST',body:{confirm:true}});closeDialog();clearProfileDraft();await refreshBackups();await refreshAccounts();toast('存档已恢复，当前存档已自动备份。');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
async function saveSettings(patch) {
  const save = async () => {
    const previous = {...settings};settings={...settings,...patch};applyTheme();if(page==='settings')render();
    if (demo) {toast('演示外观已切换，仅在当前预览中生效。');return;}
    try {const next=await api('/api/settings',{method:'PATCH',body:patch});if(next)settings={...settings,...next};applyTheme();if(page==='settings')render();}
    catch(e) {settings=previous;applyTheme();if(page==='settings')render();toast(e.message,'error');}
  };
  settingsSaveQueue = settingsSaveQueue.then(save,save);
  return settingsSaveQueue;
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
    else if(action==='select-account') await selectAccount(target.dataset.id);
    else if(action==='confirm-switch') {closeDialog();await selectAccount(target.dataset.id,true);}
    else if(action==='new-account') newAccountDialog();
    else if(action==='confirm-create') await createAccount(target);
    else if(action==='delete-account') deleteAccountDialog();
    else if(action==='confirm-delete') await deleteAccount(target);
    else if(action==='reload-profile') await loadProfile();
    else if(action==='upload-avatar') $('#avatarUpload').click();
    else if(action==='choose-avatar') {stagedAvatar=Number(target.dataset.id);stagedImage='';profileDirty=true;document.querySelectorAll('.avatar-choice').forEach(el=>el.classList.toggle('selected',el===target));$('#profileAvatarPreview').innerHTML=avatar({name:accountName(profile),avatar_url:target.dataset.url});}
    else if(action==='confirm-crop') finishCrop();
    else if(action==='admin-accounts') {gmDashboard=true;gmContext={view:'list',uin:'',section:'attributes'};gmCurrentAccount=null;render();if(!adminUrl)await openAdmin();}
    else if(action==='edit-ports') portsDialog();
    else if(action==='save-ports') await savePorts(target);
    else if(action==='stop-for-ports') {await stopGame();closeDialog();portsDialog();}
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
  if(event.target.id==='accountSearch') {accountQuery=event.target.value;const selection=event.target.selectionStart;replaceAccountListPanel();const input=$('#accountSearch');input.focus();input.setSelectionRange(selection,selection);}
  if(event.target.id==='logSearch') {logQuery=event.target.value;renderLogsOnly();}
  if(event.target.id==='cropZoom') {crop.zoom=Number(event.target.value);drawCrop();}
  if(event.target.closest('#profileForm')) {profileDirty=true;const form=$('#profileForm');profileDraft={name:form.elements.name.value,signature:form.elements.signature.value};}
});
document.addEventListener('toggle',event=>{if(event.target.matches?.('[data-account-group]') && !accountQuery){const group=event.target.dataset.accountGroup;if(event.target.open)collapsedAccountGroups.delete(group);else collapsedAccountGroups.add(group);}},true);
document.addEventListener('change',async event=>{
  if(event.target.id==='showRobotAccounts') await toggleRobotAccounts(event.target.checked);
  if(event.target.id==='gmAccount') {const id=event.target.value;if(!playerAccounts().some(a=>accountId(a)===id))return;gmDashboard=false;gmContext={view:'player',uin:id,section:'attributes'};gmCurrentAccount=playerAccounts().find(a=>accountId(a)===id);render();}
  if(event.target.id==='homeAccount') {const id=event.target.value;event.target.value=selectedId;await selectAccount(id);}
  if(event.target.id==='settingScale') await saveSettings({scale:Number(event.target.value)});
  if(event.target.id==='avatarUpload') {beginCrop(event.target.files[0]);event.target.value='';}
});
document.addEventListener('submit',async event=>{if(event.target.id==='profileForm'){event.preventDefault();await saveProfile(event.target);}});
$('#themeToggle').addEventListener('click',()=>saveSettings({theme:document.documentElement.dataset.theme==='dark'?'light':'dark'}));
$('#appDialog').addEventListener('click',event=>{if(event.target===$('#appDialog')){const r=$('#appDialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
window.addEventListener('hashchange',navigate);
window.addEventListener('message',syncGMContext);
document.addEventListener('error',event=>{const img=event.target;if(img.tagName!=='IMG'||!img.closest('.avatar'))return;if(!img.dataset.fallback){img.dataset.fallback='1';const url=new URL(img.src,location.origin);img.src=new URL('/avatar/unknow.png',url.origin).toString();}else img.parentElement.innerHTML=icon('user');},true);
matchMedia('(prefers-color-scheme:dark)').addEventListener('change',()=>{if(settings.theme==='auto')applyTheme();});
window.addEventListener('beforeunload',event=>{if(profileDirty){event.preventDefault();event.returnValue='';}});

async function boot() {
  if(booting)return;booting=true;connecting=true;connectionError='';updateChrome();
  const results=await Promise.allSettled([api('/api/state'),api(accountsEndpoint()),api('/api/settings'),api('/api/logs?after=0'),api('/api/backups')]);
  if(results[0].status==='fulfilled')appState=results[0].value;
  else connectionError=results[0].reason.message;
  if(results[1].status==='fulfilled')accounts=normalizeAccounts(results[1].value);
  if(results[2].status==='fulfilled')settings={...settings,...results[2].value};
  if(results[3].status==='fulfilled'){entries=results[3].value.entries||[];lastLogId=results[3].value.lastId||0;}
  if(results[4].status==='fulfilled')backups=normalizeBackups(results[4].value);
  const players = playerAccounts();
  selectedId=selectedId && players.some(a=>accountId(a)===selectedId)?selectedId:accountId(players.find(a=>accountId(a)===accountId(appState.currentAccount)) || players[0]);
  connecting=false;applyTheme();render();booting=false;
  if(sessionStorage.getItem('pvz-ports-updated')){sessionStorage.removeItem('pvz-ports-updated');toast('连接端口已更新。');}
  if(page==='accounts' && !profileDirty)await loadProfile();
  if(page==='gm')await openAdmin();
  clearInterval(stateTimer);clearInterval(logTimer);
  if(!demo){stateTimer=setInterval(()=>syncState(),1500);logTimer=setInterval(()=>syncLogs(),1800);}
}
function uptime(value) {
  if (value === null || value === undefined) return '—';
  const seconds = Math.max(0,Math.floor(Number(value)||0));
  return `${Math.floor(seconds/3600).toString().padStart(2,'0')}:${Math.floor(seconds/60)%60<10?'0':''}${Math.floor(seconds/60)%60}:${(seconds%60).toString().padStart(2,'0')}`;
}

function memorySize(value) {
  return value === null || value === undefined ? '—' : `${(Number(value)/1048576).toFixed(1)} MB`;
}

function homeStatus() {
  if (connectionError) return {kind:'error',title:'连接异常',text:connectionError};
  if (busy()) return {kind:'pending',title:appState.stopping ? '正在停止' : '正在启动',text:appState.statusMessage || '正在准备本地服务…'};
  if (appState.error || localLaunchError) return {kind:'error',title:'启动失败',text:appState.error || localLaunchError};
  if (running()) return {kind:'running',title:'游戏运行中',text:'服务与播放器正在运行，关闭游戏窗口后自动停止。'};
  return {kind:'ready',title:'准备就绪',text:'选择角色，启动你的游戏。'};
}

function portRows() {
  const ports = settings.ports || {web:8080,admin:8081,game:9001,policy:843};
  const services = appState.services?.length ? appState.services : demoServices;
  return services.map(s => `<div class="port-row"><div class="port-service">${icon({web:'leaf',admin:'grid',game:'server',policy:'shield'}[s.id] || 'server')}<div><strong>${esc(s.name || s.id)}</strong><small>${s.id==='policy'?'Flash 固定端口':'127.0.0.1'}</small></div></div><span class="port-number">:${esc(s.port || ports[s.id])}</span>${statusTag(s.status)}<span class="port-owner">${s.pid ? `PID ${esc(s.pid)}${s.process_name ? ` · ${esc(s.process_name)}` : ''}` : s.occupied ? '端口已占用' : s.status==='checking'?'正在检测占用':'端口空闲'}</span></div>`).join('');
}

function startupFailure() {
  const error = appState.startup?.error || appState.error || localLaunchError;
  if (!error) return '';
  return `<section class="failure-panel" role="alert"><span class="failure-icon">${icon('info')}</span><div><h2>启动失败</h2><p>${esc(typeof error === 'string' ? error : error.message || JSON.stringify(error))}</p>${appState.startup?.action ? `<small>${esc(appState.startup.action)}</small>` : ''}<div class="failure-actions"><button class="button small" data-action="edit-ports">检查端口配置</button><a class="button small" href="#services">查看完整日志</a></div></div></section>`;
}

function renderHomeLive() {
  const runtime = appState.runtime || {};
  const services = appState.services || [];
  const active = services.filter(s=>s.owned || s.status==='running').length;
  const processes = runtime.processes || [];
  const recent = entries.slice(-4).reverse();
  return `${startupFailure()}<div class="overview-metrics"><div><span>运行内存</span><strong>${memorySize(runtime.memory_bytes)}</strong><small>${runtime.pending&&runtime.memory_bytes==null?'正在采样':runtime.memory_complete===false?'部分进程未能读取':'游戏与服务进程'}</small></div><div><span>运行时长</span><strong>${uptime(runtime.uptime_seconds)}</strong><small>${running()?'当前游戏会话':'等待启动游戏'}</small></div><div><span>服务连接</span><strong>${active}<em> / 4</em></strong><small>${services.some(s=>s.status==='conflict')?'存在端口冲突':'本地服务状态'}</small></div><div><span>运行进程</span><strong>${processes.filter(p=>['server','admin','game'].includes(p.role)).length}<em> 个</em></strong><small>${appState.player || 'Flash'} 播放器</small></div></div>
    <div class="dashboard-columns"><section class="panel"><div class="panel-head"><div><h2>本地服务与端口</h2><p>查看当前连接和端口占用</p></div><button class="button small" data-action="edit-ports">${icon('settings')}切换端口</button></div><div class="port-list">${portRows()}</div></section><section class="panel"><div class="panel-head"><div><h2>最近活动</h2><p>本次启动器会话</p></div><a class="subtle-link" href="#services">全部 ${icon('chevron')}</a></div><div class="activity-list">${recent.length ? recent.map(e=>`<div class="activity-row ${esc(String(e.level).toLowerCase())}"><i></i><div><p>${esc(e.message)}</p><time>${esc(shortTime(e.time))}</time></div></div>`).join('') : '<div class="empty-state">暂无运行记录</div>'}</div></section></div>`;
}

function gmToolbar() {
  const a=gmCurrentAccount || playerAccounts().find(x=>accountId(x)===gmContext.uin);
  return `<div class="gm-context">${gmDashboard?`<span class="context-icon">${icon('user')}</span><div><strong>账号管理</strong><small>选择角色进入管理</small></div>`:`${avatar(a,'sm')}<div><strong>${esc(a?accountName(a):'角色管理')}</strong><small>UID ${esc(gmContext.uin)}</small></div>`}</div><div class="gm-toolbar-actions"><button class="button small ${gmDashboard?'active':''}" data-action="admin-accounts">账号列表</button><select class="field-control gm-account-select" id="gmAccount" aria-label="GM 当前角色"><option value="" ${gmDashboard?'selected':''} disabled>切换角色</option>${playerAccounts().map(x=>`<option value="${esc(accountId(x))}" ${!gmDashboard&&accountId(x)===gmContext.uin?'selected':''}>${esc(a&&accountId(a)===accountId(x)?accountName(a):accountName(x))}</option>`).join('')}${a&&!playerAccounts().some(x=>accountId(x)===accountId(a))?`<option value="${esc(accountId(a))}" selected ${isRobotAccount(a)?'disabled':''}>${esc(accountName(a))}${isRobotAccount(a)?' · 机器人':''}</option>`:''}</select><button class="icon-button" data-action="reload-admin" title="刷新工作台" aria-label="刷新工作台">${icon('refresh')}</button>${adminUrl?`<a class="icon-button" href="${esc(adminFrameUrl())}" target="_blank" rel="noopener" title="独立窗口" aria-label="独立窗口">${icon('external')}</a>`:''}</div>`;
}

function processRows() {
  const items=appState.runtime?.processes||[];
  return items.length?`<div class="process-table"><div class="process-row process-header"><span>进程</span><span>PID</span><span>内存</span></div>${items.map(p=>`<div class="process-row"><span>${esc(p.title||p.name||p.label||p.role)}</span><span>${esc(p.pid)}</span><strong>${memorySize(p.memory_bytes)}</strong></div>`).join('')}</div>`:'<div class="empty-state compact">游戏与服务启动后显示进程内存。</div>';
}

function portsDialog() {
  const ports=settings.ports||{web:8080,admin:8081,game:9001,policy:843};
  dialog('连接端口',`<p>修改前请停止游戏和 GM 后台。已占用的端口无法保存。</p><div class="form-grid">${[['web','游戏资源'],['admin','GM 后台'],['game','游戏服务']].map(([key,label])=>`<label class="field"><span>${label}</span><input class="field-control" type="number" min="1" max="65535" step="1" id="port-${key}" value="${esc(ports[key])}" ${running()||busy()?'disabled':''}></label>`).join('')}<label class="field"><span>Flash 策略</span><input class="field-control" type="number" value="843" disabled><small>Flash 协议要求使用固定端口</small></label></div><div class="port-dialog-feedback" id="portFeedback" role="alert"></div>`,`<button class="button" data-action="stop-for-ports">${icon('stop')}停止服务</button><button class="button primary" data-action="save-ports" ${running()||busy()||demo?'disabled':''}>保存配置</button>`);
}

async function savePorts(button) {
  if(profileDirty){$('#portFeedback').textContent='请先保存角色资料，再切换端口。';return;}
  const values={policy:843};
  for(const key of ['web','admin','game']){const input=$(`#port-${key}`);if(!input.checkValidity()||!/^\d+$/.test(input.value)){input.reportValidity();return;}values[key]=Number(input.value);}
  if(new Set(Object.values(values)).size!==4){$('#portFeedback').textContent='各项服务端口不能重复。';return;}
  button.disabled=true;
  try{
    const result=await api('/api/settings',{method:'PATCH',body:{ports:values}});
    settings={...settings,...result};adminUrl='';adminError='';closeDialog();
    sessionStorage.setItem('pvz-ports-updated','1');location.reload();
  }catch(e){$('#portFeedback').textContent=e.message;button.disabled=false;}
}

function approvedGMRoute(value, origin, context) {
  if(typeof value!=='string'||value.length>3000)return '';
  try{
    const url=new URL(value,origin);
    if(url.origin!==origin||url.username||url.password||!['/','/player','/items'].includes(url.pathname))return '';
    url.searchParams.delete('msg');url.searchParams.delete('kind');
    const keys=new Set(['uin','section','q','item_kind','page','include_robots','embedded','theme']);
    for(const key of url.searchParams.keys())if(!keys.has(key)||url.searchParams.getAll(key).length!==1)return '';
    if(url.pathname==='/'){if(context.view!=='list')return '';}
    else if(context.view!=='player'||url.searchParams.get('uin')!==String(context.uin))return '';
    return url.pathname+url.search;
  }catch{return '';}
}

function syncGMContext(event) {
  const frame=$('#gmFrame');
  if(!frame||!adminUrl||event.source!==frame.contentWindow||event.origin!==new URL(adminUrl,location.origin).origin)return;
  const data=event.data;
  if(!data||data.type!=='pvz-gm-context'||!['list','player'].includes(data.view))return;
  if(data.view==='player'&&!/^\d{1,20}$/.test(data.uin||''))return;
  const sections=['attributes','inventory','plants','cards','progress','vip','friends','reset'];
  const imagePath=typeof data.avatar_url==='string'&&/^\/avatar\/(?:[0-9]+\.png|unknow1?\.png|custom\/[0-9]+_[a-f0-9]{64}\.(?:png|jpg|jpeg))$/.test(data.avatar_url)?data.avatar_url:'/avatar/unknow.png';
  gmDashboard=data.view==='list';gmContext={view:data.view,uin:gmDashboard?'':String(data.uin),section:sections.includes(data.section)?data.section:'attributes',route:approvedGMRoute(data.url,event.origin,data)};
  gmCurrentAccount=gmDashboard?null:{id:String(data.uin),uin:String(data.uin),name:String(data.name||'角色').slice(0,80),avatar_url:imagePath,is_robot:data.is_robot===true};
  updateChrome();const toolbar=$('#gmToolbar');if(toolbar)toolbar.innerHTML=gmToolbar();
}

render();boot();
