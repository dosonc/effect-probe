"""Private vulnerable and keyed refund comparison for the semantic kernel."""

from collections.abc import Callable
from dataclasses import dataclass, field

from effectprobe._lost_result import OperationId, TrialId
from effectprobe._semantic_kernel import (
    CaseDefinition,
    CaseResult,
    CleanAssertion,
    CleanEvaluationContext,
    EvaluationDecision,
    EvidenceKind,
    EvidenceRequirement,
    RetryEvaluationContext,
    RetryInvariant,
    SurfaceCoverage,
    SurfaceObservation,
    World,
    WorldSession,
    evaluate_case,
)

_PAYMENT_ID = "payment/refund-001"
_PAYMENT_MINOR_UNITS = 10_000
_REFUND_MINOR_UNITS = 2_500
_OPERATION_ID = OperationId("operation/refund-001")
_OPERATION_KEY = "refund-key-001"


@dataclass(frozen=True, slots=True)
class RefundCommand:
    """Concrete subject-visible refund input."""

    payment_id: str
    amount_minor_units: int
    operation_key: str


@dataclass(frozen=True, slots=True)
class RefundEvent:
    """One append-only committed provider effect."""

    refund_id: str
    payment_id: str
    amount_minor_units: int
    operation_key: str | None


@dataclass(frozen=True, slots=True)
class RefundReceipt:
    """Provider result delivered to the subject when delivery succeeds."""

    refund_id: str
    payment_id: str
    amount_minor_units: int


@dataclass(frozen=True, slots=True)
class RefundState:
    """Current provider state for the refunds surface."""

    payment_id: str
    payment_minor_units: int
    refunded_minor_units: int
    key_index_size: int


type RefundObservation = SurfaceObservation[RefundState, RefundEvent]
type CanonicalRefundState = tuple[str, int, int, int]
type CanonicalRefundEvent = tuple[str, int, str | None]
type RefundCase = CaseDefinition[
    RefundCommand,
    RefundReceipt,
    RefundState,
    RefundEvent,
    CanonicalRefundState,
    CanonicalRefundEvent,
]
type RefundCaseResult = CaseResult[RefundCommand, RefundReceipt, RefundState, RefundEvent]


class FakeRefundProvider:
    """Harness-controlled provider with state, history, and keyed receipts."""

    def __init__(self) -> None:
        self._refunded_minor_units = 0
        self._history: list[RefundEvent] = []
        self._key_index: dict[str, RefundReceipt] = {}

    def refund(
        self,
        command: RefundCommand,
        *,
        operation_key: str | None,
        deliver_result: Callable[[RefundReceipt], RefundReceipt],
    ) -> RefundReceipt:
        """Commit a new refund or return the stored keyed receipt before delivery."""

        if operation_key is not None and operation_key in self._key_index:
            return deliver_result(self._key_index[operation_key])

        refund_id = f"refund/{len(self._history) + 1}"
        event = RefundEvent(
            refund_id=refund_id,
            payment_id=command.payment_id,
            amount_minor_units=command.amount_minor_units,
            operation_key=operation_key,
        )
        receipt = RefundReceipt(
            refund_id=refund_id,
            payment_id=command.payment_id,
            amount_minor_units=command.amount_minor_units,
        )
        self._refunded_minor_units += command.amount_minor_units
        self._history.append(event)
        if operation_key is not None:
            self._key_index[operation_key] = receipt
        return deliver_result(receipt)

    def observe(self) -> RefundObservation:
        """Return immutable state and complete committed history."""

        return SurfaceObservation(
            state=RefundState(
                payment_id=_PAYMENT_ID,
                payment_minor_units=_PAYMENT_MINOR_UNITS,
                refunded_minor_units=self._refunded_minor_units,
                key_index_size=len(self._key_index),
            ),
            history=tuple(self._history),
        )


