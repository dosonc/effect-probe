"""Failure mapping and lifecycle tests for the private semantic kernel."""

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from effectprobe._lost_result import OperationId, TrialId
from effectprobe._semantic_kernel import (
    AxisStatus,
    CaseConfigurationError,
    CaseDefinition,
    CleanAssertion,
    CleanEvaluationContext,
    CleanupStatus,
    EvaluationDecision,
    EvidenceKind,
    EvidenceRequirement,
    InvariantVerdict,
    RetryEvaluationContext,
    RetryInvariant,
    SurfaceCoverage,
    SurfaceObservation,
    World,
    WorldSession,
    evaluate_case,
)

type _Observation = SurfaceObservation[int, int]
type _Case = CaseDefinition[str, str, int, int, int, int]


@dataclass(slots=True)
class _Tracker:
    trials: list[str] = field(default_factory=lambda: list[str]())
    cleanup_attempts: int = 0


def _clean_pass(context: CleanEvaluationContext[str, str, int, int]) -> EvaluationDecision:
    passed = (
        context.returned_result == "receipt/1"
        and context.final.state - context.baseline.state == 1
        and context.history_delta == (1,)
    )
    return EvaluationDecision(passed, "clean matched" if passed else "clean differed")


def _retry_pass(context: RetryEvaluationContext[str, str, int, int, int]) -> EvaluationDecision:
    passed = (
        context.canonical_clean_history_delta == context.canonical_perturbed_history_delta
        and context.perturbed_final.state - context.perturbed_baseline.state == 1
    )
    return EvaluationDecision(passed, "retry matched" if passed else "retry differed")


def _build_case(
    *,
    safe_trials: frozenset[str] = frozenset(),
    no_boundary_trials: frozenset[str] = frozenset(),
    swallow_trials: frozenset[str] = frozenset(),
    corrupt_history_trials: frozenset[str] = frozenset(),
    invocation_failure_trials: frozenset[str] = frozenset(),
    observer_failure_trials: frozenset[str] = frozenset(),
    cleanup_failure_trials: frozenset[str] = frozenset(),
    provision_failure_trials: frozenset[str] = frozenset(),
    fixture_failure_trials: frozenset[str] = frozenset(),
    initial_states: dict[str, int] | None = None,
    initial_histories: dict[str, tuple[int, ...]] | None = None,
    coverage: SurfaceCoverage | None = None,
    clean_assertions: tuple[CleanAssertion[str, str, int, int], ...] | None = None,
    retry_invariants: tuple[RetryInvariant[str, str, int, int, int], ...] | None = None,
) -> tuple[_Case, _Tracker]:
    tracker = _Tracker()
    states = initial_states or {}
    histories = initial_histories or {}

    def world_factory(trial_id: TrialId) -> WorldSession[str, str, int, int]:
        state = states.get(trial_id.value, 0)
        history = list(histories.get(trial_id.value, ()))
        invocations = 0
        observations = 0

        def provision() -> World[str, str, int, int]:
            nonlocal state, invocations, observations
            tracker.trials.append(trial_id.value)
            if trial_id.value in provision_failure_trials:
                raise RuntimeError(f"provision failed: {trial_id.value}")

            def observe() -> _Observation:
                nonlocal observations
                observations += 1
                if trial_id.value in observer_failure_trials and observations >= 2:
                    raise RuntimeError(f"observer failed: {trial_id.value}")
                observed_history = tuple(history)
                if trial_id.value in corrupt_history_trials and observations >= 3:
                    observed_history = observed_history[1:]
                return SurfaceObservation(state=state, history=observed_history)

            def validate_fixture(_observation: _Observation) -> None:
                if trial_id.value in fixture_failure_trials:
                    raise ValueError(f"fixture failed: {trial_id.value}")

            def invoke(
                _input: str,
                deliver_result: Callable[[str], str],
            ) -> str:
                nonlocal invocations, state
                invocations += 1
                if trial_id.value in invocation_failure_trials:
                    raise RuntimeError(f"invocation failed: {trial_id.value}")
                if trial_id.value in no_boundary_trials:
                    return "not-delivered"
                is_repeat = invocations > 1
                if not (trial_id.value in safe_trials and is_repeat):
                    state += 1
                    history.append(1)
                receipt = "receipt/1" if state == 1 else f"receipt/{state}"
                if trial_id.value in swallow_trials:
                    try:
                        return deliver_result(receipt)
                    except Exception:
                        return "fallback"
                return deliver_result(receipt)

            return World(
                invoke=invoke,
                observe=observe,
                validate_fixture=validate_fixture,
            )

        def cleanup(_world: World[str, str, int, int] | None) -> None:
            tracker.cleanup_attempts += 1
            if trial_id.value in cleanup_failure_trials:
                raise RuntimeError(f"cleanup failed: {trial_id.value}")

        return WorldSession(provision=provision, cleanup=cleanup)

    surface = "effects"
    state_requirement = EvidenceRequirement(EvidenceKind.STATE, surface)
    history_requirement = EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, surface)
    result_requirement = EvidenceRequirement(EvidenceKind.SUBJECT_RESULT)
    default_clean = (
        CleanAssertion(
            name="one_effect",
            requirements=(state_requirement, history_requirement, result_requirement),
            evaluate=_clean_pass,
        ),
    )
    default_retry = (
        RetryInvariant(
            name="no_additional_effect",
            requirements=(state_requirement, history_requirement),
            evaluate=_retry_pass,
        ),
    )
    case = CaseDefinition(
        subject_name="test_subject",
        input="input",
        operation_id=OperationId("operation/test"),
        operation_key_selector=lambda _input: "subject-key",
        world_factory=world_factory,
        coverage=coverage
        or SurfaceCoverage(
            surface=surface,
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="test_model",
        ),
        canonicalize_state=lambda value: value,
        canonicalize_event=lambda value: value,
        clean_assertions=default_clean if clean_assertions is None else clean_assertions,
        retry_invariants=default_retry if retry_invariants is None else retry_invariants,
        scope_limitations=("test scope",),
    )
    return case, tracker


