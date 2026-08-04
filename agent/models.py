"""
models — the contracts between pipeline stages.

Everything in this file is data, not behaviour. Each stage of the agent takes
one of these types and returns another:

    Alert           -> collect  -> IncidentContext
    IncidentContext -> retrieve -> CodeContext
    both            -> analyze  -> Hypothesis
    Hypothesis      -> remediate -> Remediation

Keeping them in one file means the whole data flow of the system is readable
in a single pass. Scattering them across packages makes the pipeline shape
invisible.

Design rules that these types enforce:

1. Absence is explicit. "No logs exist because the container never started"
   must not serialise the same as "logs were not fetched". Scenario 04 is
   entirely about this distinction, so LogBundle carries a reason.

2. Evidence keeps its provenance. Every log line retains its source pod and
   line number so a later hypothesis can cite it and be checked.

3. Nothing here decides what is wrong. Collection describes; diagnosis judges.
   The moment a heuristic like "restarts > 3 means crashloop" appears in this
   file, the LLM's accuracy numbers stop meaning what they claim.
"""

from __future__ import annotations

import dataclasses
import enum
import json
from datetime import datetime
from typing import Any, Optional


# --------------------------------------------------------------------------
# intake
# --------------------------------------------------------------------------


class AlertSource(str, enum.Enum):
    KUBERNETES = "kubernetes"      # pod restarts, failed probes, pull errors
    SYNTHETIC = "synthetic"        # the watchdog: application-level errors
    MANUAL = "manual"              # a human invoked the agent directly


class Severity(str, enum.Enum):
    CRITICAL = "critical"          # users are affected now
    WARNING = "warning"            # degraded, or will affect users soon
    INFO = "info"                  # stuck rollout with no user impact
    RESOLVED = "resolved"


@dataclasses.dataclass
class Alert:
    """What triggers an investigation.

    fingerprint is the dedup key, hashed from stable attributes only —
    never pod name, never timestamp. One bad config affecting every replica
    is one incident, not one per pod.
    """

    fingerprint: str
    source: AlertSource
    severity: Severity
    namespace: str
    workload: str
    reason: str                    # OOMKilled, HighErrorRate, ImagePullBackOff
    summary: str
    first_seen: datetime
    last_seen: datetime
    evidence: dict[str, Any] = dataclasses.field(default_factory=dict)


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


class LogStatus(str, enum.Enum):
    """Why a log bundle is empty, when it is empty.

    NEVER_STARTED is the one that matters: in scenario 04 the container was
    never created, so an empty log set is a positive finding rather than a
    gap in collection. An agent that cannot tell these apart will either
    hallucinate a cause or blame its own tooling.
    """

    OK = "ok"
    EMPTY = "empty"                      # container ran but wrote nothing
    NEVER_STARTED = "never_started"      # no container, so no logs can exist
    NO_PREVIOUS = "no_previous"          # first start; nothing to look back at
    FETCH_FAILED = "fetch_failed"        # collector problem, not app evidence


@dataclasses.dataclass
class LogLine:
    """A single line, addressable so a hypothesis can cite it."""

    pod: str
    container: str
    index: int                     # position within the fetched window
    text: str
    previous: bool = False         # from the prior container instance


@dataclasses.dataclass
class LogBundle:
    """Statuses summarise the current generation only.

    A namespace-wide flag is wrong: two healthy old pods would overrule the
    one new pod that never started, which is precisely how scenario 04 got
    mislabelled no_previous instead of never_started. per_container keeps the
    detail, keyed "pod/container".
    """

    current_status: LogStatus
    previous_status: LogStatus
    lines: list[LogLine] = dataclasses.field(default_factory=list)
    per_container: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)

    @property
    def has_evidence(self) -> bool:
        return bool(self.lines)