class UnsafeRefundSubject:
    """Receive but ignore the subject-visible operation key."""

    def __init__(self, provider: FakeRefundProvider) -> None:
        self._provider = provider
        self._received_receipts: list[RefundReceipt] = []

    @property
    def received_receipts(self) -> tuple[RefundReceipt, ...]:
        return tuple(self._received_receipts)

    def invoke(
        self,
        command: RefundCommand,
        deliver_result: Callable[[RefundReceipt], RefundReceipt],
    ) -> RefundReceipt:
        receipt = self._provider.refund(
            command,
            operation_key=None,
            deliver_result=deliver_result,
        )
        self._received_receipts.append(receipt)
        return receipt


class KeyedRefundSubject:
    """Forward the original subject-visible operation key unchanged."""

    def __init__(self, provider: FakeRefundProvider) -> None:
        self._provider = provider
        self._received_receipts: list[RefundReceipt] = []

    @property
    def received_receipts(self) -> tuple[RefundReceipt, ...]:
        return tuple(self._received_receipts)

    def invoke(
        self,
        command: RefundCommand,
        deliver_result: Callable[[RefundReceipt], RefundReceipt],
    ) -> RefundReceipt:
        receipt = self._provider.refund(
            command,
            operation_key=command.operation_key,
            deliver_result=deliver_result,
        )
        self._received_receipts.append(receipt)
        return receipt


type RefundSubject = UnsafeRefundSubject | KeyedRefundSubject


@dataclass(slots=True)
class RefundWorldRecord:
    """Test-visible private record of one provisioned fresh world."""

    trial_id: TrialId
    provider: FakeRefundProvider
    subject: RefundSubject
    cleaned: bool = False


@dataclass(slots=True)
class RefundWorldTracker:
    """Track exact fresh-world and cleanup counts for acceptance evidence."""

    worlds: list[RefundWorldRecord] = field(default_factory=lambda: list[RefundWorldRecord]())
    cleanup_attempts: int = 0


def validate_refund_fixture(observation: RefundObservation) -> None:
    expected = RefundState(
        payment_id=_PAYMENT_ID,
        payment_minor_units=_PAYMENT_MINOR_UNITS,
        refunded_minor_units=0,
        key_index_size=0,
    )
    if observation.state != expected or observation.history:
        raise ValueError("refund world fixture is not fresh and empty")


def _canonicalize_state(state: RefundState) -> CanonicalRefundState:
    return (
        state.payment_id,
        state.payment_minor_units,
        state.refunded_minor_units,
        state.key_index_size,
    )


def _canonicalize_event(event: RefundEvent) -> CanonicalRefundEvent:
    return (event.payment_id, event.amount_minor_units, event.operation_key)


def _one_refund_matches_request(
    context: CleanEvaluationContext[RefundCommand, RefundReceipt, RefundState, RefundEvent],
) -> EvaluationDecision:
    command = context.input
    receipt = context.returned_result
    expected_event = RefundEvent(
        refund_id=receipt.refund_id,
        payment_id=command.payment_id,
        amount_minor_units=command.amount_minor_units,
        operation_key=context.history_delta[0].operation_key if context.history_delta else None,
    )
    state_delta = (
        context.final.state.refunded_minor_units - context.baseline.state.refunded_minor_units
    )
    passed = (
        len(context.history_delta) == 1
        and context.history_delta[0] == expected_event
        and receipt.payment_id == command.payment_id
        and receipt.amount_minor_units == command.amount_minor_units
        and state_delta == command.amount_minor_units
    )
    return EvaluationDecision(
        passed=passed,
        explanation=(
            "one committed refund, returned receipt, and final state match the request"
            if passed
            else "clean refund evidence does not match exactly one requested commit"
        ),
    )


