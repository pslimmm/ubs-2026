# Time Travelling Stonks Man

## Endpoint

`POST /stonks` accepts an array of independent cases and returns one action array per
case. The response content type is `application/json`.

```json
[
  {
    "energy": 2,
    "capital": 500,
    "timeline": {
      "2037": {"Apple": {"price": 100, "qty": 10}},
      "2036": {"Apple": {"price": 10, "qty": 50}}
    }
  }
]
```

The corresponding response is:

```json
[["j-2037-2036", "b-Apple-50", "j-2036-2037", "s-Apple-50"]]
```

## Rules

- Every journey starts in 2037 and must finish in 2037.
- Jumping between two years consumes their absolute difference in energy.
- Total jump energy must not exceed the case's `energy`.
- Buying shares consumes capital at that year's price.
- A stock-year inventory may be bought at most once. Any quantity not bought in
  that purchase is no longer available.
- A purchase cannot exceed the stock-year `qty`.
- Shares may be sold in any visited year that quotes that stock. Sale quantity is
  limited by the traveller's holdings, not by the destination's `qty`; this follows
  the supplied example, which sells 50 shares in a year whose `qty` is 10.
- Quantities are integral and short selling is not allowed.
- The objective is maximum cash after returning to 2037.

Actions have one of these forms:

```text
j-{from year}-{to year}
b-{stock name}-{quantity}
s-{stock name}-{quantity}
```

Input assumptions from the challenge are `energy > 1`, `capital > 0`, years in
`1..2037`, `price > 0`, and `qty >= 0`.

## Optimizer

The solver is an anytime, exact-first dynamic program over sparse labels:

```text
(current year, energy used, consumed inventories, holdings) -> maximum cash
```

Only non-empty timeline years that can occur in a round trip, plus 2037, are
considered because stopping in an empty or unreachable year cannot improve a route.
At each visit, a local bounded-knapsack frontier enumerates
sell and buy portfolios. Labels are discarded when another label at the same year
has no less cash or holdings, no more energy use, and no more consumed inventory.

Search uses an admissible upper bound that grants free travel, liquidation at each
stock's global maximum price, and every remaining stock-year's maximum possible
profit. A branch is discarded if it cannot return to 2037 or cannot beat the
incumbent solution. Probe mode explores labels with the greatest optimistic
mark-to-market portfolio value first so complete compounding routes are not starved
by loose bounds on unfunded inventory.

Before searching, the solver estimates the complete DP state space from energy,
reachable years, inventories, and maximum holdings. Cases whose estimate is at most
100,000 are exact mode: every integer quantity is enumerated, local frontiers are
never truncated, and search continues until the queue is exhausted. Other cases use
a 250-state optimum probe. Each visit considers bounded, economically meaningful
choices: hold or liquidate the portfolio, selective full-stock sales, the eight best
single-stock purchases, and an optimized bounded-knapsack purchase portfolio. The
knapsack residual is exact within a 50,000 scaled-capital, 1,000,000-cell portfolio,
and 2,000,000-cell case budget; work beyond those limits uses profit-density bulk
allocation. These limits keep the endpoint responsive. The best valid route is always
returned, and the server logs whether exhaustive optimality was proven plus the
remaining optimistic gap when it was not.

Output is deterministic and prioritizes maximum final cash. Because the wire grammar
uses hyphens as separators, stock names must be non-empty and cannot contain hyphens;
requests that violate this constraint receive HTTP 400.
