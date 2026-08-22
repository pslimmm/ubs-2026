"""Solver for the time-travelling stock challenge."""

from collections import defaultdict
import logging

from flask import jsonify, request

from routes import app


FINAL_YEAR = 2037
logger = logging.getLogger(__name__)


def _best_purchases(cash, candidates):
    """Choose the bounded set of purchases with the highest eventual value."""
    if cash <= 0 or not candidates:
        return cash, []

    dp = [-1] * (cash + 1)
    dp[0] = 0
    parents = []
    for sale_price, buy_price, name, available in candidates:
        next_dp = dp[:]
        parent = {}
        max_quantity = min(int(available), cash // buy_price)
        for spent, proceeds in enumerate(dp):
            if proceeds < 0:
                continue
            for quantity in range(1, max_quantity + 1):
                new_spent = spent + quantity * buy_price
                if new_spent > cash:
                    break
                new_proceeds = proceeds + quantity * sale_price
                if new_proceeds > next_dp[new_spent]:
                    next_dp[new_spent] = new_proceeds
                    parent[new_spent] = (spent, quantity)
        dp = next_dp
        parents.append(parent)

    best_spent = max(range(cash + 1), key=lambda spent: dp[spent] + cash - spent)
    best_value = dp[best_spent] + cash - best_spent
    purchases = []
    spent = best_spent
    for index in range(len(candidates) - 1, -1, -1):
        previous = parents[index].get(spent)
        if previous is None:
            continue
        old_spent, quantity = previous
        sale_price, buy_price, name, _ = candidates[index]
        purchases.append((name, buy_price, quantity, sale_price))
        spent = old_spent
    return best_value, list(reversed(purchases))


def _future_maxima(timeline, years):
    result = {}
    best = {}
    for year in reversed(years):
        for name, quote in timeline.get(str(year), {}).items():
            price = int(quote["price"])
            later = best.get(name)
            result[(year, name)] = later if later is not None and later[0] >= price else (price, year)
        for name, quote in timeline.get(str(year), {}).items():
            price = int(quote["price"])
            if name not in best or price > best[name][0]:
                best[name] = (price, year)
    return result


def solve_case(case):
    energy = int(case["energy"])
    cash = int(case["capital"])
    timeline = case["timeline"]

    # A round trip to a year d years in the past costs 2d energy.
    oldest = max(0, FINAL_YEAR - energy // 2)
    years = sorted(
        (int(year) for year in timeline if oldest <= int(year) <= FINAL_YEAR),
        key=int,
    )
    if FINAL_YEAR not in years:
        years.append(FINAL_YEAR)
        years.sort()

    logger.info(
        "stonks case energy=%s starting_capital=%s reachable_years=%s",
        energy,
        cash,
        years,
    )

    future = _future_maxima(timeline, years)
    holdings = defaultdict(list)  # name -> [(quantity, purchase price)]
    actions_by_year = defaultdict(list)

    for year in years:
        quotes = timeline.get(str(year), {})

        # Rebalance at every year where the market quotes the held stock.
        # Keeping a position merely because it may rise later can strand
        # capital while a different stock has a better reachable return.
        for name in list(holdings):
            if name not in quotes:
                continue
            current_price = int(quotes[name]["price"])
            quantity = sum(qty for qty, _ in holdings.pop(name))
            cash += quantity * current_price
            actions_by_year[year].append(("s", name, quantity))
            logger.info(
                "stonks sell year=%s stock=%s quantity=%s price=%s cash=%s",
                year, name, quantity, current_price, cash,
            )

        candidates = []
        for name, quote in quotes.items():
            price = int(quote["price"])
            quantity = int(quote["qty"])
            later_price, later_year = future.get((year, name), (price, year))
            if quantity > 0 and later_year > year and later_price > price:
                candidates.append((later_price, price, name, quantity))
        optimized_cash, purchases = _best_purchases(cash, candidates)
        for name, price, quantity, sale_price in purchases:
            cash -= quantity * price
            holdings[name].append((quantity, price))
            actions_by_year[year].append(("b", name, quantity))
            logger.info(
                "stonks buy year=%s stock=%s quantity=%s price=%s cash=%s expected_sale=%s",
                year, name, quantity, price, cash, sale_price,
            )
        if purchases:
            logger.info(
                "stonks allocation year=%s after_purchase_cash=%s projected_value=%s",
                year, cash, optimized_cash,
            )

    action_years = sorted(actions_by_year)
    actions = []
    current_year = FINAL_YEAR
    for year in action_years:
        if year != current_year:
            actions.append(f"j-{current_year}-{year}")
            current_year = year
        for action, name, quantity in actions_by_year[year]:
            actions.append(f"{action}-{name}-{quantity}")
    if current_year != FINAL_YEAR:
        actions.append(f"j-{current_year}-{FINAL_YEAR}")
    logger.info(
        "stonks case complete actions=%s ending_cash=%s response=%s",
        len(actions), cash, actions,
    )
    return actions


@app.route("/stonks", methods=["POST"])
def stonks():
    data = request.get_json(silent=True)
    logger.info(
        "stonks request remote=%s content_length=%s cases=%s",
        request.remote_addr,
        request.content_length,
        len(data) if isinstance(data, list) else None,
    )
    if not isinstance(data, list):
        logger.warning("stonks invalid request: body must be an array")
        return jsonify({"error": "request body must be an array"}), 400
    try:
        result = [solve_case(case) for case in data]
        logger.info("stonks response cases=%s", len(result))
        return jsonify(result)
    except (KeyError, TypeError, ValueError, AttributeError):
        logger.exception("stonks invalid request payload")
        return jsonify({"error": "invalid stonks request"}), 400
