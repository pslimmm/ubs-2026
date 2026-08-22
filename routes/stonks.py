import heapq
import itertools
import logging
import math
from array import array
from collections import deque
from dataclasses import dataclass
import collections

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

def stonks(case):
    logger.info(
        "stonks request remote=%s content_length=%s cases=%s",
        request.remote_addr,
        request.content_length,
        len(case) if isinstance(case, list) else None,
    )

    energy = case.get("energy", 0)
    initial_capital = case.get("capital", 0)
    timeline = case.get("timeline", {})
    
    # Parse timeline metadata
    # Format: {year_int: {stock_name: {price: int, qty: int}}}
    parsed_timeline = {}
    for year_str, stocks in timeline.items():
        parsed_timeline[int(year_str)] = stocks

    # State: (current_year, current_capital, current_energy, inventory_tuple, actions_list)
    # inventory_tuple structure: ((stock_name, qty), ...) sorted to maximize cache efficiency
    initial_state = (2037, initial_capital, energy, (), [])
    
    queue = collections.deque([initial_state])
    
    # Visited tracking: (current_year, current_energy, inventory_tuple) -> max_capital_seen
    visited = {}
    
    best_profit = -1
    best_actions = []
    
    while queue:
        curr_year, curr_cap, curr_energy, curr_inv, actions = queue.popleft()
        
        # Pruning optimization: if we can't afford to jump back to 2037, throw away state
        if curr_energy < abs(2037 - curr_year):
            continue
            
        # If back at base year 2037, attempt to liquidate everything for evaluation
        if curr_year == 2037:
            final_cap = curr_cap
            temp_actions = list(actions)
            stocks_2037 = parsed_timeline.get(2037, {})
            
            # Liquidate all inventory using 2037 market valuation
            for stock_name, qty_held in curr_inv:
                if stock_name in stocks_2037:
                    sell_price = stocks_2037[stock_name]["price"]
                    final_cap += qty_held * sell_price
                    temp_actions.append(f"s-{stock_name}-{qty_held}")
            
            if final_cap > best_profit:
                best_profit = final_cap
                best_actions = temp_actions

        state_key = (curr_year, curr_energy, curr_inv)
        if state_key in visited and visited[state_key] >= curr_cap:
            continue
        visited[state_key] = curr_cap
        
        # 1. Action Layer: Evaluate BUY options in the current year
        current_stocks = parsed_timeline.get(curr_year, {})
        for stock_name, details in current_stocks.items():
            price = details["price"]
            max_qty_avail = details["qty"]
            
            # Find current quantity of this stock already in inventory
            inv_dict = dict(curr_inv)
            current_held = inv_dict.get(stock_name, 0)
            
            # Calculate absolute max items we can afford vs market availability
            max_buyable = min(max_qty_avail, curr_cap // price)
            if max_buyable > 0:
                # Maximize profit by executing a bulk purchase operation
                new_cap = curr_cap - (max_buyable * price)
                inv_dict[stock_name] = current_held + max_buyable
                new_inv = tuple(sorted((k, v) for k, v in inv_dict.items() if v > 0))
                
                new_actions = list(actions)
                new_actions.append(f"b-{stock_name}-{max_buyable}")
                
                queue.append((curr_year, new_cap, curr_energy, new_inv, new_actions))

        # 2. Action Layer: Evaluate JUMP options to other historical years
        for target_year in parsed_timeline.keys():
            if target_year == curr_year:
                continue
            
            energy_cost = abs(target_year - curr_year)
            if curr_energy >= energy_cost:
                new_actions = list(actions)
                new_actions.append(f"j-{curr_year}-{target_year}")
                
                queue.append((target_year, curr_cap, curr_energy - energy_cost, curr_inv, new_actions))
                
    return best_actions

@app.route('/stonks', methods=['POST'])
def stonks_endpoint():
    try:
        test_cases = request.get_json()
        if not isinstance(test_cases, list):
            return jsonify({"error": "Malformed payload structure. Root element must be an array."}), 400
            
        results = []
        for case in test_cases:
            optimal_path = stonks(case)
            results.append(optimal_path)
            
        return jsonify(results), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
