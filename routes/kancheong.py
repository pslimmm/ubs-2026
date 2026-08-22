import json
import logging
import heapq
import bisect
from datetime import datetime, timezone
from flask import request

from routes import app

logger = logging.getLogger(__name__)

@app.route('/kan-cheong-delivery-driver', methods=['POST'])
def kan_cheong_delivery_driver():
    """Accept a JSON request string and return a JSON response string with the fastest route."""
    data = request.get_json()
    logging.info("data sent for evaluation {}".format(data))
    if data["start_coordinate"] == data["end_coordinate"]:
        return json.dumps(
            {
                "total_duration_sec": 0,
                "arrival_time": data["start_time"],
                "path": [],
            }
        )

    src = tuple(data["start_coordinate"])
    end = tuple(data["end_coordinate"])
    time_sec = parse_time(data["start_time"])

    graph = build_graph(data["nodes"], data["edges"])
    obstructions = data["obstructions"]

    # Collect all unique obstruction start and end times that are after start_time
    transition_times = {time_sec}
    for obs in obstructions:
        t_start = parse_time(obs["start_time"])
        t_end = parse_time(obs["end_time"])
        if t_start is not None and t_start > time_sec:
            transition_times.add(t_start)
        if t_end is not None and t_end > time_sec:
            transition_times.add(t_end)

    partitions = sorted(list(transition_times))

    # Priority queue stores: (curr_time, curr_node, path_edges)
    pq = [(time_sec, src, [])]

    # visited maps node -> {interval_index: min_arrival_time}
    visited = {}

    while pq:
        curr_time, curr_node, path = heapq.heappop(pq)

        if curr_node == end:
            return json.dumps(
                {
                    "total_duration_sec": int(round(curr_time - time_sec)),
                    "arrival_time": parse_time(int(round(curr_time)), iso_to_sec=False),
                    "path": path,
                }
            )

        interval_index = get_partition_index(partitions, curr_time)

        if curr_node in visited:
            if (
                interval_index in visited[curr_node]
                and visited[curr_node][interval_index] <= curr_time
            ):
                continue
        else:
            visited[curr_node] = {}

        visited[curr_node][interval_index] = curr_time

        if curr_node not in graph:
            continue

        for edge_id, neighbor, base_duration_sec in graph[curr_node]:
            arrival = get_travel_time(
                edge_id, curr_node, neighbor, curr_time, base_duration_sec, obstructions
            )
            if arrival is not None:
                heapq.heappush(pq, (arrival, neighbor, path + [edge_id]))

    return json.dumps(
        {
            "total_duration_sec": None,
            "arrival_time": None,
            "path": [],
        }
    )


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


def get_partition_index(partitions, t):
    # Find the interval [partitions[i], partitions[i+1]) containing t
    idx = bisect.bisect_right(partitions, t) - 1
    return max(0, idx)


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
                matching_obs.append({
                    "start": t_start_parsed,
                    "end": t_end_parsed,
                    "speed_factor": obs["speed_factor"]
                })

    if base_duration == 0:
        # Check if it is blocked at t_start
        active_sfs = [
            obs["speed_factor"] for obs in matching_obs
            if obs["start"] <= t_start <= obs["end"]
        ]
        return None if (active_sfs and min(active_sfs) == 0.0) else t_start

    # Helper to determine active speed factor in the interval [t_curr, t_next)
    def get_speed_factor(t_curr, t_next):
        active_sfs = [
            obs["speed_factor"] for obs in matching_obs
            if obs["start"] <= t_curr and (obs["end"] >= t_next if t_next != float("inf") else obs["end"] > t_curr)
        ]
        return min(active_sfs) if active_sfs else 1.0

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
        sf = get_speed_factor(t_curr, t_next)

        if sf == 0.0:
            return None  # Traversal is blocked

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
