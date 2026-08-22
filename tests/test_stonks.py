import unittest
import tracemalloc
import time

from routes import app
from routes import stonks as game


def execute(case, actions):
    year = 2037
    energy = 0
    cash = case["capital"]
    holdings = {}
    bought = set()
    for action in actions:
        kind, first, second = action.split("-")
        if kind == "j":
            assert int(first) == year
            energy += abs(int(second) - year)
            year = int(second)
            continue

        stock, qty = first, int(second)
        quote = case["timeline"][str(year)][stock]
        if kind == "b":
            assert (year, stock) not in bought
            assert 0 < qty <= quote["qty"]
            cash -= qty * quote["price"]
            holdings[stock] = holdings.get(stock, 0) + qty
            bought.add((year, stock))
        else:
            assert 0 < qty <= holdings.get(stock, 0)
            cash += qty * quote["price"]
            holdings[stock] -= qty
        assert cash >= 0

    assert year == 2037
    assert energy <= case["energy"]
    return cash


class StonksTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def post(self, *cases):
        response = self.client.post("/stonks", json=list(cases))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/json")
        return response.get_json()

    def test_sample(self):
        case = {
            "energy": 2,
            "capital": 500,
            "timeline": {
                "2037": {"Apple": {"price": 100, "qty": 10}},
                "2036": {"Apple": {"price": 10, "qty": 50}},
            },
        }

        self.assertEqual(
            self.post(case),
            [[
                "j-2037-2036",
                "b-Apple-50",
                "j-2036-2037",
                "s-Apple-50",
            ]],
        )

    def test_allocates_capital_to_the_best_portfolio(self):
        case = {
            "energy": 2,
            "capital": 10,
            "timeline": {
                "2037": {
                    "A": {"price": 12, "qty": 0},
                    "B": {"price": 7, "qty": 0},
                },
                "2036": {
                    "A": {"price": 6, "qty": 1},
                    "B": {"price": 4, "qty": 2},
                },
            },
        }

        self.assertEqual(
            self.post(case),
            [[
                "j-2037-2036",
                "b-A-1",
                "b-B-1",
                "j-2036-2037",
                "s-A-1",
                "s-B-1",
            ]],
        )

    def test_reinvests_profit_without_rebuying_consumed_inventory(self):
        case = {
            "energy": 4,
            "capital": 10,
            "timeline": {
                "2037": {
                    "A": {"price": 10, "qty": 0},
                    "B": {"price": 100, "qty": 0},
                },
                "2036": {
                    "A": {"price": 1, "qty": 10},
                    "B": {"price": 10, "qty": 10},
                },
            },
        }

        self.assertEqual(
            self.post(case),
            [[
                "j-2037-2036",
                "b-A-10",
                "j-2036-2037",
                "s-A-10",
                "j-2037-2036",
                "b-B-10",
                "j-2036-2037",
                "s-B-10",
            ]],
        )

    def test_can_sell_before_returning_to_2037(self):
        case = {
            "energy": 4,
            "capital": 10,
            "timeline": {
                "2037": {},
                "2036": {"A": {"price": 10, "qty": 0}},
                "2035": {"A": {"price": 1, "qty": 10}},
            },
        }

        self.assertEqual(
            self.post(case),
            [[
                "j-2037-2035",
                "b-A-10",
                "j-2035-2036",
                "s-A-10",
                "j-2036-2037",
            ]],
        )

    def test_returns_no_actions_when_no_profitable_round_trip_fits(self):
        case = {
            "energy": 3,
            "capital": 10,
            "timeline": {
                "2037": {"A": {"price": 10, "qty": 0}},
                "2035": {"A": {"price": 1, "qty": 10}},
            },
        }

        self.assertEqual(self.post(case), [[]])

    def test_home_year_may_be_absent_from_timeline(self):
        case = {
            "energy": 2,
            "capital": 10,
            "timeline": {"2036": {"A": {"price": 1, "qty": 10}}},
        }

        self.assertEqual(self.post(case), [[]])

    def test_rejects_non_array_payload(self):
        response = self.client.post("/stonks", json={"energy": 2})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Invalid request"})

    def test_rejects_stock_names_that_cannot_be_encoded(self):
        case = {
            "energy": 2,
            "capital": 10,
            "timeline": {
                "2037": {"BRK-B": {"price": 10, "qty": 1}},
            },
        }

        response = self.client.post("/stonks", json=[case])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Invalid request"})

    def test_large_quantity_probe_returns_promptly(self):
        case = {
            "energy": 2,
            "capital": 100_000,
            "timeline": {
                "2037": {
                    stock: {"price": 100 + index * 10, "qty": 0}
                    for index, stock in enumerate("ABCDE")
                },
                "2036": {
                    stock: {"price": 10 + index, "qty": 10_000}
                    for index, stock in enumerate("ABCDE")
                },
            },
        }

        actions = game.solve_case(case)

        self.assertIn("j-2037-2036", actions)
        self.assertIn("j-2036-2037", actions)
        self.assertTrue(any(action.startswith("b-") for action in actions))
        self.assertTrue(any(action.startswith("s-") for action in actions))
        self.assertGreater(execute(case, actions), case["capital"])

    def test_probe_prioritizes_completed_compounding_routes(self):
        case = {
            "energy": 6,
            "capital": 10,
            "timeline": {
                "2037": {
                    stock: {"price": 2, "qty": 0}
                    for stock in "ABC"
                },
                "2036": {
                    stock: {"price": 1, "qty": 1_000}
                    for stock in "ABC"
                },
            },
        }

        actions = game.solve_case(case)

        self.assertEqual(execute(case, actions), 80)

    def test_probe_uses_the_best_large_bounded_portfolio(self):
        case = {
            "energy": 2,
            "capital": 552,
            "timeline": {
                "2036": {
                    "A": {"price": 21, "qty": 87},
                    "B": {"price": 27, "qty": 31},
                    "C": {"price": 25, "qty": 137},
                    "D": {"price": 19, "qty": 51},
                    "E": {"price": 14, "qty": 40},
                },
                "2037": {
                    "A": {"price": 57, "qty": 0},
                    "B": {"price": 46, "qty": 0},
                    "C": {"price": 79, "qty": 0},
                    "D": {"price": 60, "qty": 0},
                    "E": {"price": 54, "qty": 0},
                },
            },
        }

        actions = game.solve_case(case)

        self.assertEqual(execute(case, actions), 2_113)

    def test_large_portfolio_never_prefills_an_unaffordable_stock(self):
        case = {
            "energy": 2,
            "capital": 100_001,
            "timeline": {
                "2036": {
                    "A": {"price": 200_000, "qty": 1},
                    "B": {"price": 1, "qty": 100_001},
                },
                "2037": {
                    "A": {"price": 1_200_000, "qty": 0},
                    "B": {"price": 2, "qty": 0},
                },
            },
        }

        actions = game.solve_case(case)

        self.assertEqual(execute(case, actions), 200_002)

    def test_large_integer_prices_do_not_overflow_probe_ranking(self):
        huge_profit = 10**400
        case = {
            "energy": 2,
            "capital": 100_001,
            "timeline": {
                "2036": {
                    "A": {"price": 100_000, "qty": 2},
                    "B": {"price": 1, "qty": 100_001},
                },
                "2037": {
                    "A": {"price": huge_profit + 100_000, "qty": 0},
                    "B": {"price": 2, "qty": 0},
                },
            },
        }

        actions = game.solve_case(case)

        self.assertEqual(execute(case, actions), huge_profit + 100_002)

    def test_unaffordable_probe_inventory_has_bounded_memory(self):
        case = {
            "energy": 2,
            "capital": 1_000_000,
            "timeline": {
                "2036": {
                    "A": {"price": 1_000_001, "qty": 1_000_000},
                    "B": {"price": 1_000_002, "qty": 1_000_000},
                },
                "2037": {
                    "A": {"price": 1_000_101, "qty": 0},
                    "B": {"price": 1_000_102, "qty": 0},
                },
            },
        }

        tracemalloc.start()
        try:
            actions = game.solve_case(case)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(actions, [])
        self.assertLess(peak, 8_000_000)

    def test_many_stock_probe_avoids_combinatorial_local_trades(self):
        stocks = [f"S{index}" for index in range(100)]
        case = {
            "energy": 2,
            "capital": 10_000,
            "timeline": {
                "2036": {
                    stock: {"price": index + 1, "qty": 1_000}
                    for index, stock in enumerate(stocks)
                },
                "2037": {
                    stock: {"price": 2 * (index + 1) + 1, "qty": 0}
                    for index, stock in enumerate(stocks)
                },
            },
        }

        started = time.monotonic()
        actions = game.solve_case(case)
        elapsed = time.monotonic() - started

        self.assertGreater(execute(case, actions), case["capital"])
        self.assertLess(elapsed, 3)

    def test_many_stock_reinvestment_shares_one_work_budget(self):
        first = [f"A{index}" for index in range(100)]
        second = [f"B{index}" for index in range(100)]
        case = {
            "energy": 4,
            "capital": 10_000,
            "timeline": {
                "2035": {
                    stock: {"price": 1, "qty": 100}
                    for stock in first
                },
                "2036": {
                    **{
                        stock: {"price": 2, "qty": 0}
                        for stock in first
                    },
                    **{
                        stock: {"price": 1, "qty": 1_000}
                        for stock in second
                    },
                },
                "2037": {
                    stock: {"price": 3, "qty": 0}
                    for stock in second
                },
            },
        }

        started = time.monotonic()
        actions = game.solve_case(case)
        elapsed = time.monotonic() - started

        self.assertEqual(execute(case, actions), 60_000)
        self.assertLess(elapsed, 2)

    def test_small_exact_market_ignores_unreachable_price_decoys(self):
        case = {
            "energy": 2,
            "capital": 30,
            "timeline": {
                "2037": {"C": {"price": 10, "qty": 0}},
                "2036": {
                    "A": {"price": 1, "qty": 10},
                    "B": {"price": 1, "qty": 10},
                    "C": {"price": 1, "qty": 10},
                },
                "1": {
                    "A": {"price": 1000, "qty": 0},
                    "B": {"price": 1000, "qty": 0},
                },
            },
        }

        actions = game.solve_case(case)

        self.assertEqual(execute(case, actions), 120)


if __name__ == "__main__":
    unittest.main()
