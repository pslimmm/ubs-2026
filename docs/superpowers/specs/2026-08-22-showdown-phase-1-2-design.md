# Showdown Phase 1 and Phase 2 Design

## Goal

Reliably clear Phase 1 and all four Phase 2 legs without risking a match-ending loss from an escalating raise war. Phase 1 must retain the documented standard showdown rule. Phase 2 must use the event's stable table-rule mapping learned from evaluator replays.

## Evidence

The two saved Phase 2 replays establish these ranking models:

- `verdigris`: a community match beats a non-match, then higher wins.
- `cinnabar`: 6 is the weakest number, then higher wins.
- `amaranth`: 7 is the strongest number, then higher wins.
- `obsidian`: lower wins.

The latest three losing legs ended in pre-reveal raise wars costing 183, 193, and 171 chips. The existing strategy derives raise size from the full legal ceiling and evaluates calls against a uniform opponent. That combination overbets and ignores the strong information carried by an opponent re-raise.

The latest replay also contains actions that current `main` would reject through its re-raise guard. Deployment therefore needs an observable strategy version.

## Design

Keep the implementation in the existing Showdown route. Represent each ranking rule as a small score model and retain observation-based inference as a fallback for unknown codenames. The documented `standard` rule remains immutable.

The betting policy will:

1. Preserve an already-secured target using the existing remaining-blind calculation.
2. Bet or raise only the legal minimum.
3. Open with strong hands, but never re-raise after opponent aggression.
4. Call normal opening wagers only with sufficient equity.
5. Fold large wagers unless the hand cannot lose against any opposing number.
6. Prefer check, then fold, for all remaining cases.

The large-wager defense must depend on `to_call` as well as action history, so it remains safe if action logs are missing or shaped unexpectedly.

Add `GET /health` with a Showdown strategy version. This is diagnostic only and does not affect move responses.

## Error Handling

Continue treating `legal_actions` as authoritative. Invalid cards, malformed histories, unusable amounts, and corrupt persisted observations retain safe fallbacks. Every response must be legal and require no network access.

## Verification

Use test-driven development with replay-derived cases for:

- Phase 1 pair and high-card ordering.
- All four learned Phase 2 rankings, including Cinnabar 6 and Amaranth 7.
- Minimum bet and raise sizing.
- Folding each catastrophic replay state even without action history.
- Calling a large wager only with a non-losing hand.
- Score protection, malformed input, legal-action fallback, and health version.

Run the complete Showdown suite, replay-state checks, the repository test suite available locally, and a final Git diff/status audit before synchronizing and pushing `main`.

## Scope

Only the Showdown route, its tests, and this design/implementation documentation are in scope. No Phase 3 behavior, dependency changes, or unrelated refactoring will be introduced.
