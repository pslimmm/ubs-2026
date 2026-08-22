import json
import logging
import os

from flask import jsonify, request

from routes import app

logger = logging.getLogger(__name__)
MEMORY_FILE = os.getenv("SHOWDOWN_MEMORY_FILE", "table_rules_memory.json")
CARDS = range(1, 14)
PRIMES = {2, 3, 5, 7, 11, 13}
NUMBER_METRICS = tuple(f"number_{card}" for card in CARDS)
OPEN_EQUITY = 0.60
CALL_EQUITY = 0.80
STEAL_EQUITY = 0.35
NUT_EQUITY = 12.5 / 13
MAX_CALL_BLINDS = 10
PHASE_TWO_TARGET = 25
PHASE_TWO_BUFFER = 5
MITIGATION_MAX_BLINDS = 4
OPPONENT_RAISE_EQUITY = 0.70
OPPONENT_BET_EQUITY = 0.60
RANGE_CALL_FLOOR = 0.50
RANGE_SAFETY_MARGIN = 0.10
STRATEGY_VERSION = "phase1-2-v5"


def load_memory() -> dict:
    try:
        with open(MEMORY_FILE) as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as error:
        logger.info("No usable Showdown memory: %s", error)
        return {}


def save_memory(data: dict) -> None:
    try:
        with open(MEMORY_FILE, "w") as file:
            json.dump(data, file, separators=(",", ":"))
    except OSError as error:
        logger.error("Could not save Showdown memory: %s", error)


RULE_KNOWLEDGE_BASE = load_memory()


def _observations(table_rule: str) -> list[list[int]]:
    rule_data = RULE_KNOWLEDGE_BASE.setdefault(table_rule, {})
    if not isinstance(rule_data, dict):
        rule_data = RULE_KNOWLEDGE_BASE[table_rule] = {}
    observations = rule_data.get("observations")
    if not isinstance(observations, list):
        observations = rule_data["observations"] = []
    else:
        observations[:] = [
            item for item in observations
            if isinstance(item, list)
            and len(item) == 4
            and item[0] in CARDS
            and item[1] in CARDS
            and item[2] in CARDS
            and item[1] < item[2]
            and item[3] in (-1, 0, 1)
        ]

    # Read the original exact-match memory format without discarding it.
    for community, pairs in list(rule_data.items()):
        if community == "observations" or not isinstance(pairs, dict):
            continue
        for cards, winner in pairs.items():
            try:
                low, high = map(int, cards.split("_"))
                result = 0 if winner == "tie" else (1 if winner == low else -1)
                old = [int(community), low, high, result]
                if old not in observations:
                    observations.append(old)
            except (AttributeError, TypeError, ValueError):
                continue
    return observations


def update_rule_knowledge(table_rule: str, recent_hands: list) -> None:
    """Keep unique, valid showdown comparisons for an opaque table rule."""
    if not table_rule or table_rule == "standard" or not isinstance(recent_hands, list):
        return

    observations = _observations(table_rule)
    changed = False
    for hand in recent_hands:
        if not isinstance(hand, dict) or hand.get("community_number") not in CARDS:
            continue
        try:
            shown = {int(seat): card for seat, card in hand["shown_numbers"].items()}
            winners = [int(seat) for seat in hand["winners"]]
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if len(shown) != 2 or len(winners) not in (1, 2) or not set(winners) <= set(shown):
            continue

        low, high = sorted(shown.values())
        if low == high or low not in CARDS or high not in CARDS:
            continue
        result = 0 if len(winners) == 2 else (1 if shown[winners[0]] == low else -1)
        observation = [hand["community_number"], low, high, result]
        if observation not in observations:
            observations.append(observation)
            changed = True

    if changed:
        save_memory(RULE_KNOWLEDGE_BASE)


def _metric(name: str, card: int, community: int) -> int:
    if name.startswith("number_"):
        return int(card == int(name.removeprefix("number_")))
    return {
        "card": card,
        "pair": int(card == community),
        "pair_seven": 2 if card == community else int(card == 7),
        "distance": abs(card - community),
        "parity": card % 2,
        "same_parity": int(card % 2 == community % 2),
        "prime": int(card in PRIMES),
        "above": int(card > community),
        "edge": abs(card - 7),
    }[name]


