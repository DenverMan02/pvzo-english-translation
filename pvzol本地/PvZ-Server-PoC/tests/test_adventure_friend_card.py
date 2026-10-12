"""冒险好友卡 (borrowed adventure friend cards) must not be weak and plain.

The report: "冒险好友卡植物太弱且普通，应该像真人玩家一样有升级有强化有拼图有装备等".

Three separate defects fed that symptom, and each has a check here:

1. Every injected 人机 friend lent the same hard-coded starter — 豌豆射手 (tdId 1200) at
   level 1 — so the picker never showed a real plant.
2. A borrowed card's stats were ``card_stats_at_level`` only, so the owner's 升级 / 强化(锻造) /
   拼图 / 装备(护甲) / 果实 / 觉醒 / 洗炼 / 基因 were all invisible.
3. ``store.get_friend_by_role_id`` gates the borrowed slot, and an injected 人机 has no row
   there — so the slot was silently dropped from the lineup AND from the fight.
"""
import os
import random
import sys
import tempfile
import time
import unittest

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_friend_card_"), "test.db")
store.init_db()
import bots_local  # noqa: E402
import pvz_poc_server as srv  # noqa: E402

UIN = 700009511
# Any roster slot works: bots are 900000..999999 and their uin doubles as their roleId.
BOT_UIN = int(bots_local.bots()[0]["uin"])
HIS_UID = 7000001
SLOT = 3               # a real formation slot (_FORMATION_POS == [3, 7, 9, 11, 13, 15, 17])
PLANT_ID = []          # filled by setUp: the tdPlantId of the card the bot lends


def _subs(body, field):
    return srv.parse_repeated_submessages(body, field)


class AdventureFriendCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv.load_battle_cards()
        # A real 人机 account with a real, developed main plant.  Doing the one bot by hand keeps
        # the test fast (booting the whole 50-bot roster takes ~50s) while exercising the same
        # store rows the server seeds.
        store.get_or_create_player(BOT_UIN)
        cls.card_id = max(srv._BATTLE_CARDS, key=lambda c: srv._BATTLE_CARDS[c]["rarity"])
        cls.card_level = 120
        store.add_card(BOT_UIN, HIS_UID, cls.card_id, cls.card_level, 0)
        srv.develop_npc_card(BOT_UIN, HIS_UID, cls.card_id, cls.card_level, 1.0,
                             random.Random(7))
        store.set_formation(BOT_UIN, 1, {1: HIS_UID})
        store.set_formation_leader(BOT_UIN, 1, HIS_UID)
        PLANT_ID.append(int(srv._BATTLE_CARDS[cls.card_id]["tdId"]))

    def setUp(self):
        player, _new = store.get_or_create_player(UIN)
        self.player = player
        store.save_player(UIN, name="Tester", level=200)
        self.player, _new = store.get_or_create_player(UIN)

    # --- helpers ---------------------------------------------------------------------------
    def friend_list(self, page=0, page_size=6):
        body = (srv.pb_varint_field(1, 5) + srv.pb_varint_field(2, 0)
                + srv.pb_varint_field(3, page) + srv.pb_varint_field(4, page_size))
        return srv.handle_friend_getlist(body, self.player)

    def friend_dto(self):
        """The Dto_FriendInfo the picker receives for the 人机, decoded."""
        store.add_friend(UIN, BOT_UIN, "BotTester", 0, 1)
        for dto in _subs(self.friend_list(), 7):
            if srv.parse_varint_fields(dto).get(2) == BOT_UIN:
                return dto
        self.fail("the 人机 friend is missing from the picker")

    def decoded_help_plant(self, dto):
        full = _subs(dto, 11)[0]
        base = srv.parse_varint_fields(_subs(full, 1)[0])
        return base, srv.parse_varint_fields(full)

    def own_lineup(self):
        """Put the borrowed card in a real lineup slot and read it back the way the server does."""
        uid, _cid, _lv, _st = srv._friend_help_card({}, BOT_UIN)
        store.set_formation(UIN, 1, {SLOT: uid}, {SLOT: BOT_UIN})
        return uid

    # --- defect 1: the picker offers a real plant, not a level-1 starter -------------------
    def test_the_picker_offers_the_bots_real_main_plant(self):
        base, _full = self.decoded_help_plant(self.friend_dto())
        self.assertEqual(base.get(2), self.card_id, "helpPlant.cardId is the bot's main card")
        self.assertEqual(base.get(4), self.card_level, "helpPlant.level is its real level")
        self.assertNotEqual((base.get(2), base.get(4)), (1200, 1))

    def test_the_friend_row_shows_that_same_plant_as_the_avatar(self):
        dto = self.friend_dto()
        plant_info = srv.parse_varint_fields(_subs(dto, 8)[0])
        self.assertEqual(plant_info.get(1), PLANT_ID[0])
        self.assertNotEqual(plant_info.get(1), 1200)

    def test_the_friend_rows_role_level_is_the_account_level_not_the_card_level(self):
        # roleLevel is cosmetic, but a card level (up to 300) there reads as nonsense.
        self.assertEqual(srv.parse_varint_fields(self.friend_dto()).get(7),
                         int(bots_local.bots()[0].get("level") or 1))

    # --- defect 2: the borrowed card carries the owner's investment ------------------------
    def test_a_borrowed_card_is_not_a_bare_base_card(self):
        _uid, cid, level, st = srv._friend_help_card({}, BOT_UIN)
        self.assertEqual((cid, level), (self.card_id, self.card_level))
        bare = srv.card_stats_at_level(cid, level)
        self.assertEqual([f for f in ("atk", "def_", "hp", "ability") if st[f] < bare[f]], [],
                         "a fully developed bot must never end up weaker than a bare card")
        self.assertTrue(any(st[f] > bare[f] for f in ("atk", "def_", "hp", "ability")),
                        "强化/洗炼/护甲/果实/拼图 must show through on a borrowed card")
        self.assertGreater(srv.card_power(st), srv.card_power(bare))

    def test_the_borrowed_card_ignores_the_owners_lab_bonus_but_keeps_its_own(self):
        # The borrower's deployed bonus is added by the caller, so the owner's Lab / World Tree
        # must NOT be baked in here — otherwise a borrowed plant gets boosted twice.
        owner, _new = store.get_or_create_player(BOT_UIN)
        _uid, cid, level, st = srv._friend_help_card({}, BOT_UIN)
        self.assertEqual(st, srv.friend_card_stats(BOT_UIN, cid, level))
        src = next(u for u, c, _l, _e in store.get_cards(BOT_UIN) if int(c) == cid)
        self.assertEqual(
            st, srv.effective_card_stats(owner, cid, level, srv._ZERO_STAT_BONUS, uid=src))

    def test_the_development_population_shows_on_another_plant_too(self):
        # Guarantee the gain is the owner's progression, not an accident of one card.
        second = next(c for c in sorted(srv._BATTLE_CARDS)
                      if c != self.card_id and srv._BATTLE_CARDS[c]["tdId"])
        uid2 = HIS_UID + 1
        store.add_card(BOT_UIN, uid2, second, 60, 0)
        srv.develop_npc_card(BOT_UIN, uid2, second, 60, 1.0, random.Random(11))
        st = srv.friend_card_stats(BOT_UIN, second, 60)
        bare = srv.card_stats_at_level(second, 60)
        self.assertTrue(any(st[f] > bare[f] for f in ("atk", "def_", "hp", "ability")))

    # --- defect 3: a 人机 friend is a friend, so the slot survives the lineup --------------
    def test_a_bot_friend_row_resolves_even_though_it_is_not_in_the_friends_table(self):
        self.assertIsNone(store.get_friend_by_role_id(UIN, BOT_UIN))
        row = srv.borrowed_friend(UIN, BOT_UIN)
        self.assertTrue(row)
        self.assertEqual(int(row["friend_role_id"]), BOT_UIN)

    def test_the_borrowed_slot_survives_the_adventure_lineup(self):
        uid = self.own_lineup()
        placements, owners = srv._fuben_formation_entries(self.player)
        self.assertEqual(owners.get(SLOT), BOT_UIN, "the 人机 slot must not be dropped")
        self.assertEqual(placements.get(SLOT), uid)

    def test_the_formation_response_carries_the_borrowed_slot_with_real_power(self):
        self.own_lineup()
        body = srv.handle_fuben_getformation(b"", self.player)
        formation = _subs(body, 1)[0]
        positions = [_subs(cp, 1) for cp in _subs(formation, 1)]
        self.assertTrue(positions)
        self.assertGreater(srv.parse_varint_fields(formation).get(4, 0), 0)   # totalPower

    def test_the_unit_that_fights_is_the_bots_real_plant(self):
        self.own_lineup()
        placements, owners = srv._fuben_formation_entries(self.player)
        units = srv.build_player_units(self.player, placements, owners)
        help_units = [u for u in units if u.get("is_help_card")]
        self.assertEqual(len(help_units), 1, "exactly the one borrowed friend card")
        unit = help_units[0]
        self.assertEqual(unit["card_id"], self.card_id)
        self.assertEqual(unit["level"], self.card_level)
        # The fighter's HP = the friend's own card + THIS player's deployed Lab / World-Tree bonus.
        _uid, _cid, _lv, st = srv._friend_help_card({}, BOT_UIN)
        bonus = srv.deployed_stat_bonus(self.player)
        self.assertEqual(unit["hp"], max(1, st["hp"] + bonus["hp"]))
        self.assertGreater(unit["power"], srv.card_power(srv.card_stats_at_level(
            self.card_id, self.card_level)))

    # --- the identity the client keys on must agree everywhere -----------------------------
    def test_the_friend_list_and_the_lineup_share_one_borrowed_uid(self):
        base, _full = self.decoded_help_plant(self.friend_dto())
        uid, _cid, _lv, _st = srv._friend_help_card({}, BOT_UIN)
        self.assertEqual(base.get(1), uid)
        self.assertEqual(self.own_lineup(), uid)
        self.assertEqual(uid, srv._friend_help_uid(BOT_UIN, self.card_id))

    # --- robustness -----------------------------------------------------------------------
    def test_an_unresolvable_friend_still_yields_a_non_null_card(self):
        uid, cid, level, st = srv._friend_help_card({"friend_role_id": 800001234}, 800001234)
        self.assertIn(cid, srv._BATTLE_CARDS)
        self.assertGreater(uid, 0)
        self.assertGreaterEqual(level, 1)
        self.assertGreater(st.get("hp", 0), 0)

    def test_a_friend_row_with_an_explicit_card_is_honoured(self):
        uid, cid, level, _st = srv._friend_help_card(
            {"friend_role_id": BOT_UIN, "help_card_id": self.card_id,
             "help_plant_level": 33}, BOT_UIN)
        self.assertEqual((cid, level), (self.card_id, 33))
        self.assertEqual(uid, srv._friend_help_uid(BOT_UIN, self.card_id))

    def test_empty_friend_list_returns_zero_total_and_does_not_crash(self):
        clean_uin = 700099999
        p, _ = store.get_or_create_player(clean_uin)
        # Ensure clean_uin has 0 friends
        self.assertEqual(len(store.get_friends(clean_uin)), 0)
        req = (srv.pb_varint_field(1, 5) + srv.pb_varint_field(2, 0)
               + srv.pb_varint_field(3, 0) + srv.pb_varint_field(4, 6))
        # Call request handler registered in REQUEST_HANDLERS (exercises friend_energy_repairs wrapper)
        handler = srv.REQUEST_HANDLERS["PVZ.Cmd.Cmd_Friend_GetList_CS"]
        res = handler(req, p)
        if isinstance(res, list):
            res_bytes = res[0][1]
        elif isinstance(res, tuple):
            res_bytes = res[1]
        else:
            res_bytes = res
        fields = srv.parse_varint_fields(res_bytes)
        total_count = fields.get(5, -1)
        self.assertEqual(total_count, 0)
        dtos = _subs(res_bytes, 7)
        self.assertEqual(len(dtos), 0)


if __name__ == "__main__":
    unittest.main()
