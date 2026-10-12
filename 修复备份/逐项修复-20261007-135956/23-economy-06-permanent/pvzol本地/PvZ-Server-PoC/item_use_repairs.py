"""Consume configured backpack items and commit their real effects together."""
import time

def use(g, cmd_data, player):
    fields = g.parse_varint_fields(cmd_data)
    item_id, want = int(fields.get(1, 0)), int(fields.get(2, 1))
    rows = g._read_clientdb_table('ItemInfo.txt')
    item = next((dict(zip(rows[0], row)) for row in rows[1:] if row[0] == str(item_id)), None) if rows else None
    if not item or item.get('isUse') != '1' or want <= 0:
        return [g._logic_error(902)]
    effect = item.get('useEffect', '').split(':')
    kind, amount = (int(effect[0]), int(effect[1])) if len(effect) == 2 and all(p.isdigit() for p in effect) else (-1, 0)
    puppet = g._puppet_consumable(item_id) if int(item.get('type') or 0) == 15 else None
    if kind == 0 and int(item.get('subType') or 0) == 256:
        puppet = g._puppet_consumable(amount)
    supported = (kind == 9 and amount > 0) or (kind == 20 and amount > 0) or kind == 3 or item_id == 60000 or puppet
    if not supported:
        return [g._logic_error(902)]
    with g.store.atomic_player(player):
        if int(player.get('level') or 1) < int(item.get('minLvRequired') or 1):
            return [g._logic_error(200)]
        held = g.store.get_item_count(player['uin'], item_id)
        count = want if item.get('isMulUse') == '1' else 1
        if held < count:
            return [g._logic_error(204)]
        now = int(time.time())
        vip = None
        if kind == 3:
            g.load_vip_tiers()
            vip = g._VIP_CARDS.get(amount)
            if not vip or vip['type'] != 2 or vip['day'] <= 0:
                return [g._logic_error(5100)]
            if g.vip_tier(player):
                return [g._logic_error(5101)]
            count = 1
        changes = [(item_id, -count)]
        messages = []
        if puppet:
            base_id, info, duplicate_pieces, _multi = puppet
            duplicates = count
            if not g.store.get_puppet(player['uin'], base_id):
                g.store.save_puppet(player['uin'], base_id, exp=0, level=1, star=max(1, int(info['star'] or 1)),
                    quality=int(info['quilty'] or 100), active_skill=int(info['active'] or 0), active_level=1,
                    passive_skill=int(info['passive'] or 0), passive_level=1, spars='')
                duplicates -= 1
            if duplicates:
                changes.append((info['piece'], duplicates * duplicate_pieces))
            body = b''.join(g.pb_bytes(1, g.puppet_full_dto(pid, row)) for pid, row in g.store.get_puppets(player['uin']).items())
            messages.append(('PVZ.Cmd.Cmd_Puppet_GetInfo_SC', body))
        elif kind == 9:
            energy = g.current_energy(player) + amount * count
            if energy > 4294967295:
                return [g._logic_error(902)]
            player['energy'] = energy
            g.store.save_player(player['uin'], energy=player['energy'])
            messages.append(g.energy_sync_msg(player))
        elif kind == 20:
            end = max(now, int(player.get('td_privilege_endtime') or 0)) + amount * count * 86400
            if end > 4294967295:
                return [g._logic_error(902)]
            player.update(td_privilege=1, td_privilege_endtime=end)
            g.store.save_player(player['uin'], td_privilege=1, td_privilege_endtime=end)
            messages.append(('PVZ.Cmd.Cmd_TD_OpenPrivilege_SC', g.build_td_open_privilege_sc(player)))
        elif item_id == 60000:
            player['nutrition_gold'] = int(player.get('nutrition_gold') or 0) + count
            g.store.save_player(player['uin'], nutrition_gold=player['nutrition_gold'])
            messages.extend(g.handle_nutrition_gettempbag(b'', player))
        elif vip:
            player.update(vip_level=vip['level'], vip_card_id=vip['cardId'], vip_endtime=now+vip['day']*86400,
                          vip_gift_taken=0, vip_energy_taken=0, vip_ticket_taken=0, vip_day=time.strftime('%Y%m%d'))
            g.store.save_player(player['uin'], **{key: player[key] for key in ('vip_level','vip_card_id','vip_endtime','vip_gift_taken','vip_energy_taken','vip_ticket_taken','vip_day')})
            messages.extend(g.vip_login_messages(player))
        update = g.grant_items(player, changes)
        ack = ('PVZ.Cmd.Cmd_Item_Use_SC', g.pb_varint_field(1, item_id)+g.pb_varint_field(2, count))
        return ([update] if update else []) + messages + [ack]
