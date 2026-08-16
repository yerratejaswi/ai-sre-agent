# ai-sre-agent

An agent that investigates Kubernetes incidents and works toward a root cause —
the way an on-call engineer would.

An alert fires. It collects evidence from the cluster, finds the code behind
the failure, and diagnoses what went wrong.

**Status:** Phases 0 and 1 complete. Phase 2 in progress.

## Requirements

`docker`, `kind`, `kubectl`.

## Quick start

```bash
./lab/scripts/setup.sh                            # cluster + app (~3 min)
./lab/scripts/status.sh                           # everything looks fine
./lab/scripts/break.sh 03-bad-readiness-probe     # introduce a failure
./lab/scripts/status.sh                           # now it doesn't
./lab/scripts/heal.sh                             # back to baseline
```

Collect an incident snapshot:

```bash
python -m agent.cli collect --save 03-bad-readiness-probe
```

## The lab

The lab comes first on purpose. Without reproducible failures there's no way to
tell whether the agent is right or just fluent.

Six scenarios, each with a `ground_truth.md` answer key:

| ID | Failure |
|----|---------|
| `00-healthy` | nothing — the negative
