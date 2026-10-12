"""Isolated state transition helper for Daily TD start requests.

The live dispatcher can import ``prepare_dailytd_start`` from
``handle_dailytd_starttdgame``. This module deliberately has no database or
network dependencies; the caller persists ``data`` when ``changed`` is true.
"""


def prepare_dailytd_start(data, requested_stage, unlocked_plants, valid_plants,
                          max_losses):
    """Validate and prepare a DailyTD start.

    Returns ``(can_start, changed, accepted_offer)``. When a stage is still in
    the initial ChooseCard state but the client already sent StartTDGame, the
    first offer that is both valid and unlocked is accepted as a recovery
    choice. All ordinary stage, pending-lottery, loss-limit, active-run, and
    process checks remain in force. The caller must commit ``data`` if changed.
    """
    try:
        stage = int(data.get("stage", 0))
        requested_stage = int(requested_stage)
        losses = int(data.get("losses", 0))
        process = int(data.get("process", -1))
        max_losses = int(max_losses)
    except (TypeError, ValueError):
        return False, False, None

    if requested_stage != stage:
        return False, False, None
    if data.get("lottery"):
        return False, False, None
    if losses >= max_losses:
        return False, False, None

    active = bool(data.get("active", False))
    accepted_offer = None
    changed = False

    if process == 0:
        # Do not silently restart an already active run or bypass an empty or
        # invalid offer pool.
        if active:
            return False, False, None
        unlocked = set()
        valid = set()
        try:
            unlocked = {int(plant) for plant in unlocked_plants}
            valid = {int(plant) for plant in valid_plants}
        except (TypeError, ValueError):
            return False, False, None

        for offer in data.get("offers", ()):
            try:
                plant = int(offer)
            except (TypeError, ValueError):
                continue
            if plant in unlocked and plant in valid:
                accepted_offer = plant
                break
        if accepted_offer is None:
            return False, False, None

        try:
            cards = [int(plant) for plant in data.get("cards", ())]
        except (TypeError, ValueError):
            return False, False, None
        if accepted_offer not in cards:
            cards.append(accepted_offer)
            data["cards"] = cards
        data["process"] = 1
        process = 1
        changed = True
    elif process != 1:
        return False, False, None

    if not active:
        data.update(
            active=True,
            played=True,
            run=int(data.get("run", 0)) + 1,
            sun_buys=0,
            bean_buys=0,
            cart_buys=0,
            skill_buys=0,
        )
        changed = True

    return True, changed, accepted_offer
