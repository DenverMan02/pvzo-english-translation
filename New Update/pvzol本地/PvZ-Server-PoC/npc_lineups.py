"""Shared plant-team generation for local arena/guild bots and treasure raiders.

The client rejects duplicate tdId, not just duplicate cardId. Selection therefore
happens at species level before choosing quality/awakening variants.
"""
import math
import random


def plant_family(cards, cid):
    return int(cards.get(int(cid), {}).get('tdId') or -int(cid))


def choose_captain(units, cards, rng):
    if not units:
        return
    strongest = max(max(1, u['power']) for u in units)
    weights = [(0.4 + u['power'] / strongest) *
               (2.0 if cards.get(u['card_id'], {}).get('leader_skill') else 0.25)
               for u in units]
    leader = rng.choices(range(len(units)), weights=weights, k=1)[0]
    for i, unit in enumerate(units):
        unit['is_leader'] = i == leader


def generate_units(server, target_power, *, rng=None, uid_base=20000000,
                   positions=(3, 7, 9, 11, 13, 15, 17),
                   avoid_card_ids=(), used_card_ids=(), count=6):
    rng = rng or random
    server.load_battle_cards()
    cards = server._BATTLE_CARDS
    min_levels = {}
    for rule in server.load_awake_rules().values():
        target_cid = int(rule["awake_id"])
        min_levels[target_cid] = max(min_levels.get(target_cid, 1), int(rule["card_level"]))
    families = {}
    for cid, row in sorted(cards.items()):
        if int(row.get('type') or 0) == 2 and int(row.get('tdId') or 0) >= 1000:
            families.setdefault(plant_family(cards, cid), []).append(cid)
    avoid = {plant_family(cards, cid) for cid in avoid_card_ids}
    used = {plant_family(cards, cid) for cid in used_card_ids}
    # Relax cross-team exclusions only; never relax uniqueness within a team.
    preferred = [f for f in families if f not in avoid and f not in used]
    spare = [f for f in families if f not in preferred and f not in used]
    last = [f for f in families if f in used]
    for group in (preferred, spare, last):
        rng.shuffle(group)
    chosen = (preferred + spare + last)[:min(count, len(positions))]
    if not chosen:
        return []
    target = max(1, int(target_power))
    # One or two invested carries, with less-developed supporting plants.
    shares = [rng.uniform(1.3, 1.7)] + [rng.uniform(0.65, 1.15) for _ in chosen[1:]]
    rng.shuffle(shares)
    total_share = sum(shares)
    preferred_level = max(8, min(270, int(25 + 245 * target / (target + 85000))))
    selected = []
    for family, share in zip(chosen, shares):
        budget = target * share / total_share
        jitter = {cid: rng.uniform(-0.22, 0.22) for cid in families[family]}
        def score(cid):
            level = min(max(preferred_level, min_levels.get(cid, 1)), int(cards[cid].get('maxLv') or 1))
            power = max(1, server.card_power(server.card_stats_at_level(cid, level)))
            return abs(math.log(power / max(1, budget))) + jitter[cid]
        selected.append(min(families[family], key=score))
    units = []
    for i, (cid, share) in enumerate(zip(selected, shares)):
        budget = target * share / total_share
        max_level = max(1, min(300, int(cards[cid].get('maxLv') or 1)))
        min_level = min(max_level, min_levels.get(cid, 1))
        # Very strong requests can exceed level-only power. Keep realistic,
        # uneven near-cap levels rather than filling every slot with level 300.
        if budget > server.card_power(server.card_stats_at_level(cid, max_level)):
            max_level = max(min_level, int(max_level * rng.uniform(0.84, 1.0)))
        lo, hi = min_level, max_level
        while lo < hi:
            mid = (lo + hi) // 2
            if server.card_power(server.card_stats_at_level(cid, mid)) < budget:
                lo = mid + 1
            else:
                hi = mid
        level = min({lo, max(min_level, lo - 1)}, key=lambda lv: abs(
            server.card_power(server.card_stats_at_level(cid, lv)) - budget))
        stats = dict(server.card_stats_at_level(cid, level))
        stats.update(card_id=cid, level=level, uid=uid_base + i + 1, exp=0,
                     power=server.card_power(stats), speed=server.card_level_speed(level), is_leader=False)
        units.append(stats)
    choose_captain(units, cards, rng)
    # Captain choice is independent from position and serialization order.
    slots = rng.sample(list(positions), len(units))
    for unit, pos in zip(units, slots):
        unit['pos'] = pos
    return sorted(units, key=lambda u: u['pos'])
