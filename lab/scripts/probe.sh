#!/usr/bin/env bash
# Hit the service from inside the cluster. Some scenarios only show up here.
set -euo pipefail
kubectl run curl-probe -n shop --rm -it --restart=Never --image=curlimages/curl:8.8.0 -- \
  sh -c 'for p in /healthz /orders /orders/1001; do echo "GET $p"; curl -s -o /dev/null -w "  %{http_code}\n" http://orders-api.shop.svc:8080$p; done'
