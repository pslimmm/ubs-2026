import base64
import json
import logging
import math

from flask import jsonify, request

from routes import app

logger = logging.getLogger(__name__)


@app.route('/solve', methods=['POST'])
def solve():
    data = request.get_json()
    logging.info("data sent for adaptation %s", data)

    decoded_payload = json.loads(
        base64.b64decode(data["payload"]).decode("utf-8")
    )
    adapt_input = decoded_payload["adaptInput"]

    result = {
        "adaptOutput": {
            "id": adapt_input["user"]["id"],
            "name": adapt_input["user"]["fullName"],
            "action": adapt_input["action"].lower(),
            "priority": {
                "LOW": 1,
                "MEDIUM": 2,
                "HIGH": 3,
            }[adapt_input["metadata"]["priority"]],
        }
    }

    if "heartbeats" in decoded_payload and "sloQuery" in decoded_payload:
        query = decoded_payload["sloQuery"]
        heartbeats = [
            heartbeat
            for heartbeat in decoded_payload["heartbeats"]
            if heartbeat["service"] == query["service"]
            and heartbeat["timestamp"] >= query["since"]
        ]
        latencies = sorted(heartbeat["latencyMs"] for heartbeat in heartbeats)

        result["sloOutput"] = {
            "availability": (
                sum(heartbeat["status"] == "OK" for heartbeat in heartbeats)
                / len(heartbeats)
                if heartbeats
                else 0
            ),
            "p95LatencyMs": (
                latencies[math.ceil(0.95 * len(latencies)) - 1]
                if latencies
                else 0
            ),
        }

    logging.info("adapted result: %s", result)
    return jsonify(result)
