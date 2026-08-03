#!/usr/bin/env bash
# Create the kind cluster, build the app image, load it, deploy the healthy state.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for bin in kind kubectl docker; do
  command -v "$bin" >/dev/null || { echo "missing required binary: $bin"; exit 1; }
done

if ! kind get clusters 2>/dev/null | grep -qx sre-lab; then
  echo "==> creating kind cluster 'sre-lab'"
  kind create cluster --config "$LAB/kind-cluster.yaml"
else
  echo "==> cluster 'sre-lab' already exists"
fi

kubectl config use-context kind-sre-lab

echo "==> building orders-api:v1.0.0"
docker build -t orders-api:v1.0.0 "$LAB/app"

echo "==> loading image into cluster"
kind load docker-image orders-api:v1.0.0 --name sre-lab

echo "==> applying namespace and service"
kubectl apply -f "$LAB/scenarios/namespace.yaml"

"$LAB/scripts/heal.sh"
echo "==> lab ready. try: ./scripts/break.sh 01-oomkilled"
