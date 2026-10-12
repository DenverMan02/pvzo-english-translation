"""Local, config-driven support for type-1 (main-line) quests.

This module deliberately does not import the server dispatcher or mutate the
player save database.  The dispatcher supplies rows from ``QuestInfo.txt`` and
connects confirmed gameplay events through :func:`set_progress`.  Per-player
progress and claim flags live in an additive table in the existing ``players.db``
so progress, inventory, and one-time reward claims migrate with the same save.

Quest state values match the shipped client ``QuestConstant``:
  2 = unfinished, 3 = reward available, 4 = finished (removed by the client).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from typing import Any, Iterable


STATE_UNFINISHED = 2
STATE_RECEIVE = 3
STATE_FINISHED = 4

# ItemInfo's modeled account currencies in player_store.players. Town
# prosperity (2) is a bag item (client subtype SUB_TYPE_TOWN_PROSPERITY) —
# it lands in main.items like any material. Plant XP (5) can be applied by
# the dispatcher's transaction-bound special reward writer.
_CURRENCY_COLUMNS = {
    1: "coin",
    3: "sun",
    4: "present_ticket",
    6: "friend_point",
    7: "fame",
}
_UNMODELED_REWARD_IDS = {5}
log = logging.getLogger(__name__)

_PLAYER_DB_PATH = os.path.join(
    (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
     else os.path.dirname(os.path.abspath(__file__))),
    "players.db")


def _db_path() -> str:
    # Keep quest progress and claim flags in the same save database as the
    # player's inventory so they migrate together.
    return _PLAYER_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE IF NOT EXISTS main_quest_progress ("
        "uin INTEGER NOT NULL, quest_id INTEGER NOT NULL, "
        "curr_step INTEGER NOT NULL DEFAULT 0 CHECK(curr_step >= 0), "
        "claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)), "
        "PRIMARY KEY (uin, quest_id))"
    )
    return con


@contextmanager
def _db():
    import player_store as store
    if os.path.normcase(os.path.realpath(_db_path())) == os.path.normcase(os.path.realpath(store.DB_PATH)):
        with store._db() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS main_quest_progress ("
                "uin INTEGER NOT NULL, quest_id INTEGER NOT NULL, "
                "curr_step INTEGER NOT NULL DEFAULT 0 CHECK(curr_step >= 0), "
                "claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)), "
                "PRIMARY KEY (uin, quest_id))")
            yield con
        return
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _value(row: Any, key: str, index: int, default: str = "") -> str:
    if isinstance(row, dict):
        value = row.get(key, default)
    else:
        try:
            value = row[index]
        except (IndexError, KeyError, TypeError):
            value = default
    return str(value if value is not None else default).strip()


def _parse_rows(config_rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Parse authoritative QuestInfo rows; accepts _read_clientdb_table output.

    The first row is the header when it contains ``id``.  For list rows the
    QuestInfo.txt column order is: id,type,questName,shortName,icon,desc,
    finishCondition,finishConditionDesc,reward,isHideProgress.
    """
    parsed: list[dict[str, Any]] = []
    for raw in config_rows or ():
        row_id = _value(raw, "id", 0)
        if row_id.lower() == "id" or not row_id.isdigit():
            continue
        try:
            quest_id = int(row_id)
            quest_type = int(_value(raw, "type", 1))
        except (TypeError, ValueError):
            continue
        if quest_type != 1:
            continue
        finish = _value(raw, "finishCondition", 6)
        parts = finish.split(":")
        if len(parts) != 3:
            continue
        try:
            cond_type, cond_arg, target = (int(part) for part in parts)
        except ValueError:
            continue
        if target < 0:
            continue
        parsed.append({
            "id": quest_id,
            "type": quest_type,
            "condition_type": cond_type,
            "condition_arg": cond_arg,
            "target": target,
            "reward_text": _value(raw, "reward", 8),
        })
    # Client lists are sorted by state desc then id asc; stable ID order makes
    # the initial empty-progress list deterministic and keeps the oldest main
    # quest first in the quick-quest tile.
    parsed.sort(key=lambda quest: quest["id"])
    return parsed