@dataclasses.dataclass
class ContainerState:
    """Current and previous state of one container.

    last_terminated_* is the field that separates scenario 01 from 02: both
    present as CrashLoopBackOff, and only the exit code and reason tell them
    apart. It is collected unconditionally for that reason.
    """

    name: str
    ready: bool
    started: bool
    restart_count: int
    phase: str                              # Running, Waiting, Terminated
    waiting_reason: Optional[str] = None    # CrashLoopBackOff, ImagePullBackOff
    waiting_message: Optional[str] = None
    last_terminated_reason: Optional[str] = None   # OOMKilled, Error
    last_terminated_exit_code: Optional[int] = None
    last_terminated_at: Optional[datetime] = None
    image: str = ""
    memory_limit: Optional[str] = None
    memory_request: Optional[str] = None
    cpu_limit: Optional[str] = None


class Generation(str, enum.Enum):
    """Which rollout a pod belongs to.

    A Deployment does not replace pods in place: it creates a new ReplicaSet
    and keeps the old one serving until the new one is healthy. If the new
    pods never become healthy, the old ones serve indefinitely.

    Without this distinction the collector sums healthy old pods and broken
    new pods into one number and reports that capacity is fine while
    containers crash-loop. Scenarios 02, 03, and 04 all have that shape.
    """

    CURRENT = "current"        # belongs to the newest ReplicaSet
    PREVIOUS = "previous"      # older ReplicaSet, may still be serving
    UNKNOWN = "unknown"


@dataclasses.dataclass
class PodSnapshot:
    name: str
    phase: str                     # Pending, Running, Succeeded, Failed
    ready: bool                    # all containers ready
    node: Optional[str]
    created_at: datetime
    containers: list[ContainerState] = dataclasses.field(default_factory=list)
    conditions: dict[str, str] = dataclasses.field(default_factory=dict)
    owner: Optional[str] = None            # owning ReplicaSet
    generation: Generation = Generation.UNKNOWN


@dataclasses.dataclass
class EventRecord:
    type: str                      # Normal, Warning
    reason: str                    # Unhealthy, Failed, BackOff
    message: str
    involved_object: str
    count: int
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]


@dataclasses.dataclass
class RolloutRecord:
    """A deploy. The most common answer to 'what changed'."""

    revision: int
    created_at: datetime
    image: str
    change_cause: Optional[str] = None
    is_current: bool = False


@dataclasses.dataclass
class TimeWindow:
    """Scoping for the whole investigation.

    t0 is the first bad signal — the first restart, the first Warning event,
    the first failed probe — not wall-clock now. An agent invoked forty
    minutes late with a now-minus-30 window looks straight past the deploy
    that caused the incident.
    """

    t0: datetime
    lookback_seconds: int = 1800
    t0_source: str = ""            # which signal established t0, for auditing

    @property
    def start(self) -> datetime:
        from datetime import timedelta

        return self.t0 - timedelta(seconds=self.lookback_seconds)


@dataclasses.dataclass
class IncidentContext:
    """Everything gathered about an incident, before any interpretation.

    This is the artifact that gets serialised into tests/fixtures/ so that
    retrieval, prompting, and grading can all be developed without a running
    cluster.
    """

    alert: Alert
    namespace: str
    workload: str
    window: TimeWindow
    collected_at: datetime

    pods: list[PodSnapshot] = dataclasses.field(default_factory=list)
    logs: Optional[LogBundle] = None
    events: list[EventRecord] = dataclasses.field(default_factory=list)
    rollouts: list[RolloutRecord] = dataclasses.field(default_factory=list)
    workload_spec: dict[str, Any] = dataclasses.field(default_factory=dict)

    endpoint_count: int = 0
    expected_replicas: int = 0
    ready_replicas: int = 0              # across all generations

    current_gen_pods: int = 0
    current_gen_ready: int = 0
    previous_gen_ready: int = 0

    collection_errors: list[str] = dataclasses.field(default_factory=list)

    @property
    def rollout_status(self) -> str:
        """Whether the newest generation is actually working.

        stuck_old_serving is the state shared by scenarios 02, 03, and 04:
        the new version is broken, the old one carries every request, and no
        user is affected yet. It is a ticket, not a 3am page — and reporting
        it as healthy, which summing across generations does, loses that.
        """
        if not self.current_gen_pods:
            return "unknown"
        if self.current_gen_ready == self.current_gen_pods:
            return "complete"
        if self.previous_gen_ready > 0:
            return "stuck_old_serving"
        return "stuck_no_fallback"

    @property
    def blast_radius(self) -> str:
        """Capacity impact, which is not the same as severity of the fault.

        Measured from endpoints — pods actually receiving traffic — rather
        than from pod counts, because a crash-looping new pod does not reduce
        capacity while the old generation still serves.
        """
        if self.expected_replicas == 0:
            return "unknown"
        if self.endpoint_count == 0:
            return "total_outage"
        if self.endpoint_count < self.expected_replicas:
            return "degraded"
        return "none"


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------


