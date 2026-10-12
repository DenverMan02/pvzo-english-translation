"""Restore configured prestige initiative and the garden's blue experience ball."""
from functools import wraps


def prestige_speed(g, uin, uid):
    level = g.store.get_card_evolve_level(uin, uid)
    row = g.load_plant_evolution()['levels'].get(level, {})
    return max(0, int(row.get('speed') or 0))


def install(g):
    if getattr(g, '_display_rules_repairs_20261009', False):
        return
    g._display_rules_repairs_20261009 = True
    original_prestige = g._apply_prestige_bonus
    original_sources = g._card_power_source_elements
    original_pot = g._encode_garden_pot
    original_full_info = g.build_card_full_info

    @wraps(original_prestige)
    def apply_prestige(stats, uin, uid, card_id):
        before = g.card_power(stats)
        result = original_prestige(stats, uin, uid, card_id)
        speed = prestige_speed(g, uin, uid)
        result['prestige_power'] = g.card_power(result) - before
        result['prestige_speed'] = speed
        result['refine_speed'] = int(result.get('refine_speed') or 0) + speed
        return result

    @wraps(original_full_info)
    def full_info(uid, card_id, level, stats, *args, **kwargs):
        positional = list(args)
        supplied = positional[9] if len(positional) >= 10 else kwargs.get('power_elements')
        speed = max(0, int(stats.get('prestige_speed') or 0))
        if supplied is None and speed:
            quality = kwargs.get('card_quality', positional[2] if len(positional) > 2 else None)
            quality = int(stats.get('quality', 1) if quality is None else quality)
            stage = g.card_puzzle_stage_for_display(quality,
                       kwargs.get('card_rank', positional[3] if len(positional) > 3 else 0))
            rows = [(17, g._card_fill_power(stage), 0),
                    (22, int(stats.get('prestige_power') or 0), speed)]
            refine_power = int(stats.get('refine_power') or 0)
            refine_speed = max(0, int(stats.get('refine_speed') or 0) - speed)
            if refine_power or refine_speed:
                rows.append((14, refine_power, refine_speed))
            if len(positional) >= 10:
                positional[9] = rows
            else:
                kwargs['power_elements'] = rows
        return original_full_info(uid, card_id, level, stats, *positional, **kwargs)

    @wraps(original_sources)
    def sources(player, uid, card_id, level, *args, **kwargs):
        rows = original_sources(player, uid, card_id, level, *args, **kwargs)
        speed = prestige_speed(g, player['uin'], uid)
        found = False
        result = []
        for element, power, initiative in rows:
            if element == 0:
                initiative = int(initiative or 0) + speed
            elif element == 22:
                initiative = speed
                found = True
            result.append((element, power, initiative))
        if speed and not found:
            result.append((22, 0, speed))
        return result

    @wraps(original_pot)
    def pot(seed_id, plant_time, now, *args, **kwargs):
        dto = original_pot(seed_id, plant_time, now, *args, **kwargs)
        if g.load_garden_seeds().get(seed_id, {}).get('type') == g._GARDEN_SEED_TYPE_COLOR:
            old = g.pb_bool(11, False)
            assert dto.endswith(old), 'garden pot protocol changed'
            return dto[:-len(old)] + g.pb_bool(11, True)
        return dto

    g._apply_prestige_bonus = apply_prestige
    g._card_power_source_elements = sources
    g._encode_garden_pot = pot
    g.build_card_full_info = full_info
