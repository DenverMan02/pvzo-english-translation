#!/usr/bin/env python3
"""
One-window launcher: runs all local services together so you don't have to juggle
terminals (which kept leaving stale processes behind).

  - web server   (serve_client)   : http://127.0.0.1:8080   serves the game files
  - GM admin     (admin_server)   : http://127.0.0.1:8081   local save manager
  - game server  (pvz_poc_server) : :9001 (socket) + :843 (Flash policy)

Run this in ONE terminal (as Administrator, so port 843 binds cleanly):

    python "c:\\Users\\Toad\\Documents\\PvZOnline Restored\\PvZ-Server-PoC\\run_all.py"

Ctrl+C stops all services. If a port is busy it will say so — then close this window and,
if needed, run:  taskkill /F /IM python.exe   and start again.
"""

import os
import sys
import asyncio
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serve_client            # noqa: E402
import admin_server            # noqa: E402
import pvz_poc_server as game  # noqa: E402
from runtime_status import ObservedReader, RuntimeStatus  # noqa: E402

# Observable activity for the launcher's service panel and stop dialog. This
# only counts what this process can see itself (listening ports, open game
# connections, request totals); it never inspects or changes game data.
STATUS = RuntimeStatus.from_env("server")


def _install_activity_tracking():
    if not STATUS.enabled:
        return
    original_client, original_policy = game.handle_client, game.handle_policy

    async def handle_client(reader, writer):
        observed = ObservedReader(reader, STATUS, "game", session=True)
        try:
            return await original_client(observed, writer)
        finally:
            observed.close()

    async def handle_policy(reader, writer):
        return await original_policy(ObservedReader(reader, STATUS, "policy"), writer)

    # pvz_poc_server.main() resolves these module globals when it starts listening.
    game.handle_client, game.handle_policy = handle_client, handle_policy
    original_start_server = asyncio.start_server

    async def start_server(callback, host=None, port=None, *args, **kwargs):
        server = await original_start_server(callback, host, port, *args, **kwargs)
        name = "game" if callback is handle_client else "policy" if callback is handle_policy else None
        if name and port:
            STATUS.listening(name, port)
        return server

    asyncio.start_server = start_server
    STATUS.track_http(serve_client.Handler, "web")
    STATUS.track_http(admin_server.AdminHandler, "admin", ignore_paths=("/healthz",))
    STATUS.start()


def run_web():
    try:
        serve_client.main()
    except SystemExit:
        pass
    except Exception as exc:  # keep the game server alive even if web dies
        print("[web] crashed: %r" % exc)


def run_admin():
    try:
        admin_server.serve()
    except Exception as exc:  # the game and asset server can still run without the admin UI
        print("[admin] crashed: %r" % exc)


def main():
    print(">>> PvZ PoC launcher: starting web + admin + game servers in ONE window.")
    print(">>> Ctrl+C to stop all services.\n")
    try:
        _install_activity_tracking()
    except Exception as exc:  # activity reporting is diagnostic only
        print("[status] activity tracking unavailable: %r" % exc)

    web_thread = threading.Thread(target=run_web, name="webserver", daemon=True)
    web_thread.start()
    admin_thread = threading.Thread(target=run_admin, name="adminserver", daemon=True)
    admin_thread.start()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(game.main())
    except KeyboardInterrupt:
        print("\nshutting down")


if __name__ == "__main__":
    main()
