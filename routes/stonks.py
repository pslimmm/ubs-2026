import heapq
import itertools
import logging
from dataclasses import dataclass

from flask import jsonify, request

from routes import app

HOME = 2037
MAX_SEARCH_STATES = 100_000
MAX_PROBE_STATES = 250
MAX_TRADE_STATES = 1_000
MAX_QUANTITY_CHOICES = 64


@dataclass(frozen=True)
class Lot:
    year: int
    stock: int
    price: int
    qty: int


@dataclass(frozen=True)
class Market:
    years: tuple
    stocks: tuple
    prices: dict
    lots: tuple
    lot_at: dict
    max_prices: tuple
    exact: bool


@dataclass(frozen=True)
class Label:
    year: int
    energy: int
    cash: int
    used: int
    holdings: tuple
    actions: tuple


@app.route("/stonks", methods=["POST"])
def stonks():
    batch = request.get_json(silent=True)
    if not isinstance(batch, list):
        return jsonify({"error": "Invalid request"}), 400

    try:
        return jsonify([solve_case(case) for case in batch])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Invalid request"}), 400


def solve_case(case):
    energy, capital, market = _parse(case)
    holdings = (0,) * len(market.stocks)
    start = Label(HOME, 0, capital, 0, holdings, ())
    incumbent = start
    frontier = {HOME: [start]}
    best = {_key(start): (capital, ())}
    queue = []
    counter = itertools.count()
    root_bound = _upper_bound(start, market)
    state_limit = _state_limit(market)
    heapq.heappush(queue, (-root_bound, next(counter), start))
    exact = True
    expanded = 0

    while queue and (state_limit is None or expanded < state_limit):
        neg_bound, _, label = heapq.heappop(queue)
        if -neg_bound <= incumbent.cash or best.get(_key(label)) != (
            label.cash,
            label.actions,
        ):
            continue
        expanded += 1

        trades, complete = _trade_options(label, market)
        exact &= complete
        for traded in trades:
            if traded.year == HOME and _better_terminal(traded, incumbent):
                incumbent = traded

            for year in market.years:
                if year == traded.year:
                    continue
                cost = abs(year - traded.year)
                used = traded.energy + cost
                if used + abs(HOME - year) > energy:
                    continue

                candidate = Label(
                    year,
                    used,
                    traded.cash,
                    traded.used,
                    traded.holdings,
                    traded.actions + (f"j-{traded.year}-{year}",),
                )
                bound = _upper_bound(candidate, market)
                year_frontier = frontier.setdefault(year, [])
                if bound <= incumbent.cash or _dominated(candidate, year_frontier):
                    continue

                key = _key(candidate)
                previous = best.get(key)
                value = (candidate.cash, candidate.actions)
                if previous and (
                    previous[0] > candidate.cash
                    or (previous[0] == candidate.cash and previous[1] <= candidate.actions)
                ):
                    continue

                best[key] = value
                year_frontier.append(candidate)
                heapq.heappush(queue, (-bound, next(counter), candidate))

    exact &= not queue
    gap = max(0, root_bound - incumbent.cash)
    logging.info(
        "stonks capital=%d final=%d states=%d exact=%s upper_gap=%d",
        capital,
        incumbent.cash,
        expanded,
        exact,
        0 if exact else gap,
    )
    return list(incumbent.actions)


def _parse(case):
    if not isinstance(case, dict):
        raise ValueError
    energy = _positive_int(case["energy"])
    capital = _positive_int(case["capital"])
    if energy <= 1 or not isinstance(case["timeline"], dict):
        raise ValueError

    timeline = {}
    stock_names = set()
    for raw_year, entries in case["timeline"].items():
        year = int(raw_year)
        if not 0 < year <= HOME or not isinstance(entries, dict):
            raise ValueError
        timeline[year] = {}
        for name, quote in entries.items():
            if (
                not isinstance(name, str)
                or not name
                or "-" in name
                or not isinstance(quote, dict)
            ):
                raise ValueError
            price = _positive_int(quote["price"])
            qty = quote["qty"]
            if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
                raise ValueError
            timeline[year][name] = (price, qty)
            stock_names.add(name)

    stocks = tuple(sorted(stock_names))
    stock_ids = {name: index for index, name in enumerate(stocks)}
    prices = {
        year: {stock_ids[name]: quote[0] for name, quote in entries.items()}
        for year, entries in timeline.items()
    }
    prices.setdefault(HOME, {})
    years = tuple(
        year
        for year in sorted(prices)
        if year == HOME
        or prices[year] and 2 * abs(HOME - year) <= energy
    )
    lots = []
    lot_at = {}
    for year in years:
        for name, (price, qty) in sorted(timeline.get(year, {}).items()):
            if qty:
                lot_at[(year, stock_ids[name])] = len(lots)
                lots.append(Lot(year, stock_ids[name], price, qty))

    max_prices = tuple(
        max((prices[year].get(stock, 0) for year in years), default=0)
        for stock in range(len(stocks))
    )
    exact = _exact_size(energy, years, lots, len(stocks)) <= MAX_SEARCH_STATES
    return energy, capital, Market(
        years, stocks, prices, tuple(lots), lot_at, max_prices, exact
    )


