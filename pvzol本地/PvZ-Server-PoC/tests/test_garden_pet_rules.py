"""阳光房宠物 must follow the pet-house help screen the player reported against.

    1. 4 pets with different skills, all boosting the garden's plant-exp output.
    2. 好感度: "通过花费钻石购买一天和永久购买可以提高宠物好感度，1钻石=1好感度，当好感度累积
       达到指定数量时可解锁对应宠物" — 天天 1000 / 圆圆 2000 / 甜甜 10000 / 壮壮 20000.
    3. "永久购买可以直接解锁宠物，并且减少永久购买其他宠物的花费".
    4. The pet banks one exp ball every 20s, and keeps working while offline.

The reported fault was "宠物不会工作": handle_garden_buypet required 好感度 >= unlockExp BEFORE
accepting the payment, so 天天 (needs 1000) could never be bought with 好感度 0 — and nothing else
could raise 好感度. These checks pin the un-dead-locked flow end to end.
"""
import os
import sys
import tempfile
import time
import unittest

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_garden_pet_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700009042
# 2=天天 3=圆圆 4=甜甜 5=壮壮. 天天 and 甜甜 share a pinyin, so 甜甜 gets its own name here.
TIANTIAN, YUANYUAN, TIANTIAN_SWEET, ZHUANGZHUANG = 2, 3, 4, 5


