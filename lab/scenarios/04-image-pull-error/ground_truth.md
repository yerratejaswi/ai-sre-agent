# 04-image-pull-error

- **scenario_id:** 04-image-pull-error
- **is_incident:** true
- **symptom:** new pods stuck Pending with `ErrImagePull` then
  `ImagePullBackOff`; old ReplicaSet still serving, so no user-visible outage yet
- **k8s_signals:** `Failed to pull image "orders-api:v1.4.0"`, `waiting.reason=
  ImagePullBackOff`, containerStatuses show no started time
- **log_signals:** none — the container never started, so there are no logs
- **root_cause:** the Deployment references image tag `v1.4.0`, which was never
  built or loaded into the cluster; only `v1.0.0` exists
- **fix_location:** `lab/scenarios/04-image-pull-error/deployment.yaml`,
  `spec.template.spec.containers[0].image`
- **correct_fix:** roll back to a tag that exists (`v1.0.0`), or build and load
  the missing tag
- **tests_the_agent_for:** recognising that an empty log set is itself evidence,
  and that this is a deploy-time failure rather than a runtime one. Also tests
  whether the agent proposes `kubectl rollout undo` as an immediate mitigation
  separate from the code fix.
