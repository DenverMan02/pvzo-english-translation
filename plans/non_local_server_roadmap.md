# Non-Local Server & Multiplayer Readiness Roadmap

## Overview & Goals

This document serves as the master roadmap for transitioning the Plants vs Zombies Online (PvZOL) Server Proof-of-Concept (PoC) from a single-player/local-loopback environment (`127.0.0.1`) into a robust non-local multi-player server deployment (LAN / WAN / Cloud Server), while maintaining full local offline compatibility as a fallback mode.

Each item in this document is structured as an independent, actionable task. Future automated sessions or developers can pick any task by its ID (`TASK-XXX`), implement the prescribed changes, and verify them against the repository tests.

---

## Technical Categories Overview

1. **Network Listener & Address Bindings**: Binding servers to configurable interfaces (`0.0.0.0` / dynamic host) instead of loopback (`127.0.0.1`).
2. **Flash Cross-Domain & Security Policies**: Flash Socket Policy Server (Port 843) & HTTP `crossdomain.xml` handling for remote web browsers / standalone Flash Player clients.
3. **Dynamic Asset & Avatar URL Resolution**: Eliminating hardcoded `http://127.0.0.1:8080/` prefixes in server-generated Protobuf messages (`roleQQFaceURL`, `qqFaceURL`, avatar links).
4. **Database Concurrency & Locking**: Upgrading SQLite (`players.db` and `guild_local.db`) to Write-Ahead Logging (WAL) mode and implementing robust thread/process concurrency guards.
5. **Multi-Player State & Session Synchronization**: Handling concurrent online players, global World Boss HP race conditions, Ladder / Arena rankings real-time sync, and Guild interactions across multiple sockets.
6. **Public Admin Panel & Security Isolation**: Securing or isolating the web GM Admin Server (`admin_server.py`) with token/password authentication, CSRF protections, and IP restriction when exposed publicly.
7. **Client Launcher & Dual-Mode Hosting**: Updating the launcher (`launcher.py`, `launcher_service.py`, `launcher_desktop.py`) to support connecting to a remote public server IP/domain while preserving local embedded server capabilities as a fallback.

---

## Actionable Task Checklist

### Category 1: Network Listener & Address Bindings

#### `TASK-001`: Configurable Web & Game Host Interface Bindings
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/serve_client.py`
  - `pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`
  - `pvzol本地/PvZ-Server-PoC/admin_server.py`
- **Problem**:
  - `serve_client.py` hardcodes `Server(("127.0.0.1", PORT), Handler)`, making the HTTP static web server inaccessible to external network clients.
  - `admin_server.py` hardcodes `ADMIN_HOST = "127.0.0.1"`.
- **Solution Strategy**:
  - Introduce environment variables or a configuration JSON file (e.g. `server_config.json` / `SERVER_HOST`) with default `0.0.0.0` or configurable bind address.
  - Allow `serve_client.py` and `pvz_poc_server.py` to listen on all interfaces (`0.0.0.0`) when deployed on a host machine while preserving local loopback binding options.

#### `TASK-002`: Graceful Multi-Port Socket Binding & Port Fallbacks
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`
- **Problem**:
  - `pvz_poc_server.py` binds to `GAME_PORTS = [9001, 8001, 443, 80]`. On non-root / Linux cloud environments, binding to low ports like `80` or `443` without `CAP_NET_BIND_SERVICE` or `sudo` will crash server startup.
- **Solution Strategy**:
  - Catch `PermissionError` / `OSError` on restricted ports (e.g., 80, 443) gracefully, log a clear warning, and continue serving on unprivileged ports (e.g., 9001, 8001) instead of crashing the entire server process.

---

### Category 2: Flash Cross-Domain & Security Policies

#### `TASK-003`: Remote Flash Socket Policy Server (`port 843` & Port 9001 Inline Policy)
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`
- **Problem**:
  - Flash Player clients enforce strict socket policy checks when connecting across domains or non-local IPs.
  - `handle_policy()` returns `<cross-domain-policy><allow-access-from domain="*" to-ports="*"/></cross-domain-policy>\0`. However, when port 843 requires elevated privileges on remote servers, inline policy checks on port 9001 must answer reliably before TCP connection teardown.
- **Solution Strategy**:
  - Ensure `handle_client` inline policy check supports `<policy-file-request/>` requests across all active game ports seamlessly.
  - Provide fallback socket policy responses for non-root execution where port 843 is unavailable.

#### `TASK-004`: Web Server `crossdomain.xml` & CORS Headers
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/serve_client.py`
- **Problem**:
  - Remote Flash/Ruffle clients loading assets (`.swf`, `.png`, `.xml`) across different hostnames/IPs hit Flash HTTP security restrictions if `crossdomain.xml` is missing or CORS headers are omitted.
- **Solution Strategy**:
  - Ensure `serve_client.py` serves a valid `crossdomain.xml` at root (`http://<host>:<port>/crossdomain.xml`) permitting domain access (`domain="*"`).
  - Add standard CORS headers (`Access-Control-Allow-Origin: *`) to static HTTP responses.

---

### Category 3: Dynamic Asset & Avatar URL Resolution

#### `TASK-005`: Dynamic Public Base URL for Role & Avatar HTTP Assets
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`
- **Problem**:
  - `pvz_poc_server.py` contains hardcoded `http://127.0.0.1:8080/image/head/...` strings in multiple Protobuf response constructors (e.g. `roleQQFaceURL`, `qqFaceURL`, avatar links).
  - External clients receiving `127.0.0.1` URLs attempt to load head icons and avatar images from their own local machine, causing broken images in non-local multiplayer sessions.
