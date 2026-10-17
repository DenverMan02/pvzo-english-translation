"""Backpack rewards audited against the shipped clientDB and original bag protocol.

giftPackage is a fixed-reward table; fourFrameBag is a SELECT-ONE table. Neither
is a quality-based random drop pool. Missing online weights use an explicit
local contract only when ItemInfo identifies the reward families. Unidentified
event boxes remain intact rather than paying unrelated quality-based rewards.
"""
from collections import Counter
from pathlib import Path
import random
import re


# These shipped giftPackage rows disagree with ItemInfo after event IDs were reused.
# There is no item named 神秘碎片魔袋Ⅱ anywhere in either original resource pack.
CONFLICTED_IDS = set(range(3384, 3391)) | {3781}
UNKNOWN_BONUS_IDS = set(range(3750, 3760))
FIXED_OVERRIDES = {
    # Current ItemInfo has unique, exact names/quantities, unlike stale giftPackage.
    3372: ((3443, 1), (4029, 1)),
    4087: ((4115, 1), (4028, 1), (4027, 1), (1047, 1)),
    3800: ((4006, 20), (1, 500000)),
    3904: ((4, 10), (9, 10), (2003, 2), (1, 10000)),
    3905: ((4, 20), (9, 20), (2003, 4), (1, 20000)),
    3906: ((4, 30), (9, 30), (2003, 6), (1, 30000)),
}
QUARTER_BONUSES = {4060: (95531, 1000), 4072: (95535, 10), 4073: (95539, 1)}
# Quantities/candidates are explicit in ItemInfo; original relative weights are
# absent. A uniform LOCAL draw is used only for these finite, authored outcomes.
UNIFORM_IDS = ({3203, 3205, 3660, 3770, 3764, 3766, 3767, 4066, 4077,
                4079, 4083, 4128, 3674, 3765}
               | set(range(3667, 3670)) | set(range(3675, 3678))
               | {4074, 4075, 3304, 3305, 3690, 3693, 3723, 3726, 3727,
                  3728, 3729, 3734, 4039, 4049}
               | set(range(3366, 3369)) | set(range(3449, 3453))
               | set(range(3900, 3903)) | set(range(3907, 3910))
               | set(range(4054, 4060)) | set(range(91000, 91010))
               | set(range(91501, 91511)))
CHEST_SUBTYPES = {200, 203, 204, 217, 218, 224, 226, 235}


def _rows(g, table):
    rows = g._read_clientdb_table(table)
    return [dict(zip(rows[0], row)) for row in rows[1:]] if rows else []


def config(g):
    path = Path(g._CLIENTDB_PATH)
    stamp = (str(path), path.stat().st_mtime_ns, path.stat().st_size)
    cached = getattr(g, '_CHEST_REPAIR_CONFIG', None)
    if cached and cached[0] == stamp:
        return cached[1]
    items = {int(row['id']): row for row in _rows(g, 'ItemInfo.txt')}
    fixed = {}
    for row in _rows(g, 'giftPackage.txt'):
        pairs = tuple((int(row['item%d' % i]), int(row['count%d' % i]))
                      for i in range(1, 5) if int(row['item%d' % i]) > 0
                      and int(row['count%d' % i]) > 0)
        if pairs:
            fixed[int(row['id'])] = pairs
    choices = {}
    for row in _rows(g, 'fourFrameBag.txt'):
        pairs = {int(row['itemID%d' % i]): int(row['itemNum%d' % i])
                 for i in range(1, 6) if int(row['itemID%d' % i]) > 0
                 and int(row['itemNum%d' % i]) > 0}
        if pairs:
            choices[int(row['id'])] = pairs
    result = {'items': items, 'fixed': fixed, 'choices': choices}
    g._CHEST_REPAIR_CONFIG = (stamp, result)
    return result


def _chest(item):
    return (item and item.get('type') == '2'
            and int(item.get('subType') or 0) in CHEST_SUBTYPES)


def _plant_pool(g, data, star, rarity=None, names=None):
    g.load_battle_cards()
    pool = []
    for iid, item in data['items'].items():
        if item.get('type') != '4' or not item.get('name', '').endswith('(%d星)' % star):
            continue
        try:
            card = int(item['cardItemId'].split(':', 1)[0])
        except (KeyError, ValueError):
            continue
        if card not in g._BATTLE_CARDS:
            continue
        if rarity is not None:
            match = re.search(r'稀有度[：:]\s*(\d+)', item.get('desc', ''))
            if not match or int(match[1]) not in rarity:
                continue
        if names is not None and item['name'][:-4] not in names:
            continue
        pool.append(iid)
    return sorted(pool)


def _fragment_pool(g, data, rarity):
    names = {data['items'][iid]['name'][:-4]
             for star in (1, 2, 3, 4, 5)
             for iid in _plant_pool(g, data, star, rarity)}
    return sorted(iid for iid, row in data['items'].items()
                  if row.get('type') == '5' and row.get('name', '').startswith('碎片-')
                  and row['name'][3:] in names)


