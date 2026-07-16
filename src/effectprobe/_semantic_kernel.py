"""Private semantic evaluation for clean and lost-result retry trials.

These types intentionally remain internal while the first working comparison
validates the semantics. They are not a supported extension API or report schema.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from types import TracebackType
from typing import Literal, cast

from effectprobe._lost_result import (
    AttemptEvidence,
    BoundaryNotReached,
    FaultNotPropagated,
    OperationId,
    RunEvidence,
    TrialId,
    identity_for,
    run_commit_then_lose_first_provider_result,
)


class InvariantVerdict(Enum):
    """Outcome of one private assertion or invariant."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


class AxisStatus(Enum):
    """Aggregate clean-validity or retry-safety status."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"
    UNVERIFIED = "UNVERIFIED"


class EvidenceKind(Enum):
    """Evidence inputs a contract may require."""

    STATE = "state"
    HISTORY = "history"
    COMPLETE_HISTORY = "complete_history"
    SUBJECT_RESULT = "subject_result"


class CleanupStatus(Enum):
    """Outcome of the one required cleanup attempt."""

    NOT_ATTEMPTED = "not_attempted"
    PASS = "pass"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """One evidence kind required from an optional named surface."""

    kind: EvidenceKind
    surface: str | None = None


@dataclass(frozen=True, slots=True)
class SurfaceCoverage:
    """Declared observer coverage for one external-effect surface."""

    surface: str
    state: bool
    history: bool
    complete_history: bool
    observation_interval: str
    provenance: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SurfaceObservation[StateT, EventT]:
    """Immutable state and committed-history observation."""

    state: StateT
    history: tuple[EventT, ...]


@dataclass(frozen=True, slots=True)
class EvaluationDecision:
    """Boolean contract decision with a bounded explanation."""

    passed: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class CleanEvaluationContext[InputT, ResultT, StateT, EventT]:
    """Evidence available to a clean functional assertion."""

    input: InputT
    baseline: SurfaceObservation[StateT, EventT]
    final: SurfaceObservation[StateT, EventT]
    history_delta: tuple[EventT, ...]
    returned_result: ResultT


@dataclass(frozen=True, slots=True)
class RetryEvaluationContext[InputT, ResultT, StateT, EventT, CanonicalEventT]:
    """Clean and perturbed evidence available to a retry invariant."""

    input: InputT
    clean: CleanEvaluationContext[InputT, ResultT, StateT, EventT]
    perturbed_baseline: SurfaceObservation[StateT, EventT]
    perturbed_final: SurfaceObservation[StateT, EventT]
    perturbed_history_delta: tuple[EventT, ...]
    canonical_clean_history_delta: tuple[CanonicalEventT, ...]
    canonical_perturbed_history_delta: tuple[CanonicalEventT, ...]
    run: RunEvidence[ResultT, SurfaceObservation[StateT, EventT]]


@dataclass(frozen=True, slots=True)
class CleanAssertion[InputT, ResultT, StateT, EventT]:
    """Named clean contract and its evidence requirements."""

    name: str
    requirements: tuple[EvidenceRequirement, ...]
    evaluate: Callable[
        [CleanEvaluationContext[InputT, ResultT, StateT, EventT]], EvaluationDecision
    ]


@dataclass(frozen=True, slots=True)
class RetryInvariant[InputT, ResultT, StateT, EventT, CanonicalEventT]:
    """Named retry contract and its evidence requirements."""

    name: str
    requirements: tuple[EvidenceRequirement, ...]
    evaluate: Callable[
        [RetryEvaluationContext[InputT, ResultT, StateT, EventT, CanonicalEventT]],
        EvaluationDecision,
    ]


@dataclass(frozen=True, slots=True)
class InvariantResult:
    """Recorded result for one assertion or invariant."""

    name: str
    verdict: InvariantVerdict
    explanation: str
    missing_evidence: tuple[str, ...] = ()
    candidate: bool = False


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    """A deterministic private record of an evaluation malfunction."""

    axis: Literal["clean", "retry"]
    phase: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class AxisResult:
    """Aggregate status and its constituent invariant results."""

    status: AxisStatus
    invariants: tuple[InvariantResult, ...]


@dataclass(frozen=True, slots=True)
class CleanTrialEvidence[ResultT, StateT, EventT]:
    """Immutable evidence for one ordinary invocation."""

    trial_id: TrialId
    baseline: SurfaceObservation[StateT, EventT]
    attempt: AttemptEvidence[ResultT, SurfaceObservation[StateT, EventT]]
    history_delta: tuple[EventT, ...]
    cleanup: CleanupStatus


type DeliverResult[ResultT] = Callable[[ResultT], ResultT]


@dataclass(slots=True)
class World[InputT, ResultT, StateT, EventT]:
    """Capabilities supplied by one freshly provisioned private world."""

    invoke: Callable[[InputT, DeliverResult[ResultT]], ResultT]
    observe: Callable[[], SurfaceObservation[StateT, EventT]]
    validate_fixture: Callable[[SurfaceObservation[StateT, EventT]], None]


class WorldProvisionError(RuntimeError):
    """World provisioning failed after rollback was attempted."""

    def __init__(self, cause: Exception, cleanup_error: Exception | None) -> None:
        self.cause = cause
        self.cleanup_error = cleanup_error
        super().__init__(str(cause))


class WorldCleanupError(RuntimeError):
    """World cleanup failed after the trial body completed or failed."""

    def __init__(self, cause: Exception, body_error: BaseException | None) -> None:
        self.cause = cause
        self.body_error = body_error
        super().__init__(str(cause))


class WorldSession[InputT, ResultT, StateT, EventT]:
    """Context-managed provisioning with one cleanup attempt, including rollback."""

    def __init__(
        self,
        *,
        provision: Callable[[], World[InputT, ResultT, StateT, EventT]],
        cleanup: Callable[[World[InputT, ResultT, StateT, EventT] | None], None],
    ) -> None:
        self._provision = provision
        self._cleanup = cleanup
        self._world: World[InputT, ResultT, StateT, EventT] | None = None
        self.cleanup_status = CleanupStatus.NOT_ATTEMPTED

    def __enter__(self) -> World[InputT, ResultT, StateT, EventT]:
        try:
            self._world = self._provision()
        except Exception as cause:
            cleanup_error: Exception | None = None
            try:
                self._cleanup(None)
            except Exception as error:
                cleanup_error = error
                self.cleanup_status = CleanupStatus.ERROR
            else:
                self.cleanup_status = CleanupStatus.PASS
            raise WorldProvisionError(cause, cleanup_error) from cause
        return self._world

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        del exc_type, traceback
        try:
            self._cleanup(self._world)
        except Exception as cause:
            self.cleanup_status = CleanupStatus.ERROR
            raise WorldCleanupError(cause, exc_value) from cause
        self.cleanup_status = CleanupStatus.PASS
        return False


@dataclass(frozen=True, slots=True)
class PrivateScope[InputT]:
    """Recorded local scope with explicit reportability limitations."""

    subject_name: str
    input: InputT
    operation_id: OperationId
    operation_key: str | None
    schedule: str
    coverage: SurfaceCoverage
    reportable: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaseDefinition[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT]:
    """Private definition of one concrete clean/perturbed evaluation case."""

    subject_name: str
    input: InputT
    operation_id: OperationId
    operation_key_selector: Callable[[InputT], str | None]
    world_factory: Callable[[TrialId], WorldSession[InputT, ResultT, StateT, EventT]]
    coverage: SurfaceCoverage
    canonicalize_state: Callable[[StateT], CanonicalStateT]
    canonicalize_event: Callable[[EventT], CanonicalEventT]
    clean_assertions: tuple[CleanAssertion[InputT, ResultT, StateT, EventT], ...]
    retry_invariants: tuple[RetryInvariant[InputT, ResultT, StateT, EventT, CanonicalEventT], ...]
    scope_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairResult[ResultT, StateT, EventT]:
    """Evidence and provisional evaluations from one clean/perturbed pair."""

    pair_id: str
    clean: CleanTrialEvidence[ResultT, StateT, EventT] | None
    perturbed: RunEvidence[ResultT, SurfaceObservation[StateT, EventT]] | None
    clean_results: tuple[InvariantResult, ...]
    retry_results: tuple[InvariantResult, ...]
    errors: tuple[ErrorRecord, ...]
    clean_cleanup: CleanupStatus
    perturbed_cleanup: CleanupStatus


@dataclass(frozen=True, slots=True)
class CaseResult[InputT, ResultT, StateT, EventT]:
    """Private immutable result for one concrete case evaluation."""

    scope: PrivateScope[InputT]
    clean_validity: AxisResult
    retry_safety: AxisResult
    primary: PairResult[ResultT, StateT, EventT]
    confirmations: tuple[PairResult[ResultT, StateT, EventT], ...]


class CaseConfigurationError(ValueError):
    """The private case cannot express the required retry evaluation."""


def _error(axis: Literal["clean", "retry"], phase: str, error: BaseException) -> ErrorRecord:
    return ErrorRecord(
        axis=axis,
        phase=phase,
        error_type=type(error).__name__,
        message=str(error),
    )


def _missing_requirements(
    requirements: tuple[EvidenceRequirement, ...], coverage: SurfaceCoverage
) -> tuple[str, ...]:
    missing: list[str] = []
    for requirement in requirements:
        if requirement.kind is EvidenceKind.SUBJECT_RESULT:
            continue
        if requirement.surface != coverage.surface:
            missing.append(f"{requirement.surface or '<none>'}:{requirement.kind.value}")
        elif requirement.kind is EvidenceKind.STATE and not coverage.state:
            missing.append(f"{coverage.surface}:state")
        elif requirement.kind is EvidenceKind.HISTORY and not coverage.history:
            missing.append(f"{coverage.surface}:history")
        elif requirement.kind is EvidenceKind.COMPLETE_HISTORY and not (
            coverage.history and coverage.complete_history
        ):
            missing.append(f"{coverage.surface}:complete_history")
    return tuple(missing)


def _history_delta[EventT](
    *,
    baseline: tuple[EventT, ...],
    later: tuple[EventT, ...],
) -> tuple[EventT, ...]:
    if len(later) < len(baseline) or later[: len(baseline)] != baseline:
        raise ValueError("declared append-only history does not preserve its recorded baseline")
    return later[len(baseline) :]


def _evaluate_clean_contracts[InputT, ResultT, StateT, EventT](
    case: CaseDefinition[InputT, ResultT, StateT, EventT, object, object],
    context: CleanEvaluationContext[InputT, ResultT, StateT, EventT],
) -> tuple[tuple[InvariantResult, ...], tuple[ErrorRecord, ...]]:
    results: list[InvariantResult] = []
    errors: list[ErrorRecord] = []
    for assertion in case.clean_assertions:
        missing = _missing_requirements(assertion.requirements, case.coverage)
        if missing:
            results.append(
                InvariantResult(
                    name=assertion.name,
                    verdict=InvariantVerdict.INCONCLUSIVE,
                    explanation="required clean evidence is unavailable",
                    missing_evidence=missing,
                )
            )
            continue
        try:
            decision = assertion.evaluate(context)
        except Exception as error:
            results.append(
                InvariantResult(
                    name=assertion.name,
                    verdict=InvariantVerdict.ERROR,
                    explanation="clean assertion evaluator malfunctioned",
                )
            )
            errors.append(_error("clean", f"clean_evaluator:{assertion.name}", error))
            continue
        results.append(
            InvariantResult(
                name=assertion.name,
                verdict=(InvariantVerdict.PASS if decision.passed else InvariantVerdict.FAIL),
                explanation=decision.explanation,
            )
        )
    return tuple(results), tuple(errors)


def _evaluate_retry_contracts[InputT, ResultT, StateT, EventT, CanonicalEventT](
    case: CaseDefinition[InputT, ResultT, StateT, EventT, object, CanonicalEventT],
    context: RetryEvaluationContext[InputT, ResultT, StateT, EventT, CanonicalEventT],
) -> tuple[tuple[InvariantResult, ...], tuple[ErrorRecord, ...]]:
    results: list[InvariantResult] = []
    errors: list[ErrorRecord] = []
    for invariant in case.retry_invariants:
        missing = _missing_requirements(invariant.requirements, case.coverage)
        if missing:
            results.append(
                InvariantResult(
                    name=invariant.name,
                    verdict=InvariantVerdict.INCONCLUSIVE,
                    explanation="required retry evidence is unavailable",
                    missing_evidence=missing,
                )
            )
            continue
        try:
            decision = invariant.evaluate(context)
        except Exception as error:
            results.append(
                InvariantResult(
                    name=invariant.name,
                    verdict=InvariantVerdict.ERROR,
                    explanation="retry invariant evaluator malfunctioned",
                )
            )
            errors.append(_error("retry", f"retry_evaluator:{invariant.name}", error))
            continue
        results.append(
            InvariantResult(
                name=invariant.name,
                verdict=(
                    InvariantVerdict.PASS if decision.passed else InvariantVerdict.INCONCLUSIVE
                ),
                explanation=decision.explanation,
                candidate=not decision.passed,
            )
        )
    return tuple(results), tuple(errors)


def _error_results(names: tuple[str, ...], explanation: str) -> tuple[InvariantResult, ...]:
    return tuple(
        InvariantResult(name=name, verdict=InvariantVerdict.ERROR, explanation=explanation)
        for name in names
    )


def _inconclusive_results(names: tuple[str, ...], explanation: str) -> tuple[InvariantResult, ...]:
    return tuple(
        InvariantResult(name=name, verdict=InvariantVerdict.INCONCLUSIVE, explanation=explanation)
        for name in names
    )


def _run_clean[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT](
    case: CaseDefinition[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT],
    trial_id: TrialId,
) -> tuple[
    CleanTrialEvidence[ResultT, StateT, EventT] | None,
    tuple[InvariantResult, ...],
    tuple[ErrorRecord, ...],
    CleanupStatus,
]:
    session: WorldSession[InputT, ResultT, StateT, EventT] | None = None
    evidence: CleanTrialEvidence[ResultT, StateT, EventT] | None = None
    results: tuple[InvariantResult, ...] = ()
    errors: list[ErrorRecord] = []
    stage = "world_factory"
    try:
        session = case.world_factory(trial_id)
        stage = "provision"
        with session as world:
            stage = "baseline_observer"
            baseline = world.observe()
            stage = "fixture_validation"
            world.validate_fixture(baseline)
            identity = identity_for(case.operation_id, trial_id, 1)
            stage = "subject_invocation"
            returned = world.invoke(case.input, lambda result: result)
            stage = "final_observer"
            final = world.observe()
            stage = "history_validation"
            history_delta = (
                _history_delta(baseline=baseline.history, later=final.history)
                if case.coverage.history and case.coverage.complete_history
                else final.history[len(baseline.history) :]
            )
            attempt = AttemptEvidence(
                identity=identity,
                outcome="returned",
                observation=final,
                returned_result=returned,
            )
            evidence = CleanTrialEvidence(
                trial_id=trial_id,
                baseline=baseline,
                attempt=attempt,
                history_delta=history_delta,
                cleanup=CleanupStatus.NOT_ATTEMPTED,
            )
            context = CleanEvaluationContext(
                input=case.input,
                baseline=baseline,
                final=final,
                history_delta=history_delta,
                returned_result=returned,
            )
            stage = "clean_evaluation"
            erased_case = cast(
                "CaseDefinition[InputT, ResultT, StateT, EventT, object, object]", case
            )
            results, evaluation_errors = _evaluate_clean_contracts(erased_case, context)
            errors.extend(evaluation_errors)
    except WorldProvisionError as error:
        errors.append(_error("clean", "provision", error.cause))
        if error.cleanup_error is not None:
            errors.append(_error("clean", "cleanup_after_provision", error.cleanup_error))
    except WorldCleanupError as error:
        if error.body_error is not None:
            errors.append(_error("clean", stage, error.body_error))
        errors.append(_error("clean", "cleanup", error.cause))
    except Exception as error:
        errors.append(_error("clean", stage, error))

    cleanup = session.cleanup_status if session is not None else CleanupStatus.NOT_ATTEMPTED
    if evidence is not None:
        evidence = replace(evidence, cleanup=cleanup)
    if errors and not results:
        results = _error_results(
            tuple(assertion.name for assertion in case.clean_assertions),
            "clean trial did not complete validly",
        )
    return evidence, results, tuple(errors), cleanup


def _run_perturbed[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT](
    case: CaseDefinition[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT],
    trial_id: TrialId,
    clean: CleanTrialEvidence[ResultT, StateT, EventT],
) -> tuple[
    RunEvidence[ResultT, SurfaceObservation[StateT, EventT]] | None,
    tuple[InvariantResult, ...],
    tuple[ErrorRecord, ...],
    CleanupStatus,
]:
    session: WorldSession[InputT, ResultT, StateT, EventT] | None = None
    run: RunEvidence[ResultT, SurfaceObservation[StateT, EventT]] | None = None
    results: tuple[InvariantResult, ...] = ()
    errors: list[ErrorRecord] = []
    stage = "world_factory"
    invariant_names = tuple(invariant.name for invariant in case.retry_invariants)
    try:
        session = case.world_factory(trial_id)
        stage = "provision"
        with session as world:
            stage = "baseline_observer"
            baseline = world.observe()
            stage = "fixture_validation"
            world.validate_fixture(baseline)
            stage = "baseline_comparison"
            if case.canonicalize_state(baseline.state) != case.canonicalize_state(
                clean.baseline.state
            ):
                raise ValueError("clean and perturbed canonical state baselines differ")
            stage = "lost_result_schedule"
            run = run_commit_then_lose_first_provider_result(
                operation_id=case.operation_id,
                trial_id=trial_id,
                baseline=baseline,
                invoke=lambda deliver: world.invoke(case.input, deliver),
                observe=world.observe,
            )
            final = run.attempts[-1].observation
            stage = "history_validation"
            if case.coverage.history and case.coverage.complete_history:
                previous_history = baseline.history
                for attempt in run.attempts:
                    _history_delta(
                        baseline=previous_history,
                        later=attempt.observation.history,
                    )
                    previous_history = attempt.observation.history
                perturbed_delta = _history_delta(
                    baseline=baseline.history,
                    later=final.history,
                )
            else:
                perturbed_delta = final.history[len(baseline.history) :]
            clean_context = CleanEvaluationContext(
                input=case.input,
                baseline=clean.baseline,
                final=clean.attempt.observation,
                history_delta=clean.history_delta,
                returned_result=cast("ResultT", clean.attempt.returned_result),
            )
            context = RetryEvaluationContext(
                input=case.input,
                clean=clean_context,
                perturbed_baseline=baseline,
                perturbed_final=final,
                perturbed_history_delta=perturbed_delta,
                canonical_clean_history_delta=tuple(
                    case.canonicalize_event(event) for event in clean.history_delta
                ),
                canonical_perturbed_history_delta=tuple(
                    case.canonicalize_event(event) for event in perturbed_delta
                ),
                run=run,
            )
            stage = "retry_evaluation"
            erased_case = cast(
                "CaseDefinition[InputT, ResultT, StateT, EventT, object, CanonicalEventT]",
                case,
            )
            results, evaluation_errors = _evaluate_retry_contracts(erased_case, context)
            errors.extend(evaluation_errors)
    except BoundaryNotReached as error:
        results = _inconclusive_results(invariant_names, str(error))
    except FaultNotPropagated as error:
        errors.append(_error("retry", "fault_propagation", error))
        results = _error_results(invariant_names, "fault was caught before configured retry")
    except WorldProvisionError as error:
        errors.append(_error("retry", "provision", error.cause))
        if error.cleanup_error is not None:
            errors.append(_error("retry", "cleanup_after_provision", error.cleanup_error))
    except WorldCleanupError as error:
        if error.body_error is not None:
            errors.append(_error("retry", stage, error.body_error))
        errors.append(_error("retry", "cleanup", error.cause))
    except Exception as error:
        errors.append(_error("retry", stage, error))

    cleanup = session.cleanup_status if session is not None else CleanupStatus.NOT_ATTEMPTED
    if errors and not results:
        results = _error_results(invariant_names, "perturbed trial did not complete validly")
    return run, results, tuple(errors), cleanup


def _run_pair[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT](
    case: CaseDefinition[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT],
    pair_id: str,
) -> PairResult[ResultT, StateT, EventT]:
    clean_id = TrialId(f"{pair_id}/clean")
    clean, clean_results, clean_errors, clean_cleanup = _run_clean(case, clean_id)
    if clean is None:
        retry_results = _error_results(
            tuple(invariant.name for invariant in case.retry_invariants),
            "retry comparison requires completed clean evidence",
        )
        return PairResult(
            pair_id=pair_id,
            clean=None,
            perturbed=None,
            clean_results=clean_results,
            retry_results=retry_results,
            errors=(
                *clean_errors,
                ErrorRecord(
                    axis="retry",
                    phase="clean_reference",
                    error_type="MissingCleanEvidence",
                    message="retry comparison requires completed clean evidence",
                ),
            ),
            clean_cleanup=clean_cleanup,
            perturbed_cleanup=CleanupStatus.NOT_ATTEMPTED,
        )
    perturbed_id = TrialId(f"{pair_id}/perturbed")
    perturbed, retry_results, retry_errors, perturbed_cleanup = _run_perturbed(
        case, perturbed_id, clean
    )
    return PairResult(
        pair_id=pair_id,
        clean=clean,
        perturbed=perturbed,
        clean_results=clean_results,
        retry_results=retry_results,
        errors=clean_errors + retry_errors,
        clean_cleanup=clean_cleanup,
        perturbed_cleanup=perturbed_cleanup,
    )


def _aggregate(
    results: tuple[InvariantResult, ...],
    *,
    allow_unverified: bool,
    has_error: bool,
) -> AxisResult:
    if not results:
        return AxisResult(
            status=(
                AxisStatus.UNVERIFIED if allow_unverified and not has_error else AxisStatus.ERROR
            ),
            invariants=(),
        )
    verdicts = {result.verdict for result in results}
    if InvariantVerdict.FAIL in verdicts:
        status = AxisStatus.FAIL
    elif has_error or InvariantVerdict.ERROR in verdicts:
        status = AxisStatus.ERROR
    elif InvariantVerdict.INCONCLUSIVE in verdicts:
        status = AxisStatus.INCONCLUSIVE
    else:
        status = AxisStatus.PASS
    return AxisResult(status=status, invariants=results)


def _resolve_candidates[ResultT, StateT, EventT](
    primary: PairResult[ResultT, StateT, EventT],
    confirmations: tuple[PairResult[ResultT, StateT, EventT], ...],
) -> tuple[InvariantResult, ...]:
    resolved: list[InvariantResult] = []
    for primary_result in primary.retry_results:
        if not primary_result.candidate:
            resolved.append(primary_result)
            continue
        confirmation_results: list[InvariantResult] = []
        confirmation_has_error = False
        for pair in confirmations:
            confirmation_has_error = confirmation_has_error or bool(pair.errors)
            match = next(
                (result for result in pair.retry_results if result.name == primary_result.name),
                None,
            )
            if match is None:
                confirmation_results.append(
                    InvariantResult(
                        name=primary_result.name,
                        verdict=InvariantVerdict.ERROR,
                        explanation="confirmation omitted the candidate invariant",
                    )
                )
            else:
                confirmation_results.append(match)
        if len(confirmation_results) != 2:
            resolved.append(
                replace(
                    primary_result,
                    verdict=InvariantVerdict.ERROR,
                    explanation="candidate did not receive exactly two confirmations",
                    candidate=False,
                )
            )
        elif confirmation_has_error or any(
            result.verdict is InvariantVerdict.ERROR or result.missing_evidence
            for result in confirmation_results
        ):
            resolved.append(
                replace(
                    primary_result,
                    verdict=InvariantVerdict.ERROR,
                    explanation="confirmation infrastructure or evaluation failed",
                    candidate=False,
                )
            )
        elif all(result.candidate for result in confirmation_results):
            resolved.append(
                replace(
                    primary_result,
                    verdict=InvariantVerdict.FAIL,
                    explanation=f"{primary_result.explanation}; reproduced in two confirmations",
                    candidate=False,
                )
            )
        else:
            resolved.append(
                replace(
                    primary_result,
                    verdict=InvariantVerdict.INCONCLUSIVE,
                    explanation=(
                        f"{primary_result.explanation}; a valid confirmation did not reproduce"
                    ),
                    candidate=False,
                )
            )
    return tuple(resolved)


def evaluate_case[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT](
    case: CaseDefinition[InputT, ResultT, StateT, EventT, CanonicalStateT, CanonicalEventT],
) -> CaseResult[InputT, ResultT, StateT, EventT]:
    """Evaluate one private case with fresh primary and confirmation worlds."""

    if not case.retry_invariants:
        raise CaseConfigurationError("a private case requires at least one retry invariant")
    scope = PrivateScope(
        subject_name=case.subject_name,
        input=case.input,
        operation_id=case.operation_id,
        operation_key=case.operation_key_selector(case.input),
        schedule="provider_commit_then_lose_first_result_and_retry_once",
        coverage=case.coverage,
        reportable=False,
        limitations=case.scope_limitations,
    )
    primary = _run_pair(case, "primary")
    candidate_names = tuple(result.name for result in primary.retry_results if result.candidate)
    confirmations: tuple[PairResult[ResultT, StateT, EventT], ...] = ()
    if candidate_names:
        confirmations = (
            _run_pair(case, "confirmation/1"),
            _run_pair(case, "confirmation/2"),
        )
    retry_results = _resolve_candidates(primary, confirmations)
    return CaseResult(
        scope=scope,
        clean_validity=_aggregate(
            primary.clean_results,
            allow_unverified=True,
            has_error=any(error.axis == "clean" for error in primary.errors),
        ),
        retry_safety=_aggregate(
            retry_results,
            allow_unverified=False,
            has_error=any(error.axis == "retry" for error in primary.errors),
        ),
        primary=primary,
        confirmations=confirmations,
    )
