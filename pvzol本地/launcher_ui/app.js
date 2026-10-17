"use strict";

/* PVZ OL 本地启动器界面。所有写操作都经由带令牌的本地 Python API（launcher_desktop.py）。
 * 结构：工具函数 → 状态 → 外壳（侧栏/顶栏/横幅）→ 各页面渲染 → 数据同步 → 操作 → 事件绑定 → 启动。 */
const params = new URLSearchParams(location.search);
const demo = false;
const token = params.get("token") || sessionStorage.getItem("pvz-launcher-token") || "";
if (token) sessionStorage.setItem("pvz-launcher-token", token);

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const icon = (name, cls = "") => `<svg${cls ? ` class="${esc(cls)}"` : ""} aria-hidden="true"><use href="#i-${name}"/></svg>`;
const dis = (flag) => (flag ? " disabled" : "");

const pages = { home: "首页", accounts: "账号与资料", gm: "GM 管理", services: "服务与日志", backups: "存档备份", settings: "设置" };
const pageOrder = Object.keys(pages);
let page = (location.hash || "#home").slice(1);
if (!pages[page]) page = "home";

/* ---------- 状态 ---------- */
let appState = { running: false, busy: false, services: [], currentAccount: null };
let accounts = [];
let selectedId = "";
let profile = null;
let profileError = "";
let settings = { theme: "light", scale: 100 };
let entries = [];
let lastLogId = 0;
let logCapacity = 1800;
let logLevel = "all";
let logSource = "all";
let logQuery = "";
let logPaused = false;
let logFollow = true;
let pendingWhilePaused = 0;
let backups = [];
let backupsLoaded = false;
let backupError = "";
let backupFilter = "all";
let backupPage = 1;
let accountQuery = "";
let showRobotAccounts = false;
let robotToggleLoading = false;
const collapsedAccountGroups = new Set();
let adminUrl = "";
let gmView = "player"; // dashboard | player | items（启动器最近一次让 GM 打开的视图）
let gmFrameHref = ""; // 内嵌 GM 页面自己上报的当前地址（路径+查询）；在 GM 内跳转后，工具栏、刷新与“浏览器打开”都以它为准
let adminLoading = false;
let adminError = "";
let connecting = true;
let connectionError = "";
let localBusy = false;
let playerSaving = false;
let stagedAvatar = "keep";
let stagedImage = "";
let profileDirty = false;
let profileDraft = null;
let profileSaving = false;
let cropImage = null;
let crop = { zoom: 1, x: 0, y: 0 };
let stateTimer = null;
let logTimer = null;
let overviewTimer = null;
let stateRequestId = 0;
let statePollPending = false;
let booting = false;
let settingsSaveQueue = Promise.resolve();
let overview = null; // GET /api/services
let overviewError = "";
let stopFlow = null; // 停止服务弹窗：{ phase, selected:Set, results, error, ok }
let renderedSignature = "";

const BACKUP_PAGE_SIZE = 12;
const LOG_LEVELS = [["all", "全部"], ["error", "错误"], ["warning", "警告"], ["info", "信息"], ["debug", "调试"]];
const LOG_SOURCES = [["all", "全部来源"], ["launcher", "启动器"], ["game", "游戏逻辑"], ["web", "资源网页"], ["admin", "GM 后台"], ["policy", "连接策略"]];
const SOURCE_LABEL = Object.fromEntries(LOG_SOURCES);
const LEVEL_LABEL = { error: "ERROR", warning: "WARN", info: "INFO", debug: "DEBUG" };
const SCALES = [85, 90, 100, 110, 125, 150];
const defaultServices = [
  { id: "web", name: "游戏资源", status: "stopped", port: 8080 },
  { id: "admin", name: "GM 后台", status: "stopped", port: 8081 },
  { id: "game", name: "游戏服务", status: "stopped", port: 9001 },
  { id: "policy", name: "连接策略", status: "stopped", port: 843 },
];

const DEMO_READONLY_MESSAGE = "当前为只读演示，不连接真实后台，也不会启动、停止服务或修改存档。";

/* ---------- 工具 ---------- */
function accountId(a) {
  return String(a?.id ?? a?.uin ?? a?.account_id ?? "");
}
function accountName(a) {
  return a?.name || a?.nickname || (a ? `角色 ${accountId(a)}` : "尚未选择账号");
}
function isRobotAccount(a) {
  const uin = Number(accountId(a));
  return a?.is_robot === true || (uin >= 900000 && uin < 1000000);
}
function playerAccounts() {
  return accounts.filter((a) => !isRobotAccount(a));
}
function accountsEndpoint() {
  return `/api/accounts${showRobotAccounts ? "?include_robots=1" : ""}`;
}
function findAccount(id) {
  return playerAccounts().find((a) => accountId(a) === String(id)) || null;
}
function currentAccount() {
  const players = playerAccounts();
  return (
    players.find((a) => accountId(a) === selectedId) ||
    players.find((a) => accountId(a) === accountId(appState.currentAccount)) ||
    players[0] ||
    null
  );
}
function launchAccount() {
  return running()
    ? appState.currentAccount || findAccount(appState.activeAccountId) || null
    : currentAccount();
}
function units() {
  const u = appState.units || {};
  return { services: Boolean(u.services ?? appState.running), player: Boolean(u.player ?? appState.playerRunning), admin: Boolean(u.admin ?? appState.adminRunning) };
}
// The account the running services were started with: "in game" only while the window is open.
function activeLabel() {
  return units().player ? "游戏中" : "运行中";
}
function anyUnitRunning() {
  const u = units();
  return u.services || u.player || u.admin;
}
function busy() {
  return localBusy || playerSaving || appState.busy || appState.starting || appState.stopping;
}
function running() {
  return Boolean(appState.running);
}
function writesLocked() {
  return demo || running() || busy() || Boolean(connectionError);
}
function lockReason() {
  if (demo) return DEMO_READONLY_MESSAGE;
  if (connectionError) return "本地服务未连接，暂时不能修改。";
  if (busy()) return "游戏服务正在启动或停止，请稍候。";
  if (running()) return "游戏服务运行中，资料与存档已锁定，停止服务后可修改。";
  return "";
}
function playerKind() {
  return appState.playerKind === "ruffle" ? "ruffle" : "flash";
}
function playerOptions() {
  const options = Array.isArray(appState.playerOptions) ? appState.playerOptions : [];
  return [["flash", "Flash"], ["ruffle", "Ruffle"]].map(([id, name]) => {
    const value = options.find((option) => option.id === id) || {};
    return { ...value, id, name, available: value.available === true };
  });
}
function selectedPlayer() {
  return playerOptions().find((option) => option.id === playerKind());
}
function playerReady() {
  return selectedPlayer().available;
}
function playerProblem() {
  return selectedPlayer().detail || (connecting ? "正在检查播放器文件…" : `未找到 ${selectedPlayer().name} 播放器文件，请补齐后重新打开启动器。`);
}
function avatarSource(a) {
  return a?.avatar_url || a?.avatarUrl || a?.qq_face_url || a?.image_data || "";
}
function avatar(a, size = "") {
  const src = avatarSource(a);
  const cls = `avatar${size ? ` ${size}` : ""}`;
  return `<span class="${cls}">${src ? `<img src="${esc(src)}" alt="" loading="lazy">` : esc((accountName(a) || "P").slice(0, 1))}</span>`;
}
function previewAccount(a) {
  const choice = typeof stagedAvatar === "number" ? (a.avatarChoices || []).find((v) => Number(v.id) === stagedAvatar) : null;
  return stagedImage ? { ...a, avatar_url: stagedImage } : choice ? { ...a, avatar_url: choice.url } : a;
}
function numeric(n) {
  return Number.isFinite(Number(n)) && n !== null && n !== "" ? Number(n).toLocaleString("zh-CN") : "—";
}
function humanSize(n) {
  if (typeof n === "string" && !/^\d+$/.test(n)) return n;
  const b = Number(n || 0);
  return b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : b >= 1024 ? `${(b / 1024).toFixed(1)} KB` : `${b} B`;
}
function shortTime(v) {
  if (!v) return "";
  if (/^\d\d:\d\d/.test(v)) return String(v).slice(0, 8);
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}
function dateTime(v) {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}
function relativeTime(v) {
  const d = new Date(v);
  if (!v || isNaN(d)) return "";
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} 天前`;
  return dateTime(v);
}
function duration(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h} 小时 ${m % 60} 分` : `${Math.floor(h / 24)} 天 ${h % 24} 小时`;
}
function uptimeSeconds() {
  return appState.startedAt && appState.serverTime ? appState.serverTime - appState.startedAt : null;
}
function backupReason(reason) {
  const r = String(reason || "");
  if (r === "manual") return "手动备份";
  if (r === "before-restore") return "恢复前自动备份";
  if (r.startsWith("before-delete")) return `删除账号${r.split("-")[2] ? ` ${r.split("-")[2]}` : ""}前自动备份`;
  return "操作前自动备份";
}
function loginTime(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return dateTime(v);
  return n > 0 ? dateTime(n > 1e11 ? n : n * 1000) : "—";
}
function chip(text, kind = "", dot = false) {
  return `<span class="chip${kind ? ` ${kind}` : ""}">${dot ? "<i class=\"dot\"></i>" : ""}${esc(text)}</span>`;
}
function toast(message, kind = "success") {
  const div = document.createElement("div");
  div.className = `toast ${kind}`;
  div.innerHTML = `${icon(kind === "error" ? "alert" : kind === "warn" ? "info" : "check")}<span>${esc(message)}</span>`;
  $("#toastRegion").append(div);
  setTimeout(() => div.remove(), kind === "error" ? 7000 : 4500);
}
const OFFLINE_MESSAGE = "无法连接本地启动器服务，请确认启动器仍在运行。";
async function api(path, options = {}) {
  const headers = { "X-PVZ-Token": token, ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
    });
  } catch {
    throw new Error(OFFLINE_MESSAGE);
  }
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error(`服务返回了无法读取的内容（HTTP ${response.status}）。`);
  }
  if (!response.ok || result.ok === false)
    throw new Error(result.error?.message || result.error || result.message || `操作失败（HTTP ${response.status}）`);
  return result.data === undefined ? result : result.data;
}
function normalizeAccounts(result) {
  return Array.isArray(result) ? result : result?.accounts || result?.items || [];
}
function normalizeBackups(result) {
  return Array.isArray(result) ? result : result?.backups || result?.items || [];
}
function serviceList() {
  const services = appState.services?.length ? appState.services : defaultServices;
  return Array.isArray(services)
    ? services
    : Object.entries(services).map(([id, v]) => (typeof v === "object" ? { id, ...v } : { id, name: id, status: v }));
}