def local_contract(g, item_id):
    """Recover missing online drops from names/descriptions, with LOCAL weights.

    This is not a recovered official drop table. Named outcomes use the current
    ItemInfo identities; unspecified amounts default to ONE. The existing local
    mystery-pack quantities are reused only for its same material families.
    A described rare plant/bonus has the mystery pack's local 5% chance. Explicit
    guaranteed quantities and ranges always take precedence over these defaults.
    """
    data = config(g)
    cache = data.setdefault('local', {})
    if item_id in cache:
        return cache[item_id]
    item = data['items'].get(item_id)
    spec = None
    names = {}
    for iid, row in data['items'].items():
        names.setdefault(row['name'], []).append(iid)

    def reward(name, low=1, high=None):
        # ItemInfo also has log-only currency aliases. Choose the real wallet.
        wallet_ids = {'金币': 1, '钻石': 4, '土壤': 35, '体力': 9}
        if name in wallet_ids:
            return (wallet_ids[name], low, low if high is None else high)
        matches = names.get(name, ())
        if len(matches) != 1:
            raise LookupError('reward name is missing or ambiguous: ' + name)
        return (matches[0], low, low if high is None else high)

    def plant(name, star=3):
        pool = _plant_pool(g, data, star, names={name})
        if len(pool) != 1:
            raise LookupError('plant name/star is missing or ambiguous: ' + name)
        return tuple(pool)

    def group(pool=(), plants=(), chance=.05, families=()):
        return {'pool': tuple(pool), 'plants': tuple(plants), 'plant_chance': chance,
                'families': tuple(families)}

    def recipe(fixed=(), draws=(), bonus=(), distinct=False, jackpot=None):
        return {'fixed': tuple(fixed), 'draws': tuple(draws), 'bonus': tuple(bonus),
                'distinct': distinct, 'jackpot': jackpot}

    # Matches the existing local card-pack material policy, not box quality.
    materials = tuple((iid, min(amounts), max(amounts))
                      for iid, amounts in g._MYSTERY_MATERIAL_ROLLS)
    fragments = tuple((iid, 1, 1) for iid, row in data['items'].items()
                      if row.get('type') == '5' and row['name'].startswith('碎片-'))
    try:
        if item_id == 1091:
            # ItemInfo lists these seven families and exactly THREE rolls.
            allowed = {1, 210, 211, 2101, 2046, 2020, 2018}
            spec = recipe(draws=[group([row for row in materials if row[0] in allowed])] * 3)
        elif item_id == 3020:
            # This legacy title names its sole family even though the amount is
            # not shipped. Apply the documented conservative local default.
            spec = recipe((reward('金币'),))
        elif item_id in {3325, 3444, 3447}:
            plants = _plant_pool(g, data, 4) if item_id == 3325 else _plant_pool(g, data, 3, {4})
            # New legendary box explicitly adds magic plant coins to the pool.
            pool = (reward('神奇植物币', 10, 15),) if item_id == 3447 else materials
            if plants:
                spec = recipe(draws=[group(pool, plants)] * 3)
        elif item_id in {3108, 3350}:
            fixed = (reward('经验豆'), reward('时装水晶', 2)) if item_id == 3108 else (reward('废报纸', 8), reward('小经验豆', 8))
            pool = tuple((iid, 5, 5) for iid, _, _ in fragments)
            plants = tuple(iid for star in (1, 2, 3) for iid in _plant_pool(g, data, star))
            spec = recipe(fixed, [group(pool, plants)])
        elif item_id == 3326:
            spec = recipe(draws=[group(fragments + (reward('经验豆'), reward('一星叶绿素')), _plant_pool(g, data, 3))])
        elif 3320 <= item_id <= 3324:
            # Item 5 has no durable player balance. Small beans are an exact
            # 10,000-exp voucher (ItemInfo useEffect=1:10000), preserving all
            # described experience while letting the player choose its plant.
            low, high = ((1, 4), (2, 5), (3, 5), (4, 10), (5, 15))[item_id - 3320]
            chlorophyll = (2101 + item_id - 3320, 1, 1)
            # Three described reward families have equal LOCAL weight; the 78
            # fragment candidates must not make that branch 78 times likelier.
            spec = recipe(draws=[group(families=(group(fragments), group((chlorophyll,)),
                                                group((reward('小经验豆', low, high),))))])
        elif item_id in {3335, 3349}:
            name = '双头向日葵' if item_id == 3335 else '樱桃炸弹'
            spec = recipe((reward('金币', 5000),), [group(tuple((iid, 10, 10) for iid, _, _ in fragments)), group(materials, plant(name))])
        elif item_id == 3351:
            spec = recipe((reward('土壤', 100),), [group((reward('钻石'), reward('土壤')))])
        elif item_id in {3365, 3440}:
            pool = (reward('一星叶绿素'), reward('能量珠'), reward('1级星魂')) if item_id == 3365 else (reward('一星叶绿素'), reward('洗炼药水'), reward('能量石')) + tuple((iid, 1, 1) for iid in _fragment_pool(g, data, {4}))
            spec = recipe((reward('金币'), reward('植物币')), [group(pool)])
        elif item_id == 3369:
            spec = recipe((reward('碎片-月光花', 5), reward('神奇植物币', 20)), bonus=[(.05, group(plants=plant('月光花')))])
        elif item_id == 3400:
            plants = plant('竹小龙') + plant('蒲公英') + plant('倭瓜', 2)
            spec = recipe((reward('金币', 150000),), [group((reward('四星叶绿素'), reward('钻石')), plants)])
        elif item_id in {3420, 3421}:
            pool = (reward('酒吧兑奖券'), reward('植物交换券'), reward('时装水晶'))
            if item_id == 3420:
                pool += tuple((iid, 1, 1) for iid in _fragment_pool(g, data, {2, 3}))
            spec = recipe((reward('能量珠', 15),) if item_id == 3421 else (), [group(pool)])
        elif item_id in {3422, 3423, 3424, 3425, 3426, 3427, 3430}:
            name, amount, star = {3422: ('强酸柠檬', 1, 3), 3423: ('脉冲黄桃', 1, 3), 3424: ('钻地荔枝果', 1, 3),
                                  3425: ('鳄梨', 3, 3), 3426: ('海葵花', 3, 3), 3427: ('甜椒投手', 3, 2), 3430: ('南瓜小巫', 3, 3)}[item_id]
            spec = recipe((reward('碎片-' + name, amount),), [group((reward('神奇植物币'), reward('碎片-' + name)), plant(name, star))])
        elif item_id in {3428, 3446, 3760, 3761, 3762, 3763}:
            plants = (plant('玫瑰剑客') if item_id in {3760, 3763} else _plant_pool(g, data, 2, {5}) if item_id == 3446 else _plant_pool(g, data, 3, {4, 5}))
            pool = materials + ((reward('碎片-玫瑰剑客'),) if item_id == 3763 else ())
            spec = recipe(draws=[group(pool, plants)])
        elif item_id in {3441, 3443}:
            spec = recipe((reward('钻石', 50),), [group(materials)])
        elif item_id == 3442:
            spec = recipe(draws=[group((reward('一星叶绿素'), reward('洗炼药水'), reward('植物币'), reward('碎片-甜椒投手')))] * 2)
        elif item_id == 3445:
            souls = tuple((iid, 1, 1) for iid, row in data['items'].items()
                          if row['name'].endswith('星魂') and int(row.get('quality') or 0) in range(1, 6))
            if souls:
                spec = recipe((reward('星魄'),), [group(souls)])
        elif item_id in {3653, 3654, 3655, 3656, 3657, 3658}:
            pool = (reward('金币'),) if item_id == 3658 else (reward('经验豆'), reward('金币')) if item_id == 3657 else (reward('金币'), reward('经验豆'), reward('一星叶绿素'))
            spec = recipe(draws=[group(pool)])
        elif item_id == 3659:
            # Only actual world-tree seed templates can produce the described soil.
            pool = tuple((iid, 1, 1) for iid, row in data['items'].items()
                         if row.get('subType') == '232' and '种子' in row['name'] and '土壤' in row['desc'])
            if pool:
                spec = recipe(draws=[group(pool)])
        elif item_id == 3666:
            spec = recipe((reward('一星叶绿素'), reward('经验豆'), reward('金币', 1, 10000)))
        elif item_id == 3681:
            spec = recipe(draws=[group((reward('拼图点'), reward('经验豆'), reward('一星叶绿素'), reward('金币'), reward('植物交换券')))])
        elif item_id in {3688, 3689, 3695, 3696}:
            spec = recipe(draws=[group(materials, plant({3688: '君子兰', 3689: '龙舌兰', 3695: '石榴', 3696: '石榴'}[item_id]))])
        elif item_id in {3691, 3692, 3772}:
            name, pool = {3691: ('氧气藻', (reward('诱人经验豆'), reward('时装水晶'), reward('酒吧兑奖券'))),
                          3692: ('香蒲', (reward('拼图点'), reward('能量珠'), reward('酒吧兑奖券'))),
                          3772: ('强酸柠檬', (reward('诱人经验豆'), reward('植物币'), reward('酒吧兑奖券')))}[item_id]
            spec = recipe(draws=[group(pool, plant(name))])
        elif item_id == 3694:
            spec = recipe((reward('时装水晶', 2),), [group((reward('钻石'), reward('一星叶绿素'), reward('土壤')))])
        elif item_id == 3697:
            spec = recipe((reward('碎片-石榴', 5),), [group(materials)])
        elif item_id == 3698:
            spec = recipe((reward('植物币', 100),), bonus=[(.05, group(plants=_plant_pool(g, data, 3)))])
        elif item_id in {3721, 3722, 3730, 3731, 3733, 3735, 3736, 3738, 3739}:
            # These guarantees/ranges are stated verbatim in ItemInfo.
            name, fixed, pool = {
                3721: ('钻地荔枝果', reward('金币', 150000, 250000), (reward('金币', 10000),)),
                3722: ('钻地荔枝果', reward('小经验豆', 10, 25), (reward('小经验豆'),)),
                3730: ('香蒲', reward('金币', 150000, 250000), (reward('金币', 50000),)),
                3731: ('香蒲', reward('经验豆', 3, 8), (reward('经验豆'),)),
                3733: ('香蒲', reward('土壤', 3000), (reward('坚果种子', 2, 8),)),
                3735: ('芭蕉', reward('金币', 150000, 300000), (reward('金币', 50000),)),
                3736: ('芭蕉', reward('经验豆', 3, 12), (reward('经验豆'),)),
                3738: ('芭蕉', reward('土壤', 3000), (reward('坚果种子', 2, 10),)),
                3739: ('芭蕉', reward('能量珠', 50, 200), (reward('能量珠', 50),)),
            }[item_id]
            spec = recipe((fixed,), [group(pool, plant(name))])
        elif item_id in {3724, 3725, 3732, 3737}:
            pool = (reward('小经验豆'), reward('拼图点'), reward('二星叶绿素')) if item_id == 3724 else (reward('小经验豆'), reward('二星叶绿素'), reward('金币'), reward('时装水晶'))
            spec = recipe((reward('酒吧兑奖券', 10),) if item_id == 3724 else (), [group(pool)],
                          [] if item_id == 3724 else [(.05, group(plants=plant({3725: '钻地荔枝果', 3732: '香蒲', 3737: '芭蕉'}[item_id])))])
        elif 3750 <= item_id <= 3759:
            # The fourth fixed-table column is actually the optional plant in the
            # current description. 3754's description also corrected its trio.
            text = item['desc']
            guaranteed_text, optional_text = text.split('有较小几率额外获得', 1)
            guaranteed = tuple(reward(name + '(3星)', int(amount)) for name, amount in re.findall(r'([^、，。]+)\(3星\)x(\d+)', guaranteed_text.split('所需的', 1)[1]))
            optional = re.search(r'([^、，。]+)\(3星\)x(\d+)', optional_text)
            if guaranteed and optional:
                spec = recipe(guaranteed, bonus=[(.05, group((reward(optional[1] + '(3星)', int(optional[2])),)))])
        elif item_id in {3699, 3700} or 3773 <= item_id <= 3786 or 3787 <= item_id <= 3799 or 3852 <= item_id <= 3858:
            # Resolve the actual plant named in the description, not giftPackage's
            # stale 3781 plant ID or arithmetic derived from the box ID.
            match = re.search(r'\[碎片-([^\]]+)\]', item['desc'])
            star = re.search(r'植物[-—]([^()]+)\((\d)星\)', item['desc'])
            if match and star:
                name, level = match[1], int(star[2])
                guaranteed = re.search(r'至少获得\[碎片-[^\]]+\]x(\d+)', item['desc'])
                amount = int(guaranteed[1]) if guaranteed else 1
                fixed = (reward('碎片-' + name, amount, amount if guaranteed else 5),)
                if '或神奇植物币' in item['desc']:
                    spec = recipe(draws=[group((reward('碎片-' + name, 1, 5), reward('神奇植物币')), plant(name, level))])
                else:
                    spec = recipe(fixed, bonus=[(.05, group(plants=plant(name, level)))])
        elif item_id in {3846, 3847}:
            pool = (reward('冰皮月饼'), reward('什锦月饼'), reward('时装水晶'), reward('植物币'), reward('金币')) if item_id == 3846 else (reward('高级洗炼药水'), reward('能量石'), reward('1级星魂'), reward('星魄'))
            spec = recipe(draws=[group(pool)])
        elif item_id in {4029, 4030}:
            pool = (reward('高级洗炼药水'), reward('能量石'), reward('植物精华')) if item_id == 4029 else (reward('时装水晶'), reward('土壤'), reward('植物币'))
            spec = recipe(draws=[group(pool)])
        elif item_id in {4041, 4097, 4098, 4100, 4101, 4105, 4118, 4122}:
            name, material = {4041: ('月光花', '植物精华'), 4097: ('闪电蓝莓', '植物精华'), 4098: ('月光花', '合金元素'),
                              4100: ('君子兰', '植物精华'), 4101: ('金蟾菇', '植物精华'), 4105: ('闪电蓝莓', '植物精华'),
                              4118: ('月光花', '神奇植物币'), 4122: ('芦笋战机', '神奇植物币')}[item_id]
            spec = recipe((reward(material), reward('碎片-' + name)), bonus=[(.05, group(plants=plant(name)))])
        elif item_id in {4046, 4115}:
            plants = plant('甜椒投手', 2) + plant('海星果') + plant('火爆辣椒') + plant('鳄梨')
            spec = recipe((reward('体力', 80),), [group(materials, plants)])
        elif item_id == 4071:
            # The island already has an authored local payout list. Its bag box
            # promises ONE random reward, not all four rewards from that list.
            pool = tuple((iid, amount, amount) for iid, amount in g._CHEST_OPEN_REWARDS[4071])
            spec = recipe(draws=[group(pool)])
        elif item_id == 4076:
            spec = recipe((reward('碎片-铃儿草'),), bonus=[(.05, group(plants=plant('铃儿草', 2)))])
        elif item_id == 4099:
            spec = recipe((reward('稀有傀儡宝箱', 10, 30),), [group((reward('装备碎片'), reward('初级培养液'), reward('傀儡原石')))])
        elif item_id in {4102, 4106}:
            material = '初级培养液' if item_id == 4102 else '奇异能量石'
            pool = (reward('装甲碎片礼包'), reward('精致的晶石'), reward(material), reward('傀儡原石'))
            spec = recipe(draws=[group(pool)] * 2, distinct=True,
                          jackpot=(.05, (reward('傀儡原石', 2), reward('装甲碎片礼包', 2))))
        elif item_id in {4103, 4107, 4117, 4123}:
            high = {4103: 30, 4107: 20, 4117: 18, 4123: 10}[item_id]
            stone = 4 if item_id in {4117, 4123} else 2
            fixed = (reward('傀儡原石'), reward('稀有傀儡宝箱')) + ((reward('傀儡材料包'),) if item_id in {4103, 4107} else ())
            # Bonus raises the total to the described upper outcome.
            spec = recipe(fixed, bonus=[(.05, group((reward('傀儡原石', stone - 1),))), (.05, group((reward('稀有傀儡宝箱', high - 1),)))])
        elif 4108 <= item_id <= 4114:
            pool = [reward('金币'), reward('拼图点'), reward('神奇植物币'), reward('勇士铭牌'), reward('傀儡原石')]
            pool += [(iid, 1, 1) for iid, row in data['items'].items() if row['name'] in {'[攻击]装甲碎片', '[防御]装甲碎片', '[生命]装甲碎片'}]
            if item_id <= 4109:
                pool += [reward('土壤'), reward('时装水晶'), reward('星魄')]
            elif item_id <= 4111:
                pool += [reward('洗炼药水'), reward('能量石'), reward('星魄')]
            elif item_id <= 4113:
                pool += [reward('装备碎片'), reward('初级培养液'), reward('紫色能量石')]
            else:
                pool += [reward('合金元素'), reward('初级培养液'), reward('精致的晶石')]
            spec = recipe(draws=[group(pool)])
        elif item_id == 4116:
            spec = recipe((reward('装甲碎片礼包'), reward('奇异能量石')), bonus=[(.05, group((reward('装甲碎片礼包', 4),))), (.05, group((reward('奇异能量石', 99),)))])
        elif item_id in {4119, 4120}:
            pool = (reward('5级攻击结晶'), reward('神奇植物币'), reward('钻石'), reward('稀有傀儡宝箱'), reward('基因元件礼包'), reward('觉醒礼包'), reward('高级培养液' if item_id == 4119 else '果实礼包'))
            spec = recipe(draws=[group(pool, plant('红针花'))])
        elif item_id == 4125:
            spec = recipe((reward('神奇植物币'), reward('植物精华')), bonus=[(.05, group((reward('神奇植物币', 49),))), (.05, group((reward('植物精华', 199),)))])
        elif item_id == 4126:
            spec = recipe((reward('2级装甲结晶'), reward('神奇植物币')), bonus=[(.05, group((reward('2级装甲结晶', 7),))), (.05, group((reward('神奇植物币', 79),)))])
    except (LookupError, ValueError, IndexError):
        # Do not reinterpret an ambiguous/missing name as an unrelated item.
        spec = None
    cache[item_id] = spec
    return spec


