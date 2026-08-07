"""
collect.kube — gather evidence from a live cluster.

This module describes, it does not judge. It fetches everything a human SRE
would look at and normalises it into an IncidentContext. It contains no rule
that decides what is wrong: no "restarts > 3 means crashloop", no severity
inference, no root cause. Diagnosis happens later, and the accuracy numbers
in Phase 5 only mean something if this stage stayed neutral.

Three collection rules, each forced by a scenario in the lab:

  Always fetch previous-container logs.  Scenario 02 writes one line and
  exits; current logs are empty and the evidence exists only in --previous.

  Always fetch exit codes and termination reasons.  Scenarios 01 and 02 are
  both CrashLoopBackOff and are indistinguishable without them.

  Never let a failed fetch look like an empty result.  Scenario 04 has no
  logs because the container never started, which is a finding, not a gap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from agent.models import (
    Alert,
    ContainerState,
    EventRecord,
    Generation,
    IncidentContext,
    LogBundle,
    LogLine,
    LogStatus,
    PodSnapshot,
    RolloutRecord,
    TimeWindow,
)

LOG_TAIL = 200


def load_kube_config(context: Optional[str] = "kind-sre-lab") -> None:
    """Prefer in-cluster credentials, fall back to the local kubeconfig."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(context=context)


