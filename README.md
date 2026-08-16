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
| `00-healthy` | nothing — the negative control |
| `01-oomkilled` | memory limit below working set |
| `02-crashloop-missing-env` | required env var dropped |
| `03-bad-readiness-probe` | probe points at a port and path the app doesn't serve |
| `04-image-pull-error` | image tag that was never built |
| `05-unhandled-exception` | malformed config — 500s, but pods look healthy |

The app source never changes between scenarios. Every failure is injected
through the Deployment manifest, so the agent has to reason about runtime state
and configuration together.

## How it finds the code

The obvious approach is to embed the whole repo and search it semantically.
That's deliberately the last resort here, not the first move.

Three precise strategies run first:

1. **Resolve** — the workload declares its own repo via a Kubernetes
   annotation. No guessing from service names.
2. **Traceback** — if the logs have a stack trace, it already names the file,
   line, and function. Read exactly those lines.
3. **Manifest vs. source** — compare what the config declares against what the
   code actually implements.

Vector search only runs when all three miss.

Three of six scenarios currently solve with no LLM involved.
`03-bad-readiness-probe` is the clearest case: pods running, logs clean,
nothing crashed. The bug exists only in the gap between the manifest and the
source.

## Roadmap

- **Phase 0 — lab** ✅
- **Phase 1 — collector** ✅ cluster evidence into an `IncidentContext`
- **Phase 2 — retrieval** 🟡 three of four strategies done
- **Phase 3 — root cause** ⬜ structured diagnosis with cited evidence
- **Phase 4 — remediation PR** ⬜ patch on a branch, run tests, open a PR
- **Phase 5 — evals** ⬜ accuracy, false positives, patch correctness