def _local_roll(spec):
    def amount(row):
        iid, low, high = row
        return iid, low if low == high else random.randint(low, high)

    def draw(group):
        if group.get('families'):
            return draw(random.choice(group['families']))
        if group['plants'] and (not group['pool'] or random.random() < group['plant_chance']):
            return random.choice(group['plants']), 1
        return amount(random.choice(group['pool']))

    jackpot = spec.get('jackpot')
    if jackpot and random.random() < jackpot[0]:
        return [amount(row) for row in jackpot[1]]
    loot = [amount(row) for row in spec['fixed']]
    chosen = set()
    for group in spec['draws']:
        if spec.get('distinct'):
            group = dict(group, pool=tuple(row for row in group['pool'] if row[0] not in chosen))
        result = draw(group)
        chosen.add(result[0])
        loot.append(result)
    loot.extend(draw(group) for chance, group in spec['bonus'] if random.random() < chance)
    return loot


def contract(g, item_id):
    """Return the audit status without rolling or touching a save."""
    data = config(g)
    item = data['items'].get(item_id)
    if not _chest(item):
        return 'ordinary', ''
    if item_id in data['choices']:
        return 'select', 'fourFrameBag.txt: select exactly one authored reward'
    if local_contract(g, item_id):
        return 'local', 'ItemInfo named outcomes/guarantees; explicit local 5% plant/bonus and conservative amounts'
    if item_id in CONFLICTED_IDS:
        return 'blocked', 'giftPackage and current ItemInfo conflict; original quantities/pool missing'
    if item_id in UNKNOWN_BONUS_IDS:
        return 'blocked', 'extra plant probability is unspecified in the original resources'
    if item_id in UNIFORM_IDS:
        return 'uniform', 'ItemInfo defines candidates/quantities; original weights absent, local uniform draw'
    if (item_id in QUARTER_BONUSES or item_id in {3663, 3665}
            or (3641 <= item_id <= 3651 and (item_id - 3641) % 4 < 3)):
        return 'probability', 'exact probability and amount from original ItemInfo.txt'
    if item_id in FIXED_OVERRIDES or item_id in data['fixed']:
        return 'fixed', 'giftPackage.txt, or unique exact current ItemInfo correction'
    return 'blocked', 'online reward pool, quantities or probability are absent from original resources'


