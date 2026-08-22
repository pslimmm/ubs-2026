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


def compute_multiway_equity(
    your_card: int | None, comm_card: int | None, recent_hands: list, num_opponents: int
) -> float:
    """Calculates win probability against N active opponents dynamically from recent_hands."""
    if your_card is None or num_opponents <= 0:
        return 0.5

    # Pre-reveal: calculate probability of beating one random card raised to N opponents
    if comm_card is None:
        p_single = (your_card - 1 + 0.5) / 13.0
        return p_single**num_opponents

    # 1. Build transitive card dominance matrix (13x13)
    matrix = [[False] * 14 for _ in range(14)]

    for hand in recent_hands:
        hand_comm = hand.get("community_number")
        shown = hand.get("shown_numbers", {})
        winners = [str(w) for w in hand.get("winners", [])]

        # Multiway showdown processing
        if hand_comm == comm_card and len(shown) >= 2 and winners:
            winning_cards = {
                shown[s] for s in winners if s in shown and shown[s] is not None
            }
            losing_cards = {
                shown[s] for s in shown if s not in winners and shown[s] is not None
            }

            for w_card in winning_cards:
                for l_card in losing_cards:
                    if w_card != l_card:
                        matrix[w_card][l_card] = True

    # 2. Transitive Closure (Floyd-Warshall over cards 1 to 13)
    for k in range(1, 14):
        for i in range(1, 14):
            for j in range(1, 14):
                if matrix[i][k] and matrix[k][j]:
                    matrix[i][j] = True

    # 3. Compute probability against 1 opponent, then scale exponentially for N opponents
    must_wins = sum(
        1 for opp in range(1, 14) if opp != your_card and matrix[your_card][opp]
    )
    p_single = (must_wins + 0.5) / 13.0

    return p_single**num_opponents


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
        players = data.get("players", [])

        # 1. Filter active live opponents (not folded, not busted, not self)
        active_opponents = [
            p
            for p in players
            if p.get("name") != "you"
            and not p.get("folded", False)
            and not p.get("busted", False)
        ]
        num_opps = max(len(active_opponents), 1)

        # 2. Score & Standings Evaluation
        me = next((p for p in players if p.get("name") == "you"), {})
        my_delta = me.get("chip_delta", 0)
        opp_deltas = [p.get("chip_delta", 0) for p in players if p.get("name") != "you"]
        max_opp_delta = max(opp_deltas) if opp_deltas else -200

        # Victory condition check: strictly top the table and chip_delta >= +10
        is_topping_table = (my_delta >= 10) and (my_delta > max_opp_delta)

        # 3. Compute Equity scaled for N opponents
        equity = compute_multiway_equity(your_card, comm_card, recent_hands, num_opps)

        # Relative Equity Multiplier vs Fair Share (Fair Share = 1 / (N + 1))
        fair_share = 1.0 / (num_opps + 1)
        equity_ratio = equity / fair_share if fair_share > 0 else 1.0

        can_raise = (
            "raise" in legal_actions and min_raise is not None and max_raise is not None
        )
        can_bet = (
            "bet" in legal_actions and min_raise is not None and max_raise is not None
        )

        # --- DECISION TREE ---

        # RULE A: TOP-THE-TABLE LOCKDOWN
        # If currently meeting the win condition, minimize risk and protect chips
        if is_topping_table:
            if "check" in legal_actions:
                return jsonify({"action": "check"})
            if "call" in legal_actions and to_call <= 2:
                return jsonify({"action": "call"})
            return jsonify({"action": "fold"})

        # RULE B: PRE-REVEAL MULTIWAY TIGHTENING
        # In multiway pots (3+ active opponents), fold low cards facing pre-reveal raises
        if comm_card is None and num_opps >= 2 and to_call > 2 and your_card < 9:
            return jsonify({"action": "fold"})

        # RULE C: VALUE BETTING & RAISING
        # Require holding at least 1.4x fair-share equity to open/raise multiway
        if equity_ratio >= 1.4 and (can_raise or can_bet):
            action = "raise" if can_raise else "bet"
            fraction = min((equity_ratio - 1.4) / 1.0, 1.0)
            size = int(min_raise + (max_raise - min_raise) * fraction)
            return jsonify(
                {"action": action, "amount": min(max(size, min_raise), max_raise)}
            )

        # RULE D: POT-ODDS & MULTIWAY CALLING DEFENSE
        if "call" in legal_actions:
            pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0

            # Require equity advantage when facing bets larger than small blind (2 chips)
            if to_call > 2:
                if equity >= pot_odds and equity_ratio >= 1.1:
                    return jsonify({"action": "call"})
            else:
                if equity >= pot_odds:
                    return jsonify({"action": "call"})

        # RULE E: DEFAULT SAFE FALLBACK
        if "check" in legal_actions:
            return jsonify({"action": "check"})

        return jsonify({"action": "fold"})

    except Exception as e:
        logger.error(f"Fallback triggered by error: {e}")
        return jsonify({"action": "check" if "check" in legal_actions else "fold"})
