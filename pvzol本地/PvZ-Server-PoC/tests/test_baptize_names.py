"""Verify the 洗炼 command names we register/send really exist in the client's protocol source.

Each PVZ.Cmd.Cmd_X message has Assets/src/PVZ/Cmd/Cmd_X.as with
`public static const $MessageID:String = "PVZ.Cmd.Cmd_X";` — a typo in a name would make the client
ignore the reply (or never route the request), so check every name we use.

    python tests/test_baptize_names.py       (from the PvZ-Server-PoC folder)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRV_DIR = os.path.dirname(HERE)                    # ..\PvZ-Server-PoC
CMD_DIR = os.path.join(SRV_DIR, os.pardir, "Assets", "src", "PVZ", "Cmd")
sys.path.insert(0, SRV_DIR)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store                       # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_names_"), "test.db")
store.init_db()
import pvz_poc_server as srv                       # noqa: E402

REFINE_CMDS = [
    "PVZ.Cmd.Cmd_Card_GetRefineInfo_CS",
    "PVZ.Cmd.Cmd_Card_GetRefineInfo_SC",
    "PVZ.Cmd.Cmd_Card_Refine_CS",
    "PVZ.Cmd.Cmd_Card_Refine_SC",
    "PVZ.Cmd.Cmd_Card_ConfirmRefine_CS",
    "PVZ.Cmd.Cmd_Card_ConfirmRefine_SC",
    "PVZ.Cmd.Cmd_Card_BatchRefine_CS",
    "PVZ.Cmd.Cmd_Card_BatchRefine_SC",
    "PVZ.Cmd.Cmd_Card_SwapAttribute_CS",
    "PVZ.Cmd.Cmd_Card_SwapAttribute_SC",
    "PVZ.Cmd.Cmd_Card_SwapAttributeLost_CS",
    "PVZ.Cmd.Cmd_Card_SwapAttributeLost_SC",
    "PVZ.Cmd.Dto_RefineAttribute",
    "PVZ.Cmd.Dto_RefineOperation",
]
FAILED = []


def check(label, ok, extra=""):
    if not ok:
        FAILED.append(label)
    print("%-4s %s %s" % ("PASS" if ok else "FAIL", label, extra))


def main():
    for name in REFINE_CMDS:
        cls = name.rsplit(".", 1)[-1]
        path = os.path.join(CMD_DIR, cls + ".as")
        ok = os.path.isfile(path)
        if ok:
            txt = open(path, encoding="utf-8", errors="replace").read()
            ok = ('$MessageID:String = "%s"' % name) in txt.replace(" ", "") or \
                 ('$MessageID:String="%s"' % name) in txt.replace(" ", "")
        check("client protocol class exists for %s" % name, ok, path if not ok else "")

    for name in REFINE_CMDS:
        if name.endswith("_CS"):
            check("%s is registered" % name, name in srv.REQUEST_HANDLERS)
    for name, fn in srv.REQUEST_HANDLERS.items():
        if "Refine" in name or "SwapAttribute" in name:
            check("%s -> callable" % name, callable(fn), fn)

    print("\n%s (%d failing check(s))"
          % ("NAMES OK" if not FAILED else "FAILURES: %s" % FAILED, len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