def roll(g, item_id):
    """One chest's real loot. None means the source cannot establish its contract."""
    data = config(g)
    kind, _ = contract(g, item_id)
    if kind in {'ordinary', 'select', 'blocked'}:
        return None
    if kind == 'local':
        return _local_roll(local_contract(g, item_id))
    if item_id in FIXED_OVERRIDES:
        return list(FIXED_OVERRIDES[item_id])
    if item_id in {3663, 3665}:
        guaranteed, optional = data['fixed'][item_id]
        return [guaranteed] + ([optional] if random.random() < .2 else [])
    if item_id in QUARTER_BONUSES:
        iid, amount = QUARTER_BONUSES[item_id]
        return [(iid, amount * (2 if random.random() < .25 else 1))]
    if 3641 <= item_id <= 3651 and (item_id - 3641) % 4 < 3:
        tier, index = divmod(item_id - 3641, 4)
        iid, amount = ((2130, 20 + 10 * tier), (35, 400 + 200 * tier),
                       (2018, 2 + tier))[index]
        chance = random.random()
        return [(iid, amount * (3 if chance < .2 else 2 if chance < .5 else 1))]
    if item_id in {3304, 3305}:
        coin, diamond, bean = (50000, 50, 2003) if item_id == 3304 else (10000, 10, 2002)
        return [(1, coin), random.choice([(4, diamond)] + [(bean, n) for n in range(1, 6)])]
    if 3366 <= item_id <= 3368:
        fragment = next((iid for iid, row in data['items'].items() if row['name'] == '碎片-石榴'), None)
        return [(1, (11126, 11225, 12016)[item_id - 3366]), (210, 5),
                (fragment, random.randint(1, 5))] if fragment else None
    if 3449 <= item_id <= 3452:
        medals, levels = ((100, (3, 4, 5)), (80, (2, 3, 4)), (50, (2,)), (10, (1,)))[item_id - 3449]
        return [(2028, medals), (random.choice(tuple(98000 + family * 10 + level
                                                    for family in range(4) for level in levels)), 1)]
    if item_id in {3203, 3205, 3660, 3770}:
        pool = _plant_pool(g, data, 2 if item_id == 3203 else 4 if item_id == 3660 else 3,
                           {5} if item_id == 3770 else None,
                           {'杨桃', '冰冻生菜', '全息坚果'} if item_id == 3660 else None)
        return [(random.choice(pool), 1)] if pool else None
    if item_id in {3764, 3766, 3767}:
        pool = _fragment_pool(g, data, {4, 5, 6, 7, 8} if item_id == 3764 else {4, 5})
        return [(random.choice(pool), {3764: 1, 3766: 3, 3767: 4}[item_id])] if pool else None
    if 3667 <= item_id <= 3669:
        tickets, counts = ((2, (1, 2, 3)), (5, (2, 3, 6)), (10, (3, 6, 9)))[item_id - 3667]
        return [(2025, tickets), (2003, random.choice(counts))]
    if 3675 <= item_id <= 3677:
        points, counts = ((1000, (2, 4, 6)), (2000, (4, 8, 12)),
                          (5000, (10, 20, 30)))[item_id - 3675]
        return [(2020, points), (2003, random.choice(counts))]
    if item_id == 3674:
        return [(3122, 1), (random.choice((3121, 2051)), 1)]
    if item_id == 3765:
        return [(2035, 30), (2046, random.choice((10, 20, 30)))]
    if item_id == 3690:
        return [(56500, random.randint(10, 30))]
    if item_id == 3693:
        return [random.choice(((2004, 5), (1, 1000000), (2104, 1)))]
    if item_id == 3723:
        return [(2018, 50), (2070, random.choice((3, 6, 9)))]
    if item_id in {3726, 3734}:
        return [(2046, 100), (2035, random.choice((10, 20, 35)))]
    if item_id == 3727:
        return [(35, 3000), (1041, random.choice((5, 10, 15)))]
    if item_id in {3728, 3729, 4039, 4049}:
        names = ({'香蒲', '椰子加农炮', '火爆辣椒', '海葵花', '电海松', '海星果'}
                 if item_id == 3728 else {'钻地荔枝果', '食人花', '氧气藻', '灵芝'}
                 if item_id == 3729 else None)
        pool = _plant_pool(g, data, 2 if item_id in {4039, 4049} else 3,
                           {5} if item_id == 4049 else {4}, names)
        return [(random.choice(pool), 1)] if pool else None
    if item_id in set(range(3900, 3903)) | set(range(3907, 3910)):
        tier = item_id - (3900 if item_id <= 3902 else 3907)
        fragment = 56500 if item_id <= 3902 else 59500
        return [(4, 50), (9, 50), (1, (50000, 100000, 200000)[tier]),
                random.choice(((2046, (5, 10, 20)[tier]), (fragment, (1, 3, 5)[tier])))]
    if 4054 <= item_id <= 4059:
        qualities = {1, 2} if item_id == 4054 else {item_id - 4054}
        pool = sorted(iid for iid, row in data['items'].items()
                      if row.get('name', '').endswith('图纸') and int(row['quality'] or 0) in qualities)
        if not pool:
            return None
        pairs = [(random.choice(pool), 1)]
        if 4055 <= item_id <= 4058 and random.random() < .25:
            pairs.append((random.choice(pool), 1))
        return pairs
    if item_id == 4066:
        pool = sorted(int(row['piece']) for row in g.load_dummy_config()['zombies'].values()
                      if int(row['quilty']) in {100, 200} and int(row['piece']) in data['items'])
        return [(random.choice(pool), 1)] if pool else None
    if item_id == 4074:
        return [(95537, 5), (95533, random.choice((50, 100, 150)))]
    if item_id == 4075:
        return [(95538, 5), (95535, random.choice((5, 10, 15)))]
    if item_id == 4077:
        return [(98904, 10), (random.choice((98901, 98902, 98903)), 1),
                (random.choice((98901, 98902, 98903)), 1)]
    if item_id == 4079:
        return [(random.choice((98004, 98014, 98024, 98034)), 1),
                (random.choice(tuple(98000 + family * 10 + level
                                     for family in range(4) for level in (2, 3, 4))), 1)]
    if item_id == 4083:
        return [(random.choice((98001, 98011, 98021, 98031)), 1)]
    if item_id == 4128:
        pool = _plant_pool(g, data, 3, names={'红针花'})
        return [random.choice([(iid, 1) for iid in pool] + [(4079, 1), (211, 40)])] if pool else None
    if 91000 <= item_id <= 91009:
        iid = 92001 if item_id <= 91004 else (92002, 92004, 92006, 92008, 92010)[item_id - 91005]
        low, high = (22, 32) if item_id <= 91004 else (28, 38)
        return [(iid, random.randint(low, high))]
    if 91501 <= item_id <= 91505:
        return [(random.choice((98901, 98902, 98903)), random.randint(1, 2))]
    if 91506 <= item_id <= 91510:
        low = item_id - 91500
        return [(98904, random.randint(low, low + 2))]
    return list(data['fixed'].get(item_id, ())) or None


