"""
watchdog — synthetic application-level checker for the SRE lab.

Kubernetes tells you when a pod dies. It does not tell you when a pod is
alive, Ready, and returning 500s to every request. Scenario 05 is exactly
that case, so without this the hardest incident in the suite has no trigger
and the agent can only be invoked by hand.

This polls the service from outside the cluster, tracks a sliding window of
outcomes per route, and emits an Alert when the error ratio crosses a
threshold. Alerts are deduplicated by fingerprint so a persistent failure
produces one alert, not one per poll.

Run:
    python lab/watchdog/watchdog.py
    python lab/watchdog/watchdog.py --once --json
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE_URL = "http://localhost:30080"

# Routes to poll. /healthz and /readyz are deliberately included: when they
# stay green while a business route fails, that contrast is itself evidence,
# and it is what distinguishes an application bug from an outage.
ROUTES = ["/healthz", "/readyz", "/orders", "/orders/1001"]

WINDOW = 20          # samples retained per route
MIN_SAMPLES = 5      # don't alert before the window has substance
ERROR_THRESHOLD = 0.5
RESOLVE_THRESHOLD = 0.1
POLL_INTERVAL = 3.0
TIMEOUT = 3.0


@dataclasses.dataclass
class Alert:
    """The shape the agent consumes. Kubernetes-event alerts will use the
    same schema with source='kubernetes', so intake has one input type."""

    fingerprint: str
    source: str
    severity: str
    namespace: str
    workload: str
    reason: str
    summary: str
    route: str
    error_ratio: float
    sample_count: int
    first_seen: str
    last_seen: str
    evidence: dict

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2)


def fingerprint(namespace: str, workload: str, reason: str, route: str) -> str:
    """Stable dedup key.

    Deliberately excludes pod name and timestamp: one bad config affecting
    every replica must collapse into a single incident, or the agent opens
    one pull request per pod.
    """
    raw = f"{namespace}/{workload}/{reason}/{route}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def probe(base_url: str, route: str) -> tuple[int, str]:
    """Return (status_code, body_snippet). Connection failures map to 0."""
    req = urllib.request.Request(base_url + route, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


class Watchdog:
    def __init__(self, base_url: str, namespace: str, workload: str):
        self.base_url = base_url
        self.namespace = namespace
        self.workload = workload
        self.windows: dict[str, collections.deque] = {
            r: collections.deque(maxlen=WINDOW) for r in ROUTES
        }
        self.last_body: dict[str, str] = {}
        # fingerprint -> Alert, so a firing condition stays one incident
        self.active: dict[str, Alert] = {}

    def poll_once(self) -> list[Alert]:
        new_alerts = []
        for route in ROUTES:
            status, body = probe(self.base_url, route)
            failed = status == 0 or status >= 500
            self.windows[route].append(failed)
            if failed:
                self.last_body[route] = body
            new_alerts.extend(self._evaluate(route, status))
        return new_alerts

    def _evaluate(self, route: str, last_status: int) -> list[Alert]:
        window = self.windows[route]
        if len(window) < MIN_SAMPLES:
            return []

        ratio = sum(window) / len(window)
        reason = "HighErrorRate"
        fp = fingerprint(self.namespace, self.workload, reason, route)

        if ratio >= ERROR_THRESHOLD:
            if fp in self.active:
                # Already firing. Refresh, do not re-emit.
                self.active[fp].last_seen = now_iso()
                self.active[fp].error_ratio = round(ratio, 3)
                self.active[fp].sample_count = len(window)
                return []
            alert = Alert(
                fingerprint=fp,
                source="synthetic",
                severity="critical" if ratio >= 0.9 else "warning",
                namespace=self.namespace,
                workload=self.workload,
                reason=reason,
                summary=(
                    f"{route} returning errors "
                    f"({ratio:.0%} of last {len(window)} probes)"
                ),
                route=route,
                error_ratio=round(ratio, 3),
                sample_count=len(window),
                first_seen=now_iso(),
                last_seen=now_iso(),
                evidence={
                    "last_status_code": last_status,
                    "last_response_body": self.last_body.get(route, "")[:400],
                    "healthy_routes": [
                        r
                        for r in ROUTES
                        if r != route
                        and self.windows[r]
                        and sum(self.windows[r]) / len(self.windows[r]) < 0.1
                    ],
                },
            )
            self.active[fp] = alert
            return [alert]

        if ratio <= RESOLVE_THRESHOLD and fp in self.active:
            resolved = self.active.pop(fp)
            resolved.severity = "resolved"
            resolved.last_seen = now_iso()
            resolved.summary = f"{route} recovered"
            return [resolved]

        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--namespace", default="shop")
    ap.add_argument("--workload", default="orders-api")
    ap.add_argument("--once", action="store_true", help="one probe round, then exit")
    ap.add_argument("--json", action="store_true", help="emit raw alert JSON only")
    args = ap.parse_args()

    wd = Watchdog(args.base_url, args.namespace, args.workload)

    if args.once:
        # A single round cannot fill the window, so probe MIN_SAMPLES times.
        alerts = []
        for _ in range(MIN_SAMPLES):
            alerts.extend(wd.poll_once())
            time.sleep(0.3)
        for a in alerts:
            print(a.to_json())
        if not alerts and not args.json:
            print("no alerts: all routes within thresholds")
        return 0

    if not args.json:
        print(f"watchdog polling {args.base_url} every {POLL_INTERVAL}s")
        print(f"routes: {', '.join(ROUTES)}")
        print("ctrl-c to stop\n")

    try:
        while True:
            for alert in wd.poll_once():
                if args.json:
                    print(alert.to_json(), flush=True)
                else:
                    marker = "RESOLVED" if alert.severity == "resolved" else "FIRING"
                    print(
                        f"[{alert.last_seen}] {marker} {alert.fingerprint} "
                        f"{alert.summary}",
                        flush=True,
                    )
                    if alert.severity != "resolved":
                        print(
                            f"    last body: "
                            f"{alert.evidence['last_response_body'][:160]}",
                            flush=True,
                        )
                        print(
                            f"    still healthy: "
                            f"{', '.join(alert.evidence['healthy_routes']) or 'none'}",
                            flush=True,
                        )
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