def _parse_rewards(reward_text: str) -> list[tuple[int, int]]:
    """Parse QuestInfo.reward exactly as the client does: ``id,num;...``."""
    result: list[tuple[int, int]] = []
    for token in reward_text.split(";"):
        pair = token.split(",")
        if len(pair) != 2:
            continue
        try:
            item_id, amount = int(pair[0]), int(pair[1])
        except ValueError:
            continue
        if item_id > 0 and amount > 0:
            result.append((item_id, amount))
    return result


def _quest_state(uin: int, quest_id: int) -> tuple[int, bool]:
    with _db() as con:
        row = con.execute(
            "SELECT curr_step, claimed FROM main_quest_progress WHERE uin=? AND quest_id=?",
            (int(uin), int(quest_id)),
        ).fetchone()
    if row is None:
        return 0, False
    return int(row["curr_step"]), bool(row["claimed"])


def _state_for(curr_step: int, target: int, claimed: bool) -> int:
    if claimed:
        return STATE_FINISHED
    return STATE_RECEIVE if curr_step >= target else STATE_UNFINISHED


def get_list(uin: int, config_rows: Iterable[Any]) -> list[dict[str, int]]:
    """Return all unfinished type-1 quests in the client's Dto_Quest_Info shape.

    Completed-but-unclaimed rows use state 3; claimed rows are omitted because
    the client removes state 4 from its quest list after SyncState_SC.
    """
    quests = _parse_rows(config_rows)
    result = []
    with _db() as con:
        saved = {
            int(row["quest_id"]): (int(row["curr_step"]), bool(row["claimed"]))
            for row in con.execute(
                "SELECT quest_id, curr_step, claimed FROM main_quest_progress WHERE uin=?",
                (int(uin),),
            )
        }
    for quest in quests:
        curr_step, claimed = saved.get(quest["id"], (0, False))
        if claimed:
            continue
        curr_step = min(curr_step, quest["target"])
        result.append({"id": quest["id"],
                       "state": _state_for(curr_step, quest["target"], False),
                       "currStep": curr_step})
    # Match QuestModel.updateQuestList: state descending, then id ascending.
    result.sort(key=lambda item: (-item["state"], item["id"]))
    return result


def set_progress(uin: int, config_rows: Iterable[Any], quest_id: int,
                 curr_step: int) -> dict[str, int] | None:
    """Persist an observed, server-authoritative progress value for one quest.

    This function does not infer gameplay rules or advance tasks itself.  Call
    it only after an existing server handler has successfully committed the
    corresponding event/state.  Values are clamped to the configured target,
    are monotonic, and cannot change a claimed quest.

    Returns the updated Dto_Quest_Info fields when progress changed, otherwise
    ``None``.  The caller can serialize the result as SyncState_SC.quest.
    """
    try:
        uin, quest_id, curr_step = int(uin), int(quest_id), max(0, int(curr_step))
    except (TypeError, ValueError):
        return None
    quest = next((q for q in _parse_rows(config_rows) if q["id"] == quest_id), None)
    if quest is None:
        return None
    return _store_progress(uin, quest, curr_step)


def _store_progress(uin: int, quest: dict[str, Any], curr_step: int) -> dict[str, int] | None:
    quest_id = int(quest["id"])
    new_step = min(curr_step, quest["target"])
    with _db() as con:
        import player_store as store
        store.begin_immediate(con)
        row = con.execute(
            "SELECT curr_step, claimed FROM main_quest_progress WHERE uin=? AND quest_id=?",
            (uin, quest_id),
        ).fetchone()
        if row is not None and bool(row["claimed"]):
            return None
        old_step = int(row["curr_step"]) if row is not None else 0
        new_step = max(old_step, new_step)
        if new_step == old_step:
            return None
        con.execute(
            "INSERT INTO main_quest_progress (uin, quest_id, curr_step, claimed) VALUES (?,?,?,0) "
            "ON CONFLICT(uin, quest_id) DO UPDATE SET curr_step=excluded.curr_step",
            (uin, quest_id, new_step),
        )
    return {"id": quest_id, "state": _state_for(new_step, quest["target"], False),
            "currStep": new_step}


