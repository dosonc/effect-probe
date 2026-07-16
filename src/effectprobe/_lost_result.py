"""Private orchestration for one provider-result-loss retry schedule.

The interfaces in this module are intentionally private. They exercise the first
deterministic vertical slice without establishing a supported harness API.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast


@dataclass(frozen=True, slots=True)
class OperationId:
    """Harness identity for one logical operation."""

    value: str


@dataclass(frozen=True, slots=True)
class DeliveryId:
    """Harness identity for one local delivery of an operation."""

    value: str


@dataclass(frozen=True, slots=True)
class AttemptId:
    """Harness identity for one concrete subject invocation."""

    value: str


@dataclass(frozen=True, slots=True)
class TrialId:
    """Harness identity for one clean or perturbed trial."""

    value: str


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    """The distinct identities associated with one subject invocation."""

    operation_id: OperationId
    trial_id: TrialId
    delivery_id: DeliveryId
    attempt_id: AttemptId


type LostResultOutcome = Literal["provider_result_lost", "client_result_lost"]
type AttemptOutcome = Literal["provider_result_lost", "client_result_lost", "returned"]


@dataclass(frozen=True, slots=True)
class LostResultSchedule:
    """Private description of one cooperative lost-result schedule."""

    scope_name: str
    boundary_name: str
    lost_outcome: LostResultOutcome


PROVIDER_RESULT_LOSS = LostResultSchedule(
    scope_name="provider_commit_then_lose_first_result_and_retry_once",
    boundary_name="provider_result_delivery",
    lost_outcome="provider_result_lost",
)

MCP_CLIENT_RESULT_LOSS = LostResultSchedule(
    scope_name="mcp_tool_completion_then_lose_first_client_result_and_retry_once",
    boundary_name="mcp_client_result_delivery",
    lost_outcome="client_result_lost",
)


@dataclass(frozen=True, slots=True)
class AttemptEvidence[ResultT, ObservationT]:
    """Recorded outcome and harness observation for one invocation."""

    identity: AttemptIdentity
    outcome: AttemptOutcome
    observation: ObservationT
    returned_result: ResultT | None


@dataclass(frozen=True, slots=True)
class HarnessEvidence[ResultT]:
    """Fault evidence known to the harness but not to subject recovery."""

    boundary_name: str
    reached_attempt_ids: tuple[AttemptId, ...]
    injected_attempt_id: AttemptId
    undelivered_result: ResultT


@dataclass(frozen=True, slots=True)
class RunEvidence[ResultT, ObservationT]:
    """Immutable evidence for the supported two-attempt schedule."""

    operation_id: OperationId
    trial_id: TrialId
    baseline: ObservationT
    attempts: tuple[AttemptEvidence[ResultT, ObservationT], ...]
    harness: HarnessEvidence[ResultT]
    subject_result: ResultT


class BoundaryNotReached(RuntimeError):
    """The first invocation returned without crossing the armed boundary.

    A future report layer must map this controller outcome to an affected retry
    invariant being inconclusive; it is not evidence of an invariant violation.
    """

    def __init__(self, identity: AttemptIdentity, boundary_name: str) -> None:
        self.identity = identity
        self.boundary_name = boundary_name
        super().__init__(f"{boundary_name} was not reached by {identity.attempt_id.value}")


class FaultNotPropagated(RuntimeError):
    """The fault was injected, but its signal did not escape the invocation."""

    def __init__(self, identity: AttemptIdentity, boundary_name: str) -> None:
        self.identity = identity
        self.boundary_name = boundary_name
        super().__init__(
            f"{boundary_name} loss was caught before leaving {identity.attempt_id.value}"
        )


class _ProviderResultLost(Exception):
    """Private control signal raised after a provider has committed."""


type DeliverResult[ResultT] = Callable[[ResultT], ResultT]
type InvokeSubject[ResultT] = Callable[[DeliverResult[ResultT]], ResultT]

_MISSING_BASELINE = object()
_DEFAULT_TRIAL_ID = TrialId("perturbed")


class _ResultDeliveryController[ResultT]:
    """Lose the payload at the first cooperative provider-result boundary."""

    def __init__(self, schedule: LostResultSchedule) -> None:
        self._schedule = schedule
        self._reached_attempt_ids: list[AttemptId] = []
        self._injected_attempt_id: AttemptId | None = None
        self._undelivered_result: ResultT | None = None

    @property
    def injected(self) -> bool:
        """Whether the one supported fault has been injected."""

        return self._injected_attempt_id is not None

    @property
    def reached_attempt_ids(self) -> tuple[AttemptId, ...]:
        """Attempts that crossed the boundary, in crossing order."""

        return tuple(self._reached_attempt_ids)

    @property
    def injected_attempt_id(self) -> AttemptId:
        """Return the attempt where the fault was injected after a successful run."""

        return cast("AttemptId", self._injected_attempt_id)

    @property
    def undelivered_result(self) -> ResultT:
        """Return the first payload retained only as harness truth."""

        return cast("ResultT", self._undelivered_result)

    def delivery_for(self, attempt_id: AttemptId) -> DeliverResult[ResultT]:
        """Create a cooperative delivery callback bound to an attempt."""

        def deliver(result: ResultT) -> ResultT:
            self._reached_attempt_ids.append(attempt_id)
            if not self.injected:
                self._injected_attempt_id = attempt_id
                self._undelivered_result = result
                raise _ProviderResultLost
            return result

        return deliver


def identity_for(operation_id: OperationId, trial_id: TrialId, ordinal: int) -> AttemptIdentity:
    """Derive deterministic, domain-separated structural identities."""

    return AttemptIdentity(
        operation_id=operation_id,
        trial_id=trial_id,
        delivery_id=DeliveryId(f"delivery/{operation_id.value}/{trial_id.value}/{ordinal}"),
        attempt_id=AttemptId(f"attempt/{operation_id.value}/{trial_id.value}/{ordinal}"),
    )


def run_commit_then_lose_first_provider_result[ResultT, ObservationT](
    *,
    operation_id: OperationId,
    invoke: InvokeSubject[ResultT],
    observe: Callable[[], ObservationT],
    trial_id: TrialId = _DEFAULT_TRIAL_ID,
    baseline: ObservationT | object = _MISSING_BASELINE,
    schedule: LostResultSchedule = PROVIDER_RESULT_LOSS,
) -> RunEvidence[ResultT, ObservationT]:
    """Run one invocation, lose its configured result, and retry exactly once.

    The subject receives only the cooperative result-delivery callback. Logical,
    delivery, and attempt identities remain harness evidence and cannot become an
    implicit domain idempotency key.
    """

    recorded_baseline = (
        observe() if baseline is _MISSING_BASELINE else cast("ObservationT", baseline)
    )
    controller = _ResultDeliveryController[ResultT](schedule)
    first_identity = identity_for(operation_id, trial_id, 1)

    try:
        invoke(controller.delivery_for(first_identity.attempt_id))
    except _ProviderResultLost:
        after_lost_result = observe()
    else:
        if controller.injected:
            raise FaultNotPropagated(first_identity, schedule.boundary_name)
        raise BoundaryNotReached(first_identity, schedule.boundary_name)

    second_identity = identity_for(operation_id, trial_id, 2)
    subject_result = invoke(controller.delivery_for(second_identity.attempt_id))
    final_observation = observe()

    first_attempt = AttemptEvidence[ResultT, ObservationT](
        identity=first_identity,
        outcome=schedule.lost_outcome,
        observation=after_lost_result,
        returned_result=None,
    )
    second_attempt = AttemptEvidence[ResultT, ObservationT](
        identity=second_identity,
        outcome="returned",
        observation=final_observation,
        returned_result=subject_result,
    )
    harness_evidence = HarnessEvidence(
        boundary_name=schedule.boundary_name,
        reached_attempt_ids=controller.reached_attempt_ids,
        injected_attempt_id=controller.injected_attempt_id,
        undelivered_result=controller.undelivered_result,
    )
    return RunEvidence(
        operation_id=operation_id,
        trial_id=trial_id,
        baseline=recorded_baseline,
        attempts=(first_attempt, second_attempt),
        harness=harness_evidence,
        subject_result=subject_result,
    )
