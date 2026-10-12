"""All plant families must consume/refund their own configured star fragments."""
import logging
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SERVER_DIR = HERE.parent if HERE.name == "tests" else Path(r"D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC")
sys.path.insert(0, str(SERVER_DIR))

import player_store as store


def main():
    with tempfile.TemporaryDirectory(prefix="pvz_fragment_identity_") as temp:
        store.set_db_path(os.path.join(temp, "isolated.db"))
        store.init_db()
        import pvz_poc_server as server
        logging.getLogger().setLevel(logging.WARNING)
        cards = server.load_battle_cards()
        recipes = server.load_card_upgrade_recipes()
        item_rows = server._read_clientdb_table("ItemInfo.txt")
        item_col = {name: index for index, name in enumerate(item_rows[0])}
        item_names = {int(row[item_col["id"]]): row[item_col["name"]] for row in item_rows[1:]}
        mapping_count = 0
        for card_id, card in cards.items():
            item_id = int(card.get("recycleItemId", 0))
            if item_id:
                assert item_names[item_id] == "碎片-" + card["name"], (card_id, card["name"], item_id)
                assert server._card_piece_item(card_id) == item_id, (card_id, card["name"], item_id)
                mapping_count += 1
        assert server._card_piece_item(0) == 0
        assert server._card_piece_item(999999999) == 0
        player, _ = store.get_or_create_player(700004312)
        player["level"] = 1000
        uid = 99000001
        fragment_ids = {int(recipe[2]) for recipe in recipes.values()}
        for item_id in fragment_ids:
            store.add_item(player["uin"], item_id, 100000)
        upgraded = terminal = 0
        for card_id, recipe in sorted(recipes.items()):
            level, forge, item_id, count, coins, next_id = recipe
            assert item_id == cards[card_id]["recycleItemId"], card_id
            store.add_card(player["uin"], uid, card_id, 17, 456)
            store.upgrade_card(player["uin"], uid, card_id)
            store.set_card_forge_level(player["uin"], uid, forge)
            player["coin"] = 100000000
            store.save_player(player["uin"], coin=player["coin"], level=player["level"])
            before = {item: store.get_item_count(player["uin"], item) for item in fragment_ids}
            messages = server.handle_card_usecardupgrade(server.pb_int64_field(1, uid), player)
            actual = next(card for card in store.get_cards(player["uin"]) if card[0] == uid)
            after = {item: store.get_item_count(player["uin"], item) for item in fragment_ids}
            if next_id:
                assert actual[1:4] == (next_id, 17, 456), (card_id, actual)
                assert cards[next_id]["name"] == cards[card_id]["name"]
                assert cards[next_id]["star"] == cards[card_id]["star"] + 1
                assert player["coin"] == 100000000 - coins, card_id
                assert after[item_id] == before[item_id] - count, card_id
                assert all(after[item] == before[item] for item in fragment_ids if item != item_id), card_id
                assert store.get_card_forge_level(player["uin"], uid) == 0, card_id
                assert messages[-1][0] == "PVZ.Cmd.Cmd_Card_UseCardUpgrade_SC", card_id
                refunded = server._card_star_refund(next_id)
                assert refunded and refunded[0] == (item_id, count, coins), (card_id, refunded)
                assert all(item == item_id for item, _, _ in refunded), (card_id, refunded)
                upgraded += 1
            else:
                assert actual[1] == card_id
                assert after == before
                assert player["coin"] == 100000000
                assert messages == [server._logic_error(1016)], (card_id, messages)
                terminal += 1
        # Screenshot regression: redstinger cannot consume toadstool fragments.
        store.add_card(player["uin"], uid, 1183, 1)
        store.upgrade_card(player["uin"], uid, 1183)
        store.set_card_forge_level(player["uin"], uid, 5)
        store.add_item(player["uin"], 59300, -store.get_item_count(player["uin"], 59300))
        fungal_before = store.get_item_count(player["uin"], 59100)
        player["coin"] = 1000000
        rejected = server.handle_card_usecardupgrade(server.pb_int64_field(1, uid), player)
        assert rejected == [server._logic_error(2417)], rejected
        assert store.get_item_count(player["uin"], 59100) == fungal_before
        assert next(card for card in store.get_cards(player["uin"]) if card[0] == uid)[1] == 1183
        assert player["coin"] == 1000000
        assert len(recipes) == 390 and upgraded == 312 and terminal == 78
        store.set_db_path(os.path.join(temp, "closed.db"))
        print(f"PASS: {mapping_count} fragment identities, {upgraded} upgrades, {terminal} final-star rejections; redstinger cannot consume toadstool fragments")


if __name__ == "__main__":
    main()
