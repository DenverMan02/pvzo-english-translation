"""Trial Road protocol fixes verified against ModuleTrialRoad/ModuleBattle.

The Flash Fight_SC listener snapshots data.currentStage for the result caption,
so the updated GetInfo_SC must arrive before a winning Fight_SC. The preparation
target carries its mode in type and its stage in screwUpStageId (field 7).
"""
from functools import wraps


TRIAL_TARGETS = {11: ("screwup", "tr_current", 1),
                 23: ("newscrewup", "tr_hard_current", 2)}


def trial_key(scene, stage):
    return "%s/%d" % (scene, stage)


def install(g):
    if getattr(g, "_trial_road_repairs_20261011", False):
        return
    g._trial_road_repairs_20261011 = True
    preview_request = "PVZ.Cmd.Cmd_Battle_GetFightInfo_CS"
    original_preview = g.handle_battle_getfightinfo
    original_preview_route = g.REQUEST_HANDLERS.get(preview_request, original_preview)

    def trial_preview(data, player):
        targets = g.parse_repeated_submessages(data, 1)
        target = g.parse_varint_fields(targets[0]) if targets else {}
        trial = TRIAL_TARGETS.get(target.get(1))
        if not trial:
            return None
        scene, progress, multiplier = trial
        # Field 7 is authoritative. Zero is accepted for older local callers
        # that omitted it, but the mode still comes from the explicit type.
        stage = int(target.get(7) or (int(player.get(progress) or 0) + 1))
        level = g.load_trial_road().get(stage)
        if not level:
            return b""
        g.load_battle_cards()
        g.note_battle_scene(player, scene)
        right = g._tr_enemy_squad(max(1, int(level["power"]) * multiplier), stage)
        g.remember_battle_preview(player, right, "screwup", trial_key(scene, stage),
                                 label="preview:" + trial_key(scene, stage))
        formation = g.build_formation_info(player, 1, g.default_formation_placements(player))
        body = g.pb_bytes(1, formation)
        for unit in right:
            body += g.pb_bytes(4, g.build_enemy_card(
                unit["pos"], unit["card_id"], unit["hp"], unit["level"],
                unit["is_leader"], power=unit["power"], speed=unit["speed"]))
        return body + g.pb_varint_field(5, sum(unit.get("power", 0) for unit in right))

    def preview_wrapper(original):
        @wraps(original)
        def preview(data, player):
            body = trial_preview(data, player)
            return original(data, player) if body is None else body
        return preview

    def fight_wrapper(original, scene, current, prefix):
        @wraps(original)
        def fight(data, player):
            stage = int(g.parse_varint_fields(data).get(1) or 0)
            pending = g._PENDING_PREVIEW.get(player.get("uin"))
            if pending and pending.get("family") == "screwup" and (
                    pending.get("key") != trial_key(scene, stage)):
                # The former shared empty key let a preview from another stage
                # or the other tab overwrite this encounter's zombie types.
                g._PENDING_PREVIEW.pop(player.get("uin"), None)
            messages = original(data, player)
            fight_name = "PVZ.Cmd.Cmd_%s_Fight_SC" % prefix
            info_name = "PVZ.Cmd.Cmd_%s_GetInfo_SC" % prefix
            fight_index = next((i for i, (name, _) in enumerate(messages)
                                if name == fight_name), None)
            info_index = next((i for i, (name, _) in enumerate(messages)
                               if name == info_name), None)
            if (fight_index is not None and info_index is not None and
                    info_index > fight_index and
                    g.parse_varint_fields(messages[fight_index][1]).get(5) == 1 and
                    int(player.get(current) or 0) == stage):
                # Preserve every other response's relative order, including
                # inventory, wallet and quest pushes.
                messages = list(messages)
                info = messages.pop(info_index)
                messages.insert(fight_index, info)
            return messages
        return fight

    # The route is a world-map adapter while the helper is the ordinary/endless
    # implementation. Keep both existing delegate chains and return shapes.
    g.handle_battle_getfightinfo = preview_wrapper(original_preview)
    g.handle_screwup_fight = fight_wrapper(
        g.handle_screwup_fight, "screwup", "tr_current", "ScrewUp")
    g.handle_newscrewup_fight = fight_wrapper(
        g.handle_newscrewup_fight, "newscrewup", "tr_hard_current", "NewScrewUp")
    g.REQUEST_HANDLERS.update({
        preview_request: preview_wrapper(original_preview_route),
        "PVZ.Cmd.Cmd_ScrewUp_Fight_CS": g.handle_screwup_fight,
        "PVZ.Cmd.Cmd_NewScrewUp_Fight_CS": g.handle_newscrewup_fight,
    })
