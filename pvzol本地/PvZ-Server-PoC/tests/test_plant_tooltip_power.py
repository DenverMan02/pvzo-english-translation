"""Regression checks for the power-source rows consumed by PlantCardToolTip.updatePower."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_tooltip_power_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402


def main():
    player, _new = store.get_or_create_player(700001339)
    player["plants"] = [1200]
    srv.ensure_player_cards(player)
    cards = store.get_cards(player["uin"])
    assert cards, "fixture should have starter cards"
    uid, card_id, level, _exp = cards[0]

    # Refine's CardItemVO contract uses BaptizeAttribute.power/speed rather than
    # a guessed score: raw hundredths are converted using the original weights.
    # Source rows reconcile to the effective total, including rounding carries.
    refine = {"attack": 100, "defense": 100, "hp": 100, "ability": 100}
    store.set_card_baptize(player["uin"], uid, refine)

    # All owned-card sources must add up to the same total as the effective stats used in play.
    elements = srv._card_power_source_elements(player, uid, card_id, level, include_deployed=True)
    element_values = {int(ele): int(value) for ele, value, _speed in elements}
    effective = srv.effective_card_stats(player, card_id, level, uid=uid)
    expected_total = srv.card_power(effective)
    assert element_values[0] == expected_total
    assert sum(value for ele, value in element_values.items() if ele != 0) == expected_total
    assert 1 in element_values
    assert element_values[14] == srv.baptize_power(refine)
    refine_row = next(row for row in elements if int(row[0]) == 14)
    assert refine_row[2] == srv.baptize_speed(refine)

    # The full-card package, formation card position and battle card all carry the breakdown.
    full = srv.build_card_full_info(uid, card_id, level, effective, power_elements=elements)
    full_fields = srv.parse_varint_fields(full)
    full_rows = [srv.parse_varint_fields(row) for row in srv.parse_repeated_submessages(full, 13)]
    assert full_fields[2] == expected_total
    assert {row[1] for row in full_rows} == set(element_values)

    pos = srv.build_card_pos(3, uid, player["role_id"], effective["hp"], expected_total,
                             effective.get("refine_speed", 0), power_elements=elements)
    pos_rows = [srv.parse_varint_fields(row) for row in srv.parse_repeated_submessages(pos, 6)]
    assert {row[1] for row in pos_rows} == set(element_values)

    fight = srv.build_fight_card(3, card_id, effective["hp"], level, power=expected_total,
                                 speed=effective.get("refine_speed", 0), power_elements=elements)
    fight_fields = srv.parse_varint_fields(fight)
    fight_rows = [srv.parse_varint_fields(row) for row in srv.parse_repeated_submessages(fight, 18)]
    assert fight_fields[16] == expected_total
    assert {row[1] for row in fight_rows} == set(element_values)

    # The fight DTO's existing shield fields remain correctly serializable when an actual live
    # shield value is supplied by the combat state; this test does not manufacture shield state.
    shielded = srv.parse_varint_fields(srv.build_fight_card(
        3, card_id, effective["hp"], level, power=expected_total, shield=123, max_shield=456))
    assert shielded[5] == 123 and shielded[8] == 456

    print("PASS tooltip source rows sum to total power; ele=14 retains the refine power/speed contract")
    print("PASS breakdown serializes for card, formation and battle")
    print("PASS explicitly supplied live shield and maxShield serialize to Dto_FightCardInfo")


if __name__ == "__main__":
    main()
