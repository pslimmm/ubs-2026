import math
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from flask import jsonify, request

from routes import app


class Transaction:
    def __init__(
        self,
        tx_id: str,
        from_user: str,
        to_user: str,
        amount: float,
        timestamp: datetime,
        ip_address: Optional[str] = None,
        device_id: Optional[str] = None,
    ):
        self.tx_id = tx_id
        self.from_user = from_user
        self.to_user = to_user
        self.amount = float(amount)
        self.timestamp = timestamp
        self.ip_address = ip_address
        self.device_id = device_id

    @classmethod
    def from_dict(cls, data) -> "Transaction":
        """Parses raw JSON transaction payload into a Transaction instance."""
        # Convert ISO-8601 string (e.g., '2026-06-08T12:00:00Z') to datetime
        raw_ts = data["createdAt"].replace("Z", "+00:00")

        return cls(
            tx_id=data["txId"],
            from_user=data["fromUserId"],
            to_user=data["toUserId"],
            amount=data["amount"],
            timestamp=datetime.fromisoformat(raw_ts),
            ip_address=data.get("ipAddress"),  # Handle missing optionals gracefully
            device_id=data.get("deviceId")
        )

class GraphRiskEngine:
    def __init__(self, lookback_hours: int = 24):
        self.lookback = timedelta(hours=lookback_hours)
        self.processed_txs: Dict[str, float] = {}  # txId -> score (Idempotency)
        self.history = deque()  # (timestamp, tx)
        self.adj = defaultdict(lambda: defaultdict(int))  # u -> v -> edge_capacity
        self.nodes: Set[str] = set()

    def reset(self):
        """Phase 1 Constraint: Must fully clear graph and state."""
        self.processed_txs.clear()
        self.history.clear()
        self.adj.clear()
        self.nodes.clear()

    def _evict_expired(self, current_time: datetime):
        """Phase 1 Constraint: Lookback window maintenance (24 Hours)."""
        cutoff = current_time - self.lookback
        while self.history and self.history[0][0] < cutoff:
            _, old_tx = self.history.popleft()
            u, v = old_tx.from_user, old_tx.to_user
            self.adj[u][v] -= 1
            if self.adj[u][v] <= 0:
                del self.adj[u][v]
            if not self.adj[u]:
                del self.adj[u]

        # Rebuild active node set efficiently after eviction
        self.nodes = {u for u in self.adj} | {
            v for targets in self.adj.values() for v in targets
        }

    def _find_shortest_path(self, start: str, target: str) -> Optional[int]:
        """BFS to find shortest directed path distance from start to target."""
        if start not in self.nodes or target not in self.nodes:
            return None

        queue = deque([(start, 0)])
        visited = {start}

        while queue:
            curr, dist = queue.popleft()
            if curr == target and dist > 0:
                return dist

            for neighbor in self.adj.get(curr, {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, dist + 1))
        return None

    def _count_disjoint_paths(self, start: str, target: str, max_paths: int = 5) -> int:
        """
        Calculates the number of edge-disjoint paths using BFS residual tracking.
        Prevents combinatorial explosion and correctly handles dense graph loops.
        """
        if start not in self.nodes or target not in self.nodes:
            return 0

        # Copy edge capacity graph for flow allocation
        residual = defaultdict(lambda: defaultdict(int))
        for u, targets in self.adj.items():
            for v, cap in targets.items():
                residual[u][v] = cap

        paths_found = 0
        while paths_found < max_paths:
            # Find augmenting path using BFS
            parent = {}
            queue = deque([start])
            visited = {start}
            found = False

            while queue:
                curr = queue.popleft()
                if curr == target:
                    found = True
                    break
                for nxt, cap in residual.get(curr, {}).items():
                    if cap > 0 and nxt not in visited:
                        visited.add(nxt)
                        parent[nxt] = curr
                        queue.append(nxt)

            if not found:
                break

            # Augment path
            curr = target
            while curr != start:
                p = parent[curr]
                residual[p][curr] -= 1
                curr = p
            paths_found += 1

        return paths_found

    def process_transaction(self, tx: Transaction) -> float:
        # Constraint: Idempotency Check (Duplicate txId)
        if tx.tx_id in self.processed_txs:
            return self.processed_txs[tx.tx_id]

        # Evict history outside lookback window
        self._evict_expired(tx.timestamp)

        u, v = tx.from_user, tx.to_user

        # Handle Degenerate Self-Loops
        if u == v:
            score = 0.10
            self.processed_txs[tx.tx_id] = score
            return score

        # Evaluate topology PRIOR to inserting this transaction
        return_dist = self._find_shortest_path(v, u)
        return_paths = self._count_disjoint_paths(v, u) if return_dist else 0

        conv_paths = self._count_disjoint_paths(u, v) if return_paths == 0 else 0
        is_extension = (u in self.nodes) or (v in self.nodes)

        # Apply Principle-Based Scoring Model
        if return_paths > 0 and return_dist:
            # RETURN / LOOP CLOSURE (Example 4 & 5)
            # Tighter/shorter return loops + higher path counts increase signal strength
            proximity_factor = 1.0 / math.log2(return_dist + 1)
            raw_signal = 0.50 + (0.25 * proximity_factor) + (0.15 * (return_paths - 1))
        elif conv_paths > 0:
            # CONVERGENCE (Example 3)
            raw_signal = 0.30 + (0.12 * conv_paths)
        elif is_extension:
            # EXTENSION (Example 2)
            raw_signal = 0.18
        else:
            # ISOLATED TRANSACTION (Example 1)
            raw_signal = 0.05

        # Bounded scaling into [0.0, 1.0] range
        score = round(1.0 / (1.0 + math.exp(-4.0 * (raw_signal - 0.28))), 4)
        score = max(0.0, min(1.0, score))

        # Mutate streaming state
        self.adj[u][v] += 1
        self.nodes.add(u)
        self.nodes.add(v)
        self.history.append((tx.timestamp, tx))
        self.processed_txs[tx.tx_id] = score

        return score


engine = GraphRiskEngine(lookback_hours=24)

@app.route("/ghost-chains/transactions", methods=["POST"])
def evaluate_transactions():
    """Processes array of incoming transactions sequentially and returns risk scores."""
    payload = request.get_json()
    if not payload or "transactions" not in payload:
        return jsonify({"error": "Missing 'transactions' array in payload"}), 400

    results = []

    # Process batch in incoming order; preserve response order
    for item in payload["transactions"]:
        try:
            tx = Transaction.from_dict(item)
            score = engine.process_transaction(tx)
            results.append({"txId": tx.tx_id, "riskScore": score})
        except (KeyError, ValueError) as e:
            return jsonify({"error": f"Invalid transaction structure: {str(e)}"}), 400

    return jsonify({"results": results}), 200


@app.route("/ghost-chains/reset", methods=["POST"])
def reset_state():
    """Phase 1 Constraint: Fully resets graph engine state."""
    engine.reset()
    return jsonify({"clearTransactions": True }), 200
