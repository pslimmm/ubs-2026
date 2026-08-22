import logging
import sys

from flask import Flask, jsonify, request

from routes import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def compute_pessimistic_equity(
    your_card: int, comm_card: int | None, recent_hands: list
) -> float:
    """Dynamically builds card dominance graph from recent_hands and calculates equity on-the-fly."""
    # Pre-reveal fallback (standard card value baseline)
    if comm_card is None or your_card is None:
        return (your_card - 1 + 0.5) / 13.0 if your_card else 0.5

    # 1. Initialize 14x14 adjacency matrix (cards 1 to 13)
    matrix = [[False] * 14 for _ in range(14)]

    # 2. Extract direct showdown outcomes for THIS community card
    for hand in recent_hands:
        shown = hand.get("shown_numbers", {})
        winners = hand.get("winners", [])
        hand_comm = hand.get("community_number")

        # Only process hands that reached showdown with the matching community card
        if hand_comm == comm_card and len(shown) == 2 and len(winners) == 1:
            c0, c1 = shown.get("0"), shown.get("1")
            if c0 is not None and c1 is not None and c0 != c1:
                winner_seat = str(winners[0])
                winner_card = shown.get(winner_seat)
                loser_card = c1 if winner_card == c0 else c0
                if winner_card and loser_card:
                    matrix[winner_card][loser_card] = True

    # 3. Transitive Closure (Floyd-Warshall over 13 cards)
    for k in range(1, 14):
        for i in range(1, 14):
            for j in range(1, 14):
                if matrix[i][k] and matrix[k][j]:
                    matrix[i][j] = True

    # 4. Count proven wins for your card
    must_wins = sum(
        1 for opp in range(1, 14) if opp != your_card and matrix[your_card][opp]
    )

    # Lower-bound equity (treats unobserved comparisons as non-wins)
    return (must_wins + 0.5) / 13.0


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/move", methods=["POST"])
def showdown():
    data = request.get_json(silent=True) or {}
    legal_actions = data.get("legal_actions", ["check", "fold"])

    try:
        your_card = data.get("your_number")
        comm_card = data.get("community_number")
        to_call = data.get("to_call", 0)
        pot = data.get("pot", 0)
        min_raise = data.get("min_raise_to")
        max_raise = data.get("max_raise_to")
        recent_hands = data.get("recent_hands", [])

        # Extract current score delta to protect leads
        me = next((p for p in data.get("players", []) if p.get("name") == "you"), {})
        chip_delta = me.get("chip_delta", 0)

        # Calculate equity strictly using the current payload's recent_hands
        pessimistic_equity = compute_pessimistic_equity(
            your_card, comm_card, recent_hands
        )

        can_raise = (
            "raise" in legal_actions and min_raise is not None and max_raise is not None
        )
        can_bet = (
            "bet" in legal_actions and min_raise is not None and max_raise is not None
        )

        # 1. SCORE PROTECTION: Lock in points once chip_delta >= 25 (+100pt leg threshold)
        if chip_delta >= 25:
            if "check" in legal_actions:
                return jsonify({"action": "check"})
            if "call" in legal_actions and to_call <= 2:
                return jsonify({"action": "call"})
            return jsonify({"action": "fold"})

        # 2. PRE-REVEAL FILTER: Fold low cards to large pre-reveal raises
        if comm_card is None and to_call > 4 and your_card < 8:
            return jsonify({"action": "fold"})

        # 3. VALUE BETTING / RAISING: Capitalize on strong equity
        if pessimistic_equity > 0.65 and (can_raise or can_bet):
            action = "raise" if can_raise else "bet"
            fraction = (pessimistic_equity - 0.65) / 0.35
            size = int(min_raise + (max_raise - min_raise) * fraction)
            return jsonify(
                {"action": action, "amount": min(max(size, min_raise), max_raise)}
            )

        # 4. HIGH-BET DEFENSE: Prevent calling station losses
        if "call" in legal_actions:
            pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0

            # Require 60%+ proven equity to call big bets (>5 chips)
            if to_call > 5:
                if pessimistic_equity >= 0.60:
                    return jsonify({"action": "call"})
            else:
                # Standard pot-odds call for small bets
                if pessimistic_equity >= pot_odds:
                    return jsonify({"action": "call"})

        # 5. SAFE FALLBACKS
        if "check" in legal_actions:
            return jsonify({"action": "check"})

        return jsonify({"action": "fold"})

    except Exception as e:
        logger.error(f"Fallback triggered by error: {e}")
        return jsonify({"action": "check" if "check" in legal_actions else "fold"})
