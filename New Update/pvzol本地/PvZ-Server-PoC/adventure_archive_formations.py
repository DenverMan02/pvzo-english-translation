"""Archived elite/alliance rosters with explicit evidence and local-stat provenance.

The data file fixes species, stars, count and slots. Original numeric attributes
are used only when separately transcribed; otherwise the existing local stage
calibration supplies attributes, without substituting random enemy species.
"""
from pathlib import Path
import json

DATA_PATH = Path(__file__).with_name('adventure_archive_formations.json')
VALID_POSITIONS = frozenset((4, 8, 10, 12, 14, 16, 18))
STAT_FIELDS = frozenset(('hp', 'shield', 'atk', 'def_', 'ability', 'hit', 'dodge',
                         'critical', 'tough', 'power', 'speed'))


def validate_data(document):
    if document.get('schema_version') != 1:
        raise ValueError('Unsupported archived adventure roster schema')
    index = {}
    for formation in document['formations']:
        key = tuple(int(formation[name]) for name in ('chapter', 'mode', 'stage'))
        if key[0] <= 0 or key[1] not in (2, 3) or key[2] <= 0 or key in index:
            raise ValueError('Invalid or duplicate archived stage: %r' % (key,))
        units = formation['units']
        if not 1 <= len(units) <= len(VALID_POSITIONS):
            raise ValueError('Invalid archived roster count: %r' % (key,))
        occupied = set()
        for unit in units:
            pos, cid = int(unit['pos']), int(unit['card_id'])
            if pos not in VALID_POSITIONS or pos in occupied or cid <= 0:
                raise ValueError('Invalid archived slot/card: %r' % (key,))
            occupied.add(pos)
            if int(unit.get('level') or 1) < 1:
                raise ValueError('Invalid archived enemy level')
            stats = unit.get('stats') or {}
            if set(stats) - STAT_FIELDS or any(int(value) < 0 for value in stats.values()):
                raise ValueError('Invalid archived attributes: %r' % (key,))
            if 'hp' in stats and int(stats['hp']) < 1:
                raise ValueError('Archived HP must be positive')
            if 'power' in stats and int(stats['power']) < 1:
                raise ValueError('Archived display power must be positive')
            if int(unit.get('level') or 1) > 300 and 'speed' not in stats:
                raise ValueError('Archived levels above the native speed table require explicit speed')
        if not formation.get('source_group_id') or not formation.get('mapping_status'):
            raise ValueError('Archived roster must retain source and mapping status')
        if 'max_rounds' in formation and not 1 <= int(formation['max_rounds']) <= 60:
            raise ValueError('Invalid archived round limit')
        index[key] = formation
    return index


# Fail before accepting a game connection if the mandatory roster file is lost
# or corrupt. This module only reads static data, never player storage.
FORMATIONS = validate_data(json.loads(DATA_PATH.read_text(encoding='utf-8-sig')))


def get_formation(chapter, mode, stage):
    return FORMATIONS.get((int(chapter or 0), int(mode or 0), int(stage or 0)))


def combat_options(chapter, mode, stage):
    options = {'leader_sides': ('left',), 'stop_on_elimination': True}
    formation = get_formation(chapter, mode, stage)
    if formation:
        # Activate native captain behavior only where the source actually shows
        # a captain badge; an arbitrary first-slot fallback is not evidence.
        if formation.get('enemy_captain_badge_observed'):
            options['leader_sides'] = ('left', 'right')
        if formation.get('max_rounds'):
            options['max_rounds'] = int(formation['max_rounds'])
    return options


def build_units(api, chapter, mode, stage):
    formation = get_formation(chapter, mode, stage)
    if formation is None:
        return None
    cards = api.load_battle_cards()
    total_power = api._chapter_base_power(chapter) * (1 + api._FALLBACK_STAGE_SCALE * (stage - 1))
    default_power = max(1, int(total_power / len(formation['units'])))
    result = []
    for number, row in enumerate(formation['units']):
        cid, level = int(row['card_id']), int(row.get('level') or 1)
        if cid not in cards or cards[cid]['type'] == 2:
            raise ValueError('Archived enemy is not a native zombie card: %d' % cid)
        if not cards[cid].get('plan'):
            raise ValueError('Archived enemy has no client animation plan: %d' % cid)
        template = api.card_stats_at_level(cid, level)
        authored = {name: int(value) for name, value in (row.get('stats') or {}).items()}
        power = authored.get('power', default_power)
        attack, defense = api._enemy_atk_def(power)
        ehp = max(1, power * 9 // api._ZOMBIE_HP_TEST_DIVISOR)
        hp, shield = api._zombie_hp_shield_split(cid, template, ehp)
        unit = {'pos': int(row['pos']), 'card_id': cid, 'level': level,
                'hp': hp, 'shield': shield, 'atk': attack, 'def_': defense,
                'ability': template.get('ability', 0), 'hit': template.get('hit', 0),
                'dodge': template.get('dodge', 0), 'critical': template.get('critical', 0),
                'tough': template.get('tough', 0),
                'skill1': template.get('skill1', 0), 'skill2': template.get('skill2', 0),
                'speed': api.card_level_speed(level), 'power': power,
                'name': template['name'],
                'is_leader': bool(row.get('is_leader', number == 0))}
        unit.update(authored)
        result.append(unit)
    return result
