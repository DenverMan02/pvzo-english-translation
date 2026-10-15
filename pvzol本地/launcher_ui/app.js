'use strict';

/* Local launcher UI. All writes go through the authenticated Python API. */
const params = new URLSearchParams(location.search);
const demo = params.get('demo') === 'true';
const token = params.get('token') || sessionStorage.getItem('pvz-launcher-token') || '';
if (token) sessionStorage.setItem('pvz-launcher-token', token);
const $ = (s, root = document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = (name, cls = '') => `<svg${cls ? ` class="${esc(cls)}"` : ''} aria-hidden="true"><use href="#i-${name}"/></svg>`;
const pages = {home:'Home',accounts:'Accounts & Profiles',gm:'GM Management',services:'Services & Logs',backups:'Backups',settings:'Settings'};
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
let gmDashboard = false;
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
let settingsSaveQueue = Promise.resolve();

const demoAccounts = [
  {id:'10001',uin:'10001',name:'Sunflower Afternoon',signature:'Plant every ray of sunshine into your garden.',level:35,gold:128650,face_id:0},
  {id:'10002',uin:'10002',name:'Pea Guardian',signature:'Defend the garden today as well.',level:21,gold:6800,face_id:1},
  {id:'10003',uin:'10003',name:'New Year Friend',signature:'',level:8,gold:1200,face_id:2}
];
const demoServices = [
  {id:'web',name:'Game Resources',status:'stopped',detail:'Game pages and static assets',port:8080},
  {id:'admin',name:'GM Dashboard',status:'stopped',detail:'Profile and Game Management Dashboard',port:8081},
  {id:'game',name:'Game Service',status:'stopped',detail:'Player Data & Game Logic',port:9001},
  {id:'policy',name:'Connection Policy',status:'stopped',detail:'Waiting to start',port:843}
];
const demoLogs = [
  {id:1,time:'10:32:18',level:'INFO',message:'Garden Launcher is ready'},
  {id:2,time:'10:32:18',level:'INFO',message:'3 local profiles found, waiting for profile selection'},
  {id:3,time:'10:32:19',level:'INFO',message:'Four game services are not running. Click "Start Game" to enter the garden'},
  {id:4,time:'10:32:19',level:'INFO',message:'Read-only Demo Mode: UI preview only, game saves will not be connected or modified'}
];

function accountId(a) { return String(a?.id ?? a?.uin ?? a?.account_id ?? ''); }
function accountName(a) { return a?.name || a?.nickname || (a ? `Character ${accountId(a)}` : 'No profile selected'); }
function isRobotAccount(a) {
  const uin = Number(accountId(a));
  return a?.is_robot === true || (uin >= 900000 && uin < 1000000);
}
function playerAccounts() { return accounts.filter(a => !isRobotAccount(a)); }
function accountsEndpoint() { return `/api/accounts${showRobotAccounts ? '?include_robots=1' : ''}`; }
function currentAccount() { const players = playerAccounts(); return players.find(a => accountId(a) === selectedId) || players.find(a => accountId(a) === accountId(appState.currentAccount)) || players[0] || null; }
function launchAccount() { return running() ? appState.currentAccount || accounts.find(a => accountId(a) === String(appState.activeAccountId)) || null : currentAccount(); }
function busy() { return localBusy || appState.busy || appState.starting || appState.stopping; }
function running() { return Boolean(appState.running); }
function writesLocked() { return running() || busy() || Boolean(connectionError); }
function avatarSource(a) { return a?.avatar_url || a?.avatarUrl || a?.qq_face_url || a?.image_data || ''; }
function avatar(a, size = '') {
  const src = avatarSource(a);
  return `<span class="avatar${size ? ` avatar-${size}` : ''}">${src ? `<img src="${esc(src)}" alt="${esc(accountName(a))}'s avatar" loading="lazy">` : esc((accountName(a) || 'P').slice(0,1))}</span>`;
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
  const v = {running:['green','Running'],starting:['yellow','Starting'],stopping:['yellow','Stopping'],error:['red','Error'],stopped:['','Stopped'],ready:['green','Ready']}[status] || ['', 'Stopped'];
  return `<span class="tag ${v[0]}"><i></i>${v[1]}</span>`;
}
function toast(message, kind = 'success') {
  const div = document.createElement('div'); div.className = `toast ${kind}`;
  div.innerHTML = `${icon(kind === 'error' ? 'close' : kind === 'warn' ? 'info' : 'check')}<span>${esc(message)}</span>`;
  $('#toastRegion').append(div); setTimeout(() => div.remove(), 5000);
}
async function api(path, options = {}) {
  if (demo) {
    if (options.method && options.method !== 'GET') throw new Error('This is a read-only demo. Perform this action in the local launcher.');
    if (path === '/api/state') return {running:false,busy:false,currentAccount:demoAccounts[0],services:demoServices,statusMessage:'Services stopped'};
    if (path.startsWith('/api/accounts/')) { const a = demoAccounts.find(a => accountId(a) === path.split('/').pop()); return {...a,avatarChoices:[],profile_expected:{name:a.name,signature:a.signature,face_id:a.face_id,qq_face_url:''}}; }
    if (path.startsWith('/api/accounts')) return path.includes('include_robots=1') ? [...demoAccounts,{id:'900001',uin:'900001',name:'Garden Bot',level:30,is_robot:true,face_id:0}] : demoAccounts;
    if (path.startsWith('/api/logs')) return {entries:demoLogs,lastId:4};
    if (path.startsWith('/api/backups')) return [{id:'demo-backup-1',name:'Garden Save · Manual Backup',created:'2026-10-09T10:28:00+08:00',size:3489102,reason:'manual',files:['Player Save','Guild Data']},{id:'demo-backup-2',name:'Automatic pre-operation backup',created:'2026-10-08T20:41:00+08:00',size:3470176,reason:'before-reset',files:['Player Save','Guild Data']}];
    if (path.startsWith('/api/settings')) return settings;
    if (path.startsWith('/api/admin-info')) return {url:''};
    return null;
  }
  const headers = {'X-PVZ-Token':token,...options.headers};
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path,{...options,headers,body:options.body === undefined ? undefined : JSON.stringify(options.body),cache:'no-store'});
  let result;
  try { result = await response.json(); } catch { throw new Error(`The service returned unreadable content (HTTP ${response.status}).`); }
  if (!response.ok || result.ok === false) throw new Error(result.error?.message || result.error || result.message || `Operation failed (HTTP ${response.status})`);
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
  document.body.dataset.page = page;
  $('#breadcrumbPage').textContent = pages[page];
  document.querySelectorAll('[data-page]').forEach(el => {el.classList.toggle('active',el.dataset.page === page);el.setAttribute('aria-current',el.dataset.page === page ? 'page' : 'false');});
  $('#demoBadge').hidden = !demo;
  const connection = $('#connectionBadge');
  connection.className = `connection-badge ${connectionError ? 'error' : connecting ? '' : 'connected'}`;
  connection.innerHTML = `<i></i>${demo ? 'Demo mode' : connectionError ? 'Connection error' : connecting ? 'Connecting' : 'Connected locally'}`;
}
function heading(title, subtitle, actions = '') {
  return `<div class="page-heading" aria-label="${esc(title)}"><p>${subtitle}</p>${actions}</div>`;
}
function connectionPanel() { return connectionError ? `<div class="connection-error"><strong>Unable to connect to the launcher service</strong><p>${esc(connectionError)}</p><button class="button" data-action="reconnect">${icon('refresh')}Reconnect</button></div>` : ''; }
function readonlyBanner() { return demo ? `<div class="readonly-banner">${icon('info')}This is a read-only UI demo. Starting the game, editing characters, and managing saves are disabled.</div>` : busy() || running() ? `<div class="readonly-banner">${icon('shield')}${busy() ? 'Game services are starting or stopping. Wait before editing profiles or saves.' : 'Game services are running. Profiles, GM data, and save restoration are locked; stop the game services before making changes.'}</div>` : ''; }
function progressPanel() {
  if (!busy()) return '';
  const p = typeof appState.progress === 'object' ? Number(appState.progress?.percent || 0) : Number(appState.progress || 0);
  return `<div class="panel progress-panel"><span class="spinner"></span><div class="progress-content"><div class="progress-caption"><span>${esc(appState.statusMessage || 'Preparing game services…')}</span><small>${Math.max(0,Math.min(100,p))}%</small></div><div class="progress-track"><span style="width:${Math.max(3,Math.min(100,p))}%"></span></div></div><button class="button small" data-action="stop">Cancel</button></div>`;
}
function serviceCards(detail = false) {
  const services = appState.services?.length ? appState.services : demoServices;
  const array = Array.isArray(services) ? services : Object.entries(services).map(([id,v])=> typeof v === 'object' ? {id,...v} : {id,name:id,status:v});
  return `<div class="service-grid ${detail ? 'service-detail' : ''}">${array.map((s,i) => `<article class="panel service-card"><div class="service-top"><span class="service-icon">${icon(['shield','grid','server','refresh'][i%4])}</span>${statusTag(s.status)}</div><h3>${esc(s.name || s.id)}</h3><p title="${esc(s.detail || '')}">${esc(s.detail || (s.status === 'running' ? 'Service running normally' : 'Waiting to start'))}</p>${detail ? `<div class="port">${s.port ? `LOCAL · 127.0.0.1:${esc(s.port)}` : 'LOCAL SERVICE'}</div>` : ''}</article>`).join('')}</div>`;
}
function renderHome() {
  const a = launchAccount();
  const state = connectionError
    ? {kind:'error',icon:'info',title:'Connection error',text:'Reconnect to the local launcher service.'}
    : busy()
      ? {kind:'pending',icon:'refresh',title:'Processing',text:'Please wait while game services are being processed.'}
      : appState.error
        ? {kind:'error',icon:'info',title:'Problem starting',text:String(appState.error)}
        : running()
          ? {kind:'running',icon:'check',title:'Game running',text:'Services stop automatically when you close the game window. You can also stop the game from the system tray.'}
          : {kind:'ready',icon:'play',title:'Ready',text:'Select a character and click “Launch Game” to start playing.'};
  const locked = busy() || running();
  return `${connectionPanel()}${progressPanel()}
    <div class="home-layout">
      <section class="panel launch-card" aria-labelledby="launchTitle">
        <h2 id="launchTitle">Game Character</h2>
        <div class="launch-identity">
          ${avatar(a,'lg')}
          <h3>${esc(a ? accountName(a) : 'No characters yet')}</h3>
          <p>${a ? `UID ${esc(accountId(a))}` : 'Create a character to start playing.'}</p>
        </div>
        <div class="launch-controls">
          <label for="homeAccount">Select Character</label>
          <select class="field-control" id="homeAccount" ${locked || !playerAccounts().length ? 'disabled' : ''}>
            ${playerAccounts().length ? playerAccounts().map(x => `<option value="${esc(accountId(x))}" ${accountId(x) === accountId(a) ? 'selected' : ''}>${esc(accountName(x))} · ${esc(accountId(x))}</option>`).join('') : '<option value="">No characters yet</option>'}
          </select>
          ${a || running() ? `<button class="button primary launch-button" data-action="${running() ? 'stop' : 'start'}" ${busy() || connectionError ? 'disabled' : ''}>${icon(running() ? 'stop' : 'play')}${busy() ? 'Please wait…' : running() ? 'Stop Game' : 'Launch Game'}</button>` : `<button class="button primary launch-button" data-action="new-account" ${busy() || connectionError ? 'disabled' : ''}>${icon('plus')}New Character</button>`}
          <a class="launch-profile-link" href="#accounts">Accounts & Profile ${icon('chevron')}</a>
        </div>
      </section>
      <section class="panel session-card" aria-labelledby="sessionTitle">
        <h2 id="sessionTitle">Game Status</h2>
        <div class="session-summary ${state.kind}" role="status" aria-live="polite">
          <span class="session-icon">${icon(state.icon)}</span>
          <div><h3>${esc(state.title)}</h3><p>${esc(state.text)}</p></div>
        </div>
        <a class="subtle-link" href="#services">View Services & Logs ${icon('chevron')}</a>
      </section>
    </div>`;
}
function renderAccountGroup(title, items, group, robots = false) {
  if (!items.length) return '';
  return `<details class="account-group" data-account-group="${group}" ${accountQuery || !collapsedAccountGroups.has(group) ? 'open' : ''}>
    <summary><span>${title}<small>${items.length}</small></span>${icon('chevron')}</summary>
    <div class="account-group-items">${items.map(a => {
      const selected = accountId(a) === selectedId;
      const content = `${robots ? `<span class="avatar robot-avatar">${icon('server')}</span>` : avatar(a)}<span class="account-row-content"><strong title="${esc(accountName(a))}">${esc(accountName(a))}</strong><span class="account-row-tags"><small>UID ${esc(accountId(a))}</small>${a.level !== undefined ? `<small>Lv.${esc(a.level)}</small>` : ''}${robots ? '<small class="robot-tag">Bot</small>' : ''}</span></span><span class="account-row-end">${robots ? icon('shield') : selected ? icon('check') : icon('chevron')}</span>`;
      return robots ? `<div class="account-row robot-row" title="System bot account">${content}</div>` : `<button class="account-row ${selected ? 'selected' : ''}" data-action="select-account" data-id="${esc(accountId(a))}" aria-pressed="${selected}">${content}</button>`;
    }).join('')}</div></details>`;
}
function renderAccountListPanel() {
  const filtered = accounts.filter(a => (showRobotAccounts || !isRobotAccount(a)) && `${accountName(a)} ${accountId(a)}`.toLowerCase().includes(accountQuery.trim().toLowerCase()));
  const players = filtered.filter(a => !isRobotAccount(a));
  const robots = filtered.filter(isRobotAccount);
  return `<section class="panel account-list-panel"><div class="panel-head"><h2>Account Selection</h2><span class="tag">${playerAccounts().length} characters</span></div>
    <div class="account-search">${icon('search')}<input class="field-control" id="accountSearch" placeholder="Search by character name or UID" value="${esc(accountQuery)}" aria-label="Search characters"></div>
    <div class="account-list-options"><label class="robot-visibility"><input id="showRobotAccounts" type="checkbox" role="switch" ${showRobotAccounts ? 'checked' : ''} ${robotToggleLoading ? 'disabled' : ''}><span class="switch-track" aria-hidden="true"></span><span>Show bot accounts</span></label>${robotToggleLoading ? '<span class="spinner small"></span>' : ''}</div>
    <div class="account-list">${renderAccountGroup('My Characters',players,'players')}${showRobotAccounts ? renderAccountGroup('Bot accounts',robots,'robots',true) : ''}${!filtered.length ? `<div class="empty-state">${icon('search')}<strong>${accountQuery ? 'No matching accounts found' : 'No characters yet'}</strong>${accountQuery ? 'Try another name or UID.' : 'Click New Character in the top-right.'}</div>` : ''}</div>
    <div class="account-list-footer"><span>${accountQuery ? `${filtered.length} search results` : showRobotAccounts ? `Bot accounts: ${robots.length}` : 'Bot accounts hidden'}</span><span>Select a character to edit its profile</span></div></section>`;
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
  const actions = `<div class="page-actions"><button class="button primary" data-action="new-account" ${writesLocked() || profileSaving ? 'disabled' : ''}>${icon('plus')}New Character</button></div>`;
  return `${heading('Character Management','Manage character accounts, nicknames, signatures, and avatars.',actions)}${connectionPanel()}${readonlyBanner()}<div class="account-layout">${renderAccountListPanel()}<section id="profilePanel" class="panel">${renderProfile()}</section></div>`;
}
function renderProfile() {
  if (!selectedId || !accounts.length) return `<div class="empty-state">${icon('user')}<strong>Create your character</strong>After creating a character, set its nickname, signature, and avatar here.</div>`;
  if (!profile || accountId(profile) !== selectedId) return `<div class="loading"><span class="spinner"></span>Loading character profile…</div>`;
  const a = profile; const disabled = writesLocked() || demo || profileSaving;
  const choices = a.avatarChoices || [];
  return `<div class="profile-banner">${avatar(a,'lg')}<div><h2>${esc(accountName(a))}</h2><p>UID ${esc(accountId(a))} · Your Garden Identity</p></div><span class="tag green">Local character</span></div><form id="profileForm" class="profile-form"><div class="profile-avatar"><div id="profileAvatarPreview">${avatar(previewAccount(a))}</div><div class="avatar-action"><strong>Character Avatar</strong><button type="button" class="button" data-action="upload-avatar" ${disabled ? 'disabled' : ''}>${icon('upload')}Upload & Crop</button><p class="hint">PNG / JPG / WebP supported; saved as a square avatar</p></div></div>${choices.length ? `<div class="avatar-choices" role="group" aria-label="Choose a built-in avatar">${choices.map(v => `<button type="button" class="avatar-choice ${(stagedAvatar === 'keep' && !a.qq_face_url && Number(a.face_id) === Number(v.id)) || stagedAvatar === Number(v.id) ? 'selected' : ''}" data-action="choose-avatar" data-id="${esc(v.id)}" data-url="${esc(v.url)}" title="${esc(v.label)}" ${disabled ? 'disabled' : ''}><img src="${esc(v.url)}" alt="${esc(v.label)}"></button>`).join('')}</div>` : ''}<div class="form-grid"><label class="field"><span>Character Nickname</span><input class="field-control" name="name" value="${esc(profileDraft?.name ?? a.name ?? a.nickname ?? '')}" maxlength="7" required ${disabled ? 'disabled' : ''}><small>Nickname must be 2–7 characters, matching the in-game limit</small></label><label class="field"><span>Account UID</span><input class="field-control" value="${esc(accountId(a))}" disabled><small>UID cannot be changed after creation</small></label></div><label class="field"><span>Signature</span><input class="field-control" name="signature" maxlength="30" placeholder="Tell your garden story…" value="${esc(profileDraft?.signature ?? a.signature ?? '')}" ${disabled ? 'disabled' : ''}><small>Up to 30 characters</small></label><div class="form-actions"><button type="button" class="button" data-action="reload-profile" ${disabled ? 'disabled' : ''}>Reset Changes</button><button type="submit" class="button primary" ${disabled ? 'disabled' : ''}>${icon('check')}Save Profile</button></div></form><div class="profile-details"><div><small>Character Level</small><strong>${numeric(a.level)} Lv.</strong></div><div><small>Coin Balance</small><strong>${numeric(a.gold ?? a.money ?? a.coin)}</strong></div><div><small>Current Status</small><strong>${running() && accountId(appState.currentAccount) === selectedId ? 'Game running' : 'Saved'}</strong></div></div><div class="profile-footer"><span>The save is checked before applying profile changes to avoid overwriting updates made elsewhere.</span><button class="button danger small" data-action="delete-account" ${disabled ? 'disabled' : ''}>${icon('trash')}Delete Character</button></div>`;
}
function renderGM() {
  return `${heading('GM Admin Workbench','Manage character stats, plants, inventory, and adventure progress in one place.')}${connectionPanel()}${readonlyBanner()}<section class="panel gm-panel"><div class="gm-toolbar"><span class="tag green">Local Admin</span><button class="button small ${gmDashboard ? 'primary' : ''}" data-action="admin-accounts">${icon('user')}Account List</button><select class="field-control gm-account-select" id="gmAccount" aria-label="Current GM character">${gmDashboard ? '<option value="" selected disabled>Select a character to manage</option>' : ''}${playerAccounts().map(a=>`<option value="${esc(accountId(a))}" ${!gmDashboard && accountId(a)===selectedId?'selected':''}>${esc(accountName(a))}</option>`).join('')}</select><span class="url">${adminLoading ? 'Connecting to the workbench…' : adminUrl ? 'Workbench connected' : 'Waiting for the workbench connection'}</span><button class="icon-button" data-action="reload-admin" title="Reload admin panel" aria-label="Reload admin panel">${icon('refresh')}</button>${adminUrl ? `<a class="button small" href="${esc(adminFrameUrl())}" target="_blank" rel="noopener">${icon('external')}Open in separate window</a>` : ''}</div>${adminUrl && !demo ? `<iframe class="gm-frame" id="gmFrame" src="${esc(adminFrameUrl())}" title="GM Admin Workbench"></iframe>` : `<div class="gm-empty">${adminLoading ? '<span class="spinner"></span>' : icon('grid')}<h3>${demo ? 'GM Workbench integrated into the launcher' : adminLoading ? 'Connecting to the local admin panel' : 'Connect to GM Admin Workbench'}</h3><p>${esc(demo ? 'When running, the full local admin panel appears here, allowing you to manage characters, items, plant cards, tower-defense progress, and membership perks.' : adminError || 'The admin panel starts locally. The server locks all data editing while the game is running.')}</p>${!demo && !adminLoading ? `<button class="button primary" data-action="open-admin">${icon('grid')}Open Workbench</button>` : demo ? `<span class="tag yellow">Read-only UI demo</span>` : ''}</div>`}</section>`;
}
function adminFrameUrl() {
  if (!adminUrl) return '';
  const url = new URL(adminUrl,location.origin);
  if (gmDashboard) {url.pathname = '/';url.searchParams.delete('uin');url.searchParams.delete('section');}
  else if (selectedId) {url.pathname = '/player';url.searchParams.set('uin',selectedId);url.searchParams.set('section','attributes');}
  url.searchParams.set('embedded','1');
  url.searchParams.set('theme',document.documentElement.dataset.theme || 'light');
  return url.toString();
}
function filteredLogs() { return entries.filter(e => (logLevel === 'all' || String(e.level).toLowerCase() === logLevel || (logLevel === 'warn' && String(e.level).toLowerCase() === 'warning')) && `${e.message} ${e.level} ${e.time}`.toLowerCase().includes(logQuery.toLowerCase())); }
function logLines() { const lines = filteredLogs(); return lines.length ? lines.map(e=> `<div class="log-line ${String(e.level).toLowerCase()}"><span class="log-time">${esc(shortTime(e.time))}</span><span class="log-level">${esc(String(e.level || 'INFO').toUpperCase())}</span><span class="log-text">${esc(e.message)}</span></div>`).join('') : '<div class="log-line"><span class="log-text muted">No matching log entries. Waiting for service output, or adjust the filters.</span></div>'; }
function renderServices() {
  return `${heading('Services & Logs','View service status and live logs.',`<div class="page-actions"><button class="button ${running() ? 'danger' : 'primary'}" data-action="${running() ? 'stop' : 'start'}" ${busy() || !currentAccount() ? 'disabled' : ''}>${icon(running() ? 'stop' : 'play')}${running() ? 'Stop All Services' : 'Start Game Services'}</button><button class="button" data-action="refresh-state">${icon('refresh')}Refresh</button></div>`)}${connectionPanel()}${progressPanel()}${serviceCards(true)}<section class="panel log-panel"><div class="panel-head"><div><h2>Runtime Logs</h2><p>Live output from the launcher and local game services</p></div><span class="tag ${logPaused ? 'yellow' : 'green'}" id="logLiveBadge"><i></i>${logPaused ? 'Display paused' : 'Live updates'}</span></div><div class="log-toolbar"><div class="log-levels">${[['all','All'],['info','Info'],['warn','Warning'],['error','Error']].map(([v,label])=> `<button data-action="log-level" data-level="${v}" class="${logLevel === v ? 'active' : ''}">${label}</button>`).join('')}</div><div class="log-search">${icon('search')}<input class="field-control" id="logSearch" value="${esc(logQuery)}" placeholder="Search log messages" aria-label="Search logs"></div><button class="button small" data-action="pause-logs" id="pauseLogs">${icon(logPaused ? 'play' : 'pause')}${logPaused ? 'Resume' : 'Pause'}</button><button class="button small" data-action="copy-logs">${icon('copy')}Copy</button><button class="button small" data-action="export-logs">${icon('download')}Export</button></div><div class="log-view" id="logView" role="log" aria-live="off">${logLines()}</div><div class="log-status"><span id="logCount">${filteredLogs().length} entries · retaining the most recent 2,000</span><span>${demo ? 'Read-only demo entries' : 'Local time · auto-scroll to the latest entry'}</span></div></section>`;
}
function renderBackups() {
  return `${heading('Save Backups','Create and restore snapshots of player saves and guild data.')}${connectionPanel()}${readonlyBanner()}<section class="panel backup-summary"><span class="backup-summary-icon">${icon('shield')}</span><div><h2>Save Backups</h2><p>Backups include player saves and guild data. The current save is backed up before restoration.</p></div><button class="button primary" data-action="create-backup" ${busy() || running() ? 'disabled' : ''}>${icon('plus')}Create Backup</button></section><section class="panel"><div class="panel-head"><div><h2>Backup History</h2><p>${backups.length} snapshots, sorted by creation time</p></div><button class="subtle-link" data-action="refresh-backups">${icon('refresh')}Refresh List</button></div>${backups.length ? `<div style="overflow:auto"><table class="backup-table"><thead><tr><th>Save Snapshot</th><th>Created</th><th>Size</th><th>Action</th></tr></thead><tbody>${backups.map(b => `<tr><td><div class="backup-name">${icon('archive')}<span><strong>${esc(b.name || b.id)}</strong><small>${b.reason === 'manual' ? 'Manual backup' : b.reason === 'before-restore' ? 'Automatic pre-restore backup' : 'Automatic pre-operation backup'}${b.files?.length ? ` · ${b.files.length} data files` : ''}</small></span></div></td><td>${esc(dateTime(b.created))}</td><td>${esc(humanSize(b.size))}</td><td><button class="button" data-action="restore-backup" data-id="${esc(b.id)}" ${running() || busy() ? 'disabled' : ''}>${icon('refresh')}Restore</button></td></tr>`).join('')}</tbody></table></div>` : `<div class="empty-state">${icon('archive')}<strong>Your first save snapshot</strong>Click “Create Backup” to save your current garden.</div>`}</section><p class="hint">Restoring overwrites current player and guild data. Stop game services first.</p>`;
}
function renderSettings() {
  return `${heading('Settings','Adjust the theme and display size, and view the local environment.')}${connectionPanel()}${demo ? readonlyBanner() : ''}<div class="settings-layout"><section class="panel settings-group"><div class="panel-head"><h2>Appearance & Display</h2><span class="tag">Personalization</span></div><div class="setting-row"><div><h3>Interface Theme</h3><p>Light, dark, or follow the system.</p></div><div class="theme-options">${[['light','sun','Light'],['dark','moon','Dark'],['auto','settings','Follow system']].map(([v,i,label]) => `<button class="theme-option ${settings.theme === v ? 'active' : ''}" data-action="set-theme" data-theme="${v}">${icon(i)}${label}</button>`).join('')}</div></div><div class="setting-row"><div><h3>Interface Scale</h3><p>Adjust text and control sizes to fit your screen.</p></div><select id="settingScale" class="field-control scale-select" aria-label="Interface Scale">${[85,90,100,110,125,150].map(n=> `<option value="${n}" ${Number(settings.scale) === n ? 'selected' : ''}>${n}%${n===100 ? ' · Default' : ''}</option>`).join('')}</select></div></section><section class="panel settings-group"><div class="panel-head"><h2>Local Environment</h2><span class="tag green">Available offline</span></div><div class="setting-row"><div><h3>Game and Save Directory</h3><p>${esc(settings.game_path || appState.gamePath || 'Automatically detected by the launcher')}</p></div>${icon('archive')}</div><div class="setting-row"><div><h3>Save Write Protection</h3><p>Editing is locked while the game runs; the latest save is checked before writing.</p></div><span class="tag green">${icon('shield')}Enabled</span></div><div class="setting-row"><div><h3>Service Shutdown and Recovery</h3><p>Launcher-created game processes are cleaned up on shutdown, allowing repeated launches.</p></div><span class="tag green">Automatic</span></div></section><section class="panel about-panel"><span class="brand-mark"><i></i><i></i><i></i><i></i></span><div><h3>Plants vs. Zombies Online Launcher</h3><p>Local PvZ OL</p></div><span class="tag">${esc(appState.version || '2.0')}</span></section></div>`;
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
  view.innerHTML = logLines(); $('#logCount').textContent = `${filteredLogs().length} entries · retaining the most recent 2,000`;
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
    if (page === 'accounts') $('#profilePanel').innerHTML = `<div class="empty-state">${icon('info')}<strong>Failed to load character profile</strong>${esc(e.message)}<br><button class="button small" data-action="reload-profile" style="margin-top:15px">Retry</button></div>`;
    toast(e.message,'error');
  }
}
async function syncState(force = false) {
  try {
    const next = await api('/api/state');
    const changed = JSON.stringify({running:next.running,busy:next.busy,starting:next.starting,stopping:next.stopping,services:next.services,progress:next.progress,statusMessage:next.statusMessage,error:next.error,currentAccount:next.currentAccount}) !== JSON.stringify({running:appState.running,busy:appState.busy,starting:appState.starting,stopping:appState.stopping,services:appState.services,progress:appState.progress,statusMessage:appState.statusMessage,error:appState.error,currentAccount:appState.currentAccount});
    const wasRunning = running(), wasLocked = writesLocked(), reconnected = Boolean(connectionError);
    appState = next; connecting = false; connectionError = '';
    if (wasRunning && !running()) { adminUrl = ''; adminError = ''; }
    const lockChanged = wasLocked !== writesLocked();
    if (changed || force || reconnected) {
      updateChrome();
      if (page === 'home' || page === 'services') render();
      if ((page === 'accounts' || page === 'backups') && (lockChanged || wasRunning !== running() || reconnected || force)) render();
      if (page === 'gm' && (wasRunning !== running() || lockChanged || force)) render();
      if (page === 'gm' && reconnected) $('.connection-error')?.remove();
      if (page === 'settings' && reconnected) render();
    }
    else updateChrome();
  } catch (e) {
    const changed = connectionError !== e.message;
    connectionError = e.message; connecting = false; updateChrome();
    if (changed || force) {
      if (page === 'gm') { $('.connection-error')?.remove(); $('#main').insertAdjacentHTML('afterbegin',connectionPanel()); }
      else render();
    }
  }
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
  accounts = normalizeAccounts(await api(accountsEndpoint()));
  const players = playerAccounts();
  if (!players.some(a => accountId(a) === selectedId)) selectedId = accountId(players.find(a => accountId(a) === accountId(appState.currentAccount)) || players[0]);
  updateChrome();
}
async function refreshBackups() { backups = normalizeBackups(await api('/api/backups')); if(page === 'backups') render(); }
async function startGame() {
  if (busy() || running()) return;
  if (!selectedId) {toast('Create or select a character first.','warn');location.hash = '#accounts';return;}
  localBusy = true; render();
  try {await api('/api/start',{method:'POST',body:{account_id:selectedId}});await syncState(true);toast('Launch request submitted. Preparing game services.');}
  catch(e) {toast(e.message,'error');}
  finally {localBusy = false;render();}
}
async function stopGame() {
  localBusy = true; render();
  try {await api('/api/stop',{method:'POST',body:{}});adminUrl = '';await syncState(true);toast('Stop request submitted.');}
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
  try { const result = await api('/api/admin/start',{method:'POST',body:{}}); adminUrl = result.url || '';if(!adminUrl) throw new Error('The admin service returned no URL. Check the service logs.');}
  catch(e) {adminError = e.message;toast(e.message,'error');}
  finally {adminLoading = false;if(page === 'gm') render();}
}
function dialog(title, content, actions) {
  $('#dialogBody').innerHTML = `<div class="dialog-heading"><h2>${title}</h2><button class="icon-button" data-action="close-dialog" aria-label="Close dialog">${icon('close')}</button></div><div class="dialog-content">${content}</div><div class="dialog-actions"><button class="button" data-action="close-dialog">Cancel</button>${actions}</div>`;
  $('#appDialog').showModal();
}
function closeDialog() { $('#appDialog').close(); }
function clearProfileDraft() { profile=null;profileDirty=false;profileDraft=null;stagedAvatar='keep';stagedImage=''; }
async function selectAccount(id, discard = false) {
  if (profileSaving) {toast('Profile is being saved. Please wait.','warn');return false;}
  if (id === selectedId) {if(page==='accounts' && !profile)await loadProfile();return true;}
  if (!playerAccounts().some(a => accountId(a) === id)) return false;
  if (profileDirty && !discard) {
    dialog('Switch Character',`<p>Your current profile has unsaved changes. Switching characters will discard them.</p>`,`<button class="button primary" data-action="confirm-switch" data-id="${esc(id)}">Switch Character</button>`);
    return false;
  }
  clearProfileDraft();selectedId=id;render();
  if(page==='accounts')await loadProfile();
  return true;
}
function newAccountDialog() {
  if(profileSaving){toast('Profile is being saved. Please wait.','warn');return;}
  dialog('Create a New Character',`<p>Choose an account UID for your new character. You can change its nickname and avatar after creation.</p>${profileDirty ? '<p class="confirm-note">You will switch to the new character after creation. Unsaved profile changes will be discarded.</p>' : ''}<label class="field"><span>Account UID</span><input class="field-control" id="newUin" inputmode="numeric" pattern="[0-9]+" maxlength="20" placeholder="Enter a numeric UID" autofocus></label><p class="hint">Each character has its own UID; existing characters will not be overwritten.</p>`,`<button class="button primary" data-action="confirm-create">${icon('plus')}Create Character</button>`);
}
async function createAccount(button) {
  if(profileSaving){toast('Profile is being saved. Please wait.','warn');return;}
  const uin = $('#newUin').value.trim();if (!/^\d+$/.test(uin)) {toast('UID must contain digits only.','warn');$('#newUin').focus();return;}
  button.disabled = true;
  try {const created = await api('/api/accounts',{method:'POST',body:{uin}});closeDialog();await refreshAccounts();clearProfileDraft();selectedId = accountId(created);render();await loadProfile();toast('New character created.');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
function deleteAccountDialog() {
  const a = currentAccount();
  dialog('Delete Character',`<p>This will delete character “${esc(accountName(a))}” and its save. A backup will be kept first.</p><p class="confirm-note">Enter UID <strong>${esc(selectedId)}</strong> to confirm deleting this character.</p><label class="field"><span>Confirm Character UID</span><input class="field-control" id="confirmDeleteUin" autocomplete="off" placeholder="${esc(selectedId)}"></label>`,`<button class="button danger" data-action="confirm-delete">${icon('trash')}Delete Character</button>`);
}
async function deleteAccount(button) {
  const value = $('#confirmDeleteUin').value.trim();if (value !== selectedId) {toast('The entered UID does not match the selected character.','warn');return;}
  button.disabled = true;
  try {await api(`/api/accounts/${encodeURIComponent(selectedId)}`,{method:'DELETE',body:{confirm_uin:value}});closeDialog();clearProfileDraft();await refreshAccounts();render();await loadProfile();await refreshBackups();toast('Character deleted.');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
async function saveProfile(form) {
  if (profileSaving) return;
  if (writesLocked() || demo) {toast(demo ? 'This read-only demo cannot edit character profiles.' : connectionError ? 'The local service is not connected yet. Please try again shortly.' : 'Stop game services before editing the profile.','warn');return;}
  const button = $('button[type=submit]',form);button.disabled = true;
  const body = {name:form.elements.name.value.trim(),signature:form.elements.signature.value,avatar:stagedAvatar,profile_expected:profile.profile_expected};
  if (stagedImage) body.image_data = stagedImage;
  if (!body.name) {toast('Enter a character nickname.','warn');button.disabled = false;return;}
  if(body.name !== (profile.name || profile.nickname || '') && (body.name.length<2 || body.name.length>7)) {toast('Nickname must be 2–7 characters.','warn');button.disabled=false;return;}
  profileSaving = true;
  form.querySelectorAll('input,textarea,button').forEach(control=>control.disabled=true);
  try {await api(`/api/accounts/${encodeURIComponent(selectedId)}`,{method:'PATCH',body});await refreshAccounts();await loadProfile();toast('Character profile saved.');}
  catch(e) {toast(e.message,'error');}
  finally {profileSaving=false;if(page==='accounts')render();}
}
function beginCrop(file) {
  if (!file) return;
  if (file.size > 15*1024*1024) {toast('Use an avatar image smaller than 15 MB.','warn');return;}
  const reader = new FileReader();
  reader.onerror = ()=>toast('Could not read the image. Try another one.','error');
  reader.onload = ()=>{const image = new Image();image.onerror = ()=>toast('This image could not be recognized.','error');image.onload = ()=>{cropImage = image;crop = {zoom:1,x:0,y:0};dialog('Crop Your Avatar',`<p>Drag the image to position it, then use the slider to zoom. The avatar will be saved as a square.</p><canvas id="cropCanvas" class="crop-preview" width="480" height="480" aria-label="Avatar crop preview"></canvas><label class="crop-control"><span>Zoom</span><input type="range" id="cropZoom" min="1" max="3" value="1" step="0.01"></label><p class="hint" style="text-align:center">Drag the image to adjust the crop area</p>`,`<button class="button primary" data-action="confirm-crop">${icon('check')}Use Avatar</button>`);drawCrop();installCropDrag();};image.src = reader.result;};
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
  try {await api('/api/backups',{method:'POST',body:{reason:'manual'}});await refreshBackups();toast('Full save backup created.');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
function restoreDialog(id) {
  const b = backups.find(x => String(x.id) === id);if(!b)return;
  dialog('Restore Save Snapshot',`<p>Restore “${esc(b.name || b.id)}”, overwriting the current player save and guild data.</p><p class="confirm-note">The current save will be backed up first. Confirm that this is the garden you want to return to.</p><p>Snapshot time: ${esc(dateTime(b.created))}</p>`,`<button class="button primary" data-action="confirm-restore" data-id="${esc(id)}">${icon('refresh')}Confirm Restore</button>`);
}
async function restoreBackup(id,button) {
  button.disabled = true;
  try {await api(`/api/backups/${encodeURIComponent(id)}/restore`,{method:'POST',body:{confirm:true}});closeDialog();clearProfileDraft();await refreshBackups();await refreshAccounts();toast('Save restored. The previous save was backed up automatically.');}
  catch(e) {toast(e.message,'error');button.disabled = false;}
}
async function saveSettings(patch) {
  const save = async () => {
    const previous = {...settings};settings={...settings,...patch};applyTheme();if(page==='settings')render();
    if (demo) {toast('Demo appearance changed. This only affects the current preview.');return;}
    try {const next=await api('/api/settings',{method:'PATCH',body:patch});if(next)settings={...settings,...next};applyTheme();if(page==='settings')render();}
    catch(e) {settings=previous;applyTheme();if(page==='settings')render();toast(e.message,'error');}
  };
  settingsSaveQueue = settingsSaveQueue.then(save,save);
  return settingsSaveQueue;
}
async function copyLogs() {
  const text=filteredLogs().map(e=>`[${shortTime(e.time)}] [${String(e.level||'INFO').toUpperCase()}] ${e.message}`).join('\n');
  try {await navigator.clipboard.writeText(text);toast('Filtered logs copied.');}
  catch {const textarea=document.createElement('textarea');textarea.value=text;document.body.append(textarea);textarea.select();const ok=document.execCommand('copy');textarea.remove();toast(ok?'Logs copied.':'Copy failed. Use Export Logs instead.',ok?'success':'error');}
}
async function exportLogs() {
  if(demo) {toast('The read-only demo cannot export real runtime logs.','warn');return;}
  try {const response=await fetch('/api/logs/export',{headers:{'X-PVZ-Token':token},cache:'no-store'});if(!response.ok)throw new Error('Log export failed.');const blob=await response.blob();const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=`PVZ-Run-Logs-${new Date().toISOString().slice(0,10)}.txt`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('Runtime logs exported.');}
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
    else if(action==='admin-accounts') {gmDashboard=true;render();if(!adminUrl)await openAdmin();}
    else if(action==='open-admin' || action==='reload-admin') await openAdmin(true);
    else if(action==='log-level') {logLevel=target.dataset.level;document.querySelectorAll('[data-action=log-level]').forEach(el=>el.classList.toggle('active',el===target));renderLogsOnly();}
    else if(action==='pause-logs') {logPaused=!logPaused;target.innerHTML=`${icon(logPaused?'play':'pause')}${logPaused?'Resume':'Pause'}`;$('#logLiveBadge').className=`tag ${logPaused?'yellow':'green'}`;$('#logLiveBadge').innerHTML=`<i></i>${logPaused?'Display paused':'Live updates'}`;if(!logPaused)await syncLogs();}
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
  if(event.target.id==='gmAccount' || event.target.id==='homeAccount') {const isGM=event.target.id==='gmAccount';const id=event.target.value;event.target.value=selectedId;if(isGM)gmDashboard=false;const accepted=await selectAccount(id);if(isGM && accepted)render();}
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
  if(page==='accounts' && !profileDirty)await loadProfile();
  if(page==='gm')await openAdmin();
  clearInterval(stateTimer);clearInterval(logTimer);
  if(!demo){stateTimer=setInterval(()=>syncState(),1500);logTimer=setInterval(()=>syncLogs(),1800);}
}
render();boot();
