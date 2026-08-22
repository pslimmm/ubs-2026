import json
import logging
import os
import sys
import traceback
from flask import Flask, jsonify, request

# Configure clear stdout logging for Cloud logs / ngrok terminals
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
MEMORY_FILE = "rule_hypotheses.json"

# Rule evaluator functions
def score_standard(c1, c2, comm):
    p1, p2 = (c1 == comm), (c2 == comm)
    return 1 if (p1, c1) > (p2, c2) else (-1 if (p1, c1) < (p2, c2) else 0)

def score_lowball(c1, c2, comm):
    p1, p2 = (c1 == comm), (c2 == comm)
    return 1 if (p1, -c1) > (p2, -c2) else (-1 if (p1, -c1) < (p2, -c2) else 0)

def score_closest(c1, c2, comm):
    d1, d2 = abs(c1 - comm), abs(c2 - comm)
    return 1 if d1 < d2 else (-1 if d1 > d2 else 0)

def score_furthest(c1, c2, comm):
    d1, d2 = abs(c1 - comm), abs(c2 - comm)
    return 1 if d1 > d2 else (-1 if d1 > d2 else 0)

def score_odd_even(c1, c2, comm):
    k1 = (c1 % 2 != 0, c1 == comm, c1)
    k2 = (c2 % 2 != 0, c2 == comm, c2)
    return 1 if k1 > k2 else (-1 if k1 < k2 else 0)

RULE_CANDIDATES = {
    "standard": score_standard,
    "lowball": score_lowball,
    "closest": score_closest,
    "furthest": score_furthest,
    "odd_even": score_odd_even
}

def get_rule_scores(table_rule: str) -> dict:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                data = json.load(f)
                if table_rule in data:
                    return data[table_rule]
        except Exception as e:
            logger.error(f"Failed to read memory file: {e}")
    return {name: 1.0 for name in RULE_CANDIDATES}

def update_hypotheses(table_rule: str, recent_hands: list):
    if not recent_hands:
        return

    scores = get_rule_scores(table_rule)
    updated = False

    for hand in recent_hands:
        shown = hand.get("shown_numbers", {})
        winners = hand.get("winners", [])
        comm = hand.get("community_number")

        if len(shown) == 2 and comm is not None:
            c0, c1 = shown.get("0"), shown.get("1")
            if c0 is None or c1 is None or c0 == c1:
                continue

            actual_res = 0 if len(winners) > 1 else (1 if winners[0] == 0 else -1)

            for name, fn in RULE_CANDIDATES.items():
                if scores[name] > 0:
                    pred = fn(c0, c1, comm)
                    if pred != actual_res:
                        logger.info(f"Rule rule '{table_rule}': Eliminating hypothesis '{name}' based on hand outcome.")
                        scores[name] = 0.0
                        updated = True

    if updated:
        try:
            all_data = {}
            if os.path.exists(MEMORY_FILE):
                with open(MEMORY_FILE, "r") as f:
                    all_data = json.load(f)
            all_data[table_rule] = scores
            with open(MEMORY_FILE, "w") as f:
                json.dump(all_data, f, indent=2)
            logger.info(f"Updated rule memory saved for rule: {table_rule}")
        except Exception as e:
            logger.error(f"Failed to write memory file: {e}")

def evaluate_strength(your_card: int, comm: int | None, table_rule: str) -> tuple[float, bool]:
    scores = get_rule_scores(table_rule)
    active_rules = [name for name, score in scores.items() if score > 0]
    is_confident = len(active_rules) == 1

    if comm is None:
        return (your_card - 1 + 0.5) / 13.0, is_confident

    total_wins = 0
    total_evals = 0

    for rule_name in active_rules:
        fn = RULE_CANDIDATES[rule_name]
        for opp_card in range(1, 14):
            res = fn(your_card, opp_card, comm)
            total_wins += 1.0 if res > 0 else (0.5 if res == 0 else 0.0)
            total_evals += 1

    win_prob = (total_wins / total_evals) if total_evals > 0 else 0.5
    return win_prob, is_confident

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/move", methods=["POST"])
def showdown():
    data = request.get_json(silent=True) or {}
    legal_actions = data.get("legal_actions", ["check", "fold"])

    try:
        # Extract variables with logging
        hand_num = data.get("hand_number")
        leg_num = data.get("leg_number", 1)
        round_phase = data.get("round")
        table_rule = data.get("table_rule", "standard")
        your_card = data.get("your_number")
        comm_card = data.get("community_number")
        to_call = data.get("to_call", 0)
        pot = data.get("pot", 0)
        min_raise = data.get("min_raise_to")
        max_raise = data.get("max_raise_to")

        logger.info(f"--- Leg {leg_num} | Hand #{hand_num} ({round_phase}) | Rule: '{table_rule}' ---")
        logger.info(f"Your Card: {your_card} | Comm Card: {comm_card} | Pot: {pot} | To Call: {to_call}")

        # Update knowledge base
        update_hypotheses(table_rule, data.get("recent_hands", []))

        # Evaluate hand equity
        win_prob, is_confident = evaluate_strength(your_card, comm_card, table_rule)
        logger.info(f"Win Prob: {win_prob:.2f} | Confident in Rule: {is_confident}")

        # Risk Management: Reduce aggressiveness pre-reveal if rule unknown
        if not is_confident and comm_card is None:
            win_prob = min(win_prob, 0.5)

        can_raise = "raise" in legal_actions and min_raise is not None and max_raise is not None
        can_bet = "bet" in legal_actions and min_raise is not None and max_raise is not None

        response = {}

        # 1. High Equity: Value Raise / Bet
        if win_prob > 0.70 and (can_raise or can_bet):
            action = "raise" if can_raise else "bet"
            fraction = (win_prob - 0.70) / 0.30
            size = int(min_raise + (max_raise - min_raise) * fraction)
            size = min(max(size, min_raise), max_raise)
            response = {"action": action, "amount": size}

        # 2. Moderate Equity: Open bet
        elif win_prob > 0.55 and can_bet:
            response = {"action": "bet", "amount": min_raise}

        # 3. Pot-Odds Call
        elif "call" in legal_actions:
            pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 else 0
            if win_prob >= pot_odds:
                response = {"action": "call"}

        # 4. Fallbacks
        if not response:
            if "check" in legal_actions:
                response = {"action": "check"}
            else:
                response = {"action": "fold"}

        logger.info(f"Chosen Action: {response}")
        return jsonify(response)

    except Exception as e:
        # Crash prevention: Return a safe legal move to avoid timeouts or forfeits
        logger.error(f"Unhandled exception in /move: {e}")
        logger.error(traceback.format_exc())
        fallback_action = "check" if "check" in legal_actions else "fold"
        logger.warning(f"Returning safe fallback action: {fallback_action}")
        return jsonify({"action": fallback_action})
