"""Numeric and target rules displayed by DummyGlobel.getSkillDesc."""


def effect(template, level):
    level=max(1,int(level));desc=template.get('desc','')
    kind='shield' if '的护盾' in desc else 'heal' if '治疗' in desc else 'damage' if '伤害' in desc else None
    return {'kind':kind,'fixed':(template.get('fix_base',0)+level*template.get('fix_grow',0))//100,
            'percent':template.get('percent_base',0)+level*template.get('percent_grow',0),'desc':desc}


def targets(effect, candidates, hp, maximum, lane, depth, rng):
    pool=list(candidates);desc=effect['desc'];n=3 if '3个' in desc else 1
    if not pool:return []
    if '随机' in desc:return rng.sample(pool,min(n,len(pool)))
    if '生命百分比最少' in desc:return sorted(pool,key=lambda p:(hp[p]/maximum[p],p))[:n]
    if '生命最少' in desc:return sorted(pool,key=lambda p:(hp[p],p))[:n]
    front=sorted(pool,key=lambda p:(depth(p),p))[0]
    if '所在行' in desc:return [p for p in pool if lane(p)==lane(front)]
    if '前排' in desc:
        # Each occupied lane has its own first available target; empty front
        # cells do not protect a card standing farther back in the same lane.
        return [p for p in pool if depth(p)==min(depth(q) for q in pool if lane(q)==lane(p))]
    return [front]
