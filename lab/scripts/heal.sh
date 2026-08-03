#!/usr/bin/env bash
# Return the cluster to the healthy baseline.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "==> restoring healthy deployment"
kubectl apply -f "$LAB/scenarios/00-healthy/deployment.yaml"
kubectl rollout status deployment/orders-api -n shop --timeout=120s || true
