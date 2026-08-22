# Showdown Phase 1 and Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Phase 1 correctness and reliably clear every Phase 2 leg with learned table rules and bounded betting risk.

**Architecture:** Keep the existing single-route rule engine and betting policy. Extend its generic score models with exact-number features, seed the stable event rules learned from evaluator evidence, then replace ceiling-based aggression with minimum sizing and a hard ordinary-call cap. Expose a health version to prove which strategy is deployed.

**Tech Stack:** Python 3, Flask, unittest

**Spec:** `docs/superpowers/specs/2026-08-22-showdown-phase-1-2-design.md`

## Global Constraints

- Keep Phase 1's documented pair-then-high rule immutable.
- Treat `legal_actions` and raise bounds as authoritative.
- Respond without network access and within the five-second limit.
- Do not add Phase 3 behavior, dependencies, or unrelated refactoring.
- Keep production changes inside `routes/showdown.py` and regressions inside `tests/test_showdown.py`.

---

### Task 1: Model Every Learned Table Rule

**Files:**
- Modify: `routes/showdown.py`
- Test: `tests/test_showdown.py`

**Interfaces:**
- Consumes: `_metric(name, card, community)`, `_models(table_rule)`, `compare_hands(...)`, and `evaluate_hand_strength(...)`.
- Produces: `KNOWN_MODELS: dict[str, tuple[str, int, int]]` and support for `number_1` through `number_13` metrics.

- [ ] **Step 1: Write failing ranking tests**

Add tests that assert:

```python
def test_learned_phase_two_rules(self):
    self.assertEqual(game.compare_hands(13, 8, 8, "verdigris"), -1)
    self.assertEqual(game.compare_hands(6, 5, 13, "cinnabar"), -1)
    self.assertEqual(game.compare_hands(7, 13, 1, "amaranth"), 1)
    self.assertEqual(game.compare_hands(2, 12, 7, "obsidian"), 1)

def test_learned_rules_cannot_be_overwritten_by_bad_observations(self):
    game.RULE_KNOWLEDGE_BASE["amaranth"] = {
        "observations": [[1, 7, 13, -1]]
    }
    self.assertEqual(game.compare_hands(7, 13, 1, "amaranth"), 1)
```

- [ ] **Step 2: Run the two tests and verify failure**

Run:

```bash
python -m unittest tests.test_showdown.ShowdownTests.test_learned_phase_two_rules tests.test_showdown.ShowdownTests.test_learned_rules_cannot_be_overwritten_by_bad_observations -v
```

Expected: at least Cinnabar or Amaranth fails because exact-number rules are absent.

- [ ] **Step 3: Implement exact-number models and learned mappings**

Add exact-number metric handling and these stable models:

```python
KNOWN_MODELS = {
    "verdigris": ("pair", 1, 1),
    "cinnabar": ("number_6", -1, 1),
    "amaranth": ("number_7", 1, 1),
    "obsidian": ("card", -1, 0),
}
```

Include `number_1` through `number_13` in generic candidate generation so unknown future codenames can learn the same rule shape. Return the known model before consulting observations.

- [ ] **Step 4: Run the full Showdown suite**

Run: `python -m unittest tests.test_showdown -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the rule engine**

```bash
git add routes/showdown.py tests/test_showdown.py
git commit -m "fix: learn showdown table rules"
```

---

### Task 2: Bound Betting Risk

**Files:**
- Modify: `routes/showdown.py`
- Test: `tests/test_showdown.py`

**Interfaces:**
- Consumes: `evaluate_hand_strength(...)`, `_amount_bounds(data)`, `_secured(data)`, and request fields `to_call`, `pot`, `big_blind`, `current_hand_actions`.
- Produces: `_opponent_aggressive(data) -> bool` and a `_move(data) -> dict` policy that uses minimum sizing and bounded calls.

- [ ] **Step 1: Add replay-derived failing tests**

Cover these behaviors:

```python
def test_raises_only_to_the_legal_minimum(self):
    response = self.client.post("/move", json=payload(
        table_rule="standard", your_number=13,
        community_number=None, to_call=1, pot=3,
        min_raise_to=5, max_raise_to=200,
        current_hand_actions=[],
    ))
    self.assertEqual(response.get_json(), {"action": "raise", "amount": 5})

def test_large_call_folds_without_action_history(self):
    response = self.client.post("/move", json=payload(
        table_rule="amaranth", your_number=9,
        community_number=None, to_call=86, pot=314,
        min_raise_to=207, max_raise_to=207,
        current_hand_actions=[],
    ))
    self.assertEqual(response.get_json(), {"action": "fold"})