class GardenPetRulesTest(unittest.TestCase):
    def setUp(self):
        player, _new = store.get_or_create_player(UIN)
        self.player = player
        state = {"garden_goodwill": 0, "garden_pet_data": "", "garden_exp": 0,
                 "ticket": 100000, "present_ticket": 0}
        self.player.update(state)
        store.save_player(UIN, **state)

    # --- helpers ---------------------------------------------------------------------------
    def reload(self):
        self.player, _new = store.get_or_create_player(UIN)
        return self.player

    def buy(self, pid, forever):
        body = srv.pb_varint_field(1, pid) + srv.pb_varint_field(2, 1 if forever else 0)
        return srv.handle_garden_buypet(body, self.reload())

    def used(self, pid):
        return srv.handle_garden_usepet(srv.pb_varint_field(1, pid), self.reload())

    def states(self):
        return srv._garden_pet_states(self.reload())

    # --- rule 2: prices and thresholds come from the client table -------------------------
    def test_thresholds_and_forever_prices_are_paired(self):
        pets = srv.load_garden_pets()
        self.assertEqual(sorted(pets), [1, 2, 3, 4, 5])
        for pid in srv._GARDEN_PET_IDS:
            self.assertEqual(pets[pid]["forever"], pets[pid]["unlock"],
                             "priceForever and unlockExp ship paired in GardenPet.txt")
        self.assertEqual([pets[p]["unlock"] for p in srv._GARDEN_PET_IDS],
                         [1000, 2000, 10000, 20000])
        # 天天 (2) and 甜甜 (4) are the two that share a pinyin; they are genuinely different pets.
        self.assertEqual((pets[TIANTIAN]["unlock"], pets[TIANTIAN_SWEET]["unlock"]), (1000, 10000))

    def test_a_permanent_purchase_unlocks_the_pet_with_its_own_diamonds(self):
        # 好感度 0 < 1000: the old code refused this. The 永久 purchase must go through and the
        # 1000 diamonds it costs must land as 1000 好感度.
        self.assertEqual(int(self.reload().get("garden_goodwill") or 0), 0)
        messages = self.buy(TIANTIAN, True)
        self.assertIn("PVZ.Cmd.Cmd_Garden_BuyPet_SC", [name for name, _b in messages])
        self.assertEqual(int(self.reload().get("garden_goodwill") or 0), 1000)
        state = self.states()[TIANTIAN]
        self.assertTrue(state["owned"] and state["forever"])

    def test_the_one_day_pet_stays_locked_until_the_threshold_is_reached(self):
        body = srv.pb_varint_field(1, TIANTIAN) + srv.pb_varint_field(2, 0)
        reply = srv.handle_garden_buypet(body, self.reload())
        self.assertEqual(srv.parse_varint_fields(reply[-1][1]).get(2), 0)
        self.assertEqual(int(self.reload().get("garden_goodwill") or 0), 0)

    def test_one_diamond_is_one_goodwill_point(self):
        self.buy(TIANTIAN, True)                       # 1000 diamonds -> 1000 好感度
        before = int(self.reload().get("garden_goodwill") or 0)
        price, _discount = srv._garden_pet_price(YUANYUAN, True, self.states())
        self.buy(YUANYUAN, True)
        self.assertEqual(int(self.reload().get("garden_goodwill") or 0) - before, price)

    # --- rule 3: a 永久 pet discounts the others ------------------------------------------
    def test_a_permanent_pet_discounts_the_others_forever_price(self):
        base, discount = srv._garden_pet_price(YUANYUAN, True, {})
        self.assertEqual((base, discount), (2000, 0))
        after, discount = srv._garden_pet_price(
            YUANYUAN, True, {TIANTIAN: {"forever": True}})
        self.assertEqual(discount, 10)
        self.assertEqual(after, 1800)
        # The discount is reported to the shop, not just applied to the charge.
        self.buy(TIANTIAN, True)
        pets = srv._garden_pet_dtos(self.reload())
        dto = next(p for p in pets if srv.parse_varint_fields(p).get(1) == YUANYUAN)
        self.assertEqual(srv.parse_varint_fields(dto).get(8), 1800)
        # ... and the 1-day price is untouched by it.
        self.assertEqual(srv.parse_varint_fields(dto).get(7), 20)

    def test_the_discount_is_capped(self):
        states = {pid: {"forever": True} for pid in (2, 3, 4, 5) if pid != ZHUANGZHUANG}
        price, discount = srv._garden_pet_price(ZHUANGZHUANG, True, states)
        self.assertEqual(discount, 30)
        self.assertEqual(price, 14000)
        many = {pid: {"forever": True} for pid in range(2, 40)}
        _price, capped = srv._garden_pet_price(ZHUANGZHUANG, True, many)
        self.assertEqual(capped, 50)

    def test_an_unaffordable_purchase_credits_nothing(self):
        store.save_player(UIN, ticket=0, present_ticket=0)
        reply = self.buy(TIANTIAN, True)
        self.assertEqual(srv.parse_varint_fields(reply[-1][1]).get(2), 0)
        self.assertEqual(int(self.reload().get("garden_goodwill") or 0), 0)
        self.assertEqual(self.states(), {})

    # --- the pet actually works ------------------------------------------------------------
    def test_the_bought_pet_can_be_selected_and_boosts_the_garden(self):
        self.buy(ZHUANGZHUANG, True)
        messages = self.used(ZHUANGZHUANG)
        self.assertIn("PVZ.Cmd.Cmd_Garden_UsePet_SC", [name for name, _b in messages])
        self.assertEqual(srv._garden_active_pet_id(self.reload(), int(time.time())), ZHUANGZHUANG)
        seed = srv.load_garden_seeds()[1]
        # 壮壮: averagePercent 300 (+200%) and addExp 24 per pickup.
        plain = srv._garden_pending_exp(seed, 1000, 1220, 0)
        boosted = srv._garden_pending_exp(seed, 1000, 1220, ZHUANGZHUANG)
        self.assertEqual(boosted, plain * 3 + 24 * (220 // seed["time"]))

    def test_an_idempotent_rebuy_does_not_double_charge(self):
        self.buy(TIANTIAN, True)
        diamonds = int(self.reload().get("ticket") or 0)
        goodwill = int(self.reload().get("garden_goodwill") or 0)
        self.buy(TIANTIAN, True)
        self.assertEqual(int(self.reload().get("ticket") or 0), diamonds)
        self.assertEqual(int(self.reload().get("garden_goodwill") or 0), goodwill)

    # --- 好感度 is a diamond reward, not a harvest reward ----------------------------------
    def test_harvesting_does_not_raise_goodwill(self):
        seeds = srv.load_garden_seeds()
        seed_id, sunflower = next((sid, s) for sid, s in seeds.items()
                                  if s["type"] == srv._GARDEN_SEED_TYPE_COLOR)
        now = int(time.time())
        store.set_garden_pot(UIN, 1, seed_id, now - sunflower["time"] * 3)
        player = self.reload()
        before_pool = int(player.get("garden_exp") or 0)
        messages = srv.handle_garden_harvest(srv.pb_varint_field(1, 1), player)
        self.assertIn("PVZ.Cmd.Cmd_Garden_Harvest_SC", [name for name, _b in messages])
        player = self.reload()
        self.assertGreater(int(player.get("garden_exp") or 0), before_pool)
        self.assertEqual(int(player.get("garden_goodwill") or 0), 0)


if __name__ == "__main__":
    unittest.main()