/* ---------- 主题、缩放与窗口尺寸 ---------- */
function scale() {
  return Number(settings.scale || 100) / 100;
}
function applyLayoutSize() {
  // body 使用 CSS zoom 缩放：按“视口 ÷ 缩放”换算有效尺寸，再决定布局档位。
  const s = scale();
  const root = document.documentElement;
  const width = innerWidth / s;
  const height = innerHeight / s;
  root.style.setProperty("--app-h", `${height}px`);
  root.dataset.size = width >= 1180 ? "wide" : width >= 940 ? "medium" : "compact";
  if (width < 700) root.dataset.narrow = "";
  else delete root.dataset.narrow;
  if (height < 560) root.dataset.short = "";
  else delete root.dataset.short;
}
function applyTheme() {
  const dark = settings.theme === "dark" || (settings.theme === "auto" && matchMedia("(prefers-color-scheme:dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.style.setProperty("--scale", String(scale()));
  applyLayoutSize();
  $("#themeToggle").innerHTML = icon(dark ? "moon" : "sun");
  const frame = $("#gmFrame");
  if (frame && adminUrl) {
    try {
      if (new URL(frame.src).searchParams.get("theme") !== document.documentElement.dataset.theme) frame.src = adminFrameUrl();
    } catch {
      /* 地址异常时保持现状 */
    }
  }
}

/* ---------- 外壳：侧栏、顶栏、全局横幅 ---------- */
function globalStatus() {
  const u = units();
  if (connectionError) return { kind: "error", text: "连接异常", title: connectionError };
  if (connecting) return { kind: "", text: "正在连接…", title: "正在连接本地启动器服务" };
  if (appState.stopping || /停止/.test(appState.statusMessage || "") && busy()) return { kind: "warn", text: "正在停止…", title: appState.statusMessage || "" };
  if (busy() && !playerSaving) {
    const p = progressPercent();
    return { kind: "warn", text: `正在启动 ${p}%`, title: appState.statusMessage || "正在准备游戏服务" };
  }
  if (appState.error && !running()) return { kind: "error", text: "启动未完成", title: String(appState.error) };
  if (u.services && running()) {
    const a = launchAccount();
    return u.player
      ? { kind: "ok", text: `游戏运行中${a ? ` · ${accountName(a)}` : ""}`, title: "游戏服务组与游戏窗口正在运行" }
      : { kind: "ok", text: "服务运行中 · 游戏窗口未打开", title: "游戏服务组仍在运行，可重新打开游戏窗口" };
  }
  if (u.admin) return { kind: "ok", text: "GM 后台运行中", title: "独立 GM 后台正在运行（未启动游戏）" };
  return { kind: "", text: "未启动", title: "游戏服务未运行" };
}
function progressPercent() {
  const p = typeof appState.progress === "object" ? Number(appState.progress?.percent || 0) : Number(appState.progress || 0);
  return Math.max(0, Math.min(100, Math.round(p)));
}
function updateChrome() {
  document.body.dataset.page = page;
  $("#pageTitle").textContent = pages[page];
  document.title = `${pages[page]} · 植物大战僵尸 OL 启动器`;
  $$(".navigation [data-page]").forEach((el) => {
    const active = el.dataset.page === page;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  const status = globalStatus();
  const pill = $("#statusPill");
  pill.className = `status-pill ${status.kind}`;
  pill.title = status.title;
  pill.querySelector("span").textContent = demo ? "只读演示" : status.text;
  if (demo) pill.title = DEMO_READONLY_MESSAGE;
  const stop = $("#topStop");
  stop.hidden = !(anyUnitRunning() && !connectionError);
  stop.disabled = Boolean(stopFlow && stopFlow.phase === "running");
  $("#sidebarVersion").textContent = `启动器 ${appState.version || "2.0"}`;
  const banner = $("#globalBanner");
  if (demo) {
    banner.hidden = false;
    banner.innerHTML = `${icon("info")}<strong>只读演示</strong><span class="grow">${esc(DEMO_READONLY_MESSAGE)}</span>`;
  } else if (connectionError) {
    banner.hidden = false;
    const detail = connectionError === OFFLINE_MESSAGE ? "请确认启动器窗口仍在运行；恢复后会自动重新连接。" : connectionError;
    banner.innerHTML = `${icon("alert")}<strong>与启动器服务的连接已断开</strong><span class="grow" title="${esc(detail)}">${esc(detail)}</span><button type="button" class="button sm" data-action="reconnect">${icon("refresh")}立即重试</button>`;
  } else banner.hidden = true;
}

/* ---------- 首页 ---------- */
function accountOptions(selected) {
  const players = playerAccounts();
  if (!players.length) return '<option value="">暂无账号</option>';
  const recent = (appState.recentAccountIds || []).map(findAccount).filter(Boolean).slice(0, 5);
  const option = (a) => `<option value="${esc(accountId(a))}"${accountId(a) === selected ? " selected" : ""}>${esc(accountName(a))} · ${esc(accountId(a))}</option>`;
  if (recent.length < 2 || players.length < 4) return players.map(option).join("");
  const recentIds = new Set(recent.map(accountId));
  return `<optgroup label="最近使用">${recent.map(option).join("")}</optgroup><optgroup label="其他账号">${players.filter((a) => !recentIds.has(accountId(a))).map(option).join("")}</optgroup>`;
}
function playerChoice() {
  const selected = selectedPlayer();
  const locked = connecting || writesLocked();
  const note = playerSaving
    ? "正在保存播放器选择…"
    : running()
      ? `本次使用 ${selected.name}，停止服务后可切换。`
      : busy()
        ? "游戏服务正在处理，请稍候再切换。"
        : "选择会自动记住。";
  return `<div class="field"><span id="playerLabel">播放器</span>
    <div class="player-choice" role="group" aria-labelledby="playerLabel">${playerOptions()
      .map(
        (o) =>
          `<button type="button" data-action="set-player" data-player="${o.id}" aria-pressed="${o.id === selected.id}" title="${esc(o.detail || (o.available ? "播放器文件已就绪" : "播放器文件缺失"))}"${dis(locked || !o.available)}><strong>${esc(o.name)}</strong><small class="${o.available || connecting ? "" : "missing"}">${connecting ? "检查中…" : o.available ? (o.id === "flash" ? "原生画质，推荐" : "兼容模式") : "文件缺失"}</small></button>`,
      )
      .join("")}</div>
    <small class="hint" role="status">${esc(note)}</small></div>`;
}
function launchStatus() {
  const u = units();
  if (busy() && !playerSaving) {
    const p = progressPercent();
    const stopping = appState.stopping || /停止/.test(appState.statusMessage || "");
    return `<div class="launch-status" role="status" aria-live="polite"><div class="row"><span class="spinner"></span><div class="grow"><div class="title">${stopping ? "正在停止服务" : "正在启动游戏"}</div><div class="text">${esc(appState.statusMessage || "正在准备游戏服务…")}</div></div>${stopping ? "" : `<span class="num muted">${p}%</span>`}</div>${stopping ? "" : `<div class="progress" aria-hidden="true"><span style="width:${Math.max(3, p)}%"></span></div>`}</div>`;
  }
  if (running() && u.services) {
    const up = uptimeSeconds();
    const text = u.player
      ? `已运行 ${up !== null ? duration(up) : "—"} · 关闭游戏窗口时服务会随之停止`
      : `服务组已运行 ${up !== null ? duration(up) : "—"} · 游戏窗口已关闭，可重新打开`;
    return `<div class="launch-status running" role="status"><div class="row">${icon("check")}<div class="grow"><div class="title">${u.player ? "游戏运行中" : "服务运行中"}</div><div class="text">${esc(text)}</div></div></div></div>`;
  }
  if (appState.error && !connectionError)
    return `<div class="launch-status error" role="alert"><div class="row">${icon("alert")}<div class="grow"><div class="title">上次启动未完成</div><div class="text">${esc(appState.error)}</div></div><a class="button sm" href="#services">查看日志</a></div></div>`;
  if (!connecting && !connectionError && !playerReady())
    return `<div class="notice error" role="alert">${icon("alert")}<span class="grow">${esc(playerProblem())}</span></div>`;
  return "";
}
function launchActions(a) {
  const u = units();
  if (running() || (busy() && !playerSaving)) {
    const cancel = busy() && !(appState.stopping || /停止/.test(appState.statusMessage || ""));
    return `<div class="launch-actions">${cancel ? `<button type="button" class="button lg" data-action="stop">${icon("close")}取消启动</button>` : ""}${
      appState.canReopenPlayer ? `<button type="button" class="button primary lg" data-action="reopen-player">${icon("window")}重新打开游戏窗口</button>` : ""
    }<button type="button" class="button danger-outline lg" data-action="open-stop"${dis(busy() && !cancel)}>${icon("stop")}停止服务…</button></div>`;
  }
  if (!a)
    return `<div class="launch-actions"><button type="button" class="button primary lg" data-action="new-account"${dis(connectionError)}>${icon("plus")}新建账号</button></div>`;
  const blocked = demo || connecting || Boolean(connectionError) || !playerReady();
  return `<div class="launch-actions"><button type="button" class="button primary lg" data-action="start"${dis(blocked)}>${icon("play")}${appState.error ? "重新启动游戏" : "启动游戏"}<span class="small" style="opacity:.8;font-weight:500">· ${esc(selectedPlayer().name)}</span></button></div>`;
}
function renderHome() {
  const a = launchAccount();
  const locked = busy() || running();
  const identity = a
    ? `<div class="identity">${avatar(a, "lg")}<div style="min-width:0"><h2 title="${esc(accountName(a))}">${esc(accountName(a))}</h2><div class="meta">${chip(`UID ${accountId(a)}`)}${a.level !== undefined ? chip(`Lv.${a.level}`) : ""}${running() ? chip("当前游戏账号", "ok") : ""}</div></div></div>`
    : `<div class="empty" style="padding:12px 0 4px">${icon("user")}<strong>还没有游戏账号</strong><span>新建一个账号即可开始游戏，昵称与头像可稍后修改。</span></div>`;
  const launch = `<section class="panel launch" aria-labelledby="launchTitle">
      <div class="panel-head"><h2 id="launchTitle">启动游戏</h2>${a ? '<a class="button ghost sm" href="#accounts">编辑资料</a>' : ""}</div>
      ${identity}
      <div class="controls">
        ${playerAccounts().length ? `<label class="field"><span>游戏账号</span><select class="field-control" id="homeAccount"${dis(locked || connecting)}>${accountOptions(accountId(a))}</select></label>` : ""}
        ${playerChoice()}
        ${launchStatus()}
        ${launchActions(a)}
      </div></section>`;
  return `<div class="page"><div class="home-grid">${launch}${renderHomeOverview()}</div></div>`;
}
function renderHomeOverview() {
  const services = serviceList();
  const svc = `<div class="section"><div class="section-title"><span>服务</span><a href="#services" class="small">服务与日志 ${icon("chevron")}</a></div>
    <ul class="overview-list">${services
      .map((s) => {
        const on = s.status === "running";
        const foreign = on && s.owned === false;
        const kind = foreign ? "error" : on ? "ok" : s.status === "starting" ? "warn" : "";
        const text = foreign ? "被其他程序占用" : on ? "就绪" : s.status === "starting" ? "启动中" : "未启动";
        return `<li><i class="dot ${kind}"></i><span class="name">${esc(s.name || s.id)}</span><span class="meta">${esc(s.port || "")}</span><span class="small ${foreign ? "" : "muted"}" style="${foreign ? "color:var(--danger)" : ""}">${text}</span></li>`;
      })
      .join("")}</ul></div>`;
  const recent = (appState.recentAccountIds || []).map(findAccount).filter(Boolean).slice(0, 6);
  const recentBlock =
    recent.length > 1
      ? `<div class="section"><div class="section-title"><span>最近使用的账号</span></div><div class="recent-list">${recent
          .map((r) => `<button type="button" data-action="pick-account" data-id="${esc(accountId(r))}" aria-pressed="${accountId(r) === selectedId}"${dis(busy() || running())} title="${esc(accountName(r))} · UID ${esc(accountId(r))}">${avatar(r)}<span>${esc(accountName(r))}</span></button>`)
          .join("")}</div></div>`
      : "";
  const latest = backups[0];
  const backup = `<div class="section"><div class="section-title"><span>存档</span><a href="#backups" class="small">存档备份 ${icon("chevron")}</a></div>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="grow small ${latest ? "muted" : ""}" style="flex:1;min-width:150px">${
      !backupsLoaded ? "正在读取备份记录…" : latest ? `上次备份：${esc(relativeTime(latest.created))}（${esc(backupReason(latest.reason))}）` : "还没有备份，建议先备份一次。"
    }</span><button type="button" class="button sm" data-action="create-backup"${dis(writesLocked())} title="${esc(lockReason() || "备份玩家存档与公会数据")}">${icon("archive")}立即备份</button></div></div>`;
  const issues = entries.filter((e) => e.level === "error" || e.level === "warning").slice(-3).reverse();
  const issueBlock = `<div class="section"><div class="section-title"><span>最近问题</span>${issues.length ? '<button type="button" class="link-button small" data-action="show-errors">查看全部</button>' : ""}</div>${
    issues.length
      ? `<ul class="issue-list">${issues.map((e) => `<li><i class="dot ${e.level === "error" ? "error" : "warn"}" style="margin-top:6px"></i><span class="msg" title="${esc(e.message)}"><span class="faint">${esc(shortTime(e.time))}</span> ${esc(e.message)}</span></li>`).join("")}</ul>`
      : '<p class="small muted">最近日志中没有警告或错误。</p>'
  }</div>`;
  return `<section class="panel" aria-label="运行概况">${svc}${recentBlock}${backup}${issueBlock}</section>`;
}

/* ---------- 账号与资料 ---------- */
function filteredAccounts() {
  const q = accountQuery.trim().toLowerCase();
  return accounts.filter((a) => (showRobotAccounts || !isRobotAccount(a)) && (!q || `${accountName(a)} ${accountId(a)}`.toLowerCase().includes(q)));
}
function renderAccountGroup(title, items, group, robots = false) {
  if (!items.length) return "";
  const activeId = running() ? String(appState.activeAccountId || accountId(appState.currentAccount)) : "";
  return `<details class="account-group" data-account-group="${group}"${accountQuery || !collapsedAccountGroups.has(group) ? " open" : ""}>
    <summary>${esc(title)} <span class="faint num">${items.length}</span>${icon("chevron")}</summary>
    <div role="list">${items
      .map((a) => {
        const id = accountId(a);
        const meta = `UID ${esc(id)}${a.level !== undefined && a.level !== null ? ` · Lv.${esc(a.level)}` : ""}`;
        if (robots) return `<div class="account-row robot" role="listitem" title="系统机器人账号，只读"><span class="avatar sm robot">${icon("server")}</span><span class="info"><strong>${esc(accountName(a))}</strong><small>${meta} · 机器人</small></span></div>`;
        const selected = id === selectedId;
        return `<button type="button" class="account-row" role="listitem" data-action="select-account" data-id="${esc(id)}" aria-current="${selected}">${avatar(a, "sm")}<span class="info"><strong title="${esc(accountName(a))}">${esc(accountName(a))}</strong><small>${meta}</small></span>${id === activeId ? chip(activeLabel(), "ok") : ""}</button>`;
      })
      .join("")}</div></details>`;
}
function renderAccountPane() {
  const filtered = filteredAccounts();
  const players = filtered.filter((a) => !isRobotAccount(a));
  const robots = filtered.filter(isRobotAccount);
  const q = accountQuery.trim();
  return `<section class="panel account-pane" aria-labelledby="accountPaneTitle">
    <div class="pane-head">
      <div class="top"><h2 id="accountPaneTitle">账号 <span class="faint num" style="font-weight:500">${playerAccounts().length}</span></h2><button type="button" class="button sm" data-action="new-account"${dis(writesLocked() || profileSaving)} title="${esc(lockReason() || "新建账号")}">${icon("plus")}新建</button></div>
      <label class="search">${icon("search")}<input class="field-control" id="accountSearch" type="search" placeholder="搜索昵称或 UID（/）" value="${esc(accountQuery)}" aria-label="搜索账号" autocomplete="off"></label>
      <label class="switch"><input id="showRobotAccounts" type="checkbox"${showRobotAccounts ? " checked" : ""}${dis(robotToggleLoading)}><span class="track" aria-hidden="true"></span>显示机器人账号${robotToggleLoading ? ' <span class="spinner" style="width:12px;height:12px"></span>' : ""}</label>
    </div>
    <div class="account-list" id="accountList">${renderAccountGroup("我的账号", players, "players")}${showRobotAccounts ? renderAccountGroup("机器人账号", robots, "robots", true) : ""}${
      !filtered.length
        ? `<div class="empty">${icon(q ? "search" : "user")}<strong>${q ? "没有匹配的账号" : "还没有账号"}</strong><span>${q ? (/^\d+$/.test(q) && !writesLocked() ? "" : "换个昵称或 UID 试试。") : "新建一个账号开始游戏。"}</span>${
            q && /^\d+$/.test(q) && !writesLocked() ? `<button type="button" class="button primary sm" data-action="new-account" data-uin="${esc(q)}">${icon("plus")}新建账号 ${esc(q)}</button>` : !q ? `<button type="button" class="button primary sm" data-action="new-account"${dis(writesLocked())}>${icon("plus")}新建账号</button>` : ""
          }</div>`
        : ""
    }</div>
    <div class="pane-foot"><span>${q ? `${filtered.length} 个结果` : `按最近登录排序 · ${showRobotAccounts ? `含机器人 ${robots.length} 个` : "机器人已隐藏"}`}</span><span>↑↓ 移动 · Enter 打开</span></div>
  </section>`;
}
function replaceAccountPane() {
  if (page !== "accounts") return;
  const pane = $(".account-pane");
  if (!pane) return;
  const scroll = $("#accountList")?.scrollTop || 0;
  const holder = document.createElement("div");
  holder.innerHTML = renderAccountPane();
  pane.replaceWith(holder.firstElementChild);
  const list = $("#accountList");
  if (list) list.scrollTop = scroll;
}
function renderAccounts() {
  return `<div class="page fill"><div class="accounts-grid">${renderAccountPane()}<section id="profilePanel" class="panel profile-pane" aria-label="账号资料">${renderProfile()}</section></div></div>`;
}
function profileField(name) {
  return profileDraft?.[name] ?? profile?.[name] ?? (name === "name" ? profile?.nickname : "") ?? "";
}
function renderProfile() {
  if (!playerAccounts().length || !selectedId)
    return `<div class="empty" style="margin:auto">${icon("user")}<strong>选择或新建一个账号</strong><span>可在这里修改昵称、签名与头像。</span><button type="button" class="button primary sm" data-action="new-account"${dis(writesLocked())}>${icon("plus")}新建账号</button></div>`;
  if (profileError && (!profile || accountId(profile) !== selectedId))
    return `<div class="empty" style="margin:auto">${icon("alert")}<strong>账号资料读取失败</strong><span>${esc(profileError)}</span><button type="button" class="button sm" data-action="reload-profile">${icon("refresh")}重试</button></div>`;
  if (!profile || accountId(profile) !== selectedId) return `<div class="loading" style="margin:auto"><span class="spinner"></span>正在读取账号资料…</div>`;
  const a = profile;
  const reason = lockReason();
  const disabled = Boolean(reason) || profileSaving;
  const choices = a.avatarChoices || [];
  const name = profileField("name");
  const signature = profileField("signature");
  const isActive = running() && String(appState.activeAccountId || accountId(appState.currentAccount)) === selectedId;
  const chosen = (v) => (stagedAvatar === "keep" && !stagedImage && !a.qq_face_url && Number(a.face_id) === Number(v.id)) || stagedAvatar === Number(v.id);
  return `<div class="profile-scroll">
    <div class="profile-head"><span id="profileAvatarPreview">${avatar(previewAccount(a), "lg")}</span><div class="grow"><h2 title="${esc(accountName(a))}">${esc(accountName(a))}</h2><div class="meta">${chip(`UID ${accountId(a)}`)}${a.level !== undefined ? chip(`Lv.${a.level}`) : ""}${isActive ? chip(activeLabel(), "ok", true) : ""}</div></div>
      <button type="button" class="button sm" data-action="gm-account" data-id="${esc(accountId(a))}" title="在 GM 管理中打开此账号">${icon("grid")}GM 管理</button></div>
    ${reason ? `<div class="notice warn" role="status" style="margin-bottom:14px">${icon("shield")}<span class="grow">${esc(reason)}</span>${running() ? '<button type="button" class="button sm" data-action="open-stop">停止服务…</button>' : ""}</div>` : ""}
    <form id="profileForm" autocomplete="off" novalidate>
      <div class="section"><div class="section-title"><span>基本资料</span></div>
        <div class="form-grid">
          <label class="field"><span class="field-row"><span>昵称</span><span class="counter" data-counter="name">${[...name].length}/7</span></span><input class="field-control" name="name" value="${esc(name)}" maxlength="7" required${dis(disabled)}><small class="hint" style="margin:0">2–7 个字，沿用游戏内限制</small></label>
          <label class="field"><span class="field-row"><span>个性签名</span><span class="counter" data-counter="signature">${[...signature].length}/30</span></span><input class="field-control" name="signature" value="${esc(signature)}" maxlength="30" placeholder="说说你的花园故事…"${dis(disabled)}><small class="hint" style="margin:0">最多 30 个字，可留空</small></label>
        </div>
      </div>
      <div class="section"><div class="section-title"><span>头像</span><span class="faint" style="font-weight:500">${stagedImage ? "已裁剪新头像，保存后生效" : stagedAvatar !== "keep" ? "已选择新头像，保存后生效" : ""}</span></div>
        <div class="avatar-editor">
          <div style="display:grid;gap:6px;justify-items:start"><button type="button" class="button sm" data-action="upload-avatar"${dis(disabled)}>${icon("upload")}上传并裁剪</button><small class="hint" style="margin:0">PNG / JPG / WebP / GIF，≤15 MB</small></div>
          ${choices.length ? `<div class="avatar-choices" role="group" aria-label="内置头像">${choices.map((v) => `<button type="button" class="avatar-choice" data-action="choose-avatar" data-id="${esc(v.id)}" data-url="${esc(v.url)}" title="${esc(v.label)}" aria-label="${esc(v.label)}" aria-pressed="${chosen(v)}"${dis(disabled)}><img src="${esc(v.url)}" alt=""></button>`).join("")}</div>` : ""}
        </div>
      </div>
      <div class="section"><div class="section-title"><span>账号数据</span><button type="button" class="link-button small" data-action="gm-account" data-id="${esc(accountId(a))}">在 GM 中修改</button></div>
        <dl class="kv"><dt>等级</dt><dd>${numeric(a.level)}</dd><dt>金币</dt><dd>${numeric(a.gold ?? a.money ?? a.coin)}</dd>${a.sun !== undefined ? `<dt>阳光</dt><dd>${numeric(a.sun)}</dd>` : ""}${a.vip_level !== undefined ? `<dt>VIP</dt><dd>${numeric(a.vip_level)}</dd>` : ""}${a.last_login ? `<dt>最近登录</dt><dd>${esc(loginTime(a.last_login))}</dd>` : ""}</dl>
      </div>
      <div class="section"><div class="danger-zone"><div class="grow"><strong>删除账号</strong><p class="small muted">删除前会自动备份存档；需要输入 UID 确认。</p></div><button type="button" class="button danger-outline sm" data-action="delete-account"${dis(disabled)}>${icon("trash")}删除账号…</button></div></div>
    </form>
  </div>
  <div class="profile-foot"><span class="state${profileDirty ? " dirty" : ""}" id="profileState">${profileSaving ? '<span class="spinner" style="width:13px;height:13px"></span>正在保存…' : profileDirty ? `${icon("info")}有未保存的修改` : `${icon("check")}已保存`}</span>
    <button type="button" class="button" data-action="reload-profile"${dis(disabled || !profileDirty)}>重置</button>
    <button type="submit" form="profileForm" class="button primary"${dis(disabled)} title="Ctrl+S">${icon("check")}保存资料</button></div>`;
}
function refreshProfilePanel() {
  const panel = $("#profilePanel");
  if (!panel) return;
  const scroll = $(".profile-scroll", panel)?.scrollTop || 0;
  const focusName = document.activeElement?.closest?.("#profileForm") ? document.activeElement.name : "";
  panel.innerHTML = renderProfile();
  const box = $(".profile-scroll", panel);
  if (box) box.scrollTop = scroll;
  if (focusName) $(`#profileForm [name="${focusName}"]`)?.focus();
}
function updateProfileFooter() {
  const state = $("#profileState");
  if (!state || profileSaving) return;
  state.className = `state${profileDirty ? " dirty" : ""}`;
  state.innerHTML = profileDirty ? `${icon("info")}有未保存的修改` : `${icon("check")}已保存`;
  const reset = $('[data-action="reload-profile"]', $("#profilePanel"));
  if (reset) reset.disabled = Boolean(lockReason()) || !profileDirty;
}

/* ---------- GM 管理 ---------- */
function gmLocation() {
  if (gmFrameHref) {
    const u = new URL(gmFrameHref, "http://gm.invalid");
    const uin = u.searchParams.get("uin") || "";
    return { dashboard: !uin, uin, view: u.pathname === "/items" ? "items" : "player" };
  }
  const dashboard = gmView === "dashboard" || !selectedId;
  return { dashboard, uin: dashboard ? "" : selectedId, view: gmView };
}
function adminFrameUrl() {
  if (!adminUrl) return "";
  const url = new URL(adminUrl, location.origin);
  url.search = "";
  if (gmFrameHref) {
    const current = new URL(gmFrameHref, url.origin);
    url.pathname = current.pathname;
    url.search = current.search;
    ["msg", "kind"].forEach((key) => url.searchParams.delete(key)); // 一次性的操作提示不随刷新重放
  } else if (gmView === "dashboard" || !selectedId) {
    url.pathname = "/";
  } else if (gmView === "items") {
    url.pathname = "/items";
    url.searchParams.set("uin", selectedId);
  } else {
    url.pathname = "/player";
    url.searchParams.set("uin", selectedId);
    url.searchParams.set("section", "attributes");
  }
  url.searchParams.set("embedded", "1");
  url.searchParams.set("theme", document.documentElement.dataset.theme || "light");
  return url.toString();
}
function adminExternalUrl() {
  const url = new URL(adminFrameUrl());
  url.searchParams.delete("embedded");
  return url.toString();
}
function renderGM() {
  const players = playerAccounts();
  const where = gmLocation();
  const dashboard = where.dashboard;
  const extra = !dashboard && !players.some((a) => accountId(a) === where.uin) ? `<option value="${esc(where.uin)}" selected disabled>UID ${esc(where.uin)}</option>` : "";
  // 视图切换交给 GM 页面自身的标签页；启动器只负责“打开哪个账号”，避免两套导航重复。
  const state = adminLoading ? '<span class="state"><span class="spinner" style="width:12px;height:12px"></span>正在连接 GM 后台…</span>' : "";
  const toolbar = `<div class="gm-toolbar" role="toolbar" aria-label="GM 工具栏">
    <label class="field-label" for="gmAccount">账号</label>
    <select class="field-control" id="gmAccount">${'<option value="__dashboard__"' + (dashboard ? " selected" : "") + ">全部账号（账号列表）</option>" + extra + players.map((a) => `<option value="${esc(accountId(a))}"${!dashboard && accountId(a) === where.uin ? " selected" : ""}>${esc(accountName(a))} · ${esc(accountId(a))}</option>`).join("")}</select>
    ${!dashboard ? `<button type="button" class="button sm" data-action="gm-view" data-view="items" title="打开该账号的道具发放（支持批量）"${where.view === "items" ? ' aria-pressed="true"' : ""}>${icon("gift")}道具发放</button>` : ""}
    ${state}<span class="spacer"></span>
    <button type="button" class="icon-button" data-action="reload-admin" title="重新加载 GM 页面" aria-label="重新加载 GM 页面"${dis(adminLoading)}>${icon("refresh")}</button>
    ${adminUrl ? `<a class="icon-button" href="${esc(adminExternalUrl())}" target="_blank" rel="noopener" title="在浏览器中打开（独立窗口）" aria-label="在浏览器中打开">${icon("external")}</a>` : ""}
  </div>`;
  const body = demo
    ? `<div class="gm-empty">${icon("grid")}<h2>GM 管理 · 只读演示</h2><p>正式运行时可管理角色、道具批量发放、背包、植物、进度与特权。演示不会启动后台或载入真实 GM 页面。</p></div>`
    : adminUrl
    ? `<iframe class="gm-frame" id="gmFrame" src="${esc(adminFrameUrl())}" title="GM 管理工作台"></iframe>`
    : `<div class="gm-empty">${adminLoading ? '<span class="spinner lg"></span>' : icon(adminError ? "alert" : "grid")}<h2>${adminLoading ? "正在启动 GM 后台…" : adminError ? "GM 后台未能连接" : "GM 管理工作台"}</h2><p>${esc(
        adminLoading ? "首次打开需要几秒钟。" : adminError || "角色属性、道具（含批量发放）、植物、背包、进度与特权都在这里管理。游戏运行期间后台只读。",
      )}</p>${adminLoading ? "" : `<button type="button" class="button primary" data-action="open-admin"${dis(connectionError)}>${icon(adminError ? "refresh" : "grid")}${adminError ? "重试" : "打开 GM 后台"}</button>`}${adminError ? '<a class="small" href="#services">查看服务日志</a>' : ""}</div>`;
  return `<div class="gm-wrap">${toolbar}${body}</div>`;
}

/* ---------- 服务与日志 ---------- */
function unitStatusChip(unit) {
  if (unit.starting) return chip("启动中", "warn", true);
  if (unit.running) return chip("运行中", "ok", true);
  return chip(unit.id === "player" && units().services ? "未打开" : "未运行", "", true);
}
function activityText(activity, componentId) {
  if (!activity) return "—";
  if (!activity.known) return esc(activity.text || "未知");
  const parts = [];
  if (componentId === "game") parts.push(`${numeric(activity.active)} 个连接`);
  else if (componentId === "policy") parts.push(`已应答 ${numeric(activity.total)} 次`);
  else parts.push(`${numeric(activity.total)} 次请求`);
  if (componentId === "game") parts.push(`累计 ${numeric(activity.total)}`);
  parts.push(activity.lastSeconds === null || activity.lastSeconds === undefined ? "尚无活动" : `最近 ${duration(activity.lastSeconds)}前`);
  return esc(parts.join(" · "));
}
function fallbackUnits() {
  // /api/services 尚未返回时，用 /api/state 的信息画出同样的结构，避免空白闪烁。
  const u = units();
  return [
    { id: "services", name: "游戏服务组", process: "run_all.py", running: u.services, starting: busy() && u.services, components: serviceList().map((s) => ({ id: s.id, name: s.name, port: s.port, ports: [s.port], ready: s.status === "running" && s.owned !== false, activity: null })) },
    { id: "player", name: "游戏窗口", process: selectedPlayer().name, running: u.player },
    { id: "admin", name: "独立 GM 后台", process: "admin_server.py", running: u.admin },
  ];
}
let componentsOpen = null; // 服务组明细：null = 自动（窗口够高、启动中或有未就绪服务时展开），true/false = 用户选择
function componentsShown(unit) {
  if (componentsOpen !== null) return componentsOpen;
  return unit.starting || (unit.components || []).some((c) => !c.ready) || innerHeight / scale() >= 760;
}
function componentSummary(unit) {
  const list = unit.components || [];
  const waiting = list.filter((c) => !c.ready);
  return waiting.length ? `${list.length - waiting.length}/${list.length} 就绪 · 未就绪：${waiting.map((c) => c.name).join("、")}` : `${list.length} 项服务均已就绪 · 端口 ${list.map((c) => c.port).join(" / ")}`;
}
function renderUnitsPanel() {
  const list = overview?.units || fallbackUnits();
  const foreign = overview?.foreign || [];
  const u = units();
  const startDisabled = demo || busy() || !currentAccount() || Boolean(connectionError) || connecting || !playerReady();
  const icons = { services: "server", player: "window", admin: "grid" };
  const rows = list
    .map((unit) => {
      const live = unit.id === "services" && (unit.running || unit.starting) && Boolean(unit.components);
      const expanded = live && componentsShown(unit);
      const ports = unit.id === "services" && unit.components ? `包含 ${unit.components.length} 项服务 · 端口 ${unit.components.map((c) => c.port).join(" / ")}` : "—";
      const toggle = live ? ` <button type="button" class="link-button" data-action="toggle-components" aria-expanded="${expanded}">${expanded ? "收起明细" : "展开明细"}</button>` : "";
      const main = `<tr><td><div class="unit-name">${icon(icons[unit.id] || "server")}<span><strong>${esc(unit.name)}</strong><small class="mono">${esc(unit.process || "")}</small></span></div></td><td>${unitStatusChip(unit)}</td><td class="num hide-compact">${unit.pid ? esc(unit.pid) : "—"}</td><td class="num">${unit.uptime !== null && unit.uptime !== undefined ? esc(duration(unit.uptime)) : "—"}</td><td class="activity">${unit.id === "services" ? (live ? (expanded ? "各项服务见下方" : esc(componentSummary(unit))) + toggle : esc(ports)) : unit.running ? activityText(unit.activity, unit.id) : "—"}</td></tr>`;
      const comps = expanded && unit.components
        ? unit.components
            .map((c) => `<tr class="component"><td><div class="unit-name"><span><strong style="font-weight:600">${esc(c.name)}</strong><small class="mono">端口 ${esc((c.ports || [c.port]).join(" / "))}</small></span></div></td><td>${unit.running ? (c.ready ? chip("就绪", "ok") : chip(unit.starting ? "等待中" : "未就绪", "warn")) : chip("未运行")}</td><td class="hide-compact"></td><td></td><td class="activity">${unit.running ? activityText(c.activity, c.id) : "—"}</td></tr>`)
            .join("")
        : "";
      return main + comps;
    })
    .join("");
  return `<section class="panel" id="unitsPanel" aria-labelledby="unitsTitle">
    <div class="panel-head"><div><h2 id="unitsTitle">本启动器管理的进程</h2><p class="sub">端口只是“游戏服务组”进程的组成部分；${overview?.checkedAt ? `更新于 ${esc(overview.checkedAt)}` : "正在读取运行详情…"}</p></div>
      <div class="actions">
        ${running() || busy() ? "" : `<button type="button" class="button primary" data-action="start"${dis(startDisabled)} title="${esc(currentAccount() ? `用 ${accountName(currentAccount())} 启动` : "请先新建账号")}">${icon("play")}启动游戏</button>`}
        ${appState.canReopenPlayer ? `<button type="button" class="button" data-action="reopen-player">${icon("window")}重新打开游戏窗口</button>` : ""}
        ${anyUnitRunning() ? `<button type="button" class="button danger-outline" data-action="open-stop">${icon("stop")}停止服务…</button>` : ""}
        <button type="button" class="button" data-action="open-diagnostics">${icon("pulse")}健康检查</button>
        <button type="button" class="icon-button" data-action="refresh-state" title="刷新" aria-label="刷新">${icon("refresh")}</button>
      </div></div>
    ${foreign.map((f) => `<div class="notice error" style="margin-bottom:10px">${icon("alert")}<span class="grow">${esc(f.detail)}</span></div>`).join("")}
    <div class="table-wrap"><table class="unit-table"><thead><tr><th>项目</th><th>状态</th><th class="num hide-compact">PID</th><th class="num">运行时长</th><th>可观测活动</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${overviewError ? `<p class="hint" style="color:var(--danger)">${esc(overviewError)}</p>` : ""}
  </section>`;
}
function filteredLogs() {
  const terms = logQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return entries.filter((e) => {
    const level = String(e.level || "info").toLowerCase();
    if (logLevel !== "all" && level !== logLevel && !(logLevel === "warning" && level === "warn")) return false;
    if (logSource !== "all" && (e.source || "launcher") !== logSource) return false;
    if (!terms.length) return true;
    const hay = `${e.message} ${e.time}`.toLowerCase();
    return terms.every((t) => hay.includes(t));
  });
}
function levelCounts() {
  const counts = { all: 0, error: 0, warning: 0, info: 0, debug: 0 };
  for (const e of entries) {
    if (logSource !== "all" && (e.source || "launcher") !== logSource) continue;
    counts.all += 1;
    const level = String(e.level || "info").toLowerCase();
    counts[level === "warn" ? "warning" : level] = (counts[level === "warn" ? "warning" : level] || 0) + 1;
  }
  return counts;
}
const LOG_RENDER_LIMIT = 1200;
function logLines() {
  const lines = filteredLogs();
  if (!lines.length)
    return `<div class="empty">${icon("search")}<strong>${entries.length ? "没有符合筛选条件的日志" : "暂无日志"}</strong><span>${entries.length ? "调整级别、来源或搜索词。" : "启动游戏后，这里会实时显示各服务的输出。"}</span>${entries.length ? '<button type="button" class="button sm" data-action="clear-log-filters">清除筛选</button>' : ""}</div>`;
  const shown = lines.slice(-LOG_RENDER_LIMIT);
  return (lines.length > shown.length ? `<div class="log-line debug"><span class="t"></span><span class="lv"></span><span class="src"></span><span class="msg">（较早的 ${lines.length - shown.length} 条未显示，可用搜索缩小范围或导出查看）</span></div>` : "") +
    shown
      .map((e) => {
        const level = String(e.level || "info").toLowerCase();
        return `<div class="log-line ${esc(level)}"><span class="t">${esc(shortTime(e.time))}</span><span class="lv">${esc(LEVEL_LABEL[level] || level.toUpperCase())}</span><span class="src">${esc(SOURCE_LABEL[e.source] || e.source || "启动器")}</span><span class="msg">${esc(e.message)}</span></div>`;
      })
      .join("");
}
function logStatusText() {
  const count = filteredLogs().length;
  const filtered = logLevel !== "all" || logSource !== "all" || logQuery.trim();
  return `${filtered ? `筛选出 ${numeric(count)} / ${numeric(entries.length)} 条` : `${numeric(entries.length)} 条`} · 内存中最多保留最近 ${numeric(logCapacity)} 条`;
}
function renderLogLevelButtons() {
  const counts = levelCounts();
  return LOG_LEVELS.map(([v, label]) => `<button type="button" data-action="log-level" data-level="${v}" aria-pressed="${logLevel === v}">${label}${v !== "all" && counts[v] ? ` <span class="count">${numeric(counts[v])}</span>` : ""}</button>`).join("");
}
function renderLogPanel() {
  return `<section class="panel log-panel" aria-labelledby="logTitle">
    <div class="log-toolbar">
      <h2 id="logTitle">运行日志</h2>
      <div class="segmented" role="group" aria-label="日志级别" id="logLevels">${renderLogLevelButtons()}</div>
      <label class="sr-only" for="logSource">日志来源</label><select class="field-control" id="logSource">${LOG_SOURCES.map(([v, label]) => `<option value="${v}"${logSource === v ? " selected" : ""}>${label}</option>`).join("")}</select>
      <label class="search">${icon("search")}<input class="field-control" id="logSearch" type="search" value="${esc(logQuery)}" placeholder="搜索日志（/）" title="空格分隔多个关键词，全部匹配才显示" aria-label="搜索日志" autocomplete="off"></label>
      <button type="button" class="button sm" data-action="pause-logs" id="pauseLogs" aria-pressed="${logPaused}" title="${logPaused ? "继续实时更新" : "暂停实时更新"}">${pauseLabel()}</button>
      <button type="button" class="icon-button" data-action="copy-logs" title="复制筛选结果" aria-label="复制筛选结果">${icon("copy")}</button>
      <button type="button" class="icon-button" data-action="export-logs" title="导出筛选结果" aria-label="导出筛选结果">${icon("download")}</button>
    </div>
    <div class="log-view" id="logView" role="log" aria-live="off" tabindex="0">${logLines()}</div>
    <div class="log-status"><span id="logCount">${logStatusText()}</span><span id="logLive">${logLiveText()}</span></div>
  </section>`;
}
function pauseLabel() {
  return `${icon(logPaused ? "play" : "pause")}<span class="label">${logPaused ? "继续" : "暂停"}</span>`;
}
function logLiveText() {
  if (logPaused) return `已暂停更新${pendingWhilePaused ? ` · 有新日志待显示` : ""}`;
  return logFollow ? "实时更新 · 自动滚动到最新" : '实时更新 · <button type="button" class="link-button" data-action="log-follow">回到最新</button>';
}
function renderServices() {
  return `<div class="page fill services-page" style="display:flex;flex-direction:column;gap:16px">${renderUnitsPanel()}${renderLogPanel()}</div>`;
}
function scrollLogs() {
  const view = $("#logView");
  if (view) view.scrollTop = view.scrollHeight;
}
function renderLogsOnly() {
  const view = $("#logView");
  if (!view) return;
  view.innerHTML = logLines();
  $("#logCount").textContent = logStatusText();
  $("#logLive").innerHTML = logLiveText();
  const levels = $("#logLevels");
  if (levels) levels.innerHTML = renderLogLevelButtons();
  if (logFollow) scrollLogs();
}
function refreshUnitsPanel() {
  const panel = $("#unitsPanel");
  if (!panel) return;
  const holder = document.createElement("div");
  holder.innerHTML = renderUnitsPanel();
  const focused = panel.contains(document.activeElement) ? document.activeElement.dataset?.action : "";
  panel.replaceWith(holder.firstElementChild);
  if (focused) $(`#unitsPanel [data-action="${focused}"]`)?.focus();
}

/* ---------- 存档备份 ---------- */
function backupKind(b) {
  return b.reason === "manual" ? "manual" : "auto";
}
function filteredBackups() {
  return backups.filter((b) => backupFilter === "all" || backupKind(b) === backupFilter);
}
function renderPager(total, current, size, action) {
  const pagesCount = Math.max(1, Math.ceil(total / size));
  if (pagesCount <= 1) return "";
  const btn = (p, label, aria) => `<button type="button" class="button sm" data-action="${action}" data-page="${p}" aria-label="${aria}"${dis(p < 1 || p > pagesCount || p === current)}>${label}</button>`;
  return `<nav class="pager" aria-label="分页"><span class="num">共 ${numeric(total)} 条 · 第 ${current} / ${pagesCount} 页</span><span class="steps">${btn(1, "«", "首页")}${btn(current - 1, "‹ 上一页", "上一页")}${btn(current + 1, "下一页 ›", "下一页")}${btn(pagesCount, "»", "末页")}</span></nav>`;
}
function renderBackups() {
  const reason = lockReason();
  const list = filteredBackups();
  const pagesCount = Math.max(1, Math.ceil(list.length / BACKUP_PAGE_SIZE));
  backupPage = Math.min(Math.max(1, backupPage), pagesCount);
  const pageItems = list.slice((backupPage - 1) * BACKUP_PAGE_SIZE, backupPage * BACKUP_PAGE_SIZE);
  const counts = { all: backups.length, manual: backups.filter((b) => backupKind(b) === "manual").length };
  counts.auto = counts.all - counts.manual;
  let table;
  if (!backupsLoaded && !backupError) table = `<div class="loading"><span class="spinner"></span>正在读取备份记录…</div>`;
  else if (backupError) table = `<div class="empty">${icon("alert")}<strong>备份记录读取失败</strong><span>${esc(backupError)}</span><button type="button" class="button sm" data-action="refresh-backups">${icon("refresh")}重试</button></div>`;
  else if (!backups.length) table = `<div class="empty">${icon("archive")}<strong>还没有存档快照</strong><span>点击“立即备份”保存现在的花园；删除账号、恢复备份前也会自动备份。</span></div>`;
  else
    table = `<div class="table-wrap"><table><thead><tr><th>快照时间</th><th>类型</th><th class="num">文件</th><th class="num">大小</th><th class="right">操作</th></tr></thead><tbody>${pageItems
      .map(
        (b) => `<tr><td><div class="backup-name">${icon("archive")}<span><strong>${esc(dateTime(b.created))}</strong><small title="快照编号">${esc(relativeTime(b.created))} · <span class="mono">${esc(b.id)}</span></small></span></div></td><td>${chip(backupReason(b.reason), backupKind(b) === "manual" ? "info" : "")}</td><td class="num">${b.files?.length ?? "—"}</td><td class="num">${esc(humanSize(b.size))}</td><td class="right"><button type="button" class="button sm" data-action="restore-backup" data-id="${esc(b.id)}"${dis(Boolean(reason))} title="${esc(reason || "恢复到这个快照")}">${icon("refresh")}恢复…</button></td></tr>`,
      )
      .join("")}${!pageItems.length ? `<tr><td colspan="5"><div class="empty">${icon("archive")}<strong>${backupFilter === "auto" ? "还没有自动备份" : backupFilter === "manual" ? "还没有手动备份" : "还没有备份"}</strong><span>${backupFilter === "auto" ? "删除账号或恢复快照前，启动器会自动备份当前存档。" : "点“立即备份”保存一份当前存档；建议在大量 GM 修改前备份。"}</span></div></td></tr>` : ""}</tbody></table></div>${renderPager(list.length, backupPage, BACKUP_PAGE_SIZE, "backup-page")}`;
  const filters = backups.length
    ? `<div class="segmented" role="group" aria-label="备份类型">${[["all", "全部"], ["manual", "手动"], ["auto", "自动"]].map(([v, label]) => `<button type="button" data-action="backup-filter" data-filter="${v}" aria-pressed="${backupFilter === v}">${label} <span class="count">${counts[v]}</span></button>`).join("")}</div>`
    : "";
  const panel = `<section class="panel" aria-labelledby="backupTitle"><div class="panel-head"><div><h2 id="backupTitle">存档快照</h2><p class="sub">备份玩家存档 players.db 与公会数据 guild_local.db，保存在 PvZ-Server-PoC/backups/launcher。恢复前会自动再备份一次当前存档。</p></div>
      <div class="actions">${filters}<button type="button" class="icon-button" data-action="refresh-backups" title="刷新列表" aria-label="刷新列表">${icon("refresh")}</button><button type="button" class="button primary" data-action="create-backup"${dis(Boolean(reason))} title="${esc(reason || "立即备份当前存档")}">${icon("plus")}立即备份</button></div></div>
      ${reason ? `<div class="notice warn" style="margin-bottom:12px">${icon("shield")}<span class="grow">${esc(reason)}备份与恢复需要先停止游戏服务。</span>${running() ? '<button type="button" class="button sm" data-action="open-stop">停止服务…</button>' : ""}</div>` : ""}
      ${table}</section>`;
  return `<div class="page">${panel}</div>`;
}

/* ---------- 设置 ---------- */
function renderSettings() {
  const themeButtons = [["light", "sun", "浅色"], ["dark", "moon", "深色"], ["auto", "settings", "跟随系统"]]
    .map(([v, i, label]) => `<button type="button" data-action="set-theme" data-theme="${v}" aria-pressed="${settings.theme === v}">${icon(i)}${label}</button>`)
    .join("");
  return `<div class="page"><div class="settings-page stack">
    <section class="panel"><h2>外观</h2>
      <div class="setting-row"><div class="text"><h3>主题</h3><p>GM 管理页面会同步使用相同主题。</p></div><div class="segmented" role="group" aria-label="主题">${themeButtons}</div></div>
      <div class="setting-row"><div class="text"><h3 id="scaleLabel">界面缩放</h3><p>窗口较小或屏幕分辨率较高时调整；布局会按缩放后的尺寸自动适配。</p></div><select id="settingScale" class="field-control" style="width:auto" aria-labelledby="scaleLabel">${SCALES.map((n) => `<option value="${n}"${Number(settings.scale) === n ? " selected" : ""}>${n}%${n === 100 ? "（默认）" : ""}</option>`).join("")}</select></div>
    </section>
    <section class="panel"><h2>本地环境</h2>
      <div class="setting-row"><div class="text"><h3>游戏目录</h3><p class="mono">${esc(appState.gamePath || settings.game_path || "由启动器自动定位（与启动器同一目录）")}</p></div>${appState.gamePath ? `<button type="button" class="button sm" data-action="copy-text" data-text="${esc(appState.gamePath)}">${icon("copy")}复制路径</button>` : ""}</div>
      <div class="setting-row"><div class="text"><h3>健康检查</h3><p>检查游戏文件、播放器、存档完整性、端口占用、磁盘空间与最近错误，并给出处理建议。</p></div><button type="button" class="button" data-action="open-diagnostics">${icon("pulse")}运行检查</button></div>
      <div class="setting-row"><div class="text"><h3>存档写入保护</h3><p>游戏运行时锁定资料、GM 写入与备份恢复；保存资料前校验存档是否被其他页面改过。</p></div>${chip("已启用", "ok")}</div>
    </section>
    <section class="panel"><h2>键盘快捷键</h2>
      <dl class="shortcut-list" style="margin-top:10px"><dt><kbd>Alt</kbd> + <kbd>1</kbd>…<kbd>6</kbd></dt><dd>切换到侧栏第 1–6 个页面</dd><dt><kbd>/</kbd></dt><dd>聚焦当前页的搜索框（账号、日志）</dd><dt><kbd>↑</kbd> <kbd>↓</kbd></dt><dd>在账号列表中移动；<kbd>Enter</kbd> 打开该账号</dd><dt><kbd>Ctrl</kbd> + <kbd>S</kbd></dt><dd>保存正在编辑的账号资料</dd><dt><kbd>Esc</kbd></dt><dd>关闭弹窗</dd></dl>
    </section>
    <section class="panel"><div class="setting-row" style="padding:0"><div class="text"><h3>植物大战僵尸 OL · 本地启动器</h3><p>界面与 GM 后台共用同一套设计规范。</p></div>${chip(`版本 ${appState.version || "2.0"}`)}</div></section>
  </div></div>`;
}

/* ---------- 弹窗 ---------- */
let dialogLocked = false;
function dialog(title, content, actions = "", options = {}) {
  const box = $("#appDialog");
  box.classList.toggle("wide", Boolean(options.wide));
  box.dataset.kind = options.kind || "";
  dialogLocked = false;
  $("#dialogBody").innerHTML = `<div class="dialog-head"><div><h2 id="dialogTitle">${esc(title)}</h2>${options.sub ? `<p class="sub">${options.sub}</p>` : ""}</div><button type="button" class="icon-button" data-action="close-dialog" aria-label="关闭弹窗">${icon("close")}</button></div><div class="dialog-content">${content}</div><div class="dialog-actions">${options.summary !== undefined ? `<span class="summary" id="dialogSummary">${options.summary}</span>` : ""}${options.noCancel ? "" : `<button type="button" class="button" data-action="close-dialog">${esc(options.cancelLabel || "取消")}</button>`}${actions}</div>`;
  if (!box.open) box.showModal();
  const focus = options.focus ? $(options.focus, box) : $(".dialog-content input:not([disabled]),.dialog-actions .button.primary,.dialog-actions .button.danger", box);
  (focus || $(".dialog-actions .button", box))?.focus();
}
function closeDialog(force = false) {
  if (dialogLocked && !force) return;
  const box = $("#appDialog");
  if (box.open) box.close();
}
function dialogKind() {
  const box = $("#appDialog");
  return box.open ? box.dataset.kind || "" : "";
}
$("#appDialog").addEventListener("cancel", (event) => {
  if (dialogLocked) event.preventDefault();
});
$("#appDialog").addEventListener("close", () => {
  const kind = $("#appDialog").dataset.kind;
  $("#appDialog").dataset.kind = "";
  if (kind === "stop") {
    stopFlow = null;
    clearInterval(overviewTimer);
    overviewTimer = null;
    if (page === "services") startOverviewPolling();
    updateChrome();
  }
});

/* 停止服务：列出本启动器管理的进程、职责、就绪与活动、影响和依赖，逐项返回真实结果 */
const UNIT_NAMES = { services: "游戏服务组", player: "游戏窗口", admin: "独立 GM 后台" };
async function loadOverview() {
  try {
    overview = await api("/api/services");
    overviewError = "";
  } catch (e) {
    overviewError = `运行详情读取失败：${e.message}`;
  }
  return overview;
}
function startOverviewPolling() {
  clearInterval(overviewTimer);
  overviewTimer = setInterval(async () => {
    if (document.hidden) return;
    if (stopFlow && stopFlow.phase !== "choose") return;
    await loadOverview();
    if (stopFlow?.phase === "choose") renderStopDialog();
    else if (page === "services") refreshUnitsPanel();
  }, stopFlow ? 2000 : 3000);
}
async function openStopDialog(preset) {
  if (stopFlow?.phase === "running") return;
  stopFlow = { phase: "loading", selected: new Set(), results: null, error: "", ok: true, touched: false };
  renderStopDialog();
  await loadOverview();
  if (!stopFlow) return;
  const runningUnits = (overview?.units || []).filter((u) => u.running).map((u) => u.id);
  stopFlow.selected = new Set(preset ? runningUnits.filter((id) => preset.includes(id)) : runningUnits);
  stopFlow.phase = "choose";
  renderStopDialog();
  startOverviewPolling();
}
function stopPlan() {
  const list = overview?.units || [];
  const runningIds = new Set(list.filter((u) => u.running).map((u) => u.id));
  const chosen = new Set([...stopFlow.selected].filter((id) => runningIds.has(id)));
  const implied = chosen.has("services") && runningIds.has("player") && !chosen.has("player") ? ["player"] : [];
  return { chosen, implied, runningIds, all: [...chosen, ...implied] };
}
function renderStopUnit(unit, plan) {
  const impliedPlayer = unit.id === "player" && plan.chosen.has("services") && unit.running;
  const checked = unit.running && (plan.chosen.has(unit.id) || impliedPlayer);
  const disabled = !unit.running || impliedPlayer;
  const stopping = stopFlow.phase === "running" && checked;
  const status = stopping ? `<span class="chip warn"><span class="spinner" style="width:10px;height:10px"></span>正在停止</span>` : unit.starting ? chip("启动中", "warn", true) : unit.running ? chip("运行中", "ok", true) : chip(unit.state || "未运行", "", true);
  const facts = [unit.process ? `<span class="mono">${esc(unit.process)}</span>` : "", unit.pid ? `PID <span class="num">${esc(unit.pid)}</span>` : "", unit.uptime !== null && unit.uptime !== undefined ? `已运行 ${esc(duration(unit.uptime))}` : ""].filter(Boolean).join(" · ");
  const deps = [
    unit.dependsOn?.length ? `依赖：${unit.dependsOn.map((d) => UNIT_NAMES[d] || d).join("、")}` : "",
    unit.requiredBy?.length ? `被依赖：${unit.requiredBy.map((d) => UNIT_NAMES[d] || d).join("、")}` : "",
  ].filter(Boolean).join("；");
  const components = unit.components?.length
    ? `<ul class="component-list">${unit.components
        .map((c) => `<li><span class="cname" title="${esc(c.role || "")}">${esc(c.name)}<small>${esc(c.role || "")}</small></span><span class="mono num">${esc((c.ports || [c.port]).join("/"))}</span><span class="act">${unit.running ? `${c.ready ? '<i class="dot ok"></i> 就绪' : '<i class="dot warn"></i> 未就绪'} · ${activityText(c.activity, c.id)}` : "未运行"}</span></li>`)
        .join("")}</ul>`
    : "";
  const activity = unit.id !== "services" && unit.running ? `<div>可观测活动：${activityText(unit.activity, unit.id)}</div>` : "";
  return `<label class="unit-option${disabled && !impliedPlayer ? " disabled" : ""}"><input type="checkbox" data-stop-unit="${unit.id}"${checked ? " checked" : ""}${dis(disabled || stopFlow.phase !== "choose")}>
    <span class="head"><strong>${esc(unit.name)}</strong>${status}${impliedPlayer ? chip("随服务组一并关闭", "warn") : ""}<span class="small muted">${facts}</span></span>
    <span class="body"><span>${esc(unit.role || "")}</span>${components}${activity}${unit.running ? `<span class="impact"><b>停止后：</b>${esc(unit.impact || "")}</span>` : ""}${deps ? `<span class="small">${esc(deps)}</span>` : ""}</span></label>`;
}
function stopDoneText(results) {
  const stopped = results.filter((r) => r.outcome === "stopped").length;
  const idle = results.filter((r) => r.outcome === "not_running").length;
  if (!stopped) return "所选项目本来就没有在运行，无需停止。";
  return `已停止 ${stopped} 项${idle ? `；${idle} 项本来就未运行` : ""}。需要时可在首页重新启动。`;
}
function renderStopDialog() {
  if (!stopFlow) return;
  const flow = stopFlow;
  const box = $("#appDialog");
  const keepFocus = box.open && box.contains(document.activeElement) ? document.activeElement.dataset.stopUnit || document.activeElement.dataset.action || "" : "";
  const scroller = $(".dialog-content", box);
  const scroll = scroller ? scroller.scrollTop : 0;
  let content;
  let actions = "";
  let summary = "";
  if (flow.phase === "loading") content = `<div class="loading"><span class="spinner"></span>正在读取本启动器管理的进程…</div>`;
  else if (flow.phase === "done") {
    const failed = flow.results.filter((r) => r.outcome === "failed");
    content = `${flow.error ? `<div class="notice error">${icon("alert")}<span class="grow">${esc(flow.error)}</span></div>` : failed.length ? `<div class="notice error">${icon("alert")}<span class="grow">有 ${failed.length} 项未能停止，可重试或查看日志。</span></div>` : `<div class="notice ok">${icon("check")}<span class="grow">${stopDoneText(flow.results)}</span></div>`}
      <ul class="result-list">${flow.results.map((r) => `<li class="${esc(r.outcome)}">${icon(r.outcome === "stopped" ? "check" : r.outcome === "failed" ? "alert" : "info")}<span><strong>${esc(UNIT_NAMES[r.id] || r.id)} · ${r.outcome === "stopped" ? "已停止" : r.outcome === "failed" ? "未能停止" : "未在运行"}</strong><br><span class="small muted">${esc(r.detail)}</span></span></li>`).join("")}</ul>`;
    actions = `${failed.length ? `<button type="button" class="button danger" data-action="stop-retry">${icon("refresh")}重试失败项</button>` : ""}<button type="button" class="button primary" data-action="close-dialog">完成</button>`;
  } else {
    const plan = stopPlan();
    const list = overview?.units || [];
    const foreign = overview?.foreign || [];
    const runningCount = plan.runningIds.size;
    const disabledAll = flow.phase === "running";
    content = `${overviewError ? `<div class="notice error">${icon("alert")}<span class="grow">${esc(overviewError)}</span></div>` : ""}
      ${runningCount ? "" : `<div class="notice info">${icon("info")}<span class="grow">本启动器当前没有正在运行的进程。</span></div>`}
      ${list.map((u) => renderStopUnit(u, plan)).join("")}
      ${foreign.map((f) => `<div class="notice warn">${icon("alert")}<span class="grow">${esc(f.detail)}</span></div>`).join("")}
      <p class="small faint">只会停止由本启动器启动的进程；${overview?.checkedAt ? `状态更新于 ${esc(overview.checkedAt)}，每 2 秒刷新。` : ""}已写入 players.db 的数据不会丢失。</p>`;
    summary = flow.phase === "running" ? '<span class="spinner" style="width:13px;height:13px;vertical-align:-2px"></span> 正在停止，请稍候…' : plan.all.length ? `将停止：${plan.all.map((id) => UNIT_NAMES[id]).join("、")}` : runningCount ? "请选择要停止的项目" : "";
    actions = `<button type="button" class="button danger-outline" data-action="stop-all"${dis(disabledAll || !runningCount)}>全部停止</button><button type="button" class="button danger" data-action="stop-selected"${dis(disabledAll || !plan.chosen.size)}>${flow.phase === "running" ? '<span class="spinner" style="width:14px;height:14px"></span>正在停止…' : `${icon("stop")}停止所选${plan.all.length ? `（${plan.all.length}）` : ""}`}</button>`;
  }
  const sub = flow.phase === "done" ? "以下是每一项的实际结果：已确认进程退出才记为“已停止”。" : flow.phase === "running" ? "正在停止所选项目（游戏窗口会先于服务组关闭），完成前无法关闭此窗口。" : "选择要停止的项目，下方说明每项的职责、当前状态和停止后的影响。";
  dialog("停止服务", content, actions, { kind: "stop", wide: true, sub, summary, noCancel: flow.phase === "done", cancelLabel: "取消", focus: keepFocus ? `[data-stop-unit="${keepFocus}"],[data-action="${keepFocus}"]` : flow.phase === "done" ? ".dialog-actions .button.primary" : "[data-stop-unit]:not([disabled]),.dialog-head .icon-button" });
  dialogLocked = flow.phase === "running";
  const nextScroller = $(".dialog-content", box);
  if (nextScroller) nextScroller.scrollTop = scroll;
  $$('[data-action="close-dialog"]', box).forEach((b) => (b.disabled = dialogLocked));
}
async function runStop(unitIds) {
  if (!stopFlow || stopFlow.phase === "running" || !unitIds.length) return;
  stopFlow.phase = "running";
  clearInterval(overviewTimer);
  renderStopDialog();
  updateChrome();
  try {
    const result = await api("/api/services/stop", { method: "POST", body: { units: unitIds } });
    if (!stopFlow) return;
    stopFlow.results = result.results || [];
    stopFlow.ok = result.ok !== false;
    if (result.state) applyState(result.state);
    if (stopFlow.results.some((r) => r.id === "services" || r.id === "admin")) {
      if (stopFlow.results.some((r) => (r.id === "services" || r.id === "admin") && r.outcome === "stopped")) {
        adminUrl = "";
        adminError = "";
      }
    }
  } catch (e) {
    if (!stopFlow) return;
    stopFlow.results = unitIds.map((id) => ({ id, outcome: "failed", detail: "请求未完成，实际状态请以刷新后的列表为准。" }));
    stopFlow.error = e.message;
  }
  stopFlow.phase = "done";
  renderStopDialog();
  await syncState(true);
  loadOverview().then(() => page === "services" && refreshUnitsPanel());
  const stopped = stopFlow?.results?.filter((r) => r.outcome === "stopped").length || 0;
  if (stopFlow && !stopFlow.error) toast(stopped ? `已停止 ${stopped} 项。` : "没有需要停止的项目。", stopFlow.ok ? "success" : "warn");
}

/* 健康检查 */
async function openDiagnostics() {
  dialog("健康检查", `<div class="loading"><span class="spinner"></span>正在检查游戏文件、存档与端口…</div>`, "", { kind: "diagnostics", wide: true, cancelLabel: "关闭" });
  let data;
  try {
    data = await api("/api/diagnostics");
  } catch (e) {
    if (dialogKind() !== "diagnostics") return;
    dialog("健康检查", `<div class="notice error">${icon("alert")}<span class="grow">检查未完成：${esc(e.message)}</span></div>`, `<button type="button" class="button primary" data-action="open-diagnostics">${icon("refresh")}重新检查</button>`, { kind: "diagnostics", wide: true, cancelLabel: "关闭" });
    return;
  }
  if (dialogKind() !== "diagnostics") return;
  const checks = data.checks || [];
  const errors = checks.filter((c) => c.status === "error").length;
  const warns = checks.filter((c) => c.status === "warn").length;
  const head = demo
    ? `<div class="notice info">${icon("info")}<span class="grow">健康检查界面示例：未检查本机环境，以下内容不代表实际检测结果。</span></div>`
    : errors
    ? `<div class="notice error">${icon("alert")}<span class="grow">发现 ${errors} 个问题${warns ? `、${warns} 个提醒` : ""}，请按右侧建议处理。</span></div>`
    : warns
      ? `<div class="notice warn">${icon("info")}<span class="grow">没有阻止启动的问题，有 ${warns} 个提醒。</span></div>`
      : `<div class="notice ok">${icon("check")}<span class="grow">全部检查通过。</span></div>`;
  const rows = checks
    .map((c) => `<li class="${esc(c.status)}"><i class="dot ${c.status === "ok" ? "ok" : c.status === "error" ? "error" : c.status === "warn" ? "warn" : ""}"></i><strong>${esc(c.name)}</strong><span>${esc(c.detail)}</span>${c.action ? `<span class="action">建议：${esc(c.action)}</span>` : ""}</li>`)
    .join("");
  window.__lastDiagnostics = checks;
  dialog("健康检查", `${head}<ul class="check-list">${rows}</ul><p class="small faint">检查于 ${esc(data.checkedAt || "")}，只读取文件与端口，不修改任何数据。</p>`, `<button type="button" class="button" data-action="copy-diagnostics">${icon("copy")}复制结果</button><button type="button" class="button primary" data-action="open-diagnostics">${icon("refresh")}重新检查</button>`, { kind: "diagnostics", wide: true, cancelLabel: "关闭" });
}

/* ---------- 渲染调度 ---------- */
function captureFocus() {
  const el = document.activeElement;
  if (!el || !$("#main").contains(el) || el === $("#main")) return null;
  if (el.id) return { selector: `#${CSS.escape(el.id)}`, start: el.selectionStart, end: el.selectionEnd };
  if (el.dataset?.action) {
    const keys = ["id", "player", "level", "view", "filter", "theme", "page"].filter((k) => el.dataset[k] !== undefined);
    return { selector: `[data-action="${el.dataset.action}"]${keys.map((k) => `[data-${k}="${CSS.escape(el.dataset[k])}"]`).join("")}` };
  }
  if (el.name && el.form?.id) return { selector: `#${el.form.id} [name="${el.name}"]`, start: el.selectionStart, end: el.selectionEnd };
  return null;
}
function restoreFocus(saved) {
  if (!saved) return;
  const el = $(saved.selector, $("#main"));
  if (!el || el.disabled) return;
  el.focus({ preventScroll: true });
  if (typeof saved.start === "number" && el.setSelectionRange) {
    try {
      el.setSelectionRange(saved.start, saved.end);
    } catch {
      /* 某些输入类型不支持选区 */
    }
  }
}
function pageSignature() {
  const s = appState;
  const base = { c: connecting, e: connectionError, lb: localBusy, ps: playerSaving };
  const lock = { ...base, r: s.running, b: s.busy, st: s.starting, sp: s.stopping, a: String(s.activeAccountId || "") };
  if (page === "home" || page === "services") {
    const up = uptimeSeconds();
    return JSON.stringify({ ...lock, p: s.progress, m: s.statusMessage, err: s.error, cur: accountId(s.currentAccount), pk: s.playerKind, po: s.playerOptions, sv: s.services, u: s.units, re: s.canReopenPlayer, rc: s.recentAccountIds, up: up === null ? null : Math.floor(up / 60) });
  }
  if (page === "gm") return JSON.stringify({ ...base, r: s.running, u: s.units, url: adminUrl, l: adminLoading });
  if (page === "settings") return JSON.stringify({ ...base, v: s.version, g: s.gamePath });
  return JSON.stringify(lock);
}
function render() {
  updateChrome();
  const views = { home: renderHome, accounts: renderAccounts, gm: renderGM, services: renderServices, backups: renderBackups, settings: renderSettings };
  const focus = captureFocus();
  $("#main").innerHTML = views[page]();
  restoreFocus(focus);
  if (page === "services") {
    scrollLogs();
    bindLogScroll();
  }
  renderedSignature = pageSignature();
}
function refreshGMToolbar() {
  const bar = $(".gm-toolbar");
  if (!bar) return render();
  const holder = document.createElement("div");
  holder.innerHTML = renderGM();
  const focus = captureFocus();
  bar.replaceWith(holder.querySelector(".gm-toolbar"));
  restoreFocus(focus);
}
function refreshPage() {
  // 轮询带来的状态变化只局部刷新，避免打断输入、重载 GM 页面或丢失滚动位置。
  updateChrome();
  if (page === "home" || page === "backups" || page === "settings") render();
  else if (page === "services") {
    refreshUnitsPanel();
    renderedSignature = pageSignature();
  } else if (page === "accounts") {
    replaceAccountPane();
    refreshProfilePanel();
    renderedSignature = pageSignature();
  } else if (page === "gm") {
    if (!adminUrl && $("#gmFrame")) render();
    else refreshGMToolbar();
    renderedSignature = pageSignature();
  }
}
function bindLogScroll() {
  const view = $("#logView");
  if (!view || view.dataset.bound) return;
  view.dataset.bound = "1";
  view.addEventListener("scroll", () => {
    const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 40;
    if (atBottom !== logFollow) {
      logFollow = atBottom;
      const live = $("#logLive");
      if (live) live.innerHTML = logLiveText();
    }
  });
}

/* ---------- 数据同步 ---------- */
function applyState(next) {
  const before = units();
  appState = next || appState;
  const after = units();
  if ((before.services || before.admin) && !after.services && !after.admin) {
    adminUrl = "";
    adminError = "";
  }
}
async function syncState(force = false) {
  if (statePollPending && !force) return;
  const requestId = ++stateRequestId;
  statePollPending = true;
  try {
    const next = await api("/api/state");
    if (requestId !== stateRequestId) return;
    const reconnected = Boolean(connectionError) || connecting;
    applyState(next);
    connecting = false;
    connectionError = "";
    if (logPaused && Number(appState.logCount) > lastLogId) pendingWhilePaused = Number(appState.logCount) - lastLogId;
    if (reconnected || force || pageSignature() !== renderedSignature) refreshPage();
    else updateChrome();
    if (reconnected && page === "gm" && !adminUrl && !adminLoading) openAdmin();
  } catch (e) {
    if (requestId !== stateRequestId) return;
    const changed = connectionError !== e.message;
    connectionError = e.message;
    connecting = false;
    if (changed || force) refreshPage();
  } finally {
    if (requestId === stateRequestId) statePollPending = false;
  }
}
function appendLogLines(incoming) {
  const view = $("#logView");
  if (!view) return;
  const visible = incoming.filter((e) => filteredLogsMatch(e));
  if (!visible.length || $(".empty", view) || view.childElementCount + visible.length > LOG_RENDER_LIMIT + 50) {
    renderLogsOnly();
    return;
  }
  view.insertAdjacentHTML(
    "beforeend",
    visible
      .map((e) => {
        const level = String(e.level || "info").toLowerCase();
        return `<div class="log-line ${esc(level)}"><span class="t">${esc(shortTime(e.time))}</span><span class="lv">${esc(LEVEL_LABEL[level] || level.toUpperCase())}</span><span class="src">${esc(SOURCE_LABEL[e.source] || e.source || "启动器")}</span><span class="msg">${esc(e.message)}</span></div>`;
      })
      .join(""),
  );
  $("#logCount").textContent = logStatusText();
  const levels = $("#logLevels");
  if (levels && !levels.contains(document.activeElement)) levels.innerHTML = renderLogLevelButtons();
  if (logFollow) scrollLogs();
}
function filteredLogsMatch(e) {
  const terms = logQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const level = String(e.level || "info").toLowerCase();
  if (logLevel !== "all" && level !== logLevel && !(logLevel === "warning" && level === "warn")) return false;
  if (logSource !== "all" && (e.source || "launcher") !== logSource) return false;
  const hay = `${e.message} ${e.time}`.toLowerCase();
  return terms.every((t) => hay.includes(t));
}
async function syncLogs() {
  if (logPaused) return;
  try {
    const data = await api(`/api/logs?after=${encodeURIComponent(lastLogId)}`);
    if (data.capacity) logCapacity = data.capacity;
    const incoming = (data.entries || []).filter((e) => Number(e.id) > lastLogId);
    pendingWhilePaused = 0;
    if (!incoming.length) return;
    entries.push(...incoming);
    if (entries.length > logCapacity) entries = entries.slice(-logCapacity);
    lastLogId = data.lastId ?? incoming.at(-1)?.id ?? lastLogId;
    if (page === "services") appendLogLines(incoming);
    if (page === "home" && incoming.some((e) => e.level === "error" || e.level === "warning")) replaceHomeOverview();
  } catch {
    /* 连接异常由状态轮询统一提示，这里不重复弹出 */
  }
}
function replaceHomeOverview() {
  const panel = $('#main [aria-label="运行概况"]');
  if (!panel) return;
  const holder = document.createElement("div");
  holder.innerHTML = renderHomeOverview();
  panel.replaceWith(holder.firstElementChild);
}
async function refreshAccounts() {
  accounts = normalizeAccounts(await api(accountsEndpoint()));
  const players = playerAccounts();
  if (!players.some((a) => accountId(a) === selectedId))
    selectedId = accountId(players.find((a) => accountId(a) === accountId(appState.currentAccount)) || players[0]);
  updateChrome();
}
async function refreshBackups(showErrors = false) {
  try {
    backups = normalizeBackups(await api("/api/backups"));
    backupError = "";
  } catch (e) {
    backupError = e.message;
    if (showErrors) toast(e.message, "error");
  }
  backupsLoaded = true;
  if (page === "backups") render();
  if (page === "home") replaceHomeOverview();
}
async function loadProfile(id = selectedId) {
  if (!id) {
    profile = null;
    return;
  }
  profileError = "";
  try {
    const result = await api(`/api/accounts/${encodeURIComponent(id)}`);
    if (id !== selectedId) return;
    profile = { ...result, id: String(result.id ?? result.uin ?? id) };
    stagedAvatar = "keep";
    stagedImage = "";
    profileDirty = false;
    profileDraft = null;
  } catch (e) {
    if (id !== selectedId) return;
    profile = null;
    profileError = e.message;
    toast(e.message, "error");
  }
  if (page === "accounts") refreshProfilePanel();
}

/* ---------- 操作 ---------- */
async function setPlayer(player) {
  if (connecting || writesLocked() || player === playerKind()) return;
  const option = playerOptions().find((value) => value.id === player);
  if (!option?.available) {
    toast(option?.detail || "该播放器文件缺失，暂时无法切换。", "warn");
    return;
  }
  playerSaving = true;
  render();
  try {
    const result = await api("/api/player", { method: "POST", body: { player } });
    if (result && typeof result === "object") appState = { ...appState, ...result };
    await syncState(true);
    toast(`已切换为 ${selectedPlayer().name}，下次启动继续使用。`);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    playerSaving = false;
    render();
    $(`[data-action="set-player"][data-player="${playerKind()}"]`)?.focus();
  }
}
async function startGame() {
  if (busy() || running()) return;
  if (connecting || connectionError) return toast("请先连接本地启动器服务。", "warn");
  if (!playerReady()) return toast(playerProblem(), "error");
  if (!selectedId) {
    toast("请先新建或选择一个账号。", "warn");
    location.hash = "#accounts";
    return;
  }
  localBusy = true;
  render();
  try {
    await api("/api/start", { method: "POST", body: { account_id: selectedId } });
    await syncState(true);
    toast("正在启动游戏服务，就绪后会自动打开游戏窗口。");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    localBusy = false;
    refreshPage();
  }
}
async function stopGame() {
  // 启动过程中“取消启动”：沿用原有的 /api/stop（停止本次全部服务）。
  localBusy = true;
  refreshPage();
  try {
    await api("/api/stop", { method: "POST", body: {} });
    adminUrl = "";
    await syncState(true);
    toast("已取消启动，服务已停止。");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    localBusy = false;
    refreshPage();
  }
}
async function reopenPlayer(button) {
  if (button) button.disabled = true;
  try {
    const result = await api("/api/player/reopen", { method: "POST", body: {} });
    if (result && typeof result === "object" && "running" in result) applyState(result);
    await syncState(true);
    toast("正在重新打开游戏窗口…");
  } catch (e) {
    toast(e.message, "error");
    if (button) button.disabled = false;
  }
}
async function openAdmin(force = false) {
  if (demo) {
    adminUrl = "";
    adminError = "";
    if (page === "gm") render();
    return;
  }
  if (adminLoading) return;
  if (adminUrl && !force) {
    try {
      const info = await api("/api/admin-info");
      if (info.ready) return;
    } catch (e) {
      adminError = e.message;
      return;
    }
    adminUrl = "";
  }
  adminLoading = true;
  adminError = "";
  if (page === "gm") render();
  try {
    const result = await api("/api/admin/start", { method: "POST", body: {} });
    adminUrl = result.url || "";
    if (!adminUrl) throw new Error("后台未返回访问地址，请查看服务日志。");
  } catch (e) {
    adminError = e.message;
    adminUrl = "";
  } finally {
    adminLoading = false;
    if (page === "gm") render();
  }
}
function clearProfileDraft() {
  profile = null;
  profileError = "";
  profileDirty = false;
  profileDraft = null;
  stagedAvatar = "keep";
  stagedImage = "";
}
async function selectAccount(id, discard = false) {
  if (profileSaving) {
    toast("资料正在保存，请稍候。", "warn");
    return false;
  }
  if (id === selectedId) {
    if (page === "accounts" && !profile) await loadProfile();
    if (page === "gm") render();
    return true;
  }
  if (!findAccount(id)) return false;
  if (profileDirty && !discard) {
    dialog("放弃未保存的修改？", `<p>「${esc(accountName(profile))}」的资料还没有保存。切换到其他账号会放弃这些修改。</p>`, `<button type="button" class="button primary" data-action="confirm-switch" data-id="${esc(id)}">放弃并切换</button>`, { cancelLabel: "继续编辑" });
    return false;
  }
  clearProfileDraft();
  selectedId = id;
  gmFrameHref = ""; // 换了账号：GM 下次打开新账号，而不是停留在上一个账号的页面
  if (page === "gm") render();
  else if (page === "accounts") {
    replaceAccountPane();
    refreshProfilePanel();
  } else render();
  if (page === "accounts") await loadProfile();
  return true;
}
const NEW_UIN_HINT = "只能使用数字；900000–999999 为系统机器人保留。";
function newUinProblem(uin) {
  if (!uin) return "请输入账号 UID。";
  if (!/^\d+$/.test(uin)) return "UID 只能使用数字。";
  if (Number(uin) >= 900000 && Number(uin) < 1000000) return "900000–999999 是系统机器人保留号段，请换一个。";
  if (findAccount(uin)) return `账号 ${uin} 已存在，可直接在列表中选择。`;
  return "";
}
// 边输入边校验：错误立即显示，改对后恢复默认说明。
function showNewUinHint(message) {
  const input = $("#newUin");
  const hint = $("#newUinHint");
  if (!input || !hint) return;
  if (message) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
  hint.textContent = message || NEW_UIN_HINT;
  hint.style.color = message ? "var(--danger)" : "";
}
function newAccountDialog(prefill = "") {
  if (profileSaving) return toast("资料正在保存，请稍候。", "warn");
  if (writesLocked()) return toast(lockReason(), "warn");
  dialog(
    "新建账号",
    `<p class="muted">账号 UID 即游戏存档编号，创建后不可修改；昵称和头像可随时修改。</p>${profileDirty ? '<div class="notice warn">' + icon("info") + '<span class="grow">创建后会切换到新账号，当前未保存的资料修改将被放弃。</span></div>' : ""}<label class="field"><span>账号 UID</span><input class="field-control" id="newUin" inputmode="numeric" pattern="[0-9]+" maxlength="20" placeholder="例如 10001" value="${esc(prefill)}" autocomplete="off"><small class="hint" id="newUinHint" style="margin:0" aria-live="polite">${esc(NEW_UIN_HINT)}</small></label>`,
    `<button type="button" class="button primary" data-action="confirm-create">${icon("plus")}创建账号</button>`,
    { focus: "#newUin" },
  );
}
async function createAccount(button) {
  if (profileSaving) return toast("资料正在保存，请稍候。", "warn");
  const input = $("#newUin");
  const uin = input.value.trim();
  const problem = newUinProblem(uin);
  if (problem) {
    showNewUinHint(problem);
    input.focus();
    return;
  }
  button.disabled = true;
  try {
    const created = await api("/api/accounts", { method: "POST", body: { uin } });
    closeDialog(true);
    await refreshAccounts();
    clearProfileDraft();
    selectedId = accountId(created) || uin;
    accountQuery = "";
    render();
    if (page === "accounts") await loadProfile();
    toast(`账号 ${selectedId} 已创建。`);
  } catch (e) {
    showNewUinHint(e.message);
    $("#newUin")?.focus();
    button.disabled = false;
  }
}
function deleteAccountDialog() {
  const a = profile || currentAccount();
  if (!a) return;
  dialog(
    "删除账号",
    `<p>将删除账号「${esc(accountName(a))}」（UID ${esc(selectedId)}）及其全部游戏存档。删除前会自动创建一份备份，可在“存档备份”中恢复。</p><label class="field"><span>输入 UID <strong class="mono">${esc(selectedId)}</strong> 确认删除</span><input class="field-control" id="confirmDeleteUin" autocomplete="off" inputmode="numeric" placeholder="${esc(selectedId)}"></label>`,
    `<button type="button" class="button danger" data-action="confirm-delete" disabled>${icon("trash")}删除账号</button>`,
    { focus: "#confirmDeleteUin" },
  );
}
async function deleteAccount(button) {
  const value = $("#confirmDeleteUin").value.trim();
  if (value !== selectedId) return toast("输入的 UID 与当前账号不一致。", "warn");
  button.disabled = true;
  try {
    await api(`/api/accounts/${encodeURIComponent(selectedId)}`, { method: "DELETE", body: { confirm_uin: value } });
    closeDialog(true);
    clearProfileDraft();
    await refreshAccounts();
    render();
    await loadProfile();
    await refreshBackups();
    toast("账号已删除，删除前的备份已保存。");
  } catch (e) {
    toast(e.message, "error");
    button.disabled = false;
  }
}
async function saveProfile(form) {
  if (profileSaving) return;
  if (writesLocked()) return toast(lockReason(), "warn");
  const body = {
    name: form.elements.name.value.trim(),
    signature: form.elements.signature.value,
    avatar: stagedAvatar,
    profile_expected: profile.profile_expected,
  };
  if (stagedImage) body.image_data = stagedImage;
  const nameInput = form.elements.name;
  if (!body.name) {
    nameInput.setAttribute("aria-invalid", "true");
    nameInput.focus();
    return toast("请填写昵称。", "warn");
  }
  if (body.name !== (profile.name || profile.nickname || "") && ([...body.name].length < 2 || [...body.name].length > 7)) {
    nameInput.setAttribute("aria-invalid", "true");
    nameInput.focus();
    return toast("昵称需要 2–7 个字。", "warn");
  }
  profileSaving = true;
  refreshProfilePanel();
  try {
    await api(`/api/accounts/${encodeURIComponent(selectedId)}`, { method: "PATCH", body });
    profileDirty = false;
    profileDraft = null;
    await refreshAccounts();
    await loadProfile();
    toast("资料已保存。");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    profileSaving = false;
    if (page === "accounts") {
      replaceAccountPane();
      refreshProfilePanel();
    }
  }
}
function beginCrop(file) {
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) return toast("请使用小于 15 MB 的头像图片。", "warn");
  const reader = new FileReader();
  reader.onerror = () => toast("图片读取失败，请换一张图片。", "error");
  reader.onload = () => {
    const image = new Image();
    image.onerror = () => toast("无法识别这张图片。", "error");
    image.onload = () => {
      cropImage = image;
      crop = { zoom: 1, x: 0, y: 0 };
      dialog(
        "裁剪头像",
        `<p class="muted">拖动图片调整位置，用滑块放大；保存为 256×256 的正方形头像。</p><canvas id="cropCanvas" class="crop-preview" width="480" height="480" aria-label="头像裁剪预览"></canvas><label class="crop-control"><span>缩放</span><input type="range" id="cropZoom" min="1" max="3" value="1" step="0.01"></label>`,
        `<button type="button" class="button primary" data-action="confirm-crop">${icon("check")}使用这张头像</button>`,
        { focus: "#cropZoom" },
      );
      drawCrop();
      installCropDrag();
    };
    image.src = reader.result;
  };
  reader.readAsDataURL(file);
}
function drawCrop() {
  const canvas = $("#cropCanvas");
  if (!canvas || !cropImage) return;
  const s = Math.max(canvas.width / cropImage.width, canvas.height / cropImage.height) * crop.zoom;
  const w = cropImage.width * s;
  const h = cropImage.height * s;
  crop.x = Math.max(-(w - canvas.width) / 2, Math.min((w - canvas.width) / 2, crop.x));
  crop.y = Math.max(-(h - canvas.height) / 2, Math.min((h - canvas.height) / 2, crop.y));
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(cropImage, (canvas.width - w) / 2 + crop.x, (canvas.height - h) / 2 + crop.y, w, h);
}
function installCropDrag() {
  const canvas = $("#cropCanvas");
  let origin = null;
  canvas.addEventListener("pointerdown", (e) => {
    origin = { px: e.clientX, py: e.clientY, x: crop.x, y: crop.y };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!origin) return;
    const ratio = canvas.width / canvas.getBoundingClientRect().width;
    crop.x = origin.x + (e.clientX - origin.px) * ratio;
    crop.y = origin.y + (e.clientY - origin.py) * ratio;
    drawCrop();
  });
  canvas.addEventListener("pointerup", () => (origin = null));
  canvas.addEventListener("pointercancel", () => (origin = null));
}
function finishCrop() {
  const source = $("#cropCanvas");
  const output = document.createElement("canvas");
  output.width = output.height = 256;
  output.getContext("2d").drawImage(source, 0, 0, 256, 256);
  stagedImage = output.toDataURL("image/png");
  stagedAvatar = "custom";
  profileDirty = true;
  closeDialog(true);
  captureDraft();
  refreshProfilePanel();
}
function captureDraft() {
  const form = $("#profileForm");
  if (form) profileDraft = { name: form.elements.name.value, signature: form.elements.signature.value };
}
async function createBackup(button) {
  if (writesLocked()) return toast(lockReason(), "warn");
  if (button) button.disabled = true;
  try {
    const manifest = await api("/api/backups", { method: "POST", body: { reason: "manual" } });
    await refreshBackups();
    toast(`已备份玩家与公会存档（${humanSize(manifest?.size)}）。`);
  } catch (e) {
    toast(e.message, "error");
    if (button) button.disabled = false;
  }
}
function restoreDialog(id) {
  const b = backups.find((x) => String(x.id) === id);
  if (!b) return;
  dialog(
    "恢复存档快照",
    `<p>将用「${esc(b.id)}」覆盖当前的玩家存档与公会数据。</p><dl class="kv"><dt>快照时间</dt><dd>${esc(dateTime(b.created))}（${esc(relativeTime(b.created))}）</dd><dt>类型</dt><dd>${esc(backupReason(b.reason))}</dd><dt>大小</dt><dd>${esc(humanSize(b.size))} · ${b.files?.length ?? 0} 个文件</dd></dl><div class="notice info">${icon("shield")}<span class="grow">恢复前会自动备份当前存档，可随时再恢复回来。${units().admin ? "独立 GM 后台正在运行，恢复时会先关闭它，之后可在“GM 管理”重新打开。" : ""}</span></div>`,
    `<button type="button" class="button primary" data-action="confirm-restore" data-id="${esc(id)}">${icon("refresh")}确认恢复</button>`,
  );
}
async function restoreBackup(id, button) {
  button.disabled = true;
  try {
    await api(`/api/backups/${encodeURIComponent(id)}/restore`, { method: "POST", body: { confirm: true } });
    closeDialog(true);
    clearProfileDraft();
    adminUrl = "";
    await refreshBackups();
    await refreshAccounts();
    await syncState(true);
    toast("存档已恢复，恢复前的存档已自动备份。");
  } catch (e) {
    toast(e.message, "error");
    button.disabled = false;
  }
}
async function saveSettings(patch) {
  const save = async () => {
    const previous = { ...settings };
    settings = { ...settings, ...patch };
    applyTheme();
    if (page === "settings") render();
    try {
      const next = await api("/api/settings", { method: "PATCH", body: patch });
      if (next) settings = { ...settings, ...next };
      applyTheme();
      if (page === "settings") render();
    } catch (e) {
      settings = previous;
      applyTheme();
      if (page === "settings") render();
      toast(e.message, "error");
    }
  };
  settingsSaveQueue = settingsSaveQueue.then(save, save);
  return settingsSaveQueue;
}
async function copyText(text, okMessage) {
  try {
    await navigator.clipboard.writeText(text);
    toast(okMessage);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    document.body.append(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    toast(ok ? okMessage : "复制失败，请改用导出。", ok ? "success" : "error");
  }
}
function logQueryString() {
  const q = new URLSearchParams();
  if (logLevel !== "all") q.set("level", logLevel);
  if (logSource !== "all") q.set("source", logSource);
  if (logQuery.trim()) q.set("query", logQuery.trim());
  return q.toString();
}
async function copyLogs() {
  const lines = filteredLogs();
  if (!lines.length) return toast("没有可复制的日志。", "warn");
  const text = lines.map((e) => `[${shortTime(e.time)}] [${LEVEL_LABEL[String(e.level).toLowerCase()] || "INFO"}] [${SOURCE_LABEL[e.source] || e.source || "启动器"}] ${e.message}`).join("\n");
  await copyText(text, `已复制 ${numeric(lines.length)} 条日志。`);
}
async function exportLogs() {
  try {
    const qs = logQueryString();
    let blob;
    if (demo) {
      const lines = filteredLogs().map((e) => `[${shortTime(e.time)}] [${LEVEL_LABEL[e.level] || "INFO"}] [${SOURCE_LABEL[e.source] || e.source}] ${e.message}`);
      blob = new Blob(["只读演示日志（页面示例，未连接真实 API）\n", lines.join("\n")], { type: "text/plain;charset=utf-8" });
    } else {
      const response = await fetch(`/api/logs/export${qs ? `?${qs}` : ""}`, { headers: { "X-PVZ-Token": token }, cache: "no-store" });
      if (!response.ok) throw new Error("日志导出失败。");
      blob = await response.blob();
    }
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `PVZ-运行日志-${new Date().toISOString().slice(0, 10)}${qs ? "-筛选" : ""}.txt`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast(qs ? "已按当前筛选导出日志。" : "运行日志已导出。");
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ---------- 导航 ---------- */
async function navigate() {
  const next = location.hash.slice(1);
  const previous = page;
  page = pages[next] ? next : "home";
  render();
  if (previous !== page) $("#main").scrollTop = 0;
  if (page !== "services" && !stopFlow) {
    clearInterval(overviewTimer);
    overviewTimer = null;
  }
  if (page === "accounts" && (!profileDirty || !profile || accountId(profile) !== selectedId)) await loadProfile();
  if (page === "gm") await openAdmin();
  if (page === "backups") await refreshBackups(true);
  if (page === "home" && !backupsLoaded) await refreshBackups();
  if (page === "services") {
    await loadOverview();
    if (page === "services") refreshUnitsPanel();
    if (!stopFlow) startOverviewPolling();
  }
}

/* ---------- 事件 ---------- */
document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target || target.disabled) return;
  const action = target.dataset.action;
  try {
    if (action === "start") await startGame();
    else if (action === "set-player") await setPlayer(target.dataset.player);
    else if (action === "stop") await stopGame();
    else if (action === "open-stop") await openStopDialog();
    else if (action === "stop-selected") await runStop([...stopPlan().all]);
    else if (action === "stop-all") await runStop([...stopPlan().runningIds]);
    else if (action === "stop-retry") {
      const failed = (stopFlow?.results || []).filter((r) => r.outcome === "failed").map((r) => r.id);
      stopFlow.phase = "choose";
      stopFlow.error = "";
      await loadOverview();
      stopFlow.selected = new Set(failed);
      renderStopDialog();
      startOverviewPolling();
    } else if (action === "reopen-player") await reopenPlayer(target);
    else if (action === "open-diagnostics") await openDiagnostics();
    else if (action === "copy-diagnostics") {
      const checks = window.__lastDiagnostics || [];
      await copyText(checks.map((c) => `[${c.status}] ${c.name}：${c.detail}${c.action ? `（建议：${c.action}）` : ""}`).join("\n"), "检查结果已复制。");
    } else if (action === "reconnect") await boot();
    else if (action === "go-services") location.hash = "#services";
    else if (action === "toggle-theme") await saveSettings({ theme: document.documentElement.dataset.theme === "dark" ? "light" : "dark" });
    else if (action === "refresh-state") {
      await syncState(true);
      await syncLogs();
      await loadOverview();
      refreshUnitsPanel();
    } else if (action === "select-account") await selectAccount(target.dataset.id);
    else if (action === "pick-account") await selectAccount(target.dataset.id);
    else if (action === "confirm-switch") {
      closeDialog(true);
      await selectAccount(target.dataset.id, true);
    } else if (action === "new-account") newAccountDialog(target.dataset.uin || "");
    else if (action === "confirm-create") await createAccount(target);
    else if (action === "delete-account") deleteAccountDialog();
    else if (action === "confirm-delete") await deleteAccount(target);
    else if (action === "reload-profile") await loadProfile();
    else if (action === "upload-avatar") $("#avatarUpload").click();
    else if (action === "choose-avatar") {
      stagedAvatar = Number(target.dataset.id);
      stagedImage = "";
      profileDirty = true;
      captureDraft();
      $$(".avatar-choice").forEach((el) => el.setAttribute("aria-pressed", String(el === target)));
      $("#profileAvatarPreview").innerHTML = avatar({ name: accountName(profile), avatar_url: target.dataset.url }, "lg");
      updateProfileFooter();
    } else if (action === "confirm-crop") finishCrop();
    else if (action === "gm-account") {
      const previous = gmFrameHref;
      gmView = "player";
      gmFrameHref = "";
      if (await selectAccount(target.dataset.id)) location.hash = "#gm";
      else gmFrameHref = previous;
    } else if (action === "gm-view") {
      const uin = gmLocation().uin || selectedId;
      gmView = target.dataset.view;
      gmFrameHref = gmView === "items" && uin ? `/items?uin=${encodeURIComponent(uin)}` : "";
      render();
      if (!adminUrl) await openAdmin();
    } else if (action === "open-admin" || action === "reload-admin") await openAdmin(true);
    else if (action === "log-level") {
      logLevel = target.dataset.level;
      renderLogsOnly();
      $(`[data-action="log-level"][data-level="${logLevel}"]`)?.focus();
    } else if (action === "toggle-components") {
      componentsOpen = target.getAttribute("aria-expanded") !== "true";
      refreshUnitsPanel();
      $('#unitsPanel [data-action="toggle-components"]')?.focus();
    } else if (action === "clear-log-filters") {
      logLevel = "all";
      logSource = "all";
      logQuery = "";
      const search = $("#logSearch");
      if (search) search.value = "";
      const source = $("#logSource");
      if (source) source.value = "all";
      renderLogsOnly();
      search?.focus();
    } else if (action === "show-errors") {
      logLevel = "error";
      logSource = "all";
      location.hash = "#services";
    } else if (action === "log-follow") {
      logFollow = true;
      scrollLogs();
      $("#logLive").innerHTML = logLiveText();
    } else if (action === "pause-logs") {
      logPaused = !logPaused;
      target.setAttribute("aria-pressed", String(logPaused));
      target.innerHTML = pauseLabel();
      target.title = logPaused ? "继续实时更新" : "暂停实时更新";
      $("#logLive").innerHTML = logLiveText();
      if (!logPaused) await syncLogs();
    } else if (action === "copy-logs") await copyLogs();
    else if (action === "export-logs") await exportLogs();
    else if (action === "create-backup") await createBackup(target);
    else if (action === "refresh-backups") await refreshBackups(true);
    else if (action === "backup-filter") {
      backupFilter = target.dataset.filter;
      backupPage = 1;
      render();
    } else if (action === "backup-page") {
      backupPage = Number(target.dataset.page) || 1;
      render();
    } else if (action === "restore-backup") restoreDialog(target.dataset.id);
    else if (action === "confirm-restore") await restoreBackup(target.dataset.id, target);
    else if (action === "set-theme") await saveSettings({ theme: target.dataset.theme });
    else if (action === "copy-text") await copyText(target.dataset.text || "", "已复制。");
    else if (action === "close-dialog") closeDialog();
  } catch (e) {
    toast(e.message, "error");
  }
});
document.addEventListener("input", (event) => {
  const t = event.target;
  if (t.id === "accountSearch") {
    accountQuery = t.value;
    const selection = t.selectionStart;
    replaceAccountPane();
    const input = $("#accountSearch");
    input.focus();
    input.setSelectionRange(selection, selection);
  } else if (t.id === "logSearch") {
    logQuery = t.value;
    renderLogsOnly();
  } else if (t.id === "cropZoom") {
    crop.zoom = Number(t.value);
    drawCrop();
  } else if (t.id === "confirmDeleteUin") {
    const button = $('[data-action="confirm-delete"]');
    if (button) button.disabled = t.value.trim() !== selectedId;
  } else if (t.id === "newUin") {
    const uin = t.value.trim();
    showNewUinHint(uin ? newUinProblem(uin) : "");
  } else if (t.closest("#profileForm")) {
    profileDirty = true;
    captureDraft();
    t.removeAttribute("aria-invalid");
    const counter = $(`[data-counter="${t.name}"]`);
    if (counter) {
      const max = t.name === "name" ? 7 : 30;
      const length = [...t.value].length;
      counter.textContent = `${length}/${max}`;
      counter.classList.toggle("over", length > max || (t.name === "name" && length > 0 && length < 2));
    }
    updateProfileFooter();
  }
});
document.addEventListener("change", async (event) => {
  const t = event.target;
  if (t.id === "showRobotAccounts") await toggleRobotAccounts(t.checked);
  else if (t.id === "gmAccount" && t.value === "__dashboard__") {
    gmView = "dashboard";
    gmFrameHref = "";
    render();
  } else if (t.id === "gmAccount" || t.id === "homeAccount") {
    const id = t.value;
    const previous = { view: gmView, href: gmFrameHref };
    t.value = t.id === "gmAccount" ? (gmLocation().dashboard ? "__dashboard__" : gmLocation().uin) : selectedId;
    if (t.id === "gmAccount") {
      gmView = "player";
      gmFrameHref = "";
    }
    const accepted = await selectAccount(id);
    if (!accepted && t.id === "gmAccount") {
      gmView = previous.view;
      gmFrameHref = previous.href;
    }
    if (accepted && page === "gm") render();
  } else if (t.id === "logSource") {
    logSource = t.value;
    renderLogsOnly();
  } else if (t.id === "settingScale") await saveSettings({ scale: Number(t.value) });
  else if (t.id === "avatarUpload") {
    beginCrop(t.files[0]);
    t.value = "";
  } else if (t.matches?.("[data-stop-unit]") && stopFlow?.phase === "choose") {
    if (t.checked) stopFlow.selected.add(t.dataset.stopUnit);
    else stopFlow.selected.delete(t.dataset.stopUnit);
    renderStopDialog();
  }
});
document.addEventListener(
  "toggle",
  (event) => {
    if (event.target.matches?.("[data-account-group]") && !accountQuery) {
      const group = event.target.dataset.accountGroup;
      if (event.target.open) collapsedAccountGroups.delete(group);
      else collapsedAccountGroups.add(group);
    }
  },
  true,
);
document.addEventListener("submit", async (event) => {
  if (event.target.id === "profileForm") {
    event.preventDefault();
    await saveProfile(event.target);
  }
});
document.addEventListener("keydown", (event) => {
  const typing = event.target.closest?.("input,textarea,select,[contenteditable=true]");
  if (event.altKey && !event.ctrlKey && !event.metaKey && /^[1-6]$/.test(event.key)) {
    event.preventDefault();
    if ($("#appDialog").open) return;
    location.hash = `#${pageOrder[Number(event.key) - 1]}`;
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && page === "accounts") {
    event.preventDefault();
    const form = $("#profileForm");
    if (form && !$("#appDialog").open) form.requestSubmit();
    return;
  }
  if (event.key === "/" && !typing && !$("#appDialog").open) {
    const search = $("#accountSearch") || $("#logSearch");
    if (search) {
      event.preventDefault();
      search.focus();
      search.select();
    }
    return;
  }
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && event.target.closest?.(".account-row, #accountSearch")) {
    const rows = $$("#accountList button.account-row");
    if (!rows.length) return;
    event.preventDefault();
    const index = rows.indexOf(event.target);
    const nextIndex = index < 0 ? (event.key === "ArrowDown" ? 0 : rows.length - 1) : Math.max(0, Math.min(rows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
    rows[nextIndex].focus();
    rows[nextIndex].scrollIntoView({ block: "nearest" });
  }
  if (event.key === "Enter" && event.target.id === "newUin") {
    event.preventDefault();
    $('[data-action="confirm-create"]')?.click();
  }
  if (event.key === "Enter" && event.target.id === "confirmDeleteUin") {
    event.preventDefault();
    const button = $('[data-action="confirm-delete"]');
    if (button && !button.disabled) button.click();
  }
});
async function toggleRobotAccounts(enabled) {
  if (robotToggleLoading) return;
  const previous = showRobotAccounts;
  robotToggleLoading = true;
  showRobotAccounts = enabled;
  replaceAccountPane();
  try {
    await refreshAccounts();
  } catch (e) {
    showRobotAccounts = previous;
    toast(e.message, "error");
  } finally {
    robotToggleLoading = false;
    replaceAccountPane();
    $("#showRobotAccounts")?.focus();
  }
}
$("#appDialog").addEventListener("click", (event) => {
  const box = $("#appDialog");
  if (event.target !== box) return;
  const r = box.getBoundingClientRect();
  if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeDialog();
});
window.addEventListener("hashchange", navigate);
window.addEventListener("resize", applyLayoutSize);
matchMedia("(prefers-color-scheme:dark)").addEventListener("change", () => {
  if (settings.theme === "auto") applyTheme();
});
window.addEventListener("beforeunload", (event) => {
  if (profileDirty) {
    event.preventDefault();
    event.returnValue = "";
  }
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !booting) {
    syncState(true);
    syncLogs();
  }
});
// 内嵌 GM 页面每次加载都会报告自己的地址；只接受来自当前 GM 框架、GM 源的消息。
window.addEventListener("message", (event) => {
  const frame = $("#gmFrame");
  if (!frame || event.source !== frame.contentWindow || !adminUrl) return;
  let origin = "";
  try {
    origin = new URL(adminUrl, location.origin).origin;
  } catch {
    return;
  }
  const data = event.data;
  if (event.origin !== origin || !data || data.type !== "pvz-gm-location" || typeof data.href !== "string" || !data.href.startsWith("/")) return;
  if (data.href === gmFrameHref) return;
  gmFrameHref = data.href;
  if (page === "gm") refreshGMToolbar();
});

/* ---------- 启动 ---------- */
async function boot() {
  if (booting) return;
  booting = true;
  connecting = true;
  connectionError = "";
  updateChrome();
  const results = await Promise.allSettled([api("/api/state"), api(accountsEndpoint()), api("/api/settings"), api("/api/logs?after=0"), api("/api/backups")]);
  if (results[0].status === "fulfilled") appState = results[0].value;
  else connectionError = results[0].reason.message;
  if (results[1].status === "fulfilled") accounts = normalizeAccounts(results[1].value);
  if (results[2].status === "fulfilled") settings = { ...settings, ...results[2].value };
  if (results[3].status === "fulfilled") {
    entries = results[3].value.entries || [];
    lastLogId = results[3].value.lastId || 0;
    logCapacity = results[3].value.capacity || logCapacity;
  }
  if (results[4].status === "fulfilled") {
    backups = normalizeBackups(results[4].value);
    backupsLoaded = true;
  }
  const players = playerAccounts();
  selectedId =
    selectedId && players.some((a) => accountId(a) === selectedId)
      ? selectedId
      : accountId(players.find((a) => accountId(a) === accountId(appState.currentAccount)) || players[0]);
  connecting = false;
  applyTheme();
  render();
  booting = false;
  if (page === "accounts" && !profileDirty) await loadProfile();
  if (page === "gm") await openAdmin();
  if (page === "services") {
    await loadOverview();
    refreshUnitsPanel();
    startOverviewPolling();
  }
  clearInterval(stateTimer);
  clearInterval(logTimer);
  stateTimer = setInterval(() => !document.hidden && syncState(), 1500);
  logTimer = setInterval(() => !document.hidden && syncLogs(), 1800);
}
applyTheme();
render();
boot();
