# Showdown Phase 1 and Phase 2 v4 Design

## Goal

Clear Phase 1 and all four Phase 2 legs by retaining protection against raise wars while recovering the value lost by the overly conservative v3 policy.

## Evidence

The v3 evaluator attempt finished at `+80` overall but cleared only Cinnabar and Amaranth. The supplied trajectories end near:

- Verdigris: `-11`
- Cinnabar: `+38`
- Amaranth: `+42`
- Obsidian: `+11`

Amaranth hand 1 shows the challenger raising to 4, calling an opponent re-raise to 10, then folding to a post-reveal bet of 13. Calling the re-raise with a merely strong hand wastes six additional chips and proves that v3 does not distinguish an initial opponent bet from a re-raise.

Across the two retained raw evaluator histories, the opponent sizes ordinary value bets at about two-thirds pot: 3 into 4, 7 into 10, and 13 into 20. The challenger always betting the legal minimum leaves too little value to reliably reach `+25`.

The retained histories also show Cinnabar treating low community matches as premium hands. Its showdown and action evidence supports the documented standard pair-then-high ranking. The earlier weak-6 interpretation fit two coincidental match-versus-6 outcomes but does not explain the opponent's paired-card betting.

## Rule Models

- `standard`: community match first, then higher number.
- `verdigris`: community match first, then higher number.
- `cinnabar`: community match first, then higher number.
- `amaranth`: 7 first, then higher number.
- `obsidian`: lower number.

Observation-based inference remains the fallback for unknown codenames. Stable learned models cannot be overwritten by noisy observations.

## Betting Policy

Keep one compact policy in `routes/showdown.py`, but distinguish four action contexts:

1. **Unopened pre-reveal pot:** raise to 5, clamped to legal bounds, at uniform equity `>= 0.60`.
2. **Initial opponent aggression:** call only at equity `>= 0.80`, when the additional call is at most 10 big blinds. Never raise.
3. **Opponent re-raise after our bet or raise:** fold unless the hand cannot lose against any opposing number. Never re-raise.
4. **Post-reveal free action:** value-bet approximately two-thirds pot at equity `>= 0.60`. If the opponent checked first, make only the minimum legal steal at equity `>= 0.35` because their range is capped and larger sizing gains little fold equity.

A non-losing hand may call beyond the ordinary exposure ceiling. All other rejected wagers fold. Legal-action and minimum/maximum amount fields remain authoritative.

This bounds ordinary voluntary exposure while allowing the 3/7/13 sizing already proven effective by the opponent. It does not introduce bluff state, randomness, or a long-lived opponent model.

## Target Protection

Continue using live `your_stack`, not the frozen per-hand chip delta. Replace the two-big-blinds-per-remaining-hand estimate with the exact future forced-blind schedule derived from `your_seat`, `button_seat`, small blind, big blind, and alternating position. Once the target plus all unavoidable future blinds is secured, take no optional risk.

## Error Handling

- Treat malformed action history as absent.
- Normalize numeric and string seat identifiers before comparison.
- Ignore invalid raise bounds and use another legal action.
- Return only listed legal actions and include `amount` only for bet or raise.
- Keep request handling deterministic, local, and side-effect-free apart from existing rule-memory persistence.

## Verification

Use test-driven development for:

- Cinnabar low-card community matches.
- Pre-reveal raise-to-5 sizing.
- Two-thirds-pot values of 3, 7, and 13.
- Immediate fold to the Amaranth hand-1 re-raise.
- Initial-aggression calls versus re-raise folds.
- Minimum positional steals after an opponent check.
- Exact alternating future-blind protection.
- All three historical all-in failure states.
- Phase 1 standard equity, corrupt input, legal-only fallbacks, and strategy health version `phase1-2-v4`.

Re-run all unit tests, every retained raw-showdown comparison, catastrophic-state checks, and the legal-action scenario matrix before synchronizing and pushing `main`.

## Scope

Modify only `routes/showdown.py`, `tests/test_showdown.py`, and Superpowers design/plan documentation. Do not add dependencies, Phase 3 behavior, or unrelated refactoring.