def _no_additional_refund_after_retry(
    context: RetryEvaluationContext[
        RefundCommand,
        RefundReceipt,
        RefundState,
        RefundEvent,
        CanonicalRefundEvent,
    ],
) -> EvaluationDecision:
    clean_state_delta = (
        context.clean.final.state.refunded_minor_units
        - context.clean.baseline.state.refunded_minor_units
    )
    perturbed_state_delta = (
        context.perturbed_final.state.refunded_minor_units
        - context.perturbed_baseline.state.refunded_minor_units
    )
    subject_result = context.run.subject_result
    only_perturbed_event = (
        context.perturbed_history_delta[0] if len(context.perturbed_history_delta) == 1 else None
    )
    passed = (
        context.canonical_clean_history_delta == context.canonical_perturbed_history_delta
        and clean_state_delta == perturbed_state_delta == context.input.amount_minor_units
        and only_perturbed_event is not None
        and subject_result.refund_id == only_perturbed_event.refund_id
        and subject_result.payment_id == context.input.payment_id
        and subject_result.amount_minor_units == context.input.amount_minor_units
    )
    return EvaluationDecision(
        passed=passed,
        explanation=(
            "perturbed retry preserved the clean committed-effect and receipt semantics"
            if passed
            else "perturbed retry produced an additional or inconsistent committed refund"
        ),
    )


def build_refund_case(*, keyed: bool) -> tuple[RefundCase, RefundWorldTracker]:
    """Build one private vulnerable or keyed case and its lifecycle tracker."""

    tracker = RefundWorldTracker()
    command = RefundCommand(
        payment_id=_PAYMENT_ID,
        amount_minor_units=_REFUND_MINOR_UNITS,
        operation_key=_OPERATION_KEY,
    )

    def world_factory(
        trial_id: TrialId,
    ) -> WorldSession[RefundCommand, RefundReceipt, RefundState, RefundEvent]:
        record: RefundWorldRecord | None = None

        def provision() -> World[RefundCommand, RefundReceipt, RefundState, RefundEvent]:
            nonlocal record
            provider = FakeRefundProvider()
            subject: RefundSubject = (
                KeyedRefundSubject(provider) if keyed else UnsafeRefundSubject(provider)
            )
            record = RefundWorldRecord(
                trial_id=trial_id,
                provider=provider,
                subject=subject,
            )
            tracker.worlds.append(record)
            return World(
                invoke=subject.invoke,
                observe=provider.observe,
                validate_fixture=validate_refund_fixture,
            )

        def cleanup(
            _world: World[RefundCommand, RefundReceipt, RefundState, RefundEvent] | None,
        ) -> None:
            tracker.cleanup_attempts += 1
            if record is not None:
                record.cleaned = True

        return WorldSession(provision=provision, cleanup=cleanup)

    surface = "refunds"
    state_requirement = EvidenceRequirement(EvidenceKind.STATE, surface)
    history_requirement = EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, surface)
    result_requirement = EvidenceRequirement(EvidenceKind.SUBJECT_RESULT)
    case = CaseDefinition(
        subject_name=("keyed_refund_subject" if keyed else "vulnerable_refund_subject"),
        input=command,
        operation_id=_OPERATION_ID,
        operation_key_selector=lambda value: value.operation_key,
        world_factory=world_factory,
        coverage=SurfaceCoverage(
            surface=surface,
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="harness_controlled_model",
            limitations=("does not validate production-provider semantics",),
        ),
        canonicalize_state=_canonicalize_state,
        canonicalize_event=_canonicalize_event,
        clean_assertions=(
            CleanAssertion(
                name="one_refund_matches_request",
                requirements=(state_requirement, history_requirement, result_requirement),
                evaluate=_one_refund_matches_request,
            ),
        ),
        retry_invariants=(
            RetryInvariant(
                name="no_additional_refund_after_retry",
                requirements=(state_requirement, history_requirement),
                evaluate=_no_additional_refund_after_retry,
            ),
        ),
        scope_limitations=(
            "private provisional semantic outcome",
            "code and dependency identity are not recorded",
            "runtime and environment fingerprints are not recorded",
            "runner and evidence-schema versions are not recorded",
        ),
    )
    return case, tracker


def evaluate_vulnerable_refund() -> RefundCaseResult:
    """Evaluate the vulnerable subject in fresh primary and confirmation worlds."""

    case, _tracker = build_refund_case(keyed=False)
    return evaluate_case(case)


def evaluate_keyed_refund() -> RefundCaseResult:
    """Evaluate the corrected provider-keyed subject in fresh worlds."""

    case, _tracker = build_refund_case(keyed=True)
    return evaluate_case(case)
