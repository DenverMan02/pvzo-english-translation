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


def run_web():
    try:
        serve_client.main()
    except SystemExit as exc:
        if exc.code:
            print("[launcher-error] web: resource service failed to start", flush=True)
    except Exception as exc:  # keep the game server alive even if web dies
        print("[launcher-error] web: %r" % exc, flush=True)


def run_admin():
    try:
        admin_server.serve()
    except Exception as exc:  # the game and asset server can still run without the admin UI
        print("[launcher-error] admin: %r" % exc, flush=True)


def main():
    print(">>> PvZ PoC launcher: starting web + admin + game servers in ONE window.")
    print(">>> Ctrl+C to stop all services.\n")

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
