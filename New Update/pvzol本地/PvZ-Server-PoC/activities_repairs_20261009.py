"""Daily routes, consistent plant-test opponents and source-correct world-map cells.

Install after the legacy adapters. No database is opened at install time.
The WelfareHall client method uses localQuestJump with the original QuestHelper.
"""
import json
from pathlib import Path


def client_table(path, name):
    """Read the archive's Chinese tables as authored (some use legacy GBK)."""
    data = Path(path).read_bytes()
    pos = 0
    while pos + 4 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], 'big'); pos += 4
        entry = data[pos:pos + size].decode('utf-8'); pos += size
        size = int.from_bytes(data[pos:pos + 4], 'big'); pos += 4
        chunk = data[pos:pos + size]; pos += size
        if entry == name:
            try:
                text = chunk.decode('utf-8')
            except UnicodeDecodeError:
                text = chunk.decode('gb18030')
            return [line.split('\t') for line in text.replace('\r', '').split('\n') if line.strip()]
    return []


def replace_fields(data, replacements):
    """Replace selected protobuf fields, retaining every unrelated wire byte."""
    def varint(pos):
        value = shift = 0
        while pos < len(data):
            byte = data[pos]; pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, pos
            shift += 7
            if shift > 70:
                break
        raise ValueError('invalid protobuf varint')
    output = []
    pos = 0
    while pos < len(data):
        start = pos
        tag, pos = varint(pos)
        wire = tag & 7
        if wire == 0:
            _, pos = varint(pos)
        elif wire == 1:
            pos += 8
        elif wire == 2:
            size, pos = varint(pos)
            pos += size
        elif wire == 5:
            pos += 4
        else:
            raise ValueError('unsupported protobuf wire type: %s' % wire)
        if pos > len(data):
            raise ValueError('truncated protobuf field')
        if tag >> 3 not in replacements:
            output.append(data[start:pos])
    output.extend(replacements.values())
    return b''.join(output)


# The original DailyQuest finish conditions contain a few unhandled task types;
# use the corresponding original QuestHelper entry constants for those tasks.
DAILY_JUMPS = {
    1: '1001:0:1', 2: '1003:0:1', 3: '1003:0:1', 4: '1006:0:1',
    7: '9:1:1', 8: '9:2:1', 9: '12:0:1', 10: '1007:0:1',
    12: '1004:0:1', 13: '61:0:1', 14: '1009:0:1',
    15: '1002:0:1', 16: '34:0:1', 17: '1011:0:1', 18: '1011:0:1',
    19: '1001:0:1', 20: '1012:0:1', 22: '63:0:1', 23: '1008:0:1',
    24: '62:0:1', 25: '1010:0:1', 26: '49:0:1', 27: '55:0:1',
    28: '59:0:1',
}