def test_never_reraises_opponent_aggression(self):
    response = self.client.post("/move", json=payload(
        table_rule="standard", your_number=12,
        community_number=None, to_call=3, pot=7,
        min_raise_to=9, max_raise_to=200,
        current_hand_actions=[
            {"round": "pre_reveal", "seat": 1,
             "action": "raise", "amount": 5},
        ], round="pre_reveal",
    ))
    self.assertNotEqual(response.get_json()["action"], "raise")

def test_nut_hand_may_call_a_large_wager(self):
    response = self.client.post("/move", json=payload(
        table_rule="obsidian", your_number=1,
        community_number=8, to_call=100, pot=200,
        current_hand_actions=[],
    ))
    self.assertEqual(response.get_json(), {"action": "call"})
```

Add corresponding Cinnabar and Obsidian catastrophic-state tests using `to_call` above five big blinds and no action history.

- [ ] **Step 2: Run the new policy tests and verify failure**

Run: `python -m unittest tests.test_showdown -v`

Expected: minimum sizing and history-independent large-wager tests fail under the current policy.

- [ ] **Step 3: Implement the bounded policy**

Use these constants and ordering:

```python
VALUE_EQUITY = 0.70
BET_EQUITY = 0.60
NUT_EQUITY = 12.5 / 13
MAX_CALL_BLINDS = 5
```

Normalize seat comparison with `str(...)` in `_opponent_aggressive`. In `_move`:

1. Apply score protection.
2. Raise only to `min_raise_to` when equity is at least `VALUE_EQUITY` and the opponent has not bet or raised this round.
3. If facing a wager, call any amount only at `NUT_EQUITY`; otherwise require `to_call <= big_blind * MAX_CALL_BLINDS` and equity at least both pot odds and `VALUE_EQUITY` after opponent aggression.
4. Fold an unaccepted wager.
5. Bet only to `min_raise_to` at `BET_EQUITY` when no call is due.
6. Use legal check/fold/call and minimum-size fallbacks.

- [ ] **Step 4: Run the full Showdown suite**

Run: `python -m unittest tests.test_showdown -v`

Expected: all tests pass, including prior Phase 1 and malformed-input regressions.

- [ ] **Step 5: Commit the betting policy**

```bash
git add routes/showdown.py tests/test_showdown.py
git commit -m "fix: cap showdown betting risk"
```

---

### Task 3: Make Deployment Observable

**Files:**
- Modify: `routes/showdown.py`
- Test: `tests/test_showdown.py`

**Interfaces:**
- Produces: `GET /health -> {"status": "ok", "showdown_strategy": "phase1-2-v3"}`.

- [ ] **Step 1: Write the failing health test**

```python
def test_health_reports_showdown_strategy_version(self):
    response = self.client.get("/health")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.get_json(), {
        "status": "ok",
        "showdown_strategy": "phase1-2-v3",
    })
```

- [ ] **Step 2: Run the health test and verify failure**

Run:

```bash
python -m unittest tests.test_showdown.ShowdownTests.test_health_reports_showdown_strategy_version -v
```

Expected: FAIL because `/health` is absent.

- [ ] **Step 3: Add the health route**

Add a side-effect-free Flask GET route returning the exact versioned JSON above.

- [ ] **Step 4: Run the complete test suite**

Run: `python -m unittest discover -s tests -v`

Expected: all available repository tests pass.

- [ ] **Step 5: Commit deployment diagnostics**

```bash
git add routes/showdown.py tests/test_showdown.py
git commit -m "feat: expose showdown strategy health"
```

---

### Task 4: Replay Validation and Main Synchronization

**Files:**
- Verify: `routes/showdown.py`
- Verify: `tests/test_showdown.py`

**Interfaces:**
- Consumes: the final `/move` and `/health` behavior.
- Produces: a clean, synchronized `main` containing the verified strategy.

- [ ] **Step 1: Re-run both replay failure states through `_move`**

Use the downloaded evaluator JSON to confirm the three bust sequences stop at the first oversized re-raise, and verify the four learned models predict every disambiguating showdown.

- [ ] **Step 2: Run static and test checks**

```bash
python -m compileall routes/showdown.py tests/test_showdown.py
python -m unittest discover -s tests -v
git diff --check
```

Expected: compilation succeeds, tests pass, and no whitespace errors are reported.

- [ ] **Step 3: Audit scope**

Run: `git status --short && git diff HEAD~3 --stat`

Expected: only the approved Showdown implementation, tests, spec, and plan changed.

- [ ] **Step 4: Synchronize remote changes without discarding work**

Run: `git pull --no-rebase origin main`

Resolve conflicts by retaining remote unrelated changes and the verified Showdown implementation, then rerun Step 2.

- [ ] **Step 5: Push main**

Run: `git push origin main`

Expected: remote `main` advances to the verified local commit.
