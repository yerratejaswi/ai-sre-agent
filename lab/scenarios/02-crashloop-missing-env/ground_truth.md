# 02-crashloop-missing-env

- **scenario_id:** 02-crashloop-missing-env
- **is_incident:** true
- **symptom:** pods never reach Ready; status cycles Error -> CrashLoopBackOff
- **k8s_signals:** `exitCode=1`, `reason=Error`, backoff intervals doubling,
  zero successful probe results, no OOM signals
- **log_signals:** a single line before exit — `DATABASE_URL is not set;
  refusing to start` — visible only via `kubectl logs --previous`
- **root_cause:** the `DATABASE_URL` env var was dropped from the container
  spec; `lab/app/main.py` calls `sys.exit(1)` at import time when it is absent
- **fix_location:** `lab/scenarios/02-crashloop-missing-env/deployment.yaml`,
  `spec.template.spec.containers[0].env`
- **correct_fix:** restore the `DATABASE_URL` env var (ideally sourced from a
  Secret rather than inlined)
- **tests_the_agent_for:** using `logs --previous`. Current-container logs are
  empty here, so an agent that only calls `kubectl logs` will have nothing to
  reason from and should say so rather than guess.