def install(api):
    if getattr(api, '_activities_repairs_20261009_installed', False):
        return
    original_welfare = api.handle_welfare_dailyactivity
    original_enemies = api._plant_test_enemies
    original_stageinfo = api.handle_plant_test_getstageinfo
    original_map_info = api.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS']
    original_cell_info = api.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetCellInfo_CS']
    original_guild_members = api.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetGuildMemberList_CS']
    temple_rows = None

    def temples():
        nonlocal temple_rows
        if temple_rows is None:
            rows = client_table(api._CLIENTDB_PATH, 'MapTempleTemplate.txt')
            temple_rows = [dict(zip(rows[0], row)) for row in rows[1:]
                           if len(row) >= 8 and int(row[7]) == 1 and int(row[1]) in (1, 2)]
        return temple_rows

    def corrected_city(cell):
        # MapGlobals.CITY is 1. Type 0 has no MapCellPropTemplate/VO and caused
        # the logged #1009 in MapProxy.updateCellInArea. A player's face ID is
        # not a map skin ID either; use the authored default city skin (1).
        replacements = {1: api.pb_varint_field(1, 1),
                        7: api.pb_varint_field(7, 1),
                        15: api.pb_varint_field(15, 1)}
        points = api.parse_repeated_submessages(cell, 2)
        fields = api.parse_varint_fields(points[0]) if points else {}
        if (fields.get(1, 0), fields.get(2, 0)) == (0, 0):
            pos = api.pb_varint_field(1, 1152) + api.pb_varint_field(2, 1152)
            replacements[2] = api.pb_bytes(2, pos)
        return replace_fields(cell, replacements)

    def temple_cell(row):
        x, y = map(int, row['pos'].strip().split(','))
        pos = api.pb_varint_field(1, x) + api.pb_varint_field(2, y)
        # Neutral map landmarks have no player roleId. Int64.compare(null) is
        # safe, and MapBasicCellVO then compares positions rather than merging
        # all neutral landmarks under roleId=0.
        return (api.pb_varint_field(1, 3) + api.pb_bytes(2, pos)
                + api.pb_string(4, row['name']) + api.pb_varint_field(5, 0)
                + api.pb_varint_field(6, 0)
                + api.pb_varint_field(7, 12 if int(row['type']) == 1 else 11)
                + api.pb_varint_field(8, 1) + api.pb_varint_field(9, 0)
                + api.pb_int64_field(11, 0) + api.pb_string(12, '')
                + api.pb_bytes(13, b'') + api.pb_bytes(14, b'')
                + api.pb_varint_field(15, int(row['area']))
                + api.pb_bytes(16, b'') + api.pb_varint_field(30, 0)
                + api.pb_varint_field(50, int(row['id'])))

    def map_info(cmd_data, player):
        area = int(api.parse_varint_fields(cmd_data).get(5, 1) or 1)
        if area == 1:
            result = original_map_info(cmd_data, player)
            body = result[0][1]
            cells = api.parse_repeated_submessages(body, 1)
            body = replace_fields(body, {1: b''.join(api.pb_bytes(1, corrected_city(cell))
                                                      for cell in cells)})
        else:
            body = b''.join(api.pb_bytes(1, temple_cell(row)) for row in temples()
                            if int(row['area']) == area)
        return [('PVZ.Cmd.Cmd_WorldMap_GetInfo_SC', body)]

    def corrected_city_list(body):
        cells = api.parse_repeated_submessages(body, 1)
        return replace_fields(body, {1: b''.join(api.pb_bytes(1, corrected_city(cell))
                                                 for cell in cells)})

    def correct_reply(result, command):
        if isinstance(result, bytes):
            return corrected_city_list(result)
        if isinstance(result, list):
            return [(name, corrected_city_list(body)) if name == command else (name, body)
                    for name, body in result]
        return result  # preserve the existing structured error for an unknown role

    def cell_info(cmd_data, player):
        return correct_reply(original_cell_info(cmd_data, player), 'PVZ.Cmd.Cmd_WorldMap_GetCellInfo_SC')

    def cell_info_helper(cmd_data, player):
        result = cell_info(cmd_data, player)
        if isinstance(result, list):
            return next((body for name, body in result
                         if name == 'PVZ.Cmd.Cmd_WorldMap_GetCellInfo_SC'), b'')
        return result

    def guild_members(cmd_data, player):
        return correct_reply(original_guild_members(cmd_data, player),
                             'PVZ.Cmd.Cmd_WorldMap_GetGuildMemberList_SC')

    def temple_info(cmd_data, _player):
        points = api.parse_repeated_submessages(cmd_data, 1)
        pos = points[0] if points else api.pb_varint_field(1, 0) + api.pb_varint_field(2, 0)
        area = int(api.parse_varint_fields(cmd_data).get(2, 2) or 2)
        # There is no persisted occupation/war in the local map model. Return
        # its real empty guild list and explicit zero Int64 lord, not the
        # generic all-zero phantom guild/guarder previously synthesized.
        body = (api.pb_bytes(1, pos) + api.pb_varint_field(2, area)
                + api.pb_varint_field(3, 0) + api.pb_int64_field(5, 0))
        return [('PVZ.Cmd.Cmd_Temple_GetTempleInfo_SC', body)]

    def temple_lords(_cmd_data, _player):
        body = b''.join(api.pb_bytes(1, api.pb_varint_field(1, int(row['id']))
                                   + api.pb_int64_field(2, 0) + api.pb_string(3, ''))
                        for row in temples())
        return [('PVZ.Cmd.Cmd_Temple_GetTempleLordInfo_SC', body)]

    def temple_guarders(_cmd_data, _player):
        return [('PVZ.Cmd.Cmd_Temple_GetGuarderInfo_SC', b'')]

    def welfare(cmd_data, player):
        # Preserve the four authored activity-fuben entries from the old handler.
        old = original_welfare(cmd_data, player)[0][1]
        entries = api.parse_repeated_submessages(old, 1)
        body = b''.join(api.pb_bytes(1, dto) for dto in entries
                        if api.parse_varint_fields(dto).get(1, 0) >= 58000)
        rows = client_table(api._CLIENTDB_PATH, 'dailyActivityInfo.txt')
        now = int(api.time.time())
        for row in rows[1:]:
            aid = int(row[0])
            if aid == 11:
                continue
            if aid not in DAILY_JUMPS:
                raise ValueError('missing daily activity route: %s' % aid)
            title = row[1]
            params = json.dumps({'title': title, 'content': title,
                                 'time': '每日开放', 'order': int(row[5]),
                                 'type': 'local_daily', 'localQuestJump': DAILY_JUMPS[aid]},
                                ensure_ascii=False, separators=(',', ':'))
            dto = (api.pb_varint_field(1, aid) + api.pb_varint_field(2, 0)
                   + api.pb_varint_field(3, 0) + api.pb_varint_field(4, int(row[6]))
                   + api.pb_varint_field(5, now - 86400)
                   + api.pb_varint_field(6, now + 86400 * 30) + api.pb_string(11, params))
            body += api.pb_bytes(1, dto)
        return [('PVZ.Cmd.Cmd_Welfare_DailyActivity_SC', body)]

    def enemies(player, sid, chapter):
        # The legacy team is valid, but always previews element zero (dual pea).
        # Rotate the same real plant-card IDs deterministically so preview, fight
        # and Battle_GetFightInfo all refer to the identical current opponent.
        units = original_enemies(player, sid, chapter)
        if units:
            start = ((int(sid) - 1) * 30 + int(chapter) - 1) % len(units)
            units = units[start:] + units[:start]
            for index, unit in enumerate(units):
                unit['pos'] = api._ENEMY_POS[index]
                unit['is_leader'] = index == 0
        return units

    def stageinfo(cmd_data, player):
        # Keep the original protobuf ordering, chapter counts and card DTOs.
        # Only the existing generator changes, which is shared with combat.
        return original_stageinfo(cmd_data, player)

    def empty_history(cmd_data, player):
        # Cmd_War_GetHistory_SC.historys is repeated. The generic populator
        # invented a single all-zero record with null roleName/teamStateLabel;
        # MapInfoDetailItemRenderer then crashes when applying its style.
        # There is no implemented world-map combat/history store in this build.
        # A zero-entry response is the protocol-correct real empty history.
        return b''

    api.handle_welfare_dailyactivity = welfare
    api._plant_test_enemies = enemies
    api.handle_plant_test_getstageinfo = stageinfo
    api.handle_worldmap_getcellinfo = cell_info_helper
    api.REQUEST_HANDLERS.update({
        'PVZ.Cmd.Cmd_Welfare_DailyActivity_CS': welfare,
        'PVZ.Cmd.Cmd_PlantTest_GetStageInfo_CS': stageinfo,
        'PVZ.Cmd.Cmd_War_GetHistory_CS': empty_history,
        'PVZ.Cmd.Cmd_War_GetMapHistory_CS': empty_history,
        'PVZ.Cmd.Cmd_War_GetSimpleHistory_CS': empty_history,
        'PVZ.Cmd.Cmd_WorldMap_GetInfo_CS': map_info,
        'PVZ.Cmd.Cmd_WorldMap_GetCellInfo_CS': cell_info,
        'PVZ.Cmd.Cmd_WorldMap_GetGuildMemberList_CS': guild_members,
        'PVZ.Cmd.Cmd_Temple_GetTempleInfo_CS': temple_info,
        'PVZ.Cmd.Cmd_Temple_GetTempleLordInfo_CS': temple_lords,
        'PVZ.Cmd.Cmd_Temple_GetGuarderInfo_CS': temple_guarders,
    })
    api._activities_repairs_20261009_installed = True
