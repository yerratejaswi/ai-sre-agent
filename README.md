# ai-sre-agent

An agent that investigates Kubernetes incidents the way an on-call engineer
would. An alert fires, it gathers evidence from the cluster, finds the code
behind the failure, and works out what went wrong.

**Status:** Phases 0 and 1 done. Phase 2 in progress.

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

I built the lab before the agent, on purpose. Without failures you can
reproduce on demand, there's no way to tell whether the agent is actually right
or just sounds right.

Six scenarios, each with a `ground_truth.md` answer key:

| ID | Failure |
|----|---------|
| `00-healthy` | nothing. The negative control. |
| `01-oomkilled` | memory limit set below what the app actually uses |
| `02-crashloop-missing-env` | a required env var got dropped |
| `03-bad-readiness-probe` | probe points at a port and path the app doesn't serve |
| `04-image-pull-error` | image tag that was never built |
| `05-unhandled-exception` | malformed config. Returns 500s, but the pods look fine. |

The app source never changes between scenarios. Every failure is injected
through the Deployment manifest, so the agent has to reason about runtime state
and configuration together instead of just diffing branches.

## How it finds the code

The obvious approach is to embed the whole repo and search it semantically. I
made that the last resort here rather than the first move, because vector
similarity over a stack trace mostly returns other code that looks like a stack
trace.

Three precise strategies run first:

1. **Resolve.** The workload declares its own repo through a Kubernetes
   annotation, so there's no guessing from service names.
2. **Traceback.** If the logs have a stack trace, it already names the file,
   line, and function. Read exactly those lines.
3. **Manifest vs. source.** Compare what the config declares against what the
   code actually implements.

Vector search only runs when all three miss.

Three of the six scenarios currently solve with no LLM involved at all.
`03-bad-readiness-probe` is the clearest one: pods running, logs clean, nothing
crashed. The bug only exists in the gap between the manifest and the source, and
each file is perfectly correct on its own.

## Roadmap

- **Phase 0, lab** ✅
- **Phase 1, collector** ✅ cluster evidence into an `IncidentContext`
- **Phase 2, retrieval** 🟡 three of four strategies done
- **Phase 3, root cause** ⬜ structured diagnosis with cited evidence
- **Phase 4, remediation PR** ⬜ patch on a branch, run tests, open a PR
- **Phase 5, evals** ⬜ accuracy, false positives, patch correctness
