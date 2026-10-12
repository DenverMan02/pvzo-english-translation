"""Skill-description adapters for the local combat engine.

Use shipped state IDs for Flash icons. Explicit percentages override a state's
numeric value without inventing wire IDs. Unspecified coefficients remain local
defaults, not claims about the unavailable production server.
"""
import re


def enrich_effect(sid, desc, original, states):
    effects = []
    tier = max(1, min(5, sid % 10))
    clauses = re.split(r"，|,|并", desc)

    def add(prefix, value, side, clause, turns=None, chance=100, **kw):
        ids = [i for i, p in states.items() if p[:3] == prefix]
        if not ids:
            return
        state = min(ids, key=lambda i: (abs(states[i][3] - value), i))
        duration = re.search(r"(?:持续|效果|定身|眩晕|沉默)(\d+)回合|(\d+)回合内", clause)
        effects.append(dict(state=state, param=prefix + (value,), side=side,
                            turns=turns or (int(duration[1] or duration[2]) if duration else 1),
                            chance=chance, **kw))

    # Collect EVERY effect, including an enemy bleed plus a separate self buff.
    rules = (
        (r"流血(\d+)%", (4, 3, 206), "enemies"),
        (r"每回合回复(\d+)%生命", (3, 3, 206), "allies"),
        (r"防御下降(\d+)%", (4, 2, 202), "enemies"),
        (r"伤害能力削弱(\d+)%", (14, 0, 0), "enemies"),
        (r"伤害和治疗能力削弱(\d+)%", (18, 0, 0), "enemies"),
        (r"(\d+)%吸血", (10, 3, 118), "self"),
        (r"(\d+)%反伤", (9, 3, 118), "self"),
        (r"(\d+)%攻击加成", (3, 1, 201), "self"),
        (r"(\d+)%伤害加成", (15, 0, 0), "self"),
        (r"(\d+)%免伤|免伤能力(\d+)%", (12, 0, 0), "allies"),
        (r"(\d+)%暴击加成", (3, 15, 215), "self"),
        (r"(\d+)%生命加成", (3, 6, 206), "allies"),
        (r"(\d+)%易伤", (13, 0, 0), "enemies"),
    )
    for clause in clauses:
        for pattern, prefix, side in rules:
            match = re.search(pattern, clause)
            if not match:
                continue
            if side != "enemies":
                if "自身" in clause:
                    side = "self"
                elif any(word in desc for word in ("治疗", "强化", "队友", "全体获得")):
                    side = "allies"
            chance = re.search(r"(\d+)%(?:几率|概率)", desc) if side == "enemies" else None
            # Duration often follows a comma after the effect clause.
            tail = desc[desc.index(clause):]
            add(prefix, int(next(g for g in match.groups() if g)) * 100,
                side, tail, chance=int(chance[1]) if chance else 100)
    if "清除" not in desc and any(w in desc for w in ("定身", "眩晕", "沉默")):
        control = "沉默" if "沉默" in desc else "眩晕" if "眩晕" in desc else "定身"
        chance = re.search(r"(\d+)%(?:几率|概率)", desc)
        # Unspecified procs: retain a conservative explicit local 15% default.
        percent = int(chance[1]) if chance else 15 if "有几率" in desc else 100
        add((8 if control == "沉默" else 7, 0, 0), 0, "enemies", desc, chance=percent)
        if control == "眩晕":
            effects[-1]["state"] = 800
    if "护盾" in desc and not any(w in desc for w in ("额外伤害", "除外")):
        if "当前生命50%" in desc:
            add((3, 4, 203), 5000, "self", desc)
        else:
            state = (2200 if "微量" in desc or 10301 <= sid <= 11105 else 12200) + tier
            param = states.get(state, (3, 4, 203, 1000))
            duration = re.search(r"护盾(\d+)回合", desc)
            add(param[:3], param[3], "allies", desc,
                turns=int(duration[1]) if duration else None,
                adjacent="身边" in desc)
    if 8401 <= sid <= 8405:
        # The client gives no coefficient: use an explicit local tier default.
        effects.append(dict(state=3100 + tier, param=states[3100 + tier],
                            side="allies", turns=1, chance=100,
                            healing_bonus_bp=1000 + (tier - 1) * 500, round_bound=True))
    if not effects and original.get("state"):
        effects.append(dict(original))
    if sid == 3:
        # This boss's 100% bleed has no matching max-HP state in clientDB.
        # Use the shipped attack-based tear mechanic, at one source attack
        # per tick, rather than turning the ambiguous text into an HP execute.
        effects = [dict(state=700, param=(6, 3, 201, 10000), side="enemies", turns=1, chance=100)]
    result = dict(effects[0]) if effects else {}
    result["effects"] = effects
    if original.get("cleanse"):
        result["cleanse"] = True
    return result
