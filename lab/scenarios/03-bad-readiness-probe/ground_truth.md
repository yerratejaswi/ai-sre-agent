# 03-bad-readiness-probe

- **scenario_id:** 03-bad-readiness-probe
- **is_incident:** true
- **symptom:** pods are Running with 0/1 Ready; rollout never completes; the
  Service has no endpoints, so traffic 503s at the edge
- **k8s_signals:** `Warning Unhealthy ... Readiness probe failed: connection
  refused`, restartCount 0, exit codes absent, `kubectl get endpoints
  orders-api -n shop` returns none
- **log_signals:** application logs are clean — the process is healthy and
  serving. There is no error to find in the logs.
- **root_cause:** the readiness probe targets port 8081 and path `/ready`, but
  the container listens on 8080 and exposes `/readyz`
- **fix_location:** `lab/scenarios/03-bad-readiness-probe/deployment.yaml`,
  `readinessProbe.httpGet`
- **correct_fix:** point the probe at path `/readyz` on port 8080
- **tests_the_agent_for:** cross-checking manifest against source. The answer is
  only visible by comparing the probe config to the routes defined in
  `lab/app/main.py`. This is the scenario RAG over the repo actually earns its
  keep on — logs alone cannot solve it.
