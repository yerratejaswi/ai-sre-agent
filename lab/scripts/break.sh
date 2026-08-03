#!/usr/bin/env bash
# Apply a failure scenario. Usage: ./break.sh 01-oomkilled
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO="${1:-}"

if [[ -z "$SCENARIO" || ! -d "$LAB/scenarios/$SCENARIO" ]]; then
  echo "usage: $0 <scenario>"
  echo "available:"
  find "$LAB/scenarios" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort | sed 's/^/  /'
  exit 1
fi

echo "==> applying $SCENARIO"
kubectl apply -f "$LAB/scenarios/$SCENARIO/deployment.yaml"
echo "==> waiting 45s for the failure to materialise..."
sleep 45
"$LAB/scripts/status.sh"
echo
echo "ground truth is in scenarios/$SCENARIO/ground_truth.md — don't peek before the agent answers"