def test_missing_complete_history_is_inconclusive_without_evaluators() -> None:
    calls = 0

    def should_not_run(
        _context: RetryEvaluationContext[str, str, int, int, int],
    ) -> EvaluationDecision:
        nonlocal calls
        calls += 1
        raise AssertionError("evaluator should not run")

    case, tracker = _build_case(
        coverage=SurfaceCoverage(
            surface="effects",
            state=True,
            history=True,
            complete_history=False,
            observation_interval="baseline_to_final",
            provenance="test_model",
        ),
        retry_invariants=(
            RetryInvariant(
                name="needs_complete_history",
                requirements=(EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, "effects"),),
                evaluate=should_not_run,
            ),
        ),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.INCONCLUSIVE
    assert result.retry_safety.status is AxisStatus.INCONCLUSIVE
    assert result.retry_safety.invariants[0].missing_evidence == ("effects:complete_history",)
    assert calls == 0
    assert tracker.cleanup_attempts == 2


def test_baseline_mismatch_preserves_clean_pass_and_sets_retry_error() -> None:
    case, tracker = _build_case(initial_states={"primary/perturbed": 10})

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(error.phase == "baseline_comparison" for error in result.primary.errors)
    assert tracker.cleanup_attempts == 2


def test_unreached_boundary_is_retry_inconclusive() -> None:
    case, tracker = _build_case(no_boundary_trials=frozenset({"primary/perturbed"}))

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.INCONCLUSIVE
    assert tracker.trials == ["primary/clean", "primary/perturbed"]
    assert tracker.cleanup_attempts == 2


def test_swallowed_fault_is_retry_error() -> None:
    case, _tracker = _build_case(swallow_trials=frozenset({"primary/perturbed"}))

    result = evaluate_case(case)

    assert result.retry_safety.status is AxisStatus.ERROR
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.ERROR
    assert any(error.phase == "fault_propagation" for error in result.primary.errors)


def test_subject_or_provider_invocation_failure_is_retry_error_with_cleanup() -> None:
    case, tracker = _build_case(invocation_failure_trials=frozenset({"primary/perturbed"}))

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(
        error.phase == "lost_result_schedule" and error.error_type == "RuntimeError"
        for error in result.primary.errors
    )
    assert tracker.cleanup_attempts == 2


def test_observer_failure_is_clean_error_with_cleanup() -> None:
    case, tracker = _build_case(observer_failure_trials=frozenset({"primary/clean"}))

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(
        error.phase == "final_observer" and error.error_type == "RuntimeError"
        for error in result.primary.errors
    )
    assert tracker.cleanup_attempts == 1


def test_declared_append_only_history_corruption_is_retry_error() -> None:
    case, _tracker = _build_case(
        corrupt_history_trials=frozenset({"primary/perturbed"}),
        initial_histories={"primary/perturbed": (9,)},
    )

    result = evaluate_case(case)

    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(error.phase == "history_validation" for error in result.primary.errors)


@pytest.mark.parametrize("failure", ["fixture", "provision"])
def test_invalid_or_unprovisioned_clean_fixture_is_error_with_cleanup(failure: str) -> None:
    if failure == "fixture":
        case, tracker = _build_case(fixture_failure_trials=frozenset({"primary/clean"}))
    else:
        case, tracker = _build_case(provision_failure_trials=frozenset({"primary/clean"}))

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert tracker.cleanup_attempts == 1
    assert result.primary.clean_cleanup is CleanupStatus.PASS