MODELS = tuple(
    (metric, direction, tiebreak)
    for metric in (
        "card", "pair", "pair_seven", "distance", "parity", "same_parity",
        "prime", "above", "edge"
    ) + NUMBER_METRICS
    for direction in (-1, 1)
    for tiebreak in (-1, 0, 1)
)
STANDARD_MODEL = ("pair", 1, 1)
KNOWN_MODELS = {
    "verdigris": STANDARD_MODEL,
    "cinnabar": STANDARD_MODEL,
    "amaranth": ("pair_seven", 1, 1),
    "obsidian": ("card", -1, 0),
}


def _result(model: tuple[str, int, int], first: int, second: int, community: int) -> int:
    metric, direction, tiebreak = model
    left = (direction * _metric(metric, first, community), tiebreak * first)
    right = (direction * _metric(metric, second, community), tiebreak * second)
    return (left > right) - (left < right)


def _models(table_rule: str) -> tuple[tuple[str, int, int], ...]:
    if table_rule == "standard":
        return (STANDARD_MODEL,)
    if table_rule in KNOWN_MODELS:
        return (KNOWN_MODELS[table_rule],)
    evidence = _observations(table_rule)
    if not evidence:
        return MODELS
    scores = [
        sum(
            _result(model, low, high, comm) == result
            for comm, low, high, result in evidence
        )
        for model in MODELS
    ]
    best = max(scores)
    return tuple(model for model, score in zip(MODELS, scores) if score == best)


def _matchup_equity(first: int, second: int, community: int, table_rule: str) -> float:
    if first == second:
        return 0.5
    low, high = sorted((first, second))
    if table_rule != "standard" and table_rule not in KNOWN_MODELS:
        for comm, seen_low, seen_high, result in _observations(table_rule):
            if (comm, seen_low, seen_high) == (community, low, high):
                result = result if first == low else -result
                return (result + 1) / 2
    results = [_result(model, first, second, community) for model in _models(table_rule)]
    return sum((result + 1) / 2 for result in results) / len(results)


def compare_hands(first: int, second: int, community: int, table_rule: str) -> int:
    equity = _matchup_equity(first, second, community, table_rule)
    return (equity > 0.5) - (equity < 0.5)


def evaluate_hand_strength(your_card: int | None, community_card: int | None,
                           table_rule: str) -> float:
    """Exact uniform equity, averaged over an unrevealed community card."""
    if your_card not in CARDS:
        return 0.5
    communities = CARDS if community_card is None else (community_card,)
    if any(community not in CARDS for community in communities):
        return 0.5
    equities = (
        _matchup_equity(your_card, opponent, community, table_rule)
        for community in communities
        for opponent in CARDS
    )
    return sum(equities) / (len(communities) * len(CARDS))


def _amount_bounds(data: dict) -> tuple[int, int] | None:
    low, high = data.get("min_raise_to"), data.get("max_raise_to")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None
    low, high = int(low), int(high)
    return (low, high) if 0 <= low <= high else None


def _future_blind_cost(data: dict) -> float:
    remaining = max(data.get("total_hands", 0) - data.get("hand_number", 0), 0)
    seat, button = data.get("your_seat"), data.get("button_seat")
    small, big = data.get("small_blind", 1), data.get("big_blind", 2)
    if not all(isinstance(value, (int, float))
               for value in (seat, button, small, big)):
        return big * remaining if isinstance(big, (int, float)) else 2 * remaining
    return sum(
        small if seat == (button + offset) % 2 else big
        for offset in range(1, remaining + 1)
    )


def _live_delta(data: dict) -> float:
    seat = data.get("your_seat")
    you = next((player for player in data.get("players", [])
                if player.get("seat") == seat or player.get("name") == "you"), {})
    delta = you.get("chip_delta", 0)
    stack, starting = data.get("your_stack"), data.get("starting_stack")
    if isinstance(stack, (int, float)) and isinstance(starting, (int, float)):
        delta = stack - starting
    return delta if isinstance(delta, (int, float)) else 0


def _secured(data: dict) -> bool:
    delta = _live_delta(data)
    if data.get("phase") == 2:
        return delta >= PHASE_TWO_TARGET + PHASE_TWO_BUFFER
    if data.get("phase") == 1:
        return delta >= 10 + _future_blind_cost(data)
    return False


