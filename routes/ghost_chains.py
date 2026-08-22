import heapq
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set, Tuple

from flask import jsonify, request

from routes import app


@dataclass(frozen=True)
class Transaction:
    tx_id: str
    from_user: str
    to_user: str
    amount: float
    timestamp: datetime
    ip_address: Optional[str] = None
    device_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data) -> "Transaction":
        timestamp = datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return cls(
            tx_id=str(data["txId"]),
            from_user=str(data["fromUserId"]),
            to_user=str(data["toUserId"]),
            amount=float(data["amount"]),
            timestamp=timestamp,
            ip_address=data.get("ipAddress"),
            device_id=data.get("deviceId"),
        )

    @property
    def signature(self) -> Tuple:
        return (
            self.from_user,
            self.to_user,
            self.amount,
            self.timestamp,
            self.ip_address,
            self.device_id,
        )


class GraphRiskEngine:
    def __init__(self, lookback_hours: int = 24):
        self.lookback = timedelta(hours=lookback_hours)
        self.processed_txs: Dict[str, Tuple[Tuple, float]] = {}
        self.history = []
        self.adj = defaultdict(lambda: defaultdict(int))
        self.rev = defaultdict(lambda: defaultdict(int))
        self.nodes: Set[str] = set()
        self.latest_time: Optional[datetime] = None
        self.sequence = 0

    def reset(self):
        self.processed_txs.clear()
        self.history.clear()
        self.adj.clear()
        self.rev.clear()
        self.nodes.clear()
        self.latest_time = None
        self.sequence = 0

    def _remove_edge(self, source: str, target: str):
        for graph, left, right in (
            (self.adj, source, target),
            (self.rev, target, source),
        ):
            graph[left][right] -= 1
            if graph[left][right] == 0:
                del graph[left][right]
            if not graph[left]:
                del graph[left]

    def _advance_window(self, timestamp: datetime) -> datetime:
        self.latest_time = max(self.latest_time or timestamp, timestamp)
        cutoff = self.latest_time - self.lookback
        while self.history and self.history[0][0] < cutoff:
            _, _, expired = heapq.heappop(self.history)
            self._remove_edge(expired.from_user, expired.to_user)
        self.nodes = set(self.adj) | {
            target for targets in self.adj.values() for target in targets
        }
        return cutoff

    def _shortest_path(self, start: str, target: str) -> Optional[int]:
        queue = deque([(start, 0)])
        visited = {start}
        while queue:
            current, distance = queue.popleft()
            if current == target and distance:
                return distance
            for neighbor in self.adj.get(current, {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, distance + 1))
        return None

    @staticmethod
    def _reachable(start: str, graph) -> Set[str]:
        queue = deque([start])
        visited = {start}
        while queue:
            current = queue.popleft()
            for neighbor in graph.get(current, {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        visited.remove(start)
        return visited

    def _count_disjoint_paths(self, start: str, target: str, limit: int = 5) -> int:
        residual = {source: set(targets) for source, targets in self.adj.items()}
        count = 0
        while count < limit:
            queue = deque([start])
            parent = {start: None}
            while queue and target not in parent:
                current = queue.popleft()
                for neighbor in residual.get(current, set()):
                    if neighbor not in parent:
                        parent[neighbor] = current
                        queue.append(neighbor)
            if target not in parent:
                break
            current = target
            while parent[current] is not None:
                previous = parent[current]
                residual[previous].remove(current)
                current = previous
            count += 1
        return count

    def _structural_score(self, source: str, target: str) -> float:
        if source == target:
            return 0.8

        return_distance = self._shortest_path(target, source)
        common_origins = len(
            self._reachable(source, self.rev) & self._reachable(target, self.rev)
        )

        if return_distance is not None:
            return_paths = self._count_disjoint_paths(target, source)
            signal = (
                0.58
                + 0.10 / return_distance
                + 0.08 * min(common_origins, 3)
                + 0.08 * min(return_paths - 1, 2)
            )
        elif common_origins:
            signal = 0.32 + 0.06 * min(common_origins, 3)
        elif source in self.nodes or target in self.nodes:
            signal = 0.18
        else:
            signal = 0.05
        return round(min(signal, 1.0), 4)

    def _score(self, tx: Transaction) -> float:
        """Extension point for later identity and value signal phases."""
        return self._structural_score(tx.from_user, tx.to_user)

    def _add_active(self, tx: Transaction):
        self.adj[tx.from_user][tx.to_user] += 1
        self.rev[tx.to_user][tx.from_user] += 1
        self.nodes.update((tx.from_user, tx.to_user))
        heapq.heappush(self.history, (tx.timestamp, self.sequence, tx))
        self.sequence += 1

    def process_transaction(self, tx: Transaction) -> float:
        previous = self.processed_txs.get(tx.tx_id)
        if previous is not None:
            signature, score = previous
            if signature != tx.signature:
                raise ValueError(f"txId '{tx.tx_id}' was reused with different data")
            return score

        cutoff = self._advance_window(tx.timestamp)
        score = self._score(tx)
        if tx.timestamp >= cutoff:
            self._add_active(tx)
        self.processed_txs[tx.tx_id] = (tx.signature, score)
        return score


engine = GraphRiskEngine()


@app.route("/ghost-chains/transactions", methods=["POST"])
def evaluate_transactions():
    payload = request.get_json(silent=True)
    items = payload.get("transactions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return jsonify({"error": "Missing 'transactions' array in payload"}), 400

    results = []
    for item in items:
        try:
            tx = Transaction.from_dict(item)
            results.append({"txId": tx.tx_id, "riskScore": engine.process_transaction(tx)})
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            return jsonify({"error": f"Invalid transaction structure: {error}"}), 400
    return jsonify({"transactions": results}), 200


@app.route("/ghost-chains/reset", methods=["POST"])
def reset_state():
    engine.reset()
    return jsonify({"clearTransactions": True}), 200


@app.route("/ghost-chains/health", methods=["GET"])
def check_health():
    return jsonify({"status": "ok"}), 200