def _validate(g, loot):
    data = config(g)
    g.load_battle_cards()
    for iid, amount in loot:
        item = data['items'].get(iid)
        if not item or not 0 < amount <= 4294967295 or iid in g._UNMODELED_CURRENCIES:
            return False
        if item.get('type') == '4':
            try:
                card = int(item['cardItemId'].split(':', 1)[0])
            except (KeyError, ValueError):
                return False
            if card not in g._BATTLE_CARDS:
                return False
        if item.get('type') == '7' and not g._equipment_template(iid):
            return False
    return True


def _can_receive(g, player, loot, consumed_id, count):
    """Check FINAL uint32 balances inside the transaction before spending the box."""
    data = config(g)
    for iid, amount in loot:
        if iid in g._CURRENCY_SYNC:
            field = g._CURRENCY_SYNC[iid][0]
            total = int(player.get(field) or 0) + amount
        elif iid == 9:
            total = g.current_energy(player) + amount
        elif data['items'][iid].get('type') in {'4', '7'}:
            continue
        else:
            total = g.store.get_item_count(player['uin'], iid) + amount
            if iid == consumed_id:
                total -= count
        if not 0 <= total <= 4294967295:
            return False
    return True


def _grant(g, player, loot, action_from):
    """Cards/equipment are instances; energy is real energy, never inert bag stacks."""
    data = config(g)
    regular, cards, equipment, messages = [], [], [], []
    taken = {row[0] for row in g.store.get_cards(player['uin'])}
    for iid, amount in loot:
        item = data['items'][iid]
        if item.get('type') == '4':
            card = int(item['cardItemId'].split(':', 1)[0])
            for _ in range(amount):
                _uid, dto = g._add_drawn_card(player, card, taken)
                cards.append(dto)
        elif item.get('type') == '7':
            for _ in range(amount):
                uid = g.store.next_equipment_uid(player['uin'])
                g.store.add_equipment(player['uin'], uid, iid)
                equipment.append(g.store.get_equipment(player['uin'], uid))
        elif iid == 9:
            player['energy'] = g.current_energy(player) + amount
            if player['energy'] > 4294967295:
                raise ValueError('chest energy exceeds uint32')
            g.store.save_player(player['uin'], energy=player['energy'])
            messages.append(g.energy_sync_msg(player))
        else:
            regular.append((iid, amount))
    messages.extend(g.grant_fuben_reward(player, regular))
    if cards:
        messages.append(('PVZ.Cmd.Cmd_Card_SyncFullInfo_SC',
                         b''.join(g.pb_bytes(2, dto) for dto in cards) + g.pb_varint_field(4, action_from)))
    if equipment:
        messages.append(g._equipment_item_update(equipment))
    return messages


def _ack(g, item_id, count):
    return ('PVZ.Cmd.Cmd_Item_Use_SC', g.pb_varint_field(1, item_id) + g.pb_varint_field(2, count))


def use(g, cmd_data, player):
    fields = g.parse_varint_fields(cmd_data)
    item_id, want = int(fields.get(1, 0)), int(fields.get(2, 1))
    item = config(g)['items'].get(item_id)
    if not _chest(item):
        import item_use_repairs
        return item_use_repairs.use(g, cmd_data, player)
    status, reason = contract(g, item_id)
    if item.get('isUse') != '1' or not 1 <= want <= 999 or status in {'select', 'blocked'}:
        g.log.warning('chest retained: item=%d status=%s reason=%s', item_id, status, reason)
        return [g._logic_error(902), _ack(g, item_id, 0)]
    with g.store.atomic_player(player):
        if int(player.get('level') or 1) < int(item.get('minLvRequired') or 1):
            return [g._logic_error(200), _ack(g, item_id, 0)]
        held = g.store.get_item_count(player['uin'], item_id)
        count = want if item.get('isMulUse') == '1' else 1
        if held < count:
            return [g._logic_error(204), _ack(g, item_id, 0)]
        rewards = Counter()
        for _ in range(count):
            result = roll(g, item_id)
            if not result:
                return [g._logic_error(902), _ack(g, item_id, 0)]
            for iid, amount in result:
                rewards[iid] += amount
        loot = sorted(rewards.items())
        if not _validate(g, loot) or not _can_receive(g, player, loot, item_id, count):
            return [g._logic_error(902), _ack(g, item_id, 0)]
        messages = [g.grant_items(player, [(item_id, -count)])]
        # ActionFrom.as: OpenGift=39, OpenChest=40.
        messages.extend(_grant(g, player, loot, 39 if item.get('subType') in {'204', '224'} else 40))
        body = g.pb_varint_field(1, item_id)
        dtos = [g.pb_varint_field(1, iid) + g.pb_varint_field(2, amount) for iid, amount in loot]
        if count > 1:
            body += g.pb_varint_field(2, count) + b''.join(g.pb_bytes(3, dto) for dto in dtos)
            response = 'PVZ.Cmd.Cmd_Item_OpenGiftChestBatch_SC'
        elif item.get('subType') in {'204', '224'}:
            body += b''.join(g.pb_bytes(2, dto) for dto in dtos)
            response = 'PVZ.Cmd.Cmd_Item_OpenGift_SC'
        else:
            # allItems (field 2) is shown BEFORE opening, then pulled back into
            # the box by the original client. Winners belong only in openedItems
            # (field 3), or they are previewed and revealed a second time.
            body += b''.join(g.pb_bytes(3, dto) for dto in dtos)
            response = 'PVZ.Cmd.Cmd_Item_OpenChest_SC'
        messages.extend([(response, body), _ack(g, item_id, count)])
        g.log.info('backpack chest opened: uin=%d %s(%d) x%d contract=%s -> %s',
                   player['uin'], item['name'], item_id, count, status, loot)
        return [message for message in messages if message]