def set_condition_progress(uin: int, config_rows: Iterable[Any], condition_type: int,
                           condition_arg: int, curr_step: int) -> list[dict[str, int]]:
    """Apply an authoritative progress observation to quests with an exact condition key.

    Exact matching intentionally avoids inventing semantics for condition IDs.
    For threshold-style conditions whose third field is the threshold (for
    example role level/power), call :func:`set_progress` for each matching
    quest ID with the observed current value instead.
    """
    target_type, target_arg = int(condition_type), int(condition_arg)
    quests = [quest for quest in _parse_rows(list(config_rows or ()))
              if quest["condition_type"] == target_type
              and quest["condition_arg"] == target_arg]
    if not quests:
        return []

    # A single condition may back many threshold quests (role level and power
    # each have hundreds of configured milestones). Persist those observations
    # in one transaction instead of opening one SQLite connection per quest.
    updates = []
    with _db() as con:
        import player_store as store
        store.begin_immediate(con)
        for quest in quests:
            quest_id = int(quest["id"])
            new_step = min(max(0, int(curr_step)), int(quest["target"]))
            row = con.execute(
                "SELECT curr_step, claimed FROM main_quest_progress WHERE uin=? AND quest_id=?",
                (int(uin), quest_id),
            ).fetchone()
            if row is not None and bool(row["claimed"]):
                continue
            old_step = int(row["curr_step"]) if row is not None else 0
            new_step = max(old_step, new_step)
            if new_step == old_step:
                continue
            con.execute(
                "INSERT INTO main_quest_progress (uin, quest_id, curr_step, claimed) VALUES (?,?,?,0) "
                "ON CONFLICT(uin, quest_id) DO UPDATE SET curr_step=excluded.curr_step",
                (int(uin), quest_id, new_step),
            )
            updates.append({"id": quest_id,
                            "state": _state_for(new_step, quest["target"], False),
                            "currStep": new_step})
    return updates


def record_td_clear(uin: int, config_rows: Iterable[Any], stage: int,
                    level: int, sub_level: int) -> list[dict[str, int]]:
    """Record a successful TD clear using levelselectInfo's authoritative id.

    levelselectInfo IDs encode ``stage*10000 + level*100 + subLevel``; for
    example stage 1 / level 1 / sublevel 1 is 10101, matching QuestInfo rows.
    Call only from the successful branch after ``store.pass_level``.
    """
    condition_id = int(stage) * 10000 + int(level) * 100 + int(sub_level)
    return set_condition_progress(uin, config_rows, 1, condition_id, 1)


def record_fuben_clear(uin: int, config_rows: Iterable[Any], chapter: int,
                       mode: int, stage: int) -> list[dict[str, int]]:
    """Record a successful adventure clear by the matching fuben.txt row id.

    fuben.txt.id is ``chapter*10000 + stage*10 + mode`` (for example 10011 is
    chapter 1 / stage 1 / normal, and 10012 is the elite counterpart). Call only
    from the win branch after ``store.pass_fuben_stage``.
    """
    condition_id = int(chapter) * 10000 + int(stage) * 10 + int(mode)
    return set_condition_progress(uin, config_rows, 11, condition_id, 1)


