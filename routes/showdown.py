import logging
from flask import request, jsonify
from routes import app


logger = logging.getLogger(__name__)


@app.route('/move', methods=['POST'])
def showdown():
    data = request.get_json(silent=True) or {}
    match_id: str = data.get("match_id")
    logging.info("match id: %s", match_id)

    # Extract key parameters
    your_card : int | None = data.get("your_number")
    community_card : int | None = data.get("community_number")
    to_call = data.get("to_call", 0)
    pot = data.get("pot", 0)
    legal_actions = data.get("legal_actions", [])
    min_raise = data.get("min_raise_to")
    max_raise = data.get("max_raise_to")

    # Calculate hand equity (0.0 to 1.0)
    win_prob = evaluate_hand_strength(your_card, community_card)

    # 1. RAISE: Strong equity edge (> 70% win chance)
    if win_prob > 0.70 and "raise" in legal_actions and min_raise is not None:
        # Size raise proportionally to equity edge, bounded by legal limits
        raise_size = int(min_raise + (max_raise - min_raise) * (win_prob - 0.70))
        raise_size = min(max(raise_size, min_raise), max_raise) # type: ignore
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


def evaluate_hand_strength(your_card: int | None, community_card: int | None) -> float:
    """
    Calculates your win probability based on remaining unknown cards (1-13).
    Assumes standard rule: higher combinations/pairs beat lower ones.
    """
    if your_card is None:
        return 0.5  # Default fallback

    # Pre-reveal stage: estimate win probability based strictly on standard card rank (1 to 13)
    if community_card is None:
        return (your_card - 1) / 12.0  # Card 13 = 100% relative strength, Card 1 = 0%

    # Post-reveal stage: calculate exact equity against remaining unknown cards
    known_cards = {your_card, community_card}
    deck = [card for card in range(1, 14) if card not in known_cards]

    wins = 0
    ties = 0
    your_score = (your_card == community_card, your_card)  # Pair priority, then high card

    for opp_card in deck:
        opp_score = (opp_card == community_card, opp_card)
        if your_score > opp_score:
            wins += 1
        elif your_score == opp_score:
            ties += 1

    return (wins + 0.5 * ties) / len(deck)