def test_cleanup_failure_is_recorded_and_prevents_a_passing_axis() -> None:
    case, tracker = _build_case(
        safe_trials=frozenset({"primary/perturbed"}),
        cleanup_failure_trials=frozenset({"primary/perturbed"}),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.ERROR
    assert result.primary.perturbed_cleanup is CleanupStatus.ERROR
    assert any(error.phase == "cleanup" for error in result.primary.errors)
    assert tracker.cleanup_attempts == 2


def test_evaluator_malfunctions_affect_only_their_axes() -> None:
    def broken_clean(
        _context: CleanEvaluationContext[str, str, int, int],
    ) -> EvaluationDecision:
        raise RuntimeError("clean evaluator failed")

    def broken_retry(
        _context: RetryEvaluationContext[str, str, int, int, int],
    ) -> EvaluationDecision:
        raise RuntimeError("retry evaluator failed")

    requirements = (
        EvidenceRequirement(EvidenceKind.STATE, "effects"),
        EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, "effects"),
    )
    case, _tracker = _build_case(
        safe_trials=frozenset({"primary/perturbed"}),
        clean_assertions=(CleanAssertion("broken_clean", requirements, broken_clean),),
        retry_invariants=(RetryInvariant("broken_retry", requirements, broken_retry),),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert {error.phase for error in result.primary.errors} == {
        "clean_evaluator:broken_clean",
        "retry_evaluator:broken_retry",
    }


def test_nonreproducing_confirmation_is_inconclusive() -> None:
    case, tracker = _build_case(safe_trials=frozenset({"confirmation/2/perturbed"}))

    result = evaluate_case(case)

    assert result.retry_safety.status is AxisStatus.INCONCLUSIVE
    assert result.retry_safety.invariants[0].candidate is False
    assert "did not reproduce" in result.retry_safety.invariants[0].explanation
    assert len(result.confirmations) == 2
    assert len(tracker.trials) == 6


def test_confirmation_cleanup_failure_makes_candidate_error() -> None:
    case, _tracker = _build_case(cleanup_failure_trials=frozenset({"confirmation/2/perturbed"}))

    result = evaluate_case(case)

    assert result.retry_safety.status is AxisStatus.ERROR
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.ERROR


def test_confirmed_failure_precedes_unrelated_primary_cleanup_error() -> None:
    case, _tracker = _build_case(cleanup_failure_trials=frozenset({"primary/perturbed"}))

    result = evaluate_case(case)

    assert result.retry_safety.status is AxisStatus.FAIL
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.FAIL
    assert any(error.phase == "cleanup" for error in result.primary.errors)


def test_two_candidates_share_the_same_confirmation_pairs() -> None:
    requirements = (
        EvidenceRequirement(EvidenceKind.STATE, "effects"),
        EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, "effects"),
    )
    case, tracker = _build_case(
        retry_invariants=(
            RetryInvariant("first", requirements, _retry_pass),
            RetryInvariant("second", requirements, _retry_pass),
        )
    )

    result = evaluate_case(case)

    assert result.retry_safety.status is AxisStatus.FAIL
    assert tuple(item.verdict for item in result.retry_safety.invariants) == (
        InvariantVerdict.FAIL,
        InvariantVerdict.FAIL,
    )
    assert len(result.confirmations) == 2
    assert len(tracker.trials) == 6


def test_clean_can_be_unverified_while_retry_passes() -> None:
    case, _tracker = _build_case(
        safe_trials=frozenset({"primary/perturbed"}),
        clean_assertions=(),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.UNVERIFIED
    assert result.retry_safety.status is AxisStatus.PASS


def test_each_trial_uses_its_own_history_baseline() -> None:
    case, _tracker = _build_case(
        safe_trials=frozenset({"primary/perturbed"}),
        initial_histories={
            "primary/clean": (10,),
            "primary/perturbed": (20,),
        },
    )

    result = evaluate_case(case)

    assert result.retry_safety.status is AxisStatus.PASS
    assert result.primary.clean is not None
    assert result.primary.perturbed is not None
    assert result.primary.clean.baseline.history == (10,)
    assert result.primary.perturbed.baseline.history == (20,)
    assert result.primary.clean.history_delta == (1,)
    assert result.primary.perturbed.attempts[-1].observation.history == (20, 1)


def test_empty_retry_contract_is_configuration_error() -> None:
    case, tracker = _build_case(retry_invariants=())

    with pytest.raises(CaseConfigurationError, match="at least one retry invariant"):
        evaluate_case(case)

    assert tracker.trials == []
    assert tracker.cleanup_attempts == 0
