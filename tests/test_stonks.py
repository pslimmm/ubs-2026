import unittest

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
