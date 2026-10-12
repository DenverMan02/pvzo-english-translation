"""Regression checks for the card wardrobe (时装) command family.

The old local implementation returned empty Buy/Use/Forge responses.  The Flash
wardrobe then dereferenced ``avatarInfo``/``avatarAllAttr`` and raised ActionScript
Error #1009.  This test keeps a real pea-shooter card in a temporary database and
checks the complete list -> forge -> buy -> use flow.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_avatar_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700003199


def msg(messages, name):
    return next((body for command, body in messages if command == name), None)


def main():
    player, _ = store.get_or_create_player(UIN)
    player["plants"] = [1200]
    srv.ensure_player_cards(player)
    card = next(row for row in store.get_cards(UIN) if row[1] == 1201)
    uid = card[0]
    request = srv.pb_int64_field(1, uid)

    info = srv.handle_card_getavatarinfo(request, player)
    avatars = srv.parse_repeated_submessages(info, 1)
    assert len(avatars) == 5, avatars
    decoded = [srv.parse_varint_fields(raw) for raw in avatars]
    assert decoded[0][1] == 1201 and decoded[0][2] == 4, decoded[0]
    assert all(raw.get(3) is not None and
               srv.parse_repeated_submessages(avatars[i], 6) and
               srv.parse_repeated_submessages(avatars[i], 7)
               for i, raw in enumerate(decoded)), decoded

    # Type 1, level 1 costs 3 of item 2018.  A real forge response must contain
    # the item update and a complete avatar response, not an empty SC.
    store.add_item(UIN, 2018, 3)
    forged = srv.handle_card_forgeavatar(
        request + srv.pb_varint_field(2, 1201), player)
    assert msg(forged, "PVZ.Cmd.Cmd_Card_ForgeAvatar_SC")
    assert store.get_card_avatars(UIN, uid)[1201]["forge_level"] == 1
    assert store.get_item_count(UIN, 2018) == 0

    # Buy an optional pea costume and activate it.  Item 2018 is the configured
    # unlock material for avatar 1203.
    store.add_item(UIN, 2018, 30)
    bought = srv.handle_card_buyavatar(
        request + srv.pb_varint_field(2, 1203), player)
    assert msg(bought, "PVZ.Cmd.Cmd_Card_BuyAvatar_SC")
    assert store.get_card_avatars(UIN, uid)[1203]["status"] == 3
    used = srv.handle_card_useavatar(
        request + srv.pb_varint_field(2, 1203), player)
    changed = srv.parse_repeated_submessages(
        msg(used, "PVZ.Cmd.Cmd_Card_UseAvatar_SC"), 1)
    assert any(srv.parse_varint_fields(raw).get(1) == 1203 and
               srv.parse_varint_fields(raw).get(2) == 4 for raw in changed)
    assert store.get_card_avatars(UIN, uid)[1203]["status"] == 4
    assert store.get_card_avatars(UIN, uid)[1201]["status"] == 3

    # The selected avatar must also travel with the ordinary card DTO and the
    # fight DTO; otherwise the wardrobe works but card/battle art stays default.
    stats = srv.effective_card_stats(player, card[1], card[2], uid=uid)
    full = srv.build_card_full_info(uid, card[1], card[2], stats, avatar_id=1203)
    base = srv.parse_repeated_submessages(full, 1)[0]
    assert srv.parse_varint_fields(base).get(9) == 1203
    fight = srv.build_fight_card(3, card[1], stats["hp"], avatar_id=1203)
    assert srv.parse_varint_fields(fight).get(15) == 1203

    # Buying an already-owned costume must not charge its unlock materials twice.
    store.add_item(UIN, 2018, 30)
    srv.handle_card_buyavatar(request + srv.pb_varint_field(2, 1203), player)
    assert store.get_item_count(UIN, 2018) == 30

    # Awakening uses a new tdId but must keep this UID's selected costume.
    awakened = (uid, 101205, 60, 0)
    assert srv._card_avatar_plant_id(awakened) == 1200
    assert srv._card_avatar_selected(player, uid, awakened) == 1203
    assert srv._card_avatar_total_level(player, uid, awakened) == 1

    # Every catalog costume has a real card binding.  TD selection is restricted
    # to the subset for which ModuleAvatarConfig contains a TD bone binding.
    templates = srv.load_card_avatar_templates()
    assets = srv.load_card_avatar_assets()
    assert len(templates) == 617 and len(assets) >= 617
    assert [aid for aid in templates if not assets.get(aid, {}).get("pvp")] == [9001]
    assert 0 < sum(1 for aid in templates if assets.get(aid, {}).get("td")) < len(templates)
    print("PASS card avatar wardrobe flow")


if __name__ == "__main__":
    main()
