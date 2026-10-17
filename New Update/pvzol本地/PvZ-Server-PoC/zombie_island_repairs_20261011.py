"""Synchronize zombie-island restarts, sweeps and completed-ladder progress.

ModuleZombieIsland.swf's GetRefreshInfo handler changes only restart counters.
Its GetIslandInfo handler owns nextLevel and rewardBox. The sweep handler opens
the reward window and requests GetIslandInfo; it never advances local progress.
ZombieIslandModel regards nextLevel - 1 as cleared islands, and permits opening
boxes only while levelId < nextLevel. Thus the completed 12-island ladder needs
nextLevel 13 even though enemy-generation helpers still clamp battle IDs to 12.
"""


def install(api):
    if getattr(api, '_zombie_island_repairs_20261011_installed', False):
        return

    original_state = api._zi_state
    original_restart = api.handle_zi_refreshchallenge
    original_fight = api._zi_run_fight
    original_enemies = api._zi_enemy_units
    normal = api._ZI_NORMAL

    def state(player, mode):
        result = original_state(player, mode)
        next_level = api.store.island_progress(player['uin'], mode)[0]
        result['level'] = max(1, min(int(next_level or 1), api.zi_island_count() + 1))
        return result

    def refreshchallenge(cmd_data, player, mode=normal):
        # Preserve the existing GetRefreshInfo workaround: the compiled normal
        # RefreshChallenge handler reads a null local and raises TypeError 1009.
        with api.store.atomic_player(player):
            messages = original_restart(cmd_data, player, mode)
            if mode == normal:
                messages.append(('PVZ.Cmd.Cmd_ZombieIsland_GetIslandInfo_SC',
                                 api.handle_zi_getinfo(b'', player, mode)))
        return messages

    def run_fight(player, mode, level):
        info_name = ('PVZ.Cmd.Cmd_NewZombieIsland_GetIslandInfo_SC' if mode == api._ZI_ADV
                     else 'PVZ.Cmd.Cmd_ZombieIsland_GetIslandInfo_SC')
        if (int(level or 1) > api.zi_island_count()
                or api._zi_state(player, mode)['level'] > api.zi_island_count()):
            # A completed ladder has no next opponent. The legacy clamp would
            # turn level 13 back into 12 and repeatedly pay the final fight.
            return [(info_name, api.handle_zi_getinfo(b'', player, mode))]
        messages = original_fight(player, mode, level)
        # The existing battle helper includes GetIslandInfo only on a win.
        # Reuse that outcome and all its report, reward and quest behavior.
        if api._zi_clamp_level(level) == api.zi_island_count() and any(
                name == info_name for name, _body in messages):
            api.store.save_island_progress(player['uin'], mode,
                                           level=api.zi_island_count() + 1)
            info_body = api.handle_zi_getinfo(b'', player, mode)
            messages = [(name, info_body if name == info_name else body)
                        for name, body in messages]
        return messages

    def enemies(mode, level):
        # Battle_GetFightInfo also reads the same next-level sentinel. Do not
        # preview island 12 after all twelve islands have been completed.
        if int(level or 1) > api.zi_island_count():
            return []
        return original_enemies(mode, level)

    def sweep(cmd_data, player):
        with api.store.atomic_player(player):
            st = api._zi_state(player, normal)
            today = api._zi_today()
            cleared = max(0, min(st['max'], api.zi_island_count()))
            # isCanSweep in the compiled client requires a fresh climb and the
            # same role level as the recorded pass. An empty ladder must not
            # consume today's sweep or award anything.
            eligible = (st['level'] == 1 and cleared > 0
                        and st['last_role_level'] == int(player.get('level', 1))
                        and player.get('zi_sweep_day') != today)
            body, total = b'', []
            if eligible:
                for level in range(1, cleared + 1):
                    rewards = [(api._ZI_MEDAL_ITEM, 4 + level)]
                    total += rewards
                    dto = (api.pb_varint_field(1, level)
                           + api._zi_idnum_body(rewards, field=2))
                    body += api.pb_tag(1, 2) + api.uvarint(len(dto)) + dto
                player['zi_sweep_day'] = today
                api.store.save_player(player['uin'], zi_sweep_day=today)
                api.store.save_island_progress(player['uin'], normal, level=cleared + 1)
            messages = [('PVZ.Cmd.Cmd_ZombieIsland_Sweep_SC', body)]
            if total:
                messages += api.grant_fuben_reward(player, total)
            messages.append(('PVZ.Cmd.Cmd_ZombieIsland_GetIslandInfo_SC',
                             api.handle_zi_getinfo(b'', player, normal)))
            api.log.info('    zombie island sweep: eligible=%s cleared=%d next=%d rewards=%s',
                         eligible, cleared, api._zi_state(player, normal)['level'], total)
        return messages

    api._zi_state = state
    api._zi_run_fight = run_fight
    api._zi_enemy_units = enemies
    api.handle_zi_refreshchallenge = refreshchallenge
    api.handle_zi_sweep = sweep
    api.REQUEST_HANDLERS.update({
        'PVZ.Cmd.Cmd_ZombieIsland_RefreshChallenge_CS':
            lambda data, player: refreshchallenge(data, player, normal),
        'PVZ.Cmd.Cmd_NewZombieIsland_RefreshChallenge_CS':
            lambda data, player: refreshchallenge(data, player, api._ZI_ADV),
        'PVZ.Cmd.Cmd_ZombieIsland_Sweep_CS': sweep,
    })
    api._zombie_island_repairs_20261011_installed = True
