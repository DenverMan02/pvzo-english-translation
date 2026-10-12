"""Adventure rules supported by the local configuration and recovered evidence.

The supplied observation corpus does not identify original server enemy IDs or
fully observe battles. It must not be used as an executable enemy table. The
legacy chapter-1 wiki table is explicitly normal-only; other modes retain the
local fallback statistics until a complete roster can be mapped independently.
"""


def valid_passed(stage_ids, passed):
    """Keep actual clears of configured stages; stray save rows unlock nothing."""
    configured = set(stage_ids)
    return {stage: stars for stage, stars in passed.items()
            if stage in configured and isinstance(stars, int) and 1 <= stars <= 3}


def chapter_cleared(stage_ids, passed):
    """Every configured normal stage must be cleared, rather than just N rows."""
    configured = set(stage_ids)
    return bool(configured) and configured <= valid_passed(configured, passed).keys()


def stage_allowed(chapters, stages, get_passed, level, chapter, mode, stage):
    """Mirror the server's contiguous chapter/stage progression before settlement.

    ``get_passed(chapter, mode)`` is a read-only callback. Difficulty membership
    comes from clientDB, including authored alliance mode 3. No unverified
    cross-difficulty prerequisites or enemy power multipliers are introduced.
    """
    configured = sorted(set(stages.get((chapter, mode), ())))
    if stage not in configured:
        return False
    chapter_rows = sorted(chapters)
    if chapter not in {ch for ch, _need in chapter_rows}:
        return False
    for ch, need in chapter_rows:
        if ch > chapter:
            break
        if level < need:
            return False
        if ch < chapter and not chapter_cleared(
                stages.get((ch, 1), ()), get_passed(ch, 1)):
            return False
    passed = valid_passed(configured, get_passed(chapter, mode))
    # A previously cleared valid stage remains replayable for legacy saves.
    if stage in passed:
        return True
    return all(previous in passed for previous in configured[:configured.index(stage)])


def normal_wiki_level(levels, chapter, mode, stage):
    """The handwritten normal-mode table is not evidence for elite/alliance."""
    return levels.get(chapter, {}).get(stage) if mode == 1 else None


def fallback_seed_parts(chapter, mode, stage):
    """Preserve normal fallback picks while separating every other difficulty."""
    if mode == 1:
        return "fallback", chapter, stage
    return "fallback", chapter, mode, stage
