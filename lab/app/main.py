"""
orders-api — a deliberately fragile demo service.

Every failure mode this app can exhibit is controlled by an environment
variable, so a scenario is just a Deployment manifest with different env.
The application code itself never changes between scenarios. That matters:
it means the agent has to reason about config + runtime state, not about
which branch of the repo it is looking at.
"""

import json
import logging
import os
import sys
import threading
import time

from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("orders-api")

app = Flask(__name__)

# --- startup configuration -------------------------------------------------
# DATABASE_URL is required. Omitting it kills the process at import time,
# which surfaces as CrashLoopBackOff rather than a failing readiness probe.

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    log.error("DATABASE_URL is not set; refusing to start")
    sys.exit(1)

log.info("connecting to database at %s", DATABASE_URL.split("@")[-1])

# FEATURE_FLAGS is optional and parsed lazily. A malformed value does not
# stop the process — it produces 500s on one endpoint while the pod stays
# Ready. This is the hardest scenario for the agent: no crash, no restart,
# nothing in `kubectl get pods` to point at.
RAW_FEATURE_FLAGS = os.environ.get("FEATURE_FLAGS", "{}")

# MEMORY_BALLAST_MB simulates a leak. Set it above the container memory
# limit and the kubelet OOMKills the container.
BALLAST_MB = int(os.environ.get("MEMORY_BALLAST_MB", "0"))

_ballast = []


def _grow_ballast():
    """Allocate BALLAST_MB over ~20s so the pod becomes Ready before it dies."""
    for i in range(BALLAST_MB):
        _ballast.append(bytearray(1024 * 1024))
        if i % 16 == 0:
            log.info("heap grew to approximately %d MiB", i)
        time.sleep(0.08)


if BALLAST_MB:
    log.warning("MEMORY_BALLAST_MB=%d; allocating in background", BALLAST_MB)
    threading.Thread(target=_grow_ballast, daemon=True).start()


# --- fake data -------------------------------------------------------------

ORDERS = {
    "1001": {"id": "1001", "customer": "acme", "total": 42.50, "region": "us-east"},
    "1002": {"id": "1002", "customer": "globex", "total": 118.00, "region": "eu-west"},
    "1003": {"id": "1003", "customer": "initech", "total": 7.25, "region": "us-east"},
}


def load_feature_flags():
    """Parse FEATURE_FLAGS on every request.

    Deliberately unguarded: a malformed value raises here and propagates
    as a 500. The traceback names this function and this file, which is
    the hook the retrieval layer is meant to find.
    """
    return json.loads(RAW_FEATURE_FLAGS)


# --- endpoints -------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return jsonify(status="ok"), 200


@app.get("/readyz")
def readyz():
    return jsonify(status="ready"), 200


@app.get("/orders")
def list_orders():
    return jsonify(orders=list(ORDERS.values())), 200


@app.get("/orders/<order_id>")
def get_order(order_id):
    flags = load_feature_flags()
    order = ORDERS.get(order_id)
    if order is None:
        return jsonify(error="order not found", order_id=order_id), 404
    if flags.get("enrich_region"):
        order = dict(order, region_name=REGION_NAMES[order["region"]])
    return jsonify(order=order), 200


REGION_NAMES = {"us-east": "US East (N. Virginia)", "eu-west": "EU West (Ireland)"}


@app.errorhandler(Exception)
def handle_error(exc):
    log.exception("unhandled exception serving %s", request.path)
    return jsonify(error=type(exc).__name__, message=str(exc)), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log.info("orders-api listening on :%d", port)
    app.run(host="0.0.0.0", port=port)