def _positive_int(value):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError
    return value


def _state_limit(market):
    return None if market.exact else MAX_PROBE_STATES


def _exact_size(energy, years, lots, stock_count):
    totals = [0] * stock_count
    for lot in lots:
        totals[lot.stock] += lot.qty
    size = (energy + 1) * len(years) * (1 << len(lots))
    for qty in totals:
        size *= qty + 1
        if size > MAX_SEARCH_STATES:
            break
    return size


def _trade_options(label, market):
    prices = market.prices[label.year]
    original = label.holdings
    states = {(label.used, original): (label.cash, label.actions)}
    exact = True

    # Sell first: at one timestamp all sale proceeds are available for purchases.
    for stock in sorted(prices):
        held = original[stock]
        if not held:
            continue
        choices, complete = _quantities(held, market.exact)
        exact &= complete
        updated = {}
        for (used, holdings), (cash, actions) in states.items():
            for qty in choices:
                values = list(holdings)
                values[stock] -= qty
                next_actions = actions
                if qty:
                    next_actions += (f"s-{market.stocks[stock]}-{qty}",)
                _keep(updated, used, tuple(values), cash + qty * prices[stock], next_actions)
        states, complete = _trim(updated, market)
        exact &= complete

    # Buy after every possible sale portfolio has been generated.
    for stock in sorted(prices):
        lot_id = market.lot_at.get((label.year, stock))
        if lot_id is None or label.used & (1 << lot_id):
            continue
        price = prices[stock]
        if price >= market.max_prices[stock]:
            continue
        lot = market.lots[lot_id]
        updated = {}
        for (used, holdings), (cash, actions) in states.items():
            # Selling and rebuying the same fungible stock at one price is dominated.
            sold_here = holdings[stock] < original[stock]
            maximum = 0 if sold_here else min(lot.qty, cash // price)
            choices, complete = _quantities(maximum, market.exact)
            exact &= complete
            for qty in choices:
                values = list(holdings)
                values[stock] += qty
                next_used = used | ((1 << lot_id) if qty else 0)
                next_actions = actions
                if qty:
                    next_actions += (f"b-{market.stocks[stock]}-{qty}",)
                _keep(
                    updated,
                    next_used,
                    tuple(values),
                    cash - qty * price,
                    next_actions,
                )
        states, complete = _trim(updated, market)
        exact &= complete

    labels = [
        Label(label.year, label.energy, cash, used, holdings, actions)
        for (used, holdings), (cash, actions) in states.items()
    ]
    labels.sort(key=lambda item: (-item.cash, item.used, item.holdings, item.actions))
    return labels, exact


def _quantities(limit, exact):
    if exact or limit <= MAX_QUANTITY_CHOICES:
        return range(limit + 1), True
    values = {
        0,
        1,
        2,
        4,
        limit // 4,
        limit // 2,
        3 * limit // 4,
        limit - 4,
        limit - 2,
        limit - 1,
        limit,
    }
    return tuple(sorted(value for value in values if 0 <= value <= limit)), False


def _keep(states, used, holdings, cash, actions):
    key = (used, holdings)
    current = states.get(key)
    if current is None or cash > current[0] or (
        cash == current[0] and actions < current[1]
    ):
        states[key] = (cash, actions)


def _trim(states, market):
    if market.exact or len(states) <= MAX_TRADE_STATES:
        return states, True
    ranked = sorted(
        states.items(),
        key=lambda item: (
            item[1][0]
            + sum(
                qty * market.max_prices[stock]
                for stock, qty in enumerate(item[0][1])
            ),
            item[1][0],
        ),
        reverse=True,
    )[:MAX_TRADE_STATES]
    return dict(ranked), False


def _upper_bound(label, market):
    value = label.cash + sum(
        qty * market.max_prices[stock]
        for stock, qty in enumerate(label.holdings)
    )
    for lot_id, lot in enumerate(market.lots):
        if not label.used & (1 << lot_id):
            value += lot.qty * max(0, market.max_prices[lot.stock] - lot.price)
    return value


def _key(label):
    return label.energy, label.year, label.used, label.holdings


def _dominated(candidate, labels):
    for other in labels:
        if (
            other.energy <= candidate.energy
            and other.cash >= candidate.cash
            and other.used & ~candidate.used == 0
            and all(a >= b for a, b in zip(other.holdings, candidate.holdings))
        ):
            return True
    return False


def _better_terminal(candidate, incumbent):
    return (
        candidate.cash > incumbent.cash
        or candidate.cash == incumbent.cash
        and (candidate.energy, candidate.actions)
        < (incumbent.energy, incumbent.actions)
    )