def claim_and_grant(player_con: sqlite3.Connection, uin: int,
                    config_rows: Iterable[Any], quest_id: int,
                    special_reward_writer=None) -> dict[str, Any] | None:
    """Claim and grant a main-quest reward in one SQLite transaction.

    ``player_con`` must be a fresh connection to the existing player database
    (for example, yielded by ``player_store._db()``) with no transaction yet
    started. Quest state, player currencies, inventory and cards all live in
    this same database. A caller may provide ``special_reward_writer`` to
    persist additional virtual rewards (such as plant XP) inside that same
    transaction. The caller owns the surrounding context/commit.

    Invalid, incomplete, non-main, or already-claimed requests return ``None``
    without changing inventory.

    Returns configured reward pairs, the state-4 sync DTO, resulting currency
    balances and bag counts. A disconnect after commit is recoverable from the
    save on the next login; a retry cannot grant again.
    """
    try:
        uin, quest_id = int(uin), int(quest_id)
    except (TypeError, ValueError):
        return None
    if player_con.in_transaction:
        raise RuntimeError("claim_and_grant requires a fresh player DB connection")
    quest = next((q for q in _parse_rows(config_rows) if q["id"] == quest_id), None)
    if quest is None:
        return None

    player_con.execute(
        "CREATE TABLE IF NOT EXISTS main_quest_progress ("
        "uin INTEGER NOT NULL, quest_id INTEGER NOT NULL, "
        "curr_step INTEGER NOT NULL DEFAULT 0 CHECK(curr_step >= 0), "
        "claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)), "
        "PRIMARY KEY (uin, quest_id))"
    )
    player_con.execute("BEGIN IMMEDIATE")
    row = player_con.execute(
        "SELECT curr_step, claimed FROM main_quest_progress WHERE uin=? AND quest_id=?",
        (uin, quest_id),
    ).fetchone()
    if row is None or bool(row["claimed"]):
        return None
    curr_step = min(int(row["curr_step"]), quest["target"])
    if curr_step < quest["target"]:
        return None
    rewards = _parse_rewards(quest["reward_text"])
    changed = player_con.execute(
        "UPDATE main_quest_progress SET claimed=1 "
        "WHERE uin=? AND quest_id=? AND claimed=0",
        (uin, quest_id),
    ).rowcount
    if changed != 1:
        return None

    # Apply currencies and bag items on the caller-owned connection so every
    # modeled reward commits with the claim. Special virtual rewards are never
    # fabricated as bag items; an optional writer can persist them through
    # their actual game model on this same connection.
    combined: dict[int, int] = {}
    currency_deltas: dict[str, int] = {}
    unmodeled: dict[int, int] = {}
    for item_id, amount in rewards:
        column = _CURRENCY_COLUMNS.get(item_id)
        if column:
            currency_deltas[column] = currency_deltas.get(column, 0) + amount
        elif item_id in _UNMODELED_REWARD_IDS:
            unmodeled[item_id] = unmodeled.get(item_id, 0) + amount
        else:
            combined[item_id] = combined.get(item_id, 0) + amount
    player_fields = list(_CURRENCY_COLUMNS.values())
    player_row = player_con.execute(
        "SELECT " + ",".join(player_fields) + " FROM main.players WHERE uin=?", (uin,)
    ).fetchone()
    if player_row is None:
        raise RuntimeError(f"main quest reward player {uin} is missing from players")
    currency_counts = {}
    for column, delta in sorted(currency_deltas.items()):
        new_value = int(player_row[column]) + delta
        player_con.execute(f"UPDATE main.players SET {column}=? WHERE uin=?", (new_value, uin))
        currency_counts[column] = new_value

    # Match player_store.add_item's stack semantics, but execute on the same
    # transaction as the currency updates and quest claim.
    item_counts = []
    for item_id, amount in sorted(combined.items()):
        old = player_con.execute(
            "SELECT count FROM main.items WHERE uin=? AND item_id=?", (uin, item_id)
        ).fetchone()
        new_count = max(0, (int(old["count"]) if old else 0) + amount)
        player_con.execute(
            "INSERT INTO main.items (uin, item_id, count) VALUES (?,?,?) "
            "ON CONFLICT(uin, item_id) DO UPDATE SET count=excluded.count",
            (uin, item_id, new_count),
        )
        item_counts.append((item_id, new_count))

    special_rewards = {}
    if special_reward_writer is not None:
        special_rewards = special_reward_writer(player_con, uin, rewards) or {}
        for item_id in special_rewards.get("handledItemIds", ()):
            unmodeled.pop(int(item_id), None)
    if unmodeled:
        log.warning("main quest reward has unmodeled items uin=%d quest=%d rewards=%s",
                    uin, quest_id, sorted(unmodeled.items()))

    return {
        "questId": quest_id,
        "rewardList": rewards,
        "itemCounts": item_counts,
        "currencyCounts": currency_counts,
        "unmodeledRewards": sorted(unmodeled.items()),
        "specialRewards": special_rewards,
        "quest": {"id": quest_id, "state": STATE_FINISHED, "currStep": curr_step},
    }