def _mitigation_limit(data: dict) -> float | None:
    """Maximum optional chips exposed by one Phase 2 decision."""
    if data.get("phase") != 2:
        return None
    blind = data.get("big_blind", 2)
    blind = blind if isinstance(blind, (int, float)) and blind > 0 else 2
    delta = _live_delta(data)
    if delta >= PHASE_TWO_TARGET + PHASE_TWO_BUFFER:
        return 0
    if delta >= PHASE_TWO_TARGET:
        return blind
    shortfall = PHASE_TWO_TARGET - delta
    return max(blind, min(MITIGATION_MAX_BLINDS * blind, shortfall))


def _current_actions(data: dict) -> tuple[dict, ...]:
    actions = data.get("current_hand_actions", [])
    if not isinstance(actions, list):
        return ()
    return tuple(
        action for action in actions
        if isinstance(action, dict) and action.get("round") == data.get("round")
    )


def _all_actions(data: dict) -> tuple[dict, ...]:
    actions = data.get("current_hand_actions", [])
    if not isinstance(actions, list):
        return ()
    return tuple(action for action in actions if isinstance(action, dict))


def _is_opponent_action(action: dict, data: dict) -> bool:
    return str(action.get("seat")) != str(data.get("your_seat"))


def _opponent_range(data: dict) -> tuple[int, ...]:
    """Conservative card range implied by the opponent's visible actions."""
    candidates = tuple(CARDS)
    our_last_pre_action = None
    table_rule = data.get("table_rule", "standard")
    community = data.get("community_number")

    for action in _all_actions(data):
        action_round = action.get("round")
        action_name = action.get("action")
        if not _is_opponent_action(action, data):
            if action_round == "pre_reveal":
                our_last_pre_action = action_name
            continue

        threshold = None
        upper = None
        action_community = None
        if action_round == "pre_reveal" and action_name == "raise":
            threshold = (0.50 if our_last_pre_action == "call"
                         else OPPONENT_RAISE_EQUITY)
        elif action_round == "post_reveal" and action_name == "bet":
            threshold = OPPONENT_BET_EQUITY
            action_community = community
        elif action_round == "post_reveal" and action_name == "raise":
            threshold = OPPONENT_RAISE_EQUITY
            action_community = community
        elif action_round == "post_reveal" and action_name == "check":
            upper = OPPONENT_BET_EQUITY
            action_community = community
        else:
            continue

        narrowed = tuple(
            card for card in candidates
            if ((threshold is None
                 or evaluate_hand_strength(card, action_community, table_rule)
                 >= threshold)
                and (upper is None
                     or evaluate_hand_strength(card, action_community, table_rule)
                     < upper))
        )
        if narrowed:
            candidates = narrowed
    return candidates


def _range_equity(data: dict) -> float:
    your_card = data.get("your_number")
    community = data.get("community_number")
    if your_card not in CARDS:
        return 0.5
    communities = CARDS if community is None else (community,)
    if any(card not in CARDS for card in communities):
        return 0.5
    opponent_range = _opponent_range(data)
    equities = (
        _matchup_equity(your_card, opponent, card,
                        data.get("table_rule", "standard"))
        for card in communities
        for opponent in opponent_range
    )
    return sum(equities) / (len(communities) * len(opponent_range))


def _opponent_raised_pre_reveal(data: dict) -> bool:
    return any(
        action.get("round") == "pre_reveal"
        and _is_opponent_action(action, data)
        and action.get("action") == "raise"
        for action in _all_actions(data)
    )


def _opponent_aggressive(data: dict) -> bool:
    our_seat = str(data.get("your_seat"))
    return any(
        str(action.get("seat")) != our_seat
        and action.get("action") in ("bet", "raise")
        for action in _current_actions(data)
    )


def _opponent_checked(data: dict) -> bool:
    our_seat = str(data.get("your_seat"))
    return any(
        str(action.get("seat")) != our_seat and action.get("action") == "check"
        for action in _current_actions(data)
    )


def _current_contribution(data: dict) -> float:
    seat = data.get("your_seat")
    players = data.get("players", [])
    if isinstance(players, list):
        you = next((player for player in players
                    if isinstance(player, dict)
                    and (player.get("seat") == seat or player.get("name") == "you")), {})
        contribution = you.get("bet_this_round")
        if isinstance(contribution, (int, float)) and contribution >= 0:
            return contribution
    amounts = [
        action.get("amount") for action in _current_actions(data)
        if not _is_opponent_action(action, data)
        and isinstance(action.get("amount"), (int, float))
    ]
    return amounts[-1] if amounts else 0