class RetrievalStrategy(str, enum.Enum):
    """How a chunk was found. Recorded so the eval can report which strategy
    actually contributed, rather than assuming vector search earned its place.
    """

    TRACEBACK = "traceback"                # frame named the file directly
    MANIFEST_SOURCE = "manifest_source"    # declared config vs actual source
    SYMBOL = "symbol"                      # named symbol lookup
    VECTOR = "vector"                      # embedding similarity


@dataclasses.dataclass
class CodeChunk:
    chunk_id: str                  # citable in a hypothesis
    repo: str
    path: str
    start_line: int
    end_line: int
    symbol: Optional[str]
    content: str
    strategy: RetrievalStrategy
    score: Optional[float] = None


@dataclasses.dataclass
class CodeContext:
    repo: Optional[str]
    chunks: list[CodeChunk] = dataclasses.field(default_factory=list)
    resolution_notes: list[str] = dataclasses.field(default_factory=list)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


class Outcome(str, enum.Enum):
    """A diagnosis is allowed to decline.

    INSUFFICIENT_EVIDENCE and NO_INCIDENT exist so that 00-healthy has a
    correct answer that is not a root cause. Without them, a model asked
    'what is wrong' will always find something.
    """

    ROOT_CAUSE_IDENTIFIED = "root_cause_identified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_INCIDENT = "no_incident"


@dataclasses.dataclass
class Citation:
    """A claim must point at something that was actually collected.

    kind is 'log' (with a LogLine index), 'event', 'code' (a chunk_id), or
    'spec' (a path into workload_spec). The fraction of claims carrying a
    resolvable citation is the grounding rate — the hallucination metric.
    """

    kind: str
    ref: str
    quote: str = ""


@dataclasses.dataclass
class Hypothesis:
    outcome: Outcome
    summary: str
    root_cause: Optional[str]
    confidence: float                       # 0.0 - 1.0
    severity: Severity
    fix_location: Optional[str] = None      # file path, or manifest field path
    suggested_fix: Optional[str] = None
    latent_defect: Optional[str] = None     # deeper issue beyond the immediate
    citations: list[Citation] = dataclasses.field(default_factory=list)
    ruled_out: list[str] = dataclasses.field(default_factory=list)


# --------------------------------------------------------------------------
# remediation
# --------------------------------------------------------------------------


class VerificationResult(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


@dataclasses.dataclass
class Remediation:
    """Attempt counters live here because the repair loop must be bounded.

    Unbounded self-repair with the ability to edit tests eventually 'fixes'
    a failure by deleting the assertion. attempt is checked against a cap,
    and the patch is rejected if it touches test files.
    """

    hypothesis: Hypothesis
    patch: str                              # unified diff
    target_files: list[str] = dataclasses.field(default_factory=list)
    verification: VerificationResult = VerificationResult.NOT_RUN
    verification_output: str = ""
    attempt: int = 1
    pr_url: Optional[str] = None
    escalated: bool = False
    escalation_reason: Optional[str] = None


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------


def _encode(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, enum.Enum):
        return obj.value
    raise TypeError(f"not serialisable: {type(obj)}")


def to_json(obj: Any, indent: int = 2) -> str:
    """Serialise any model above. Used to write tests/fixtures/."""
    return json.dumps(dataclasses.asdict(obj), default=_encode, indent=indent)