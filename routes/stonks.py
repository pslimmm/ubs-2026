import heapq
import itertools
import logging
import math
from array import array
from collections import deque
from dataclasses import dataclass
from fractions import Fraction

from flask import jsonify, request

from routes import app

HOME = 2037
MAX_SEARCH_STATES = 100_000
MAX_PROBE_STATES = 250
MAX_KNAPSACK_CAPACITY = 50_000
MAX_KNAPSACK_CELLS = 1_000_000
MAX_KNAPSACK_REQUEST_CELLS = 2_000_000
MAX_PROBE_CHOICES = 8


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
    knapsack_cells = [MAX_KNAPSACK_REQUEST_CELLS]
    heapq.heappush(queue, (_priority(start, market), -root_bound, next(counter), start))
    exact = True
    expanded = 0

    while queue and (state_limit is None or expanded < state_limit):
        _, neg_bound, _, label = heapq.heappop(queue)
        if -neg_bound <= incumbent.cash or best.get(_key(label)) != (
            label.cash,
            label.actions,
        ):
            continue
        expanded += 1

        trades, complete = _trade_options(label, market, knapsack_cells)
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
                heapq.heappush(
                    queue,
                    (_priority(candidate, market), -bound, next(counter), candidate),
                )

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


def _trade_options(label, market, knapsack_cells):
    if not market.exact:
        return _probe_trade_options(label, market, knapsack_cells), False

    prices = market.prices[label.year]
    original = label.holdings
    states = {(label.used, original): (label.cash, label.actions)}

    # Sell first: at one timestamp all sale proceeds are available for purchases.
    for stock in sorted(prices):
        held = original[stock]
        if not held:
            continue
        updated = {}
        for (used, holdings), (cash, actions) in states.items():
            for qty in range(held + 1):
                values = list(holdings)
                values[stock] -= qty
                next_actions = actions
                if qty:
                    next_actions += (f"s-{market.stocks[stock]}-{qty}",)
                _keep(updated, used, tuple(values), cash + qty * prices[stock], next_actions)
        states = updated

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
            for qty in range(maximum + 1):
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
        states = updated

    labels = [
        Label(label.year, label.energy, cash, used, holdings, actions)
        for (used, holdings), (cash, actions) in states.items()
    ]
    labels.sort(key=lambda item: (-item.cash, item.used, item.holdings, item.actions))
    return labels, True


def _keep(states, used, holdings, cash, actions):
    key = (used, holdings)
    current = states.get(key)
    if current is None or cash > current[0] or (
        cash == current[0] and actions < current[1]
    ):
        states[key] = (cash, actions)


def _probe_trade_options(label, market, knapsack_cells):
    original = label.holdings
    prices = market.prices[label.year]
    held = [stock for stock in sorted(prices) if original[stock]]
    sale_sets = [(), tuple(held)]
    ranked_held = sorted(
        held,
        key=lambda stock: original[stock]
        * (market.max_prices[stock] - prices[stock]),
        reverse=True,
    )[:MAX_PROBE_CHOICES]
    sale_sets += [(stock,) for stock in ranked_held]
    sale_sets += [tuple(item for item in held if item != stock) for stock in ranked_held]

    sales = {}
    required = []
    for index, sold in enumerate(sale_sets):
        holdings = list(original)
        cash = label.cash
        actions = label.actions
        for stock in sold:
            quantity = holdings[stock]
            holdings[stock] = 0
            cash += quantity * prices[stock]
            actions += (f"s-{market.stocks[stock]}-{quantity}",)
        holdings = tuple(holdings)
        _keep(sales, label.used, holdings, cash, actions)
        if index < 2:
            required.append((label.used, holdings))

    candidates = dict(sales)
    ranked = sorted(
        sales.items(),
        key=lambda item: _portfolio_value(item[1][0], item[0][1], market),
        reverse=True,
    )
    selected_keys = list(dict.fromkeys(required))
    for key, _ in ranked:
        if len(selected_keys) >= MAX_PROBE_CHOICES:
            break
        if key not in selected_keys:
            selected_keys.append(key)
    selected = [(key, sales[key]) for key in selected_keys]
    for (used, holdings), (cash, actions) in selected:
        _keep(
            candidates,
            *_best_buys(
                label.year,
                used,
                holdings,
                cash,
                actions,
                original,
                market,
                knapsack_cells,
            ),
        )
        items = sorted(
            _buy_items(label.year, used, holdings, original, market),
            key=lambda item: (Fraction(item[4], item[2]), item[4]),
            reverse=True,
        )[:MAX_PROBE_CHOICES]
        for lot_id, stock, price, available, _ in items:
            quantity = min(available, cash // price)
            if not quantity:
                continue
            values = list(holdings)
            values[stock] += quantity
            _keep(
                candidates,
                used | (1 << lot_id),
                tuple(values),
                cash - quantity * price,
                actions + (f"b-{market.stocks[stock]}-{quantity}",),
            )

    labels = [
        Label(label.year, label.energy, cash, used, holdings, actions)
        for (used, holdings), (cash, actions) in candidates.items()
    ]
    labels.sort(key=lambda item: (_priority(item, market), item.actions))
    return labels


def _best_buys(
    year, used, holdings, cash, actions, original, market, knapsack_cells
):
    items = _buy_items(year, used, holdings, original, market)
    quantities = _bounded_portfolio(cash, items, knapsack_cells)
    values = list(holdings)
    for quantity, (lot_id, stock, price, _, _) in zip(quantities, items):
        if not quantity:
            continue
        cash -= quantity * price
        values[stock] += quantity
        used |= 1 << lot_id
        actions += (f"b-{market.stocks[stock]}-{quantity}",)
    return used, tuple(values), cash, actions


def _buy_items(year, used, holdings, original, market):
    items = []
    for stock in sorted(market.prices[year]):
        lot_id = market.lot_at.get((year, stock))
        if (
            lot_id is None
            or used & (1 << lot_id)
            or holdings[stock] < original[stock]
        ):
            continue
        lot = market.lots[lot_id]
        profit = market.max_prices[stock] - lot.price
        if profit > 0:
            items.append((lot_id, stock, lot.price, lot.qty, profit))
    return items


def _bounded_portfolio(cash, items, knapsack_cells):
    quantities = [0] * len(items)
    if not items or cash <= 0:
        return quantities

    total_cost = sum(price * quantity for _, _, price, quantity, _ in items)
    if total_cost <= cash:
        return [item[3] for item in items]

    divisor = math.gcd(*(item[2] for item in items))
    remaining = [list(item) for item in items]
    capacity_limit = min(
        MAX_KNAPSACK_CAPACITY,
        max(1, MAX_KNAPSACK_CELLS // len(items)),
        knapsack_cells[0] // len(items),
    )
    reserve = capacity_limit * divisor
    if cash > reserve:
        order = sorted(
            range(len(items)),
            key=lambda index: (
                Fraction(items[index][4], items[index][2]),
                items[index][4],
            ),
            reverse=True,
        )
        for index in order:
            if cash <= reserve:
                break
            price, available = remaining[index][2:4]
            quantity = min(
                available,
                cash // price,
                (cash - reserve + price - 1) // price,
            )
            quantities[index] += quantity
            remaining[index][3] -= quantity
            cash -= quantity * price

    if not capacity_limit:
        return quantities

    capacity = min(cash // divisor, capacity_limit)
    knapsack_cells[0] -= capacity * len(items)
    unreachable = -1
    profits = [unreachable] * (capacity + 1)
    profits[0] = 0
    take_rows = []
    for _, _, price, available, profit in remaining:
        cost = price // divisor
        next_profits = [unreachable] * (capacity + 1)
        takes = array("I", [0]) * (capacity + 1)
        for residue in range(min(cost, capacity + 1)):
            candidates = deque()
            step = 0
            for spent in range(residue, capacity + 1, cost):
                base = profits[spent]
                if base >= 0:
                    score = base - step * profit
                    while candidates and candidates[-1][1] <= score:
                        candidates.pop()
                    candidates.append((step, score))
                while candidates and candidates[0][0] < step - available:
                    candidates.popleft()
                if candidates:
                    previous, score = candidates[0]
                    next_profits[spent] = score + step * profit
                    takes[spent] = step - previous
                step += 1
        profits = next_profits
        take_rows.append(takes)

    spent = max(range(capacity + 1), key=profits.__getitem__)
    for index in range(len(items) - 1, -1, -1):
        quantity = take_rows[index][spent]
        quantities[index] += quantity
        spent -= quantity * (remaining[index][2] // divisor)
    return quantities


def _upper_bound(label, market):
    value = label.cash + sum(
        qty * market.max_prices[stock]
        for stock, qty in enumerate(label.holdings)
    )
    for lot_id, lot in enumerate(market.lots):
        if not label.used & (1 << lot_id):
            value += lot.qty * max(0, market.max_prices[lot.stock] - lot.price)
    return value


def _priority(label, market):
    return -_portfolio_value(label.cash, label.holdings, market)


def _portfolio_value(cash, holdings, market):
    return cash + sum(
        qty * market.max_prices[stock]
        for stock, qty in enumerate(holdings)
    )


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
