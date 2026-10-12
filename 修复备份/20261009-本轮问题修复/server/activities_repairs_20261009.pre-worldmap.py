"""Daily activity routes, authored plant-test opponents and valid empty war histories.

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
    api.REQUEST_HANDLERS.update({
        'PVZ.Cmd.Cmd_Welfare_DailyActivity_CS': welfare,
        'PVZ.Cmd.Cmd_PlantTest_GetStageInfo_CS': stageinfo,
        'PVZ.Cmd.Cmd_War_GetHistory_CS': empty_history,
        'PVZ.Cmd.Cmd_War_GetMapHistory_CS': empty_history,
        'PVZ.Cmd.Cmd_War_GetSimpleHistory_CS': empty_history,
    })
    api._activities_repairs_20261009_installed = True
