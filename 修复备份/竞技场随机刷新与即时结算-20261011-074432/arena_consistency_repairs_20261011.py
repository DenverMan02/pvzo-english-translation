"""Keep arena response ranks, leaderboard rows and yesterday's reward snapshot consistent.

Cmd_Arena_GetMyInfo_SC has separate rank (1), curGrade (3), nextGrade (4).
ArenaMyInfoViewMediator displays rank as the position within the current group;
ArenaTopPlayerViewMediator uses the score-sorted list order for first/second/third.
The current local group remains unchanged. Cross-server ranktype 105 must use
AllServerRankArena state, not the ladder's rank_arena_data or its cached rows.
"""
from functools import wraps


def _replace_varints(g, body, values):
    """Preserve the report, recorders and every unrelated protobuf field verbatim."""
    out, pos = [], 0
    while pos < len(body):
        start = pos
        key, pos = g.read_uvarint(body, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            _, pos = g.read_uvarint(body, pos)
        elif wire == 2:
            size, pos = g.read_uvarint(body, pos)
            pos += size
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise ValueError('unsupported arena protobuf wire type')
        if pos > len(body):
            raise ValueError('truncated arena protobuf field')
        if wire != 0 or field not in values:
            out.append(body[start:pos])
    out.extend(g.pb_varint_field(field, value) for field, value in values.items())
    return b''.join(out)


def install(g):
    if getattr(g, '_ARENA_CONSISTENCY_20261011_INSTALLED', False):
        return
    g._ARENA_CONSISTENCY_20261011_INSTALLED = True

    def ordinary_rank(player):
        # Match _arena_leaderboard_entries' stable score ordering exactly.
        rows = [(int(bot['uin']), int(state.get('score') or 0))
                for bot, state in g._bot_roster_with_state()]
        state = g._arena_state(player, 'arena_data')
        rows.append((int(player['uin']), int(state.get('score') or 0)))
        rows.sort(key=lambda row: -row[1])
        return next(rank for rank, row in enumerate(rows, 1) if row[0] == int(player['uin']))

    def remember_cross_rank(player, ranked=None):
        # _asra_state performs rollover before recording today's rank, retaining
        # the previous saved rank as yesterday's immutable reward snapshot.
        if ranked is None:
            ranked, _ = g._asra_ranked(player)
        rank = next((rank for row, rank in ranked if int(row[0]) == int(player['uin'])), 0)
        state = g._asra_state(player)
        state['last_rank'] = rank
        if rank:
            state['max_rank'] = min(int(state.get('max_rank') or rank), rank)
        g._save_json_state(player, 'allserver_rank_arena_data', state)
        return rank

    def bind(command, helper, handler):
        g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + command + '_CS'] = handler
        setattr(g, helper, handler)

    for command, helper in (('Arena_GetMyInfo', 'handle_arena_getmyinfo'),
                            ('Arena_Challenge', 'handle_arena_challenge')):
        previous = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + command + '_CS']
        def wrap_ordinary(previous=previous, command=command):
            @wraps(previous)
            def handler(data, player):
                # Let the existing challenge prepare NPCs before its own DB
                # transaction; those refreshes can use another connection.
                result = previous(data, player)
                name = 'PVZ.Cmd.Cmd_' + command + '_SC'
                return [(key, _replace_varints(g, body, {1: ordinary_rank(player)})
                         if key == name else body) for key, body in result]
            return handler
        bind(command, helper, wrap_ordinary())

    for command, helper in (
            ('AllServerRankArena_Info', 'handle_allserver_rankarena_info'),
            ('AllServerRankArena_Rank', 'handle_allserver_rankarena_rank'),
            ('AllServerRankArena_Challenge', 'handle_allserver_rankarena_challenge')):
        # Wrap final routes, including the consistency repair's daily score
        # and transactional paid-challenge logic, rather than the old helpers.
        previous = g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + command + '_CS']
        def wrap_cross(previous=previous, command=command):
            @wraps(previous)
            def handler(data, player):
                with g.store.atomic_player(player):
                    result = previous(data, player)
                    response = 'PVZ.Cmd.Cmd_' + command + '_SC'
                    if not any(name == response for name, _ in result):
                        return result
                    rank = remember_cross_rank(player)
                    if command.endswith('_Rank') or command.endswith('_Challenge'):
                        field = 1 if command.endswith('_Rank') else 2
                        result = [(name, _replace_varints(g, body, {field: rank})
                                   if name == response else body) for name, body in result]
                    return result
            return handler
        bind(command, helper, wrap_cross())

    previous_board = g._rank_board
    @wraps(previous_board)
    def rank_board(ranktype, player):
        if int(ranktype) != 105:
            return previous_board(ranktype, player)
        ranked, _ = g._asra_ranked(player)
        remember_cross_rank(player, ranked)
        return [(rank, g._rank_row(uin=int(row[0]), role_id=int(row[0]),
                                  name=str(row[1]), level=int(row[2]), power=int(row[3]),
                                  score=int(row[4]), wins=int(row[5]), losses=int(row[6])))
                for row, rank in ranked]
    g._rank_board = rank_board