def exchange(g, cmd_data, player):
    fields = g.parse_varint_fields(cmd_data)
    item_id, count, reward_id = (int(fields.get(i, 0)) for i in (1, 2, 3))
    data = config(g)
    item = data['items'].get(item_id)
    amount = data['choices'].get(item_id, {}).get(reward_id, 0)
    if not _chest(item) or item.get('isUse') != '1' or not amount or not 1 <= count <= 999:
        return [g._logic_error(902)]
    loot = [(reward_id, amount * count)]
    if not _validate(g, loot):
        return [g._logic_error(902)]
    with g.store.atomic_player(player):
        if int(player.get('level') or 1) < int(item.get('minLvRequired') or 1):
            return [g._logic_error(200)]
        if g.store.get_item_count(player['uin'], item_id) < count:
            return [g._logic_error(204)]
        if not _can_receive(g, player, loot, item_id, count):
            return [g._logic_error(902)]
        messages = [g.grant_items(player, [(item_id, -count)])]
        messages.extend(_grant(g, player, loot, 8))  # ActionFrom_Use
        messages.append(('PVZ.Cmd.Cmd_Item_Exchange_SC', g.pb_varint_field(1, item_id)
                         + g.pb_varint_field(2, count) + g.pb_varint_field(3, reward_id)
                         + g.pb_varint_field(4, amount * count)))
        return [message for message in messages if message]
