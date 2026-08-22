import base64
import json
import logging
import math

from flask import jsonify, request

from routes import app

logger = logging.getLogger(__name__)
PRIORITIES = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def decode_payload(encoded_payload):
    if not isinstance(encoded_payload, str):
        raise ValueError("payload must be a Base64 string")

    encoded_payload = "".join(encoded_payload.split())
    encoded_payload += "=" * (-len(encoded_payload) % 4)
    decoded = base64.b64decode(
        encoded_payload, altchars=b"-_", validate=True
    ).decode("utf-8")
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("decoded payload must be an object")
    return payload


def adapt_output(adapt_input):
    priority = adapt_input["metadata"]["priority"].upper()
    return {
        "id": adapt_input["user"]["id"],
        "name": adapt_input["user"]["fullName"],
        "action": adapt_input["action"].lower(),
        "priority": PRIORITIES[priority],
    }


def slo_output(heartbeats, query):
    matching = [
        heartbeat
        for heartbeat in heartbeats
        if heartbeat["service"] == query["service"]
        and heartbeat["timestamp"] >= query["since"]
    ]
    if not matching:
        return {"availability": 0, "p95LatencyMs": 0}

    latencies = sorted(heartbeat["latencyMs"] for heartbeat in matching)
    ok_count = sum(
        heartbeat["status"].upper() == "OK" for heartbeat in matching
    )
    p95_position = math.ceil(0.95 * len(latencies))
    p95_latency_ms = latencies[p95_position - 1]
    logging.info(
        "SLO calculation service=%s since=%s matching=%d ok=%d "
        "sorted_latencies=%s p95_position=%d p95_latency_ms=%s",
        query["service"],
        query["since"],
        len(matching),
        ok_count,
        latencies,
        p95_position,
        p95_latency_ms,
    )
    return {
        "availability": ok_count / len(matching),
        "p95LatencyMs": p95_latency_ms,
    }


@app.route('/solve', methods=['POST'])
def solve():
    data = request.get_json(silent=True)
    logging.info("data sent for adaptation %s", data)

    try:
        decoded_payload = decode_payload(data["payload"])
        result = {"adaptOutput": adapt_output(decoded_payload["adaptInput"])}

        has_heartbeats = "heartbeats" in decoded_payload
        has_query = "sloQuery" in decoded_payload
        if has_heartbeats != has_query:
            raise ValueError("heartbeats and sloQuery must be provided together")
        if has_heartbeats:
            result["sloOutput"] = slo_output(
                decoded_payload["heartbeats"], decoded_payload["sloQuery"]
            )
    except (AttributeError, KeyError, TypeError, UnicodeDecodeError, ValueError) as error:
        logging.warning("invalid adaptation request: %s", error)
        return jsonify({"error": "Invalid request"}), 400

    logging.info("adapted result: %s", result)
    return jsonify(result)
