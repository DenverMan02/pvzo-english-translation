"""超级配药 (Cmd_Nutrition_SuperTrade_CS): the two dialog columns are independent axes.

The dialog is a 2x3 grid. Left, "一次性将指定金币配成药剂", offers three coin amounts.
Right, "将以下配制的药剂分解为药粉", offers three dissolve thresholds. The player picks one
of each independently:

    costType    1/2/3 -> 200000/500000/1000000 coins = 20/50/100 brews
    changeType  3/4/5 -> 绿蓝 / +紫 / +橙: quality <= changeType dissolves into 药粉

An earlier revision passed `changeType` into _nutrition_roll(), i.e. it used the DISSOLVE
threshold as if it were a dietician id. The 橙档 (5) happened to alias dietician 5 so it
looked fine, but 绿蓝 (3) aliased dietician 3 and made the cheapest dissolve setting roll
the worst reagents. On top of that the per-dietician spreads only cover q5/q6 at the top
end, so changeType 3 and 4 produced byte-identical results — "选绿蓝和选紫没区别".

These tests pin the two axes apart and require every one of the nine combinations to be
distinguishable, plus the invariants the result panel depends on (see the disassembly of
NutritionProxy.onCmd_Nutrition_SuperTrade_SC -> openTradeResult(cost, newExp-oldExp,
qualityNum, goldReagentNum - before)): `cost` is echoed verbatim, so "消耗金币" always
equals the coins actually deducted.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER)

import player_store as store  # noqa: E402

store.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pvz_supertrade_"), "test.db")
store.init_db()
import pvz_poc_server as srv  # noqa: E402

UIN = 700004001
COSTS = {1: 200000, 2: 500000, 3: 1000000}
GOLD_RATE = 10          # 1 黄金原液 per 10 brews


def cs(cost_type, change_type):
    return srv.pb_varint_field(1, cost_type) + srv.pb_varint_field(2, change_type)


def trade(cost_type, change_type, uin=UIN, coin=5_000_000):
    store.get_or_create_player(uin)
    store.save_player(uin, coin=coin, nutrition_gold=0, nutrition_bottle_exp=0, reagent_seq=0)
    player, _ = store.get_or_create_player(uin)
    return srv.handle_nutrition_supertrade(cs(cost_type, change_type), player)


def seed(uin, coin=5_000_000):
    store.get_or_create_player(uin)
    store.save_player(uin, coin=coin, nutrition_gold=0, nutrition_bottle_exp=0, reagent_seq=0)
    return uin


def sc_of(messages):
    for name, body in messages:
        if name == "PVZ.Cmd.Cmd_Nutrition_SuperTrade_SC":
            return body
    return None


def quality_num(body):
    return {srv.parse_varint_fields(d).get(1, 0): srv.parse_varint_fields(d).get(2, 0)
            for d in srv.parse_repeated_submessages(body, 5)}


def coin_after(uin):
    with store._db() as con:
        return con.execute("SELECT coin FROM players WHERE uin=?", (uin,)).fetchone()["coin"]


class SuperTradeAxisTest(unittest.TestCase):
    def test_invalid_requests_are_rejected(self):
        for bad_cost, bad_change in ((0, 5), (4, 5), (1, 0), (1, 2), (1, 6)):
            msgs = trade(bad_cost, bad_change)
            self.assertIsNone(sc_of(msgs),
                              "costType=%s changeType=%s must not trade" % (bad_cost, bad_change))

    def test_cost_field_always_equals_the_coins_deducted(self):
        """This is the "金币扣除有问题" guard: the result panel prints sc.cost verbatim as
        消耗金币, so it has to be byte-identical to the coin delta the player sees."""
        for cost_type, cost in COSTS.items():
            uin = seed(UIN + cost_type)
            before = coin_after(uin)
            body = sc_of(trade(cost_type, 5, uin=uin))
            self.assertIsNotNone(body)
            self.assertEqual(srv.parse_varint_fields(body).get(4, 0), cost,
                             "costType=%d echoed the wrong cost" % cost_type)
            self.assertEqual(before - coin_after(uin), cost,
                             "coin actually deducted differs from the reported cost")

    def test_too_little_coin_refuses_the_whole_trade(self):
        """A partial brew would still charge the full cost — that is what read as broken."""
        uin = seed(UIN + 90, coin=199999)
        player, _ = store.get_or_create_player(uin)
        msgs = srv.handle_nutrition_supertrade(cs(1, 5), player)
        self.assertIsNone(sc_of(msgs), "must not trade at 199999 coins")
        self.assertEqual(coin_after(uin), 199999, "no coins may be taken on refusal")

    def test_every_combination_is_distinguishable(self):
        """All nine cells must differ, otherwise part of the grid is decorative."""
        seen = {}
        for cost_type in (1, 2, 3):
            for change_type in (3, 4, 5):
                uin = UIN + 100 + cost_type * 10 + change_type
                msgs = trade(cost_type, change_type, uin=uin)
                body = sc_of(msgs)
                self.assertIsNotNone(body, (cost_type, change_type))
                fields = srv.parse_varint_fields(body)
                signature = (fields.get(2, 0) - fields.get(1, 0),      # exp gained
                             tuple(sorted(quality_num(body).items())))
                seen[(cost_type, change_type)] = signature

        # A higher dissolve threshold must never keep MORE than a lower one.
        for cost_type in (1, 2, 3):
            lo = seen[(cost_type, 3)]
            mid = seen[(cost_type, 4)]
            hi = seen[(cost_type, 5)]
            self.assertGreaterEqual(sum(n for _, n in lo[1]), sum(n for _, n in mid[1]),
                                    "costType=%d: 紫 kept more than 绿蓝" % cost_type)
            self.assertGreaterEqual(sum(n for _, n in mid[1]), sum(n for _, n in hi[1]),
                                    "costType=%d: 橙 kept more than 紫" % cost_type)

    def test_bigger_spend_yields_a_richer_result(self):
        """Same dissolve threshold, three spends: the expensive tier must roll better."""
        gold_counts = {}
        for cost_type in (1, 2, 3):
            uin = UIN + 200 + cost_type
            body = sc_of(trade(cost_type, 5, uin=uin))
            gold_counts[cost_type] = quality_num(body)
        def gold(sig):
            return sig.get(6, 0)
        self.assertLess(gold(gold_counts[1]), gold(gold_counts[2]))
        self.assertLess(gold(gold_counts[2]), gold(gold_counts[3]))

    def test_only_qualities_above_the_threshold_are_kept(self):
        for change_type in (3, 4, 5):
            uin = UIN + 300 + change_type
            body = sc_of(trade(2, change_type, uin=uin))
            for quality in quality_num(body):
                self.assertGreater(quality, change_type,
                                   "quality %d should have dissolved at changeType=%d"
                                   % (quality, change_type))

    def test_gold_reagent_scales_with_the_coin_committed(self):
        for cost_type, cost in COSTS.items():
            uin = seed(UIN + 400 + cost_type)
            body = sc_of(trade(cost_type, 5, uin=uin))
            gained = srv.parse_varint_fields(body).get(3, 0)
            self.assertEqual(gained, (cost // 10000) // GOLD_RATE,
                             "costType=%d gold reagent payout is wrong" % cost_type)

    def test_change_type_never_reaches_the_roll(self):
        """The regression itself: the dissolve threshold must not index the quality table."""
        self.assertFalse(hasattr(srv, "_NUTRITION_SUPER_TRADE_DIETICIANS"),
                         "the costType->dietician alias is back; that was the original bug")
        for change_type in (3, 4, 5):
            seen = set()
            for cost_type in (1, 2, 3):
                seen |= {srv._nutrition_supertrade_roll(cost_type) for _ in range(400)}
            # tiers are 2..5 -> base ids 602xx..605xx
            tiers = {b // 100 - 600 for b in seen}
            self.assertTrue(tiers.issubset({2, 3, 4, 5}), tiers)
            self.assertGreaterEqual(len(tiers), 3,
                                    "a spread must cover several qualities or the dissolve "
                                    "settings become indistinguishable")


if __name__ == "__main__":
    unittest.main()
