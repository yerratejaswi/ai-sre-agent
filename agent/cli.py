"""
cli — run the collector against the lab and save the result.

    python -m agent.cli collect
    python -m agent.cli collect --save 05-unhandled-exception
    python -m agent.cli collect --raw

The --save form writes tests/fixtures/<name>.json. Once a fixture exists for
each scenario, retrieval, prompting, and grading can all be developed with no
cluster running. That is the difference between a three second iteration loop
and a ninety second one, and it is most of why the later phases get finished.

The alert here is synthesised from cluster state rather than received from a
webhook. Intake proper comes later; for now the point is to exercise
collection and see what the LLM will actually be given.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timezone

from agent.collect.kube import Collector, load_kube_config
from agent.models import Alert, AlertSource, IncidentContext, Severity, to_json

FIXTURE_DIR = pathlib.Path("tests/fixtures")


def synthesise_alert(namespace: str, workload: str) -> Alert:
    """Build a placeholder alert so collection can run before intake exists.

    Deliberately dumb: reason is 'ManualInvestigation' rather than a guess at
    the failure type. Inferring 'OOMKilled' here would be diagnosis leaking
    into intake, and would hand the answer to the model for free.
    """
    now = datetime.now(timezone.utc)
    return Alert(
        fingerprint=f"manual-{namespace}-{workload}",
        source=AlertSource.MANUAL,
        severity=Severity.INFO,
        namespace=namespace,
        workload=workload,
        reason="ManualInvestigation",
        summary=f"manual collection for {namespace}/{workload}",
        first_seen=now,
        last_seen=now,
    )


def summarise(ctx: IncidentContext) -> str:
    """Human-readable digest. Reports what was collected, never what is wrong."""
    out: list[str] = []
    add = out.append

    add(f"namespace/workload : {ctx.namespace}/{ctx.workload}")
    add(f"blast radius       : {ctx.blast_radius}")
    add(f"rollout status     : {ctx.rollout_status}")
    add(f"current generation : {ctx.current_gen_ready}/{ctx.current_gen_pods} ready")
    add(f"previous generation: {ctx.previous_gen_ready} ready (still serving)")
    add(f"endpoints          : {ctx.endpoint_count} "
        f"(desired replicas {ctx.expected_replicas})")
    add(f"t0                 : {ctx.window.t0.isoformat()}")
    add(f"t0 source          : {ctx.window.t0_source}")
    add("")

    add(f"pods ({len(ctx.pods)})")
    for pod in ctx.pods:
        add(f"  [{pod.generation.value:8}] {pod.name}  phase={pod.phase} ready={pod.ready}")
        for c in pod.containers:
            bits = [f"restarts={c.restart_count}", f"state={c.phase}"]
            if c.waiting_reason:
                bits.append(f"waiting={c.waiting_reason}")
            if c.last_terminated_reason:
                bits.append(
                    f"lastTerminated={c.last_terminated_reason}"
                    f"(exit {c.last_terminated_exit_code})"
                )
            if c.memory_limit:
                bits.append(f"memLimit={c.memory_limit}")
            add(f"    {c.name}: " + "  ".join(bits))
    add("")

    logs = ctx.logs
    add("logs")
    if logs:
        add(f"  current gen: current={logs.current_status.value}  "
            f"previous={logs.previous_status.value}")
        add(f"  lines collected: {len(logs.lines)} "
            f"({sum(1 for l in logs.lines if l.previous)} from previous containers)")
        for key, st in logs.per_container.items():
            add(f"    {key} [{st['generation']}]: "
                f"current={st['current']} previous={st['previous']}")
        for line in logs.lines[-4:]:
            tag = "prev" if line.previous else "curr"
            add(f"    [{tag}] {line.text[:110]}")
    else:
        add("  none")
    add("")

    warnings = [e for e in ctx.events if e.type == "Warning"]
    add(f"events: {len(ctx.events)} total, {len(warnings)} warnings")
    for ev in warnings[-4:]:
        add(f"  {ev.reason} x{ev.count}: {ev.message[:100]}")
    add("")

    add(f"rollouts ({len(ctx.rollouts)})")
    for r in ctx.rollouts[:4]:
        marker = "*" if r.is_current else " "
        add(f"  {marker} rev {r.revision}  {r.image}  {r.created_at.isoformat()}")
    add("")

    containers = ctx.workload_spec.get("containers", [])
    add("spec (evidence in its own right)")
    for c in containers:
        add(f"  {c['name']}  image={c['image']}")
        add(f"    ports: {[p['containerPort'] for p in c['ports']]}")
        add(f"    readinessProbe: {c['readinessProbe']}")
        add(f"    env keys: {sorted(c['env'].keys())}")
        add(f"    limits: {c['resources']['limits']}")

    if ctx.collection_errors:
        add("")
        add("collection errors")
        for err in ctx.collection_errors:
            add(f"  {err}")

    return "\n".join(out)


def cmd_collect(args: argparse.Namespace) -> int:
    load_kube_config(args.context)
    alert = synthesise_alert(args.namespace, args.workload)
    ctx = Collector(args.namespace, args.workload, args.lookback).collect(alert)

    if args.raw:
        print(to_json(ctx))
    else:
        print(summarise(ctx))

    if args.save:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        path = FIXTURE_DIR / f"{args.save}.json"
        path.write_text(to_json(ctx), encoding="utf-8")
        print(f"\nfixture written: {path}")

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="gather evidence from the cluster")
    c.add_argument("--namespace", default="shop")
    c.add_argument("--workload", default="orders-api")
    c.add_argument("--context", default="kind-sre-lab")
    c.add_argument("--lookback", type=int, default=1800)
    c.add_argument("--raw", action="store_true", help="print full JSON")
    c.add_argument("--save", metavar="NAME", help="write tests/fixtures/NAME.json")
    c.set_defaults(func=cmd_collect)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())