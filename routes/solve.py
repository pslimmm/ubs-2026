import base64
import json
import logging

from flask import jsonify, request

from routes import app

logger = logging.getLogger(__name__)


@app.route('/solve', methods=['POST'])
def solve():
    data = request.get_json()
    logging.info("data sent for adaptation %s", data)

    decoded_payload = base64.b64decode(data["payload"]).decode("utf-8")
    adapt_input = json.loads(decoded_payload)["adaptInput"]

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

    logging.info("adapted result: %s", result)
    return jsonify(result)
