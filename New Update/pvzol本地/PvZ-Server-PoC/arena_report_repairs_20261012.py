"""Only advertise arena history entries whose original replay is available.

Older saves contain timestamp/role IDs or no report ID at all. Keep those
result records in the save, but do not offer an unusable replay button. Never
simulate a new fight and present it as the missing original match.
"""
from functools import wraps


HISTORY = {
    'Arena_GetMyInfo': ('handle_arena_getmyinfo', 8),
    'RankArena_GetMyInfo': ('handle_rankarena_getmyinfo', 1),
    'AllServerRankArena_GetRecorder': ('handle_allserver_rankarena_getrecorder', 1),
}
CHALLENGES = {
    'Arena_Challenge': 'handle_arena_challenge',
    'RankArena_Challenge': 'handle_rankarena_challenge',
    'AllServerRankArena_Challenge': 'handle_allserver_rankarena_challenge',
}


def _available(g):
    ids = {int(uid) for uid, report in g._BATTLE_REPORTS.items() if report}
    with g.store._db() as con:
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='local_rank_arena_reports'").fetchone()
        if exists:
            ids.update(int(row[0]) for row in con.execute(
                'SELECT DISTINCT report_id FROM local_rank_arena_reports WHERE length(payload)>0'))
    ids.discard(0)
    return ids


def _history_body(g, body, field, available):
    # Copy every unrelated protobuf field verbatim, including repeated
    # opponents/rewards, ranks and attempt counters.
    out, pos = [], 0
    while pos < len(body):
        start = pos
        key, pos = g.read_uvarint(body, pos)
        number, wire = key >> 3, key & 7
        payload = None
        if wire == 0:
            _, pos = g.read_uvarint(body, pos)
        elif wire == 2:
            size, pos = g.read_uvarint(body, pos)
            payload = body[pos:pos + size]
            pos += size
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise ValueError('unsupported arena history protobuf wire type')
        if pos > len(body):
            raise ValueError('truncated arena history protobuf field')
        if number == field and wire == 2:
            uid = int(g.parse_varint_fields(payload).get(1) or 0)
            if uid not in available:
                continue
        out.append(body[start:pos])
    return b''.join(out)


def install(g):
    if getattr(g, '_ARENA_REPORT_20261012_INSTALLED', False):
        return
    g._ARENA_REPORT_20261012_INSTALLED = True
    fields = {'PVZ.Cmd.Cmd_' + command + '_SC': field
              for command, (_, field) in HISTORY.items()}
    commands = {command: helper for command, (helper, _) in HISTORY.items()}
    commands.update(CHALLENGES)
    for command, helper in commands.items():
        previous = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + command + '_CS']

        def wrap(previous=previous):
            @wraps(previous)
            def handler(data, player):
                result = previous(data, player)
                if not any(name in fields for name, _ in result):
                    return result
                available = _available(g)
                return [(name, _history_body(g, body, fields[name], available)
                         if name in fields else body) for name, body in result]
            return handler

        handler = wrap()
        g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + command + '_CS'] = handler
        setattr(g, helper, handler)
