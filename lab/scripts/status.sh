#!/usr/bin/env bash
# Everything a human SRE would glance at first.
set -euo pipefail
NS=shop
echo "--- pods ---"
kubectl get pods -n "$NS" -o wide
echo
echo "--- endpoints ---"
kubectl get endpoints orders-api -n "$NS"
echo
echo "--- recent events ---"
kubectl get events -n "$NS" --sort-by=.lastTimestamp | tail -15
echo
echo "--- container states ---"
kubectl get pods -n "$NS" -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].restartCount}{" restarts\t"}{.status.containerStatuses[0].state}{"\n"}{end}' 2>/dev/null || true