- **Solution Strategy**:
  - Refactor static `http://127.0.0.1:8080` strings into a central helper function `get_public_asset_url(path, request_host=None)`.
  - Dynamically construct asset URLs based on server configuration or the incoming HTTP `Host` header.

---

### Category 4: Database Concurrency & Persistent Storage Locking

#### `TASK-006`: SQLite Write-Ahead Logging (WAL Mode) & Busy Timeout Setup
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/player_store.py`
  - `pvzol本地/PvZ-Server-PoC/guild_local.py`
- **Problem**:
  - `players.db` and `guild_local.db` currently operate in default `journal_mode = delete`.
  - Under multiple concurrent player connections and background timer loops (e.g. `push_worldmap_updates`, async guild auto-saves), SQLite `database is locked` (`OperationalError`) errors occur frequently when concurrent transactions execute.
- **Solution Strategy**:
  - Execute `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=5000;` on database initialization for both `players.db` and `guild_local.db`.
  - Enable thread-safe connection pooling or per-thread SQLite connections with immediate transactions where mutation occurs.

#### `TASK-007`: Thread-Safe Database Transaction Wrappers & Lock Guards
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/player_store.py`
  - `pvzol本地/PvZ-Server-PoC/guild_local.py`
  - `pvzol本地/PvZ-Server-PoC/admin_server.py`
- **Problem**:
  - `admin_server.py` uses `WRITE_LOCK = threading.RLock()`, but asyncio tasks in `pvz_poc_server.py` access `player_store.py` and `guild_local.py` without sharing this mutex, causing race conditions between GM panel edits and active player gameplay saves.
- **Solution Strategy**:
  - Unify database access locks between `admin_server.py`, `player_store.py`, and `guild_local.py` under shared connection context managers or thread/asyncio mutex locks.

---

### Category 5: Multi-Player Real-Time Session & State Synchronization

#### `TASK-008`: Active Player Connection Registry & Duplicate Login Handling
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`
- **Problem**:
  - When the same account (UIN) logs in from two separate machines simultaneously, both sockets manipulate the same in-memory player state concurrently, causing state corruption and out-of-order Protobuf sequence numbers.
- **Solution Strategy**:
  - Implement a central `_ONLINE_SESSIONS` registry mapping `uin -> (writer, session_id)`.
  - When a duplicate login request is received for an already active UIN, cleanly notify and disconnect the previous socket session (`kick_previous_session`).

#### `TASK-009`: Concurrent Guild State Mutex & Cross-Player Broadcasts
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/guild_local.py`
  - `pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`
- **Problem**:
  - Guild actions (donations, boss battles, member approvals, power calculations) modify in-memory dictionaries concurrently without locking.
  - Actions taken by Member A (e.g., Guild Boss damage) do not push real-time updates to Member B who is currently online in the same guild screen.
- **Solution Strategy**:
  - Wrap guild mutations with asyncio locks.
  - Implement active session notification channels so guild status updates broadcast to all online members belonging to the same guild ID.

#### `TASK-010`: World Boss HP Race Condition & Global Battle State Locking
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`
- **Problem**:
  - `_WORLD_BOSS_HP` dictionary is mutated directly by incoming attack completion packets across async handlers. Multiple players attacking World Boss simultaneously can cause negative HP over-deductions or missed kill attributions.
- **Solution Strategy**:
  - Enforce atomic HP subtraction and reward calculation routines guarded by an asyncio lock for World Boss battles.

---

### Category 6: Public Admin Panel & Security Isolation

#### `TASK-011`: GM Admin Server Authentication & Secret Key Guards
- **Files Impacted**:
  - `pvzol本地/PvZ-Server-PoC/admin_server.py`
- **Problem**:
  - `admin_server.py` currently relies on `hostname not in ("127.0.0.1", "localhost", "::1")` for access restriction.
  - If hosted on a public server, any user on the network could potentially access `/` and execute arbitrary GM commands, account wipes, or item injections.
- **Solution Strategy**:
  - Add optional HTTP Basic Auth or session-token authentication (`ADMIN_SECRET_KEY` / `ADMIN_PASSWORD`).
  - Allow administrators to bind `admin_server.py` strictly to local loopback or behind an authenticated reverse proxy.

---

### Category 7: Client Launcher & Dual-Mode Hosting Strategy

#### `TASK-012`: Remote Server Connection Config in Launcher
- **Files Impacted**:
  - `pvzol本地/launcher_config.json`
  - `pvzol本地/launcher.py`
  - `pvzol本地/launcher_service.py`
  - `pvzol本地/launcher_desktop.py`
- **Problem**:
  - Launcher currently constructs client URLs pointing strictly to `127.0.0.1` (`http://127.0.0.1:8080/loader_p.swf?server=127.0.0.1:9001...`).
- **Solution Strategy**:
  - Add a configurable setting in `launcher_config.json` / UI (`remote_server_ip` / `remote_server_port`).
  - When non-empty, launcher formats `loader_p.swf` flashvars to target the specified host (`server=<remote_ip>:<port>`), fallback to `127.0.0.1` if empty or offline.

---

## Instructions for Future Task Execution

1. Choose a task from the list above by its ID (`TASK-XXX`).
2. Read the **Files Impacted**, **Problem**, and **Solution Strategy**.
3. Implement the code changes in the source files.
4. Run unit tests (`python3 -m unittest discover -s pvzol本地/PvZ-Server-PoC/tests`).
5. Update this document to mark the task as `[X] Completed` with the PR/commit reference.
