# ai-sre-agent

An autonomous agent that diagnoses Kubernetes incidents and opens remediation
pull requests. This repo currently contains **Phase 0: the lab** — a cluster
that breaks on demand, with labelled ground truth for every failure.

The lab comes first deliberately. Without reproducible incidents there is no
way to measure whether the agent is right, and an SRE agent you cannot measure
is a demo, not a system.

## Requirements

`docker`, `kind`, `kubectl`, and `make`.

## Quick start

```bash
make setup                  # cluster + image + healthy deployment (~3 min)
make status                 # everything looks fine

make break S=01-oomkilled   # introduce a failure
make status                 # now it doesn't

make heal                   # back to baseline
make scenarios              # list all failures
make teardown               # delete the cluster
```

## The scenarios

| ID | Failure | Visible in `kubectl get pods`? | Solvable from logs alone? |
|----|---------|-------------------------------|---------------------------|
| `00-healthy` | none (negative control) | — | — |
| `01-oomkilled` | memory limit below working set | yes | partly |
| `02-crashloop-missing-env` | required env var dropped | yes | only via `--previous` |
| `03-bad-readiness-probe` | probe points at wrong port/path | partly | no |
| `04-image-pull-error` | image tag that was never built | yes | no logs exist |
| `05-unhandled-exception` | malformed config, 500s only | **no** | yes, plus source |

The spread is the point. `01` is easy and tests basic signal reading. `03` can
only be solved by comparing the manifest against the routes in the source, so
it justifies retrieval over the repo. `05` is the hardest: the cluster looks
perfectly healthy, and the answer requires a traceback plus the function it
names plus an env var three files away.

Each scenario directory holds a `ground_truth.md` with the symptom, the
signals, the root cause, the fix location, and — where relevant — the
distractor the agent is likely to fall for. Treat those files as the answer
key: they become the eval fixtures in Phase 5, so don't let the agent's own
output edit them.

## Design notes

The application source never changes between scenarios. Every failure is
introduced through the Deployment manifest, and the demo app translates env
vars into failure modes. This keeps the agent honest — it has to reason about
runtime state and configuration together, rather than diffing branches.

`05-unhandled-exception` has two defensible fix locations: the malformed env
value (immediate cause) and the unguarded `json.loads` in
`load_feature_flags` (latent defect). A good diagnosis names both and says
which one it is patching. Grade for that in Phase 5.

## What comes next

- **Phase 1 — collector.** Normalise pod status, exit codes, `logs --previous`,
  namespace events, resource usage, and recent rollouts into an
  `IncidentContext`. No LLM involved.
- **Phase 2 — retrieval.** AST-chunked index over the repo. Extract file paths
  and symbols from tracebacks for direct lookup; use vector search only to pull
  in callers and config.
- **Phase 3 — root cause.** One structured call returning hypothesis,
  confidence, cited evidence, and suggested fix location.
- **Phase 4 — remediation PR.** Patch on a branch, run tests, open the PR only
  if they pass. Never apply directly to the cluster.
- **Phase 5 — evals.** Run all six scenarios; measure root-cause accuracy,
  false-positive rate on `00-healthy`, and patch correctness.