def _utc(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


class Collector:
    def __init__(self, namespace: str, workload: str, lookback_seconds: int = 1800):
        self.namespace = namespace
        self.workload = workload
        self.lookback_seconds = lookback_seconds
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.errors: list[str] = []

    # -- entrypoint --------------------------------------------------------

    def collect(self, alert: Alert) -> IncidentContext:
        # Generation attribution must happen before anything counts pods.
        # Old and new ReplicaSets coexist during a rollout, and summing them
        # reports healthy capacity while the new version is crash-looping.
        current_rs, rs_revisions = self._current_replicaset()
        pods = self._pods(current_rs)
        logs = self._logs(pods)
        events = self._events()
        rollouts = self._rollouts(rs_revisions)
        spec, expected = self._workload_spec()
        endpoints = self._endpoint_count()

        window = self._window(alert, pods, events)

        current = [p for p in pods if p.generation is Generation.CURRENT]
        previous = [p for p in pods if p.generation is Generation.PREVIOUS]

        return IncidentContext(
            alert=alert,
            namespace=self.namespace,
            workload=self.workload,
            window=window,
            collected_at=datetime.now(timezone.utc),
            pods=pods,
            logs=logs,
            events=events,
            rollouts=rollouts,
            workload_spec=spec,
            endpoint_count=endpoints,
            expected_replicas=expected,
            ready_replicas=sum(1 for p in pods if p.ready),
            current_gen_pods=len(current),
            current_gen_ready=sum(1 for p in current if p.ready),
            previous_gen_ready=sum(1 for p in previous if p.ready),
            collection_errors=self.errors,
        )

    # -- generation --------------------------------------------------------

    def _current_replicaset(self) -> tuple[Optional[str], dict[str, int]]:
        """Identify the newest ReplicaSet via the Deployment's revision.

        Returns its name plus a name->revision map, so rollout history can be
        filtered to entries that still matter.
        """
        try:
            deploy = self.apps.read_namespaced_deployment(self.workload, self.namespace)
            rs_list = self.apps.list_namespaced_replica_set(
                self.namespace, label_selector=f"app={self.workload}"
            )
        except ApiException as e:
            self.errors.append(f"resolve generation failed: {e.status}")
            return None, {}

        target = (deploy.metadata.annotations or {}).get(
            "deployment.kubernetes.io/revision"
        )
        revisions: dict[str, int] = {}
        current = None
        for rs in rs_list.items:
            rev = (rs.metadata.annotations or {}).get(
                "deployment.kubernetes.io/revision"
            )
            if rev and rev.isdigit():
                revisions[rs.metadata.name] = int(rev)
            if rev == target:
                current = rs.metadata.name
        return current, revisions

    # -- pods --------------------------------------------------------------

    def _pods(self, current_rs: Optional[str] = None) -> list[PodSnapshot]:
        try:
            resp = self.core.list_namespaced_pod(
                self.namespace, label_selector=f"app={self.workload}"
            )
        except ApiException as e:
            self.errors.append(f"list pods failed: {e.status} {e.reason}")
            return []

        snapshots = []
        for pod in resp.items:
            limits_by_container = {
                c.name: (c.resources.limits or {}) if c.resources else {}
                for c in pod.spec.containers
            }
            requests_by_container = {
                c.name: (c.resources.requests or {}) if c.resources else {}
                for c in pod.spec.containers
            }

            containers = []
            for cs in pod.status.container_statuses or []:
                state = cs.state
                phase = "Unknown"
                waiting_reason = waiting_message = None
                if state.running:
                    phase = "Running"
                elif state.waiting:
                    phase = "Waiting"
                    waiting_reason = state.waiting.reason
                    waiting_message = state.waiting.message
                elif state.terminated:
                    phase = "Terminated"

                # The discriminator between scenarios 01 and 02. Collected
                # unconditionally: the current state tells you a container is
                # restarting, the last state tells you why.
                last = cs.last_state.terminated if cs.last_state else None
                limits = limits_by_container.get(cs.name, {}) or {}
                requests = requests_by_container.get(cs.name, {}) or {}

                containers.append(
                    ContainerState(
                        name=cs.name,
                        ready=bool(cs.ready),
                        started=bool(cs.started),
                        restart_count=cs.restart_count or 0,
                        phase=phase,
                        waiting_reason=waiting_reason,
                        waiting_message=waiting_message,
                        last_terminated_reason=last.reason if last else None,
                        last_terminated_exit_code=last.exit_code if last else None,
                        last_terminated_at=_utc(last.finished_at) if last else None,
                        image=cs.image or "",
                        memory_limit=limits.get("memory"),
                        memory_request=requests.get("memory"),
                        cpu_limit=limits.get("cpu"),
                    )
                )

            conditions = {
                c.type: c.status for c in (pod.status.conditions or [])
            }

            owner = None
            for ref in pod.metadata.owner_references or []:
                if ref.kind == "ReplicaSet":
                    owner = ref.name
                    break

            if owner is None or current_rs is None:
                generation = Generation.UNKNOWN
            elif owner == current_rs:
                generation = Generation.CURRENT
            else:
                generation = Generation.PREVIOUS

            snapshots.append(
                PodSnapshot(
                    name=pod.metadata.name,
                    phase=pod.status.phase or "Unknown",
                    ready=conditions.get("Ready") == "True",
                    node=pod.spec.node_name,
                    created_at=_utc(pod.metadata.creation_timestamp)
                    or datetime.now(timezone.utc),
                    containers=containers,
                    conditions=conditions,
                    owner=owner,
                    generation=generation,
                )
            )
        return snapshots

    # -- logs --------------------------------------------------------------

    def _logs(self, pods: list[PodSnapshot]) -> LogBundle:
        """Fetch logs per container, and summarise over the current generation.

        The earlier version computed one never_started flag across every pod
        in the namespace. Two healthy old pods overruled the one new pod that
        never started, so scenario 04 reported no_previous — "ordinary first
        run" — instead of never_started — "no container exists, stop looking
        for application evidence". Status is per container for that reason.
        """
        lines: list[LogLine] = []
        per_container: dict[str, dict[str, str]] = {}

        if not pods:
            return LogBundle(LogStatus.FETCH_FAILED, LogStatus.FETCH_FAILED)

        for pod in pods:
            for container in pod.containers:
                key = f"{pod.name}/{container.name}"

                # started is False for a container that never launched:
                # ImagePullBackOff, or an unschedulable pod. Absence of logs
                # is then a finding, not a gap in collection.
                if not container.started and container.restart_count == 0:
                    per_container[key] = {
                        "current": LogStatus.NEVER_STARTED.value,
                        "previous": LogStatus.NEVER_STARTED.value,
                        "generation": pod.generation.value,
                    }
                    continue

                text, status = self._read_log(pod.name, container.name, False)
                if status is LogStatus.OK:
                    lines.extend(self._to_lines(pod.name, container.name, text, False))

                prev, prev_status = self._read_log(pod.name, container.name, True)
                if prev_status is LogStatus.OK:
                    lines.extend(self._to_lines(pod.name, container.name, prev, True))

                per_container[key] = {
                    "current": status.value,
                    "previous": prev_status.value,
                    "generation": pod.generation.value,
                }

        current_status, previous_status = self._summarise_log_status(pods, per_container)
        return LogBundle(current_status, previous_status, lines, per_container)

    @staticmethod
    def _summarise_log_status(
        pods: list[PodSnapshot], per_container: dict[str, dict[str, str]]
    ) -> tuple[LogStatus, LogStatus]:
        """Summarise over the current generation, falling back to all pods.

        The current generation is the one under investigation; healthy pods
        from the previous ReplicaSet should not mask its state.
        """
        current_names = {
            f"{p.name}/{c.name}"
            for p in pods
            if p.generation is Generation.CURRENT
            for c in p.containers
        }
        scope = {k: v for k, v in per_container.items() if k in current_names}
        if not scope:
            scope = per_container
        if not scope:
            return LogStatus.FETCH_FAILED, LogStatus.FETCH_FAILED

        def pick(field: str) -> LogStatus:
            values = [v[field] for v in scope.values()]
            # Any real evidence wins; otherwise the most informative absence.
            for preferred in (
                LogStatus.OK,
                LogStatus.NEVER_STARTED,
                LogStatus.FETCH_FAILED,
                LogStatus.NO_PREVIOUS,
                LogStatus.EMPTY,
            ):
                if preferred.value in values:
                    return preferred
            return LogStatus.EMPTY

        return pick("current"), pick("previous")

    def _read_log(
        self, pod: str, container: str, previous: bool
    ) -> tuple[str, LogStatus]:
        try:
            raw = self.core.read_namespaced_pod_log(
                name=pod,
                namespace=self.namespace,
                container=container,
                previous=previous,
                tail_lines=LOG_TAIL,
                timestamps=True,
                _preload_content=False,
            )
            # The client hands back bytes here. str() on bytes produces a
            # description of the data — one long line containing literal
            # backslash-n — rather than the data itself. Decode explicitly.
            text = raw.data.decode("utf-8", "replace")
            return (text, LogStatus.OK) if text.strip() else ("", LogStatus.EMPTY)
        except ApiException as e:
            # 400 on --previous means there is no prior container instance.
            # That is a fact about the pod, not a failure of the collector.
            if previous and e.status == 400:
                return "", LogStatus.NO_PREVIOUS
            if e.status == 400:
                return "", LogStatus.NEVER_STARTED
            self.errors.append(f"logs {pod}/{container} previous={previous}: {e.status}")
            return "", LogStatus.FETCH_FAILED

    @staticmethod
    def _to_lines(pod: str, container: str, text: str, previous: bool) -> list[LogLine]:
        return [
            LogLine(pod=pod, container=container, index=i, text=line, previous=previous)
            for i, line in enumerate(text.strip().splitlines())
            if line.strip()
        ]

    # -- events ------------------------------------------------------------

    def _events(self) -> list[EventRecord]:
        try:
            resp = self.core.list_namespaced_event(self.namespace)
        except ApiException as e:
            self.errors.append(f"list events failed: {e.status}")
            return []

        records = []
        for ev in resp.items:
            obj = ev.involved_object
            records.append(
                EventRecord(
                    type=ev.type or "",
                    reason=ev.reason or "",
                    message=ev.message or "",
                    involved_object=f"{obj.kind}/{obj.name}" if obj else "",
                    count=ev.count or 1,
                    first_seen=_utc(ev.first_timestamp),
                    last_seen=_utc(ev.last_timestamp or ev.event_time),
                )
            )
        records.sort(key=lambda r: r.last_seen or datetime.min.replace(tzinfo=timezone.utc))
        return records

    # -- rollouts ----------------------------------------------------------

    def _rollouts(self, revisions: Optional[dict[str, int]] = None) -> list[RolloutRecord]:
        """ReplicaSet history: the usual answer to 'what changed'."""
        try:
            deploy = self.apps.read_namespaced_deployment(self.workload, self.namespace)
            rs_list = self.apps.list_namespaced_replica_set(
                self.namespace, label_selector=f"app={self.workload}"
            )
        except ApiException as e:
            self.errors.append(f"read rollouts failed: {e.status}")
            return []

        current_rev = (deploy.metadata.annotations or {}).get(
            "deployment.kubernetes.io/revision"
        )
        records = []
        for rs in rs_list.items:
            # Kubernetes retains scaled-to-zero ReplicaSets for rollback
            # history. After a day of scenario runs that is six dead entries
            # burying the change that matters, so keep only live ones plus
            # the current revision.
            replicas = rs.spec.replicas or 0
            ann = rs.metadata.annotations or {}
            rev_ann = ann.get("deployment.kubernetes.io/revision")
            if replicas == 0 and rev_ann != current_rev:
                continue
            rev = ann.get("deployment.kubernetes.io/revision")
            containers = rs.spec.template.spec.containers
            records.append(
                RolloutRecord(
                    revision=int(rev) if rev and rev.isdigit() else 0,
                    created_at=_utc(rs.metadata.creation_timestamp)
                    or datetime.now(timezone.utc),
                    image=containers[0].image if containers else "",
                    change_cause=ann.get("kubernetes.io/change-cause"),
                    is_current=rev == current_rev,
                )
            )
        records.sort(key=lambda r: r.revision, reverse=True)
        return records

    # -- spec and endpoints ------------------------------------------------

    def _workload_spec(self) -> tuple[dict[str, Any], int]:
        """The container spec, kept verbatim.

        Scenario 03's answer is a probe field and scenario 05's is an env
        value, so the manifest is evidence in its own right — not merely
        context for the logs.
        """
        try:
            deploy = self.apps.read_namespaced_deployment(self.workload, self.namespace)
        except ApiException as e:
            self.errors.append(f"read deployment failed: {e.status}")
            return {}, 0

        containers = []
        for c in deploy.spec.template.spec.containers:
            containers.append(
                {
                    "name": c.name,
                    "image": c.image,
                    "ports": [
                        {"containerPort": p.container_port} for p in (c.ports or [])
                    ],
                    "env": {e.name: e.value for e in (c.env or []) if e.value is not None},
                    "resources": {
                        "limits": dict(c.resources.limits or {}) if c.resources else {},
                        "requests": dict(c.resources.requests or {}) if c.resources else {},
                    },
                    "readinessProbe": self._probe(c.readiness_probe),
                    "livenessProbe": self._probe(c.liveness_probe),
                }
            )

        # The lab tags each Deployment with its scenario id. That label is the
        # answer key — leaking it into IncidentContext would make every eval
        # score meaningless. Drop it at the boundary.
        annotations = {
            k: v
            for k, v in (deploy.metadata.annotations or {}).items()
            if not k.startswith("sre-lab/")
        }

        spec = {
            "annotations": annotations,
            "replicas": deploy.spec.replicas or 0,
            "containers": containers,
        }
        return spec, deploy.spec.replicas or 0

    @staticmethod
    def _probe(probe: Any) -> Optional[dict[str, Any]]:
        if probe is None or probe.http_get is None:
            return None
        return {
            "path": probe.http_get.path,
            "port": probe.http_get.port,
            "initialDelaySeconds": probe.initial_delay_seconds,
            "periodSeconds": probe.period_seconds,
        }

    def _endpoint_count(self) -> int:
        try:
            ep = self.core.read_namespaced_endpoints(self.workload, self.namespace)
        except ApiException:
            return 0
        return sum(len(s.addresses or []) for s in (ep.subsets or []))

    # -- time window -------------------------------------------------------

    def _window(
        self, alert: Alert, pods: list[PodSnapshot], events: list[EventRecord]
    ) -> TimeWindow:
        """Anchor on the first bad signal, not on now.

        An agent invoked forty minutes after an incident began, using a
        now-minus-30-minutes window, looks straight past the deploy that
        caused it. Candidates are the earliest container termination, the
        earliest Warning event, and the alert's own first_seen.
        """
        candidates: list[tuple[datetime, str]] = []

        for pod in pods:
            for c in pod.containers:
                if c.last_terminated_at:
                    candidates.append(
                        (c.last_terminated_at, f"container {c.name} terminated")
                    )

        for ev in events:
            if ev.type == "Warning" and ev.first_seen:
                candidates.append((ev.first_seen, f"Warning event {ev.reason}"))

        if alert.first_seen:
            candidates.append((alert.first_seen, f"alert {alert.reason}"))

        if not candidates:
            return TimeWindow(
                t0=datetime.now(timezone.utc),
                lookback_seconds=self.lookback_seconds,
                t0_source="no bad signal found; anchored on now",
            )

        t0, source = min(candidates, key=lambda c: c[0])
        return TimeWindow(t0=t0, lookback_seconds=self.lookback_seconds, t0_source=source)


def collect_incident(
    alert: Alert, namespace: str, workload: str, kube_context: str = "kind-sre-lab"
) -> IncidentContext:
    load_kube_config(kube_context)
    return Collector(namespace, workload).collect(alert)