"""Install the archived limited-time activity adapter into REQUEST_HANDLERS.

Copy this file beside old_limited_activities.py into PvZ-Server-PoC, then call:
    import pvz_poc_server
    from REQUEST_HANDLERS_old_activities import install_request_handlers
    install_request_handlers(
        pvz_poc_server.REQUEST_HANDLERS,
        pvz_poc_server._activity_server_api(),
    )

This adapter only republishes historical activity icons and supplies safe empty
stat/claim replies. It does not recreate unavailable historical reward schedules.
"""

import old_limited_activities

GET_LIST = "PVZ.Cmd.Cmd_FlexibleActivity_GetActivityList_CS"
GET_STAT = "PVZ.Cmd.Cmd_FlexibleActivity_GetActivityStat_CS"
TAKE_REWARD = "PVZ.Cmd.Cmd_FlexibleActivity_TakeReward_CS"
GET_LIST_SC = "PVZ.Cmd.Cmd_FlexibleActivity_GetActivityList_SC"
TAKE_REWARD_SC = "PVZ.Cmd.Cmd_FlexibleActivity_TakeReward_SC"
_MARK = "_old_limited_activities_adapter"


def _append_activity_entries(result, entries):
    if not entries:
        return result
    if result is None:
        return entries
    if isinstance(result, (bytes, bytearray, memoryview)):
        return bytes(result) + entries
    if isinstance(result, list):
        updated = list(result)
        for index, message in enumerate(updated):
            if (isinstance(message, (tuple, list)) and len(message) == 2
                    and message[0] == GET_LIST_SC):
                updated[index] = (message[0], bytes(message[1]) + entries)
                return updated
        updated.append((GET_LIST_SC, entries))
        return updated
    raise TypeError("GetActivityList handler must return bytes or a list of (sc_name, body)")


def install_request_handlers(request_handlers, server_api):
    """Wrap the three FlexibleActivity handlers without replacing other behavior.

    `server_api` must expose parse_varint_fields and the protobuf/clientDB helpers
    used by old_limited_activities.py. The function is safe to call twice on the
    same handler dictionary; it won't wrap these handlers more than once.
    """
    missing = [name for name in (GET_LIST, GET_STAT, TAKE_REWARD)
               if name not in request_handlers]
    if missing:
        raise KeyError("REQUEST_HANDLERS is missing: " + ", ".join(missing))

    current = [request_handlers[name] for name in (GET_LIST, GET_STAT, TAKE_REWARD)]
    if all(getattr(handler, _MARK, False) for handler in current):
        return request_handlers

    original_get_list, original_get_stat, original_take_reward = current

    def get_list(cmd_data, player):
        base = original_get_list(cmd_data, player)
        entries = old_limited_activities.common_entries(server_api)
        return _append_activity_entries(base, entries)

    def get_stat(cmd_data, player):
        activity_id = server_api.parse_varint_fields(cmd_data).get(1, 0)
        archived = old_limited_activities.empty_stat(server_api, activity_id)
        if archived is not None:
            return archived
        return original_get_stat(cmd_data, player)

    def take_reward(cmd_data, player):
        activity_id = server_api.parse_varint_fields(cmd_data).get(1, 0)
        if activity_id in old_limited_activities.archived_activity_ids(server_api):
            return [(TAKE_REWARD_SC, b"")]
        return original_take_reward(cmd_data, player)

    for wrapper in (get_list, get_stat, take_reward):
        setattr(wrapper, _MARK, True)
    request_handlers[GET_LIST] = get_list
    request_handlers[GET_STAT] = get_stat
    request_handlers[TAKE_REWARD] = take_reward
    return request_handlers
