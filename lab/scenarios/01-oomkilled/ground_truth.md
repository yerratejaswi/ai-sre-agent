# 01-oomkilled

- **scenario_id:** 01-oomkilled
- **is_incident:** true
- **symptom:** pods reach Ready, then restart every ~40s with exit code 137
- **k8s_signals:** `lastState.terminated.reason=OOMKilled`, `exitCode=137`,
  restartCount climbing, container memory usage approaching the limit
- **log_signals:** repeated `heap grew to approximately N MiB` lines that stop
  abruptly with no error and no shutdown message
- **root_cause:** the container memory limit is 96Mi but `MEMORY_BALLAST_MB=512`
  causes the process to allocate ~512 MiB, so the kubelet OOMKills it
- **fix_location:** `lab/scenarios/01-oomkilled/deployment.yaml`, container
  `orders-api` — `resources.limits.memory` and/or the `MEMORY_BALLAST_MB` env var
- **correct_fix:** remove `MEMORY_BALLAST_MB` (the leak), or raise the memory
  limit above the working set. Removing the env var is the better answer;
  raising the limit treats the symptom.
- **distractor:** the liveness probe is also failing during the kill loop. An
  agent that blames the probe is wrong — probe failures are downstream of the OOM.
