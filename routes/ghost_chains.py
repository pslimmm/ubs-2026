import heapq
import logging
import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set, Tuple

from flask import jsonify, request

from routes import app


logger = logging.getLogger(__name__)


BASELINE_RISK = 0.05
RETURN_PATH_WEIGHT = 2.0
SATURATION_SCALE = 6.0
IDENTITY_CONNECTED_WEIGHT = 0.16
IDENTITY_DISCONNECTED_WEIGHT = 0.05
IDENTITY_DIVERGENCE_WEIGHT = 0.10
IDENTITY_MISSING_WEIGHT = 0.12


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
        # Identity indexes are maintained independently for IP and device so
        # that either signal can contribute without masking the other.
        self.identity_user_values = {
            "ip": defaultdict(Counter),
            "device": defaultdict(Counter),
        }
        self.identity_value_users = {
            "ip": defaultdict(set),
            "device": defaultdict(set),
        }

    def reset(self):
        self.processed_txs.clear()
        self.history.clear()
        self.adj.clear()
        self.rev.clear()
        self.nodes.clear()
        self.latest_time = None
        self.sequence = 0
        for index in self.identity_user_values.values():
            index.clear()
        for index in self.identity_value_users.values():
            index.clear()

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

    def _identity_pairs(self, tx: Transaction):
        return (
            ("ip", tx.ip_address),
            ("device", tx.device_id),
        )

    def _remove_identity(self, tx: Transaction):
        for kind, value in self._identity_pairs(tx):
            if value is None or value == "":
                continue
            user_counts = self.identity_user_values[kind][tx.from_user]
            user_counts[value] -= 1
            if user_counts[value] <= 0:
                del user_counts[value]
                self.identity_value_users[kind][value].discard(tx.from_user)
                if not self.identity_value_users[kind][value]:
                    del self.identity_value_users[kind][value]
            if not user_counts:
                del self.identity_user_values[kind][tx.from_user]

    def _remove_active(self, tx: Transaction):
        self._remove_edge(tx.from_user, tx.to_user)
        self._remove_identity(tx)

    def _advance_window(self, timestamp: datetime) -> datetime:
        self.latest_time = max(self.latest_time or timestamp, timestamp)
        cutoff = self.latest_time - self.lookback
        # The active window is inclusive: a transaction exactly 24 hours old
        # is still within the most recent 24 hours. It expires once it is
        # strictly older than the cutoff.
        while self.history and self.history[0][0] < cutoff:
            _, _, expired = heapq.heappop(self.history)
            self._remove_active(expired)
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
        residual = defaultdict(dict)
        for source, targets in self.adj.items():
            for target_node in targets:
                residual[source][target_node] = 1
                residual[target_node].setdefault(source, 0)

        count = 0
        while count < limit:
            queue = deque([start])
            parent = {start: None}
            while queue and target not in parent:
                current = queue.popleft()
                for neighbor in sorted(residual[current]):
                    if residual[current][neighbor] and neighbor not in parent:
                        parent[neighbor] = current
                        queue.append(neighbor)
            if target not in parent:
                break
            current = target
            while parent[current] is not None:
                previous = parent[current]
                residual[previous][current] -= 1
                residual[current][previous] = residual[current].get(previous, 0) + 1
                current = previous
            count += 1
        return count

    def _structural_score(self, source: str, target: str) -> float:
        upstream = self._reachable(source, self.rev) | {source}
        downstream = self._reachable(target, self.adj) | {target}
        route_pairs = len(upstream) * len(downstream)
        parallel_edges = self.adj.get(source, {}).get(target, 0)

        if parallel_edges:
            impact = math.log2(1 + parallel_edges)
        else:
            alternative_origins = upstream & self._reachable(target, self.rev)
            alternative_pairs = len(alternative_origins) * len(downstream)
            impact = math.log2(route_pairs) + math.log2(1 + alternative_pairs)

            old_distance = self._shortest_path(source, target)
            if old_distance and old_distance > 1:
                impact += (1 - 1 / old_distance) * math.log2(1 + route_pairs)

        if source == target:
            impact += RETURN_PATH_WEIGHT
        else:
            return_distance = self._shortest_path(target, source)
            if return_distance:
                return_paths = self._count_disjoint_paths(target, source)
                impact += RETURN_PATH_WEIGHT * return_paths / return_distance

        score = 1 - (1 - BASELINE_RISK) * math.exp(-impact / SATURATION_SCALE)
        return round(score, 4)

    def _identity_score(self, tx: Transaction) -> float:
        """Return bounded identity evidence from the active graph.

        Connected reuse is the strongest signal. Reuse outside the current
        structural neighborhood is weaker, while a changed or missing value
        on a previously identified path is treated as an anomaly only when
        there is surrounding identity evidence.
        """
        upstream = self._reachable(tx.from_user, self.rev)
        downstream = self._reachable(tx.to_user, self.adj)
        connected_users = upstream | downstream | {tx.from_user, tx.to_user}
        identity_score = 0.0

        for kind, value in self._identity_pairs(tx):
            users_by_value = self.identity_value_users[kind]
            user_values = self.identity_user_values[kind]
            connected_values = {
                observed
                for user in connected_users
                for observed in user_values.get(user, {})
            }

            if value is None or value == "":
                # Missing identity is meaningful only if the connected
                # neighborhood has already carried this identity dimension.
                if connected_values:
                    identity_score += IDENTITY_MISSING_WEIGHT
                continue

            connected_matches = len(users_by_value.get(value, set()) & connected_users)
            disconnected_matches = len(users_by_value.get(value, set()) - connected_users)

            if connected_matches:
                identity_score += IDENTITY_CONNECTED_WEIGHT
            elif disconnected_matches:
                identity_score += IDENTITY_DISCONNECTED_WEIGHT

            # A value shift in a connected flow is stronger than a merely
            # shared value in an unrelated component.
            if connected_values and value not in connected_values:
                identity_score += IDENTITY_DIVERGENCE_WEIGHT

        return min(identity_score, 0.45)

    def _score(self, tx: Transaction) -> float:
        structural = self._structural_score(tx.from_user, tx.to_user)
        identity = self._identity_score(tx)
        # Treat identity as corroborating evidence rather than replacing the
        # structural score. This keeps scores bounded and preserves ordering
        # for Phase 1-only transactions.
        combined = 1 - (1 - structural) * (1 - identity)
        return round(min(max(combined, 0.0), 1.0), 4)

    def _add_active(self, tx: Transaction):
        self.adj[tx.from_user][tx.to_user] += 1
        self.rev[tx.to_user][tx.from_user] += 1
        self.nodes.update((tx.from_user, tx.to_user))
        for kind, value in self._identity_pairs(tx):
            if value is None or value == "":
                continue
            self.identity_user_values[kind][tx.from_user][value] += 1
            self.identity_value_users[kind][value].add(tx.from_user)
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
    logger.info(
        "ghost-chains request method=%s path=%s payload=%s",
        request.method,
        request.path,
        payload,
    )
    items = payload.get("transactions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        response = {"error": "Missing 'transactions' array in payload"}
        logger.warning(
            "ghost-chains response method=%s path=%s status=%s payload=%s",
            request.method,
            request.path,
            400,
            response,
        )
        return jsonify(response), 400

    results = []
    for item in items:
        try:
            tx = Transaction.from_dict(item)
            results.append({"txId": tx.tx_id, "riskScore": engine.process_transaction(tx)})
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            response = {"error": f"Invalid transaction structure: {error}"}
            logger.warning(
                "ghost-chains response method=%s path=%s status=%s payload=%s",
                request.method,
                request.path,
                400,
                response,
            )
            return jsonify(response), 400

    response = {"transactions": results}
    logger.info(
        "ghost-chains response method=%s path=%s status=%s payload=%s",
        request.method,
        request.path,
        200,
        response,
    )
    return jsonify(response), 200


@app.route("/ghost-chains/reset", methods=["POST"])
def reset_state():
    logger.info(
        "ghost-chains request method=%s path=%s payload=%s",
        request.method,
        request.path,
        request.get_json(silent=True),
    )
    engine.reset()
    response = {"clearTransactions": True}
    logger.info(
        "ghost-chains response method=%s path=%s status=%s payload=%s",
        request.method,
        request.path,
        200,
        response,
    )
    return jsonify(response), 200


@app.route("/ghost-chains/health", methods=["GET"])
def check_health():
    logger.info(
        "ghost-chains request method=%s path=%s payload=%s",
        request.method,
        request.path,
        None,
    )
    response = {"status": "ok"}
    logger.info(
        "ghost-chains response method=%s path=%s status=%s payload=%s",
        request.method,
        request.path,
        200,
        response,
    )
    return jsonify(response), 200
