import heapq
import logging
from datetime import datetime, timezone

from flask import jsonify, request

from routes import app

logger = logging.getLogger(__name__)


@app.route('/kan-cheong-delivery-driver', methods=['POST'])
def kan_cheong_delivery_driver():
    """Return the fastest route for every independent case in the request batch."""
    batch = request.get_json()
    logging.info("data sent for evaluation %s", batch)
    return jsonify({case_id: solve_case(data) for case_id, data in batch.items()})


def solve_case(data):
    if data["start_coordinate"] == data["end_coordinate"]:
        return {
            "total_duration_sec": 0,
            "arrival_time": data["start_time"],
            "path": [],
        }

    src = tuple(data["start_coordinate"])
    end = tuple(data["end_coordinate"])
    time_sec = parse_time(data["start_time"])

    graph = build_graph(data["nodes"], data["edges"])
    obstructions = data["obstructions"]

    obstruction_end_times = []
    for obs in obstructions:
        t_end = parse_time(obs["end_time"])
        if t_end is not None:
            obstruction_end_times.append(t_end)
    dynamic_until = max(obstruction_end_times, default=time_sec)

    # Priority queue stores: (curr_time, curr_node, path_edges)
    pq = [(time_sec, src, [])]

    # Before all obstructions end, later visits to the same node can be useful:
    # cycling is the only legal way to consume time. Once the network is static,
    # the usual earliest-arrival dominance rule is safe again.
    dynamic_labels = set()
    static_best = {}

    while pq:
        curr_time, curr_node, path = heapq.heappop(pq)

        if curr_node == end:
            return {
                "total_duration_sec": int(round(curr_time - time_sec)),
                "arrival_time": parse_time(int(round(curr_time)), iso_to_sec=False),
                "path": path,
            }

        if curr_time < dynamic_until:
            label = (curr_node, round(curr_time, 9))
            if label in dynamic_labels:
                continue
            dynamic_labels.add(label)
        else:
            if static_best.get(curr_node, float("inf")) <= curr_time:
                continue
            static_best[curr_node] = curr_time

        if curr_node not in graph:
            continue

        for edge_id, neighbor, base_duration_sec in graph[curr_node]:
            arrival = get_travel_time(
                edge_id, curr_node, neighbor, curr_time, base_duration_sec, obstructions
            )
            if arrival is not None:
                heapq.heappush(pq, (arrival, neighbor, path + [edge_id]))

    return {
        "total_duration_sec": None,
        "arrival_time": None,
        "path": [],
    }


def build_graph(nodes, edges):
    graph = {}
    for node in nodes:
        graph[tuple(node)] = []

    for edge in edges:
        edge_id = edge["edge_id"]
        node1 = tuple(edge["node1"])
        node2 = tuple(edge["node2"])
        base_duration = edge["base_duration_sec"]
        graph[node1].append((edge_id, node2, base_duration))
        graph[node2].append((edge_id, node1, base_duration))

    return graph


def get_travel_time(edge_id, u, v, t_start, base_duration, obstructions):
    # Filter and pre-parse obstructions for this edge in this direction
    matching_obs = []
    for obs in obstructions:
        if (
            obs["edge_id"] == edge_id
            and tuple(obs["edge"]["from"]) == u
            and tuple(obs["edge"]["to"]) == v
        ):
            t_start_parsed = parse_time(obs["start_time"])
            t_end_parsed = parse_time(obs["end_time"])
            if t_start_parsed is not None and t_end_parsed is not None:
                matching_obs.append(
                    {
                        "start": t_start_parsed,
                        "end": t_end_parsed,
                        "speed_factor": obs["speed_factor"],
                    }
                )

    def speed_factor_at(time):
        return min(
            (
                obs["speed_factor"]
                for obs in matching_obs
                if obs["start"] <= time < obs["end"]
            ),
            default=1.0,
        )

    if speed_factor_at(t_start) == 0.0:
        return None
    if base_duration == 0:
        return t_start

    # Collect all transition times for this edge after t_start
    transitions = {t_start}
    for obs in matching_obs:
        if obs["start"] > t_start:
            transitions.add(obs["start"])
        if obs["end"] > t_start:
            transitions.add(obs["end"])

    T = sorted(list(transitions))
    T.append(float("inf"))

    # Simulate traversal using remaining base duration
    remaining_duration = float(base_duration)
    curr_time = t_start

    for i in range(len(T) - 1):
        t_curr, t_next = T[i], T[i + 1]
        sf = speed_factor_at(t_curr)

        if sf == 0.0:
            curr_time = t_next
            continue

        if t_next == float("inf"):
            # We must finish in this final, obstruction-free interval
            return curr_time + remaining_duration / sf

        dt = t_next - t_curr
        max_progress = dt * sf  # Max base duration we can cover in this interval

        if remaining_duration <= max_progress:
            # We finish during this interval
            return curr_time + remaining_duration / sf

        remaining_duration -= max_progress
        curr_time = t_next

    return None


def parse_time(time, iso_to_sec=True):
    if iso_to_sec:
        try:
            return int(datetime.fromisoformat(time).timestamp())
        except:
            return None
    else:
        try:
            return (
                datetime.fromtimestamp(time, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except:
            return None
