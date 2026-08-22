import json
import logging
import os

from flask import jsonify, request

from routes import app

logger = logging.getLogger(__name__)
MEMORY_FILE = "table_rules_memory.json"


# MEMORY
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
    """Parses recent showdowns to record pairwise comparison facts for this table_rule."""
    if not table_rule or not recent_hands:
        return

    rule_data = RULE_KNOWLEDGE_BASE.setdefault(table_rule, {})
    updated = False

    for hand in recent_hands:
        shown = hand.get("shown_numbers", {})
        winners = hand.get("winners", [])
        comm = hand.get("community_number")

        # We need a showdown where both cards are visible and a single winner is decided
        if len(shown) == 2 and comm is not None:
            c0, c1 = shown.get("0"), shown.get("1")
            if c0 is None or c1 is None or c0 == c1:
                continue

            comm_key = str(comm)
            comm_data = rule_data.setdefault(comm_key, {})

            # Pair key normalized by card order
            pair_key = f"{min(c0, c1)}_{max(c0, c1)}"

            # Determine who won
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


# MAIN ENDPOINT
@app.route("/move", methods=["POST"])
def showdown():
    data = request.get_json(silent=True) or {}
    match_id = data.get("match_id")  # type: ignore
    logging.info("match id: %s\n", match_id)
    table_rule = data.get("table_rule")  # type: ignore
    logging.info("table_rule: %s\n", table_rule)

    # Extract key parameters
    your_card: int | None = data.get("your_number")
    community_card: int | None = data.get("community_number")
    to_call = data.get("to_call", 0)
    pot = data.get("pot", 0)
    legal_actions = data.get("legal_actions", [])
    min_raise = data.get("min_raise_to")
    max_raise = data.get("max_raise_to")

    # Calculate hand equity (0.0 to 1.0)
    win_prob = evaluate_hand_strength(your_card, community_card, table_rule)

    # 1. RAISE: Strong equity edge (> 70% win chance)
    if win_prob > 0.70 and "raise" in legal_actions and min_raise is not None:
        # Size raise proportionally to equity edge, bounded by legal limits
        raise_size = int(min_raise + (max_raise - min_raise) * (win_prob - 0.70))
        raise_size = min(max(raise_size, min_raise), max_raise)  # type: ignore
        return jsonify({"action": "raise", "amount": raise_size})

    # 2. CALL: Positive Pot Odds calculation (EV > 0)
    # Pot Odds threshold = to_call / (pot + to_call)
    pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0
    if "call" in legal_actions and win_prob >= pot_odds:
        return jsonify({"action": "call"})

    # 3. CHECK: Free action if available
    if "check" in legal_actions or to_call == 0:
        return jsonify({"action": "check"})

    # 4. FOLD: Poor equity and unfavorable pot odds
    return jsonify({"action": "fold"})


def evaluate_hand_strength(
    your_card: int | None,
    community_card: int | None,
    table_rule: str,
) -> float:
    """Calculates win probability against all remaining unknown cards in the deck."""
    if your_card is None:
        return 0.5

    # Pre-reveal fallback before community card is shown
    if community_card is None:
        return (your_card - 1) / 12.0

    known_cards = {your_card, community_card}
    deck = [card for card in range(1, 14) if card not in known_cards]

    wins = 0
    ties = 0

    for opp_card in deck:
        res = compare_hands(your_card, opp_card, community_card, table_rule)
        if res > 0:
            wins += 1
        elif res == 0:
            ties += 0.5

    return (wins + ties) / len(deck)


def compare_hands(c1: int, c2: int, comm: int, table_rule: str) -> int:
    """
    Compares card c1 vs c2 given community card.
    Returns: 1 if c1 wins, -1 if c2 wins, 0 if tie/unknown.
    """
    if c1 == c2:
        return 0

    rule_data = RULE_KNOWLEDGE_BASE.get(table_rule, {})
    comm_data = rule_data.get(str(comm), {})
    pair_key = f"{min(c1, c2)}_{max(c1, c2)}"

    # Check directly learned outcome
    if pair_key in comm_data:
        winner = comm_data[pair_key]
        if winner == c1:
            return 1
        elif winner == c2:
            return -1
        elif winner == "tie":
            return 0

    # Fallback heuristic (Standard rank) if pair hasn't been observed yet
    score1 = (c1 == comm, c1)
    score2 = (c2 == comm, c2)
    if score1 > score2:
        return 1
    elif score1 < score2:
        return -1
    return 0
