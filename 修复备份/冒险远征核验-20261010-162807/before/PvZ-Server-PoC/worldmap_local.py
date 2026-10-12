"""Safe local responses for the world-map viewport commands.

The original client still sends the protobuf WorldMap requests, but the PoC server
had only implemented CityInfo and CellInfo.  This module fills the missing read-only
routes without touching player persistence or inventing attackable enemy cities.

Call ``make_handlers(server_api)`` after the server's protobuf helpers and store have
been initialized, then merge the returned dictionary into ``REQUEST_HANDLERS``.

Coordinates: the client renders the world map from the TMX embedded in ModuleMap.swf
(WorldMapRes1/WorldMapRes2, 36x36 staggered-iso grid, tile 128x64).  The 72 city
plots are the object layer of that TMX; their pixel positions were extracted and are
listed in ``CITY_PLOT_XY``.  Dto_WorldMap_Pos.posX/posY are int32 PIXEL coordinates
in that same space (the client's Cmd_WorldMap_GetInfo_CS viewport corners only add
up to ~100 body bytes when some of them are negative 10-byte-varint pixels, never
with small cell indices).  Earlier this module sent a bogus grid cell (18,18) which
is not a plot at all; the client's MapProxy.updateCellInArea/createCellVoByDto then
dereferenced a null cell VO -> TypeError #1009 the moment the map opened.
"""

# (posX, posY) pixel positions of every city plot, taken verbatim from the
# objectgroup "对象层 1" of WorldMapRes1.tmx (ModuleMap.swf DefineBinaryData #1).
CITY_PLOT_XY = [
    (384, 384), (768, 384), (768, 768), (1152, 768), (384, 768), (768, 1152),
    (384, 1152), (384, 1536), (384, 1920), (768, 1920), (768, 2304), (1152, 1920),
    (768, 1536), (1152, 2304), (1536, 2304), (1536, 1920), (1920, 2304),
    (1536, 1536), (1152, 1536), (1152, 1152), (1536, 1152), (1920, 1536),
    (1920, 1920), (2304, 2304), (2304, 1920), (2304, 1536), (384, 2304),
    (1152, 384), (1536, 768), (1920, 1152), (1536, 384), (1920, 768), (1920, 384),
    (2304, 768), (2304, 1152), (2304, 384), (192, 1728), (192, 192), (576, 192),
    (576, 576), (192, 576), (576, 960), (192, 960), (576, 1344), (192, 1344),
    (576, 1728), (576, 2112), (192, 2112), (960, 192), (960, 576), (960, 960),
    (960, 1344), (960, 1728), (960, 2112), (1344, 2112), (1344, 1728),
    (1344, 1344), (1344, 960), (1344, 576), (1344, 192), (1728, 192), (1728, 576),
    (1728, 960), (1728, 1344), (1728, 1728), (1728, 2112), (2112, 2112),
    (2112, 1728), (2112, 1344), (2112, 960), (2112, 576), (2112, 192),
]

# Stable home plot for the single local player: the plot closest to the map centre.
_HOME_PLOT_XY = (1152, 1152)


def make_handlers(api):
    """Build WorldMap request handlers using the main server's codec helpers."""

    def build_cell(player, area_type):
        pos = api.pb_varint_field(1, _HOME_PLOT_XY[0]) \
            + api.pb_varint_field(2, _HOME_PLOT_XY[1])
        diamond = api.build_player_diamond(player)
        return (
            api.pb_varint_field(1, 0)  # type: local city (same value as CellInfo)
            + api.pb_bytes(2, pos)
            + api.pb_int64_field(3, int(player.get("role_id") or player.get("uin") or 0))
            + api.pb_string(4, player.get("name") or "Player")
            + api.pb_varint_field(5, 0)  # warState
            + api.pb_varint_field(6, 0)  # stateEndTime
            + api.pb_varint_field(7, int(player.get("face_id") or 0))
            + api.pb_varint_field(8, 1)  # E_WarFreeType_None
            + api.pb_varint_field(9, 0)  # warFreeEndTime
            + api.pb_int64_field(11, 0)  # no local guild assignment
            + api.pb_string(12, "")
            # These are message fields. Send empty messages so the Flash client can
            # dereference them safely instead of seeing null values.
            + api.pb_bytes(13, b"")
            + api.pb_bytes(14, b"")
            + api.pb_varint_field(15, int(area_type or 1))
            + api.pb_bytes(16, diamond)
            + api.pb_varint_field(30, 0)  # mineState
            + api.pb_varint_field(50, 0)  # templeId
        )

    def get_info(cmd_data, player):
        # areaType is field 5 in Cmd_WorldMap_GetInfo_CS. Bounds are intentionally
        # ignored: there is no persistent city grid in this offline server, and a
        # single stable local marker is more useful than fabricating remote cities.
        fields = api.parse_varint_fields(cmd_data)
        area_type = int(fields.get(5, 1) or 1)
        if area_type not in (1, 2, 3, 4):
            area_type = 1
        cell = build_cell(player, area_type)
        body = api.pb_bytes(1, cell)  # Cmd_WorldMap_GetInfo_SC.cellList
        return [("PVZ.Cmd.Cmd_WorldMap_GetInfo_SC", body)]

    def get_enemy_list(_cmd_data, _player):
        # No other players or PvP state exist in a local save. A valid empty list
        # prevents the client from waiting on an unhandled command.
        return [("PVZ.Cmd.Cmd_WorldMap_GetEnemyList_SC", b"")]

    def get_guild_members(_cmd_data, _player):
        # Guild membership is not represented by the world-map save model. The guild
        # system can extend this handler when it supplies actual member coordinates.
        return [("PVZ.Cmd.Cmd_WorldMap_GetGuildMemberList_SC", b"")]

    def operate_stat(_cmd_data, _player):
        # This is telemetry only; the protocol defines no response message.
        return []

    return {
        "PVZ.Cmd.Cmd_WorldMap_GetInfo_CS": get_info,
        "PVZ.Cmd.Cmd_WorldMap_GetEnemyList_CS": get_enemy_list,
        "PVZ.Cmd.Cmd_WorldMap_GetGuildMemberList_CS": get_guild_members,
        "PVZ.Cmd.Cmd_WorldMap_OperateStatInfo_CS": operate_stat,
    }
