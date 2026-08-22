"""Solver for the time-travelling stock challenge."""

from collections import defaultdict
import logging

from flask import jsonify, request

from routes import app


FINAL_YEAR = 2037
logger = logging.getLogger(__name__)


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

        # Sell lots at a local maximum before choosing the next investment.
        for name in list(holdings):
            if name not in quotes:
                continue
            current_price = int(quotes[name]["price"])
            later = future.get((year, name), (current_price, year))
            if year == FINAL_YEAR or current_price >= later[0]:
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
        # Exact ratio comparison avoids floating point errors for large prices.
        candidates.sort(key=lambda item: (item[0] / item[1], item[0]), reverse=True)

        for _, price, name, available in candidates:
            quantity = min(available, cash // price)
            if quantity <= 0:
                continue
            cash -= quantity * price
            holdings[name].append((quantity, price))
            actions_by_year[year].append(("b", name, quantity))
            logger.info(
                "stonks buy year=%s stock=%s quantity=%s price=%s cash=%s expected_sale=%s",
                year, name, quantity, price, cash, later_price,
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
