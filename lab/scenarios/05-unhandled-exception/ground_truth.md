# 05-unhandled-exception

- **scenario_id:** 05-unhandled-exception
- **is_incident:** true
- **symptom:** every request to `/orders/<id>` returns 500; `/healthz`,
  `/readyz`, and `/orders` are fine. Pods are 2/2 Ready with zero restarts.
- **k8s_signals:** none. This is the point of the scenario — `kubectl get pods`
  is completely clean.
- **log_signals:** repeated tracebacks ending in
  `json.decoder.JSONDecodeError`, raised from `load_feature_flags` in
  `/app/main.py`, logged by the `handle_error` handler
- **root_cause:** `FEATURE_FLAGS` is set to malformed JSON
  (`{"enrich_region": true, "beta_pricing": }` — trailing key with no value).
  `load_feature_flags()` in `lab/app/main.py` calls `json.loads` without a
  guard, and `get_order` calls it on every request.
- **fix_location:** two valid answers —
  1. `lab/scenarios/05-unhandled-exception/deployment.yaml`, the
     `FEATURE_FLAGS` env value (the immediate cause)
  2. `lab/app/main.py`, `load_feature_flags` (the latent defect: config
     parsing should fail closed at startup, not per-request)
- **correct_fix:** repair the JSON. A strong diagnosis also proposes parsing
  and validating `FEATURE_FLAGS` once at import time so bad config fails the
  rollout instead of silently 500ing in production.
- **tests_the_agent_for:** the full pipeline. Cluster state gives nothing; the
  answer requires reading the traceback, retrieving `load_feature_flags` from
  the repo, and connecting it back to an env var in the manifest.