def _wager_amount(data: dict, bounds: tuple[int, int]) -> int | None:
    blind = data.get("big_blind", 2)
    blind = blind if isinstance(blind, (int, float)) and blind > 0 else 2
    pot = data.get("pot", 0)
    pot = pot if isinstance(pot, (int, float)) and pot >= 0 else 0
    target = (round(2.5 * blind) if data.get("round") == "pre_reveal"
              else round(2 * pot / 3))
    limit = _mitigation_limit(data)
    if limit is not None:
        if limit <= 0:
            return None
        maximum = _current_contribution(data) + limit
        if bounds[0] > maximum:
            return None
        if _live_delta(data) >= PHASE_TWO_TARGET:
            target = bounds[0]
        else:
            target = min(target, maximum)
    return max(bounds[0], min(target, bounds[1]))


def _minimum_wager(data: dict, bounds: tuple[int, int]) -> int | None:
    limit = _mitigation_limit(data)
    if (limit is not None
            and bounds[0] > _current_contribution(data) + limit):
        return None
    return bounds[0]


def _move(data: dict) -> dict:
    legal = data.get("legal_actions") or []
    if _secured(data):
        for action in ("check", "fold", "call"):
            if action in legal:
                return {"action": action}

    equity = evaluate_hand_strength(
        data.get("your_number"), data.get("community_number"),
        data.get("table_rule", "standard"),
    )
    to_call, pot = data.get("to_call", 0), data.get("pot", 0)
    to_call = to_call if isinstance(to_call, (int, float)) and to_call > 0 else 0
    pot = pot if isinstance(pot, (int, float)) and pot >= 0 else 0
    blind = data.get("big_blind", 2)
    blind = blind if isinstance(blind, (int, float)) and blind > 0 else 2
    opponent_aggressive = _opponent_aggressive(data)
    bounds = _amount_bounds(data)

    if (equity >= OPEN_EQUITY and to_call <= blind and not opponent_aggressive
            and "raise" in legal and bounds):
        amount = _wager_amount(data, bounds)
        if amount is not None:
            return {"action": "raise", "amount": amount}

    if to_call:
        pot_odds = to_call / (pot + to_call)
        facing_wager = opponent_aggressive or to_call > blind
        limit = _mitigation_limit(data)
        if limit is not None and to_call > limit:
            if "fold" in legal:
                return {"action": "fold"}
        decision_equity = _range_equity(data) if facing_wager else equity
        if limit is None:
            required = max(pot_odds, CALL_EQUITY if facing_wager else 0)
            affordable = (decision_equity >= NUT_EQUITY
                          or to_call <= blind * MAX_CALL_BLINDS)
        else:
            required = (max(RANGE_CALL_FLOOR, pot_odds + RANGE_SAFETY_MARGIN)
                        if facing_wager else pot_odds)
            affordable = to_call <= limit
        if ("call" in legal and affordable
                and decision_equity >= required):
            return {"action": "call"}
        if "fold" in legal:
            return {"action": "fold"}

    if (data.get("round") == "post_reveal"
            and _opponent_raised_pre_reveal(data)
            and not any(_is_opponent_action(action, data)
                        for action in _current_actions(data))
            and "check" in legal):
        return {"action": "check"}

    if "bet" in legal and bounds:
        if _opponent_checked(data) and _range_equity(data) >= STEAL_EQUITY:
            amount = _minimum_wager(data, bounds)
            if amount is not None:
                return {"action": "bet", "amount": amount}
        if equity >= OPEN_EQUITY:
            amount = _wager_amount(data, bounds)
            if amount is not None:
                return {"action": "bet", "amount": amount}
    for action in ("check", "fold", "call"):
        if action in legal:
            return {"action": action}
    if bounds:
        for action in ("bet", "raise"):
            if action in legal:
                return {"action": action, "amount": bounds[0]}
    return {"action": "check"}


@app.route("/health", methods=["GET"])
def showdown_health():
    return jsonify(status="ok", showdown_strategy=STRATEGY_VERSION)


@app.route("/move", methods=["POST"])
def showdown():
    data = request.get_json(silent=True) or {}
    update_rule_knowledge(data.get("table_rule", "standard"), data.get("recent_hands", []))
    return jsonify(_move(data))
