# 00-healthy

- **scenario_id:** 00-healthy
- **is_incident:** false
- **expected_state:** all replicas Ready, zero restarts
- **symptom:** none
- **root_cause:** none

## Why this exists

The agent must not invent a root cause when nothing is wrong. This is the
negative control: any non-empty diagnosis counts as a false positive.
