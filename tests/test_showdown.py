import unittest
from unittest.mock import patch

from routes import app
from routes import showdown as game


def payload(**changes):
    data = {
        "phase": 2,
        "table_rule": "onyx",
        "your_number": 7,
        "community_number": 7,
        "to_call": 2,
        "pot": 10,
        "legal_actions": ["fold", "call", "raise"],
        "min_raise_to": 4,
        "max_raise_to": 200,
        "your_seat": 0,
        "hand_number": 1,
        "total_hands": 40,
        "big_blind": 2,
        "players": [
            {"seat": 0, "name": "you", "chip_delta": 0},
            {"seat": 1, "name": "bot", "chip_delta": 0},
        ],
        "recent_hands": [],
    }
    data.update(changes)
    return data


def showdown_hand(number, community, low, high, winner, integer_keys=False):
    keys = (0, 1) if integer_keys else ("0", "1")
    return {
        "hand_number": number,
        "community_number": community,
        "shown_numbers": {keys[0]: low, keys[1]: high},
        "winners": [0 if winner == low else 1],
    }


class ShowdownTests(unittest.TestCase):
    def setUp(self):
        game.RULE_KNOWLEDGE_BASE.clear()
        self.client = app.test_client()
        self.no_save = patch.object(game, "save_memory")
        self.no_save.start()

    def tearDown(self):
        self.no_save.stop()

    def test_phase_one_pre_reveal_equity_includes_future_pair_outcomes(self):
        self.assertAlmostEqual(
            game.evaluate_hand_strength(1, None, "standard"),
            18.5 / 169,
        )
        self.assertAlmostEqual(
            game.evaluate_hand_strength(13, None, "standard"),
            150.5 / 169,
        )

    def test_standard_rule_cannot_be_overwritten_by_observations(self):
        misleading = [showdown_hand(1, 7, 2, 12, winner=2)]
        game.RULE_KNOWLEDGE_BASE["standard"] = {"7": {"2_12": 2}}

        game.update_rule_knowledge("standard", misleading)

        self.assertEqual(game.compare_hands(2, 12, 7, "standard"), -1)

    def test_learned_phase_two_rules(self):
        self.assertEqual(game.compare_hands(13, 8, 8, "verdigris"), -1)
        self.assertEqual(game.compare_hands(6, 5, 5, "cinnabar"), -1)
        self.assertEqual(game.compare_hands(6, 5, 13, "cinnabar"), 1)
        self.assertEqual(game.compare_hands(7, 13, 1, "amaranth"), 1)
        self.assertEqual(game.compare_hands(2, 12, 7, "obsidian"), 1)

    def test_learned_rules_cannot_be_overwritten_by_bad_observations(self):
        game.RULE_KNOWLEDGE_BASE["amaranth"] = {
            "observations": [[1, 7, 13, -1]]
        }

        self.assertEqual(game.compare_hands(7, 13, 1, "amaranth"), 1)

    def test_hidden_low_card_rule_generalizes_to_unseen_matchups(self):
        evidence = [
            showdown_hand(1, 1, 2, 12, winner=2),
            showdown_hand(2, 13, 3, 11, winner=3, integer_keys=True),
            showdown_hand(3, 7, 2, 5, winner=2),
            showdown_hand(4, 4, 6, 10, winner=6),
        ]
        game.update_rule_knowledge("onyx", evidence)

        low = game.evaluate_hand_strength(2, 9, "onyx")
        high = game.evaluate_hand_strength(12, 9, "onyx")

        self.assertGreater(low, high)

    def test_incomplete_showdown_history_is_ignored(self):
        incomplete = {
            "community_number": 5,
            "shown_numbers": {"0": 2, "1": 9},
            "winners": [],
        }

        response = self.client.post(
            "/move", json=payload(recent_hands=[incomplete])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(response.get_json()["action"], payload()["legal_actions"])

    def test_health_reports_showdown_strategy_version(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"status": "ok", "showdown_strategy": "phase1-2-v3"},
        )

    def test_corrupt_rule_memory_is_recovered(self):
        game.RULE_KNOWLEDGE_BASE["onyx"] = {
            "observations": [["bad", "low", "high", "result"]]
        }

        response = self.client.post("/move", json=payload())

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(
            game.RULE_KNOWLEDGE_BASE["onyx"]["observations"], list
        )
        self.assertEqual(game.RULE_KNOWLEDGE_BASE["onyx"]["observations"], [])

    def test_missing_raise_ceiling_falls_back_to_another_legal_action(self):
        response = self.client.post(
            "/move",
            json=payload(max_raise_to=None, legal_actions=["fold", "call", "raise"]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(response.get_json()["action"], ["fold", "call", "raise"])

    def test_only_legal_action_is_always_returned(self):
        response = self.client.post(
            "/move",
            json=payload(
                your_number=1,
                community_number=13,
                pot=0,
                to_call=20,
                legal_actions=["call"],
                min_raise_to=None,
                max_raise_to=None,
            ),
        )

        self.assertEqual(response.get_json(), {"action": "call"})

    def test_all_in_raise_uses_the_single_legal_amount(self):
        response = self.client.post(
            "/move",
            json=payload(table_rule="standard", min_raise_to=17, max_raise_to=17),
        )

        self.assertEqual(response.get_json(), {"action": "raise", "amount": 17})

    def test_secured_phase_two_leg_avoids_optional_risk(self):
        players = payload()["players"]
        players[0]["chip_delta"] = 30

        response = self.client.post(
            "/move",
            json=payload(hand_number=39, players=players),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_secured_leg_uses_exact_future_blinds(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="standard",
                your_number=13,
                community_number=13,
                hand_number=38,
                total_hands=40,
                your_seat=0,
                button_seat=0,
                small_blind=1,
                big_blind=2,
                starting_stack=200,
                your_stack=228,
            ),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_committed_chips_are_not_counted_as_a_secured_lead(self):
        players = payload()["players"]
        players[0]["chip_delta"] = 30

        response = self.client.post(
            "/move",
            json=payload(
                table_rule="standard",
                hand_number=40,
                players=players,
                starting_stack=200,
                your_stack=205,
            ),
        )

        self.assertEqual(response.get_json()["action"], "raise")

    def test_pre_reveal_value_raise_uses_five(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="standard",
                round="pre_reveal",
                your_number=10,
                community_number=None,
                to_call=1,
                pot=3,
                min_raise_to=4,
                max_raise_to=200,
                current_hand_actions=[],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "raise", "amount": 5})

    def test_two_thirds_pot_value_sizing(self):
        for pot, expected in ((4, 3), (10, 7), (20, 13)):
            with self.subTest(pot=pot):
                response = self.client.post(
                    "/move",
                    json=payload(
                        table_rule="standard",
                        round="post_reveal",
                        your_number=10,
                        community_number=1,
                        to_call=0,
                        pot=pot,
                        legal_actions=["check", "bet"],
                        min_raise_to=2,
                        max_raise_to=200,
                        current_hand_actions=[],
                    ),
                )

                self.assertEqual(
                    response.get_json(), {"action": "bet", "amount": expected}
                )

    def test_amaranth_hand_one_folds_to_opponent_reraise(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="amaranth",
                round="pre_reveal",
                your_number=11,
                community_number=None,
                to_call=6,
                pot=14,
                min_raise_to=16,
                max_raise_to=200,
                current_hand_actions=[
                    {
                        "round": "pre_reveal",
                        "seat": 0,
                        "action": "raise",
                        "amount": 4,
                    },
                    {
                        "round": "pre_reveal",
                        "seat": 1,
                        "action": "raise",
                        "amount": 10,
                    },
                ],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_checked_to_steal_uses_minimum(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="standard",
                round="post_reveal",
                your_number=6,
                community_number=13,
                to_call=0,
                pot=10,
                legal_actions=["check", "bet"],
                min_raise_to=2,
                max_raise_to=200,
                current_hand_actions=[
                    {"round": "post_reveal", "seat": 1, "action": "check"}
                ],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "bet", "amount": 2})

    def test_large_amaranth_call_folds_without_action_history(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="amaranth",
                your_number=9,
                community_number=None,
                to_call=86,
                pot=314,
                min_raise_to=207,
                max_raise_to=207,
                current_hand_actions=[],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_large_cinnabar_call_folds_without_action_history(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="cinnabar",
                your_number=11,
                community_number=None,
                to_call=104,
                pot=248,
                current_hand_actions=[],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_large_obsidian_call_folds_without_action_history(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="obsidian",
                your_number=4,
                community_number=None,
                to_call=122,
                pot=276,
                current_hand_actions=[],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_never_reraises_opponent_aggression(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="standard",
                your_number=12,
                community_number=None,
                to_call=3,
                pot=7,
                min_raise_to=9,
                round="pre_reveal",
                current_hand_actions=[
                    {
                        "round": "pre_reveal",
                        "seat": 1,
                        "action": "raise",
                        "amount": 5,
                    }
                ],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "call"})

    def test_nut_hand_may_call_a_large_wager(self):
        response = self.client.post(
            "/move",
            json=payload(
                table_rule="obsidian",
                your_number=1,
                community_number=8,
                to_call=100,
                pot=200,
                current_hand_actions=[],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "call"})

    def test_folds_cinnabar_hand_after_opponent_reraises(self):
        evidence = [
            showdown_hand(1, 5, 6, 12, winner=12),
            showdown_hand(2, 5, 1, 11, winner=11),
            showdown_hand(3, 2, 5, 8, winner=8),
            showdown_hand(4, 1, 7, 12, winner=12),
        ]
        game.update_rule_knowledge("cinnabar", evidence)

        response = self.client.post(
            "/move",
            json=payload(
                table_rule="cinnabar",
                round="pre_reveal",
                your_number=10,
                community_number=None,
                pot=27,
                to_call=11,
                min_raise_to=31,
                current_hand_actions=[
                    {"round": "pre_reveal", "seat": 0, "action": "raise", "amount": 7},
                    {"round": "pre_reveal", "seat": 1, "action": "raise", "amount": 18},
                ],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_folds_amaranth_hand_after_opponent_reraises(self):
        evidence = [
            showdown_hand(1, 13, 6, 10, winner=10),
            showdown_hand(2, 10, 3, 12, winner=12),
            showdown_hand(3, 12, 4, 6, winner=6),
            showdown_hand(4, 11, 4, 8, winner=8),
        ]
        game.update_rule_knowledge("amaranth", evidence)

        response = self.client.post(
            "/move",
            json=payload(
                table_rule="amaranth",
                round="post_reveal",
                your_number=10,
                community_number=13,
                pot=81,
                to_call=38,
                min_raise_to=97,
                current_hand_actions=[
                    {"round": "post_reveal", "seat": 1, "action": "bet", "amount": 7},
                    {"round": "post_reveal", "seat": 0, "action": "raise", "amount": 19},
                    {"round": "post_reveal", "seat": 1, "action": "raise", "amount": 57},
                ],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "fold"})

    def test_calls_instead_of_reraising_with_near_nut_equity(self):
        evidence = [
            showdown_hand(1, 13, 1, 5, winner=1),
            showdown_hand(2, 3, 4, 12, winner=4),
            showdown_hand(3, 12, 2, 8, winner=2),
            showdown_hand(4, 1, 4, 8, winner=4),
            showdown_hand(5, 5, 2, 5, winner=2),
            showdown_hand(6, 5, 5, 8, winner=5),
        ]
        game.update_rule_knowledge("obsidian", evidence)

        response = self.client.post(
            "/move",
            json=payload(
                table_rule="obsidian",
                round="pre_reveal",
                your_number=1,
                community_number=None,
                pot=60,
                to_call=20,
                min_raise_to=90,
                current_hand_actions=[
                    {"round": "pre_reveal", "seat": 0, "action": "raise", "amount": 30},
                    {"round": "pre_reveal", "seat": 1, "action": "raise", "amount": 50},
                ],
            ),
        )

        self.assertEqual(response.get_json(), {"action": "call"})


if __name__ == "__main__":
    unittest.main()
