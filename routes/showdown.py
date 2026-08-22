import json
import logging
import os

from flask import jsonify, request
from routes import app

logger = logging.getLogger(__name__)
MEMORY_FILE = "table_rules_memory.json"


def load_memory() -> dict:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error loading rule memory: %s", e)
    return {}


def save_memory(data: dict):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Error saving rule memory: %s", e)


RULE_KNOWLEDGE_BASE = load_memory()


def update_rule_knowledge(table_rule: str, recent_hands: list):
    """Parses recent showdowns to memoize pairwise comparison facts."""
    if not table_rule or not recent_hands:
        return

    rule_data = RULE_KNOWLEDGE_BASE.setdefault(table_rule, {})
    updated = False

    for hand in recent_hands:
        shown = hand.get("shown_numbers", {})
        winners = hand.get("winners", [])
        comm = hand.get("community_number")

        if len(shown) == 2 and comm is not None:
            c0, c1 = shown.get("0"), shown.get("1")
            if c0 is None or c1 is None or c0 == c1:
                continue

            comm_key = str(comm)
            comm_data = rule_data.setdefault(comm_key, {})
            pair_key = f"{min(c0, c1)}_{max(c0, c1)}"

            if len(winners) > 1:
                winner_card = "tie"
            else:
                winner_seat = str(winners[0])
                winner_card = shown.get(winner_seat)

            if pair_key not in comm_data:
                comm_data[pair_key] = winner_card
                updated = True

    if updated:
        save_memory(RULE_KNOWLEDGE_BASE)


@app.route("/move", methods=["POST"])
def showdown():
    data = request.get_json(silent=True) or {}
    table_rule = data.get("table_rule", "standard")

    # [MODIFICATION 1] Wire the learning loop into the request cycle
    recent_hands = data.get("recent_hands", [])
    update_rule_knowledge(table_rule, recent_hands)

    your_card = data.get("your_number")
    community_card = data.get("community_number")
    to_call = data.get("to_call", 0)
    pot = data.get("pot", 0)
    legal_actions = data.get("legal_actions", [])
    min_raise = data.get("min_raise_to")
    max_raise = data.get("max_raise_to")

    win_prob = evaluate_hand_strength(your_card, community_card, table_rule)

    # [MODIFICATION 2] Value Betting Logic added for high-equity free actions
    if win_prob > 0.70 and "raise" in legal_actions and min_raise is not None:
        raise_size = int(min_raise + (max_raise - min_raise) * (win_prob - 0.70))
        raise_size = min(max(raise_size, min_raise), max_raise)
        return jsonify({"action": "raise", "amount": raise_size})

    elif win_prob > 0.55 and "bet" in legal_actions and to_call == 0 and min_raise is not None:
        return jsonify({"action": "bet", "amount": min_raise})

    pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0

    if "call" in legal_actions and win_prob >= pot_odds:
        return jsonify({"action": "call"})

    if "check" in legal_actions:
        return jsonify({"action": "check"})

    return jsonify({"action": "fold"})


def evaluate_hand_strength(
    your_card: int | None,
    community_card: int | None,
    table_rule: str,
) -> float:
    """Calculates equity using independent uniform distribution."""
    if your_card is None:
        return 0.5

    # [MODIFICATION 3] Fixed stochastic model: Cards are drawn with replacement.
    # We evaluate against all 13 possible opponent cards.
    deck = range(1, 14)

    if community_card is None:
        wins = your_card - 1
        ties = 1
        return (wins + ties * 0.5) / 13.0

    wins = 0
    ties = 0

    for opp_card in deck:
        res = compare_hands(your_card, opp_card, community_card, table_rule)
        if res > 0:
            wins += 1
        elif res == 0:
            ties += 0.5

    return (wins + ties) / 13.0


def compare_hands(c1: int, c2: int, comm: int, table_rule: str) -> int:
    if c1 == c2:
        return 0

    rule_data = RULE_KNOWLEDGE_BASE.get(table_rule, {})
    comm_data = rule_data.get(str(comm), {})
    pair_key = f"{min(c1, c2)}_{max(c1, c2)}"

    if pair_key in comm_data:
        winner = comm_data[pair_key]
        if winner == c1:
            return 1
        elif winner == c2:
            return -1
        elif winner == "tie":
            return 0

    score1 = (c1 == comm, c1)
    score2 = (c2 == comm, c2)
    if score1 > score2:
        return 1
    elif score1 < score2:
        return -1
    return 0
