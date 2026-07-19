"""Deterministic property checks for the private semantic contracts."""

from collections.abc import Callable
from dataclasses import dataclass, field
from tempfile import TemporaryDirectory
from typing import Literal, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.configuration import set_hypothesis_home_dir

from effectprobe._lost_result import PROVIDER_RESULT_LOSS, OperationId, TrialId, identity_for
from effectprobe._semantic_kernel import (
    AxisStatus,
    CaseConfigurationError,
    CaseDefinition,
    CleanAssertion,
    CleanEvaluationContext,
    CleanupStatus,
    ErrorRecord,
    EvaluationDecision,
    EvidenceKind,
    EvidenceRequirement,
    InvariantResult,
    InvariantVerdict,
    PairResult,
    RetryEvaluationContext,
    RetryInvariant,
    SurfaceCoverage,
    SurfaceObservation,
    World,
    WorldSession,
    _aggregate,  # pyright: ignore[reportPrivateUsage]
    _evaluate_retry_contracts,  # pyright: ignore[reportPrivateUsage]
    _history_delta,  # pyright: ignore[reportPrivateUsage]
    _resolve_candidates,  # pyright: ignore[reportPrivateUsage]
    evaluate_case,
)

_HYPOTHESIS_HOME = TemporaryDirectory(prefix="effectprobe-hypothesis-")
set_hypothesis_home_dir(_HYPOTHESIS_HOME.name)

_PROFILE = "effectprobe-semantic-properties"
settings.register_profile(
    _PROFILE,
    max_examples=100,
    derandomize=True,
    database=None,
    deadline=None,
)
_PROPERTY_SETTINGS = settings.get_profile(_PROFILE)

_IDENTIFIER = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=12)
_HISTORY = st.lists(st.integers(min_value=-10, max_value=10), max_size=5).map(tuple)
_VERDICTS = st.lists(st.sampled_from(tuple(InvariantVerdict)), min_size=1, max_size=6)


@given(baseline=_HISTORY, suffix=_HISTORY)
@_PROPERTY_SETTINGS
def test_append_only_history_returns_exact_trial_local_suffix(
    baseline: tuple[int, ...], suffix: tuple[int, ...]
) -> None:
    assert _history_delta(baseline=baseline, later=baseline + suffix) == suffix


@given(
    baseline=st.lists(st.integers(min_value=-10, max_value=10), min_size=1, max_size=6).map(tuple),
    selector=st.integers(min_value=0, max_value=100),
)
@_PROPERTY_SETTINGS
def test_append_only_history_rejects_shortened_or_changed_prefixes(
    baseline: tuple[int, ...], selector: int
) -> None:
    index = selector % len(baseline)
    changed = list(baseline)
    changed[index] += 1

    with pytest.raises(ValueError, match="append-only"):
        _history_delta(baseline=baseline, later=baseline[:index])
    with pytest.raises(ValueError, match="append-only"):
        _history_delta(baseline=baseline, later=tuple(changed))


@given(
    operation_body=_IDENTIFIER,
    trial_body=_IDENTIFIER,
    key_body=_IDENTIFIER,
    ordinals=st.sampled_from(((1, 2), (1, 3), (2, 3), (3, 4))),
)
@_PROPERTY_SETTINGS
def test_structural_identities_are_deterministic_and_domain_separated(
    operation_body: str,
    trial_body: str,
    key_body: str,
    ordinals: tuple[int, int],
) -> None:
    operation_id = OperationId(f"operation/{operation_body}")
    trial_id = TrialId(f"trial/{trial_body}")
    operation_key = f"key/{key_body}"
    first = identity_for(operation_id, trial_id, ordinals[0])
    repeated = identity_for(operation_id, trial_id, ordinals[0])
    second = identity_for(operation_id, trial_id, ordinals[1])

    assert first == repeated
    assert first.operation_id == second.operation_id == operation_id
    assert first.trial_id == second.trial_id == trial_id
    assert first.delivery_id != first.attempt_id
    assert second.delivery_id != second.attempt_id
    assert first.delivery_id != second.delivery_id
    assert first.attempt_id != second.attempt_id
    assert operation_key not in {
        operation_id.value,
        first.delivery_id.value,
        first.attempt_id.value,
        second.delivery_id.value,
        second.attempt_id.value,
    }


def _unused_world_factory(_trial_id: TrialId) -> WorldSession[str, str, int, int]:
    raise AssertionError("contract evaluation must not provision a world")


@given(
    kind=st.sampled_from(tuple(EvidenceKind)),
    has_state=st.booleans(),
    has_history=st.booleans(),
    has_complete_history=st.booleans(),
    wrong_surface=st.booleans(),
)
@_PROPERTY_SETTINGS
def test_evidence_requirements_gate_evaluator_execution(
    kind: EvidenceKind,
    has_state: bool,
    has_history: bool,
    has_complete_history: bool,
    wrong_surface: bool,
) -> None:
    calls = 0

    def evaluate(
        _context: RetryEvaluationContext[str, str, int, int, int],
    ) -> EvaluationDecision:
        nonlocal calls
        calls += 1
        return EvaluationDecision(True, "generated evidence was sufficient")

    coverage = SurfaceCoverage(
        surface="effects",
        state=has_state,
        history=has_history,
        complete_history=has_complete_history,
        observation_interval="baseline_to_final",
        provenance="generated_test_model",
    )
    requested_surface = (
        None if kind is EvidenceKind.SUBJECT_RESULT else ("other" if wrong_surface else "effects")
    )
    requirement = EvidenceRequirement(kind, requested_surface)
    case: CaseDefinition[str, str, int, int, int, int] = CaseDefinition(
        subject_name="generated_subject",
        input="input",
        operation_id=OperationId("operation/generated"),
        operation_key_selector=lambda _value: "key/generated",
        world_factory=_unused_world_factory,
        coverage=coverage,
        canonicalize_state=lambda value: value,
        canonicalize_event=lambda value: value,
        clean_assertions=(),
        retry_invariants=(RetryInvariant("generated_requirement", (requirement,), evaluate),),
        scope_limitations=("generated private test model",),
    )
    erased_case = cast("CaseDefinition[str, str, int, int, object, int]", case)
    context = cast("RetryEvaluationContext[str, str, int, int, int]", None)

    results, errors = _evaluate_retry_contracts(erased_case, context)

    expected_missing: tuple[str, ...] = ()
    if kind is not EvidenceKind.SUBJECT_RESULT:
        if wrong_surface:
            expected_missing = (f"other:{kind.value}",)
        elif kind is EvidenceKind.STATE and not has_state:
            expected_missing = ("effects:state",)
        elif kind is EvidenceKind.HISTORY and not has_history:
            expected_missing = ("effects:history",)
        elif kind is EvidenceKind.COMPLETE_HISTORY and not (has_history and has_complete_history):
            expected_missing = ("effects:complete_history",)

    assert errors == ()
    assert results[0].missing_evidence == expected_missing
    assert results[0].verdict is (
        InvariantVerdict.INCONCLUSIVE if expected_missing else InvariantVerdict.PASS
    )
    assert calls == (0 if expected_missing else 1)


type _ConfirmationOutcome = Literal["reproduced", "not_reproduced", "error", "missing"]


def _confirmation_pair(outcome: _ConfirmationOutcome, ordinal: int) -> PairResult[str, int, int]:
    if outcome == "missing":
        retry_results: tuple[InvariantResult, ...] = ()
    else:
        retry_results = (
            InvariantResult(
                name="no_additional_effect",
                verdict=(
                    InvariantVerdict.INCONCLUSIVE
                    if outcome == "reproduced"
                    else InvariantVerdict.PASS
                    if outcome == "not_reproduced"
                    else InvariantVerdict.ERROR
                ),
                explanation=f"generated confirmation {outcome}",
                candidate=outcome == "reproduced",
            ),
        )
    errors = (
        (ErrorRecord("retry", "confirmation", "GeneratedError", "generated error"),)
        if outcome == "error"
        else ()
    )
    return PairResult(
        pair_id=f"confirmation/{ordinal}",
        clean=None,
        perturbed=None,
        clean_results=(),
        retry_results=retry_results,
        errors=errors,
        clean_cleanup=CleanupStatus.NOT_ATTEMPTED,
        perturbed_cleanup=CleanupStatus.NOT_ATTEMPTED,
    )


@given(
    outcomes=st.tuples(
        st.sampled_from(("reproduced", "not_reproduced", "error", "missing")),
        st.sampled_from(("reproduced", "not_reproduced", "error", "missing")),
    )
)
@_PROPERTY_SETTINGS
def test_confirmation_resolution_preserves_fail_inconclusive_and_error(
    outcomes: tuple[_ConfirmationOutcome, _ConfirmationOutcome],
) -> None:
    primary: PairResult[str, int, int] = PairResult(
        pair_id="primary",
        clean=None,
        perturbed=None,
        clean_results=(),
        retry_results=(
            InvariantResult(
                name="no_additional_effect",
                verdict=InvariantVerdict.INCONCLUSIVE,
                explanation="generated primary contradiction",
                candidate=True,
            ),
        ),
        errors=(),
        clean_cleanup=CleanupStatus.NOT_ATTEMPTED,
        perturbed_cleanup=CleanupStatus.NOT_ATTEMPTED,
    )

    resolved = _resolve_candidates(
        primary,
        (
            _confirmation_pair(outcomes[0], 1),
            _confirmation_pair(outcomes[1], 2),
        ),
    )[0]

    expected = (
        InvariantVerdict.FAIL
        if outcomes == ("reproduced", "reproduced")
        else InvariantVerdict.ERROR
        if "error" in outcomes or "missing" in outcomes
        else InvariantVerdict.INCONCLUSIVE
    )
    assert resolved.verdict is expected
    assert resolved.candidate is False


def _expected_axis(verdicts: tuple[InvariantVerdict, ...], has_error: bool) -> AxisStatus:
    if InvariantVerdict.FAIL in verdicts:
        return AxisStatus.FAIL
    if has_error or InvariantVerdict.ERROR in verdicts:
        return AxisStatus.ERROR
    if InvariantVerdict.INCONCLUSIVE in verdicts:
        return AxisStatus.INCONCLUSIVE
    return AxisStatus.PASS


@given(
    clean_verdicts=_VERDICTS.map(tuple),
    retry_verdicts=_VERDICTS.map(tuple),
    clean_has_error=st.booleans(),
    retry_has_error=st.booleans(),
)
@_PROPERTY_SETTINGS
def test_axis_aggregation_is_independent_and_uses_declared_precedence(
    clean_verdicts: tuple[InvariantVerdict, ...],
    retry_verdicts: tuple[InvariantVerdict, ...],
    clean_has_error: bool,
    retry_has_error: bool,
) -> None:
    def results(verdicts: tuple[InvariantVerdict, ...]) -> tuple[InvariantResult, ...]:
        return tuple(
            InvariantResult(f"invariant/{index}", verdict, "generated result")
            for index, verdict in enumerate(verdicts)
        )

    clean = _aggregate(results(clean_verdicts), allow_unverified=True, has_error=clean_has_error)
    retry = _aggregate(results(retry_verdicts), allow_unverified=False, has_error=retry_has_error)

    assert clean.status is _expected_axis(clean_verdicts, clean_has_error)
    assert retry.status is _expected_axis(retry_verdicts, retry_has_error)


@dataclass(frozen=True, slots=True)
class _Command:
    amount: int
    operation_key: str


@dataclass(slots=True)
class _Tracker:
    provisioned: list[str] = field(default_factory=lambda: list[str]())
    cleanup_attempts: int = 0


def _build_generated_case(
    *,
    keyed: bool,
    command: _Command,
    operation_id: OperationId,
    baseline_state: int,
    clean_prefix: tuple[int, ...],
    perturbed_prefix: tuple[int, ...],
) -> tuple[CaseDefinition[_Command, str, int, int, int, int], _Tracker]:
    tracker = _Tracker()

    def world_factory(trial_id: TrialId) -> WorldSession[_Command, str, int, int]:
        prefix = clean_prefix if trial_id.value.endswith("/clean") else perturbed_prefix
        state = baseline_state
        history = list(prefix)
        stored_result: str | None = None

        def provision() -> World[_Command, str, int, int]:
            nonlocal state, history, stored_result
            state = baseline_state
            history = list(prefix)
            stored_result = None
            tracker.provisioned.append(trial_id.value)

            def invoke(
                input_value: _Command,
                deliver_result: Callable[[str], str],
            ) -> str:
                nonlocal state, stored_result
                if keyed and stored_result is not None:
                    return deliver_result(stored_result)
                state += input_value.amount
                history.append(input_value.amount)
                result = f"receipt/{state}"
                if keyed:
                    stored_result = result
                return deliver_result(result)

            def observe() -> SurfaceObservation[int, int]:
                return SurfaceObservation(state=state, history=tuple(history))

            def validate_fixture(observation: SurfaceObservation[int, int]) -> None:
                if observation != SurfaceObservation(state=baseline_state, history=prefix):
                    raise ValueError("generated fixture does not match its declared baseline")

            return World(invoke=invoke, observe=observe, validate_fixture=validate_fixture)

        def cleanup(_world: World[_Command, str, int, int] | None) -> None:
            tracker.cleanup_attempts += 1

        return WorldSession(provision=provision, cleanup=cleanup)

    def clean_matches(
        context: CleanEvaluationContext[_Command, str, int, int],
    ) -> EvaluationDecision:
        passed = (
            context.final.state - context.baseline.state == context.input.amount
            and context.history_delta == (context.input.amount,)
            and context.returned_result == f"receipt/{context.final.state}"
        )
        return EvaluationDecision(passed, "generated clean model matched")

    def retry_matches(
        context: RetryEvaluationContext[_Command, str, int, int, int],
    ) -> EvaluationDecision:
        passed = (
            context.canonical_clean_history_delta
            == context.canonical_perturbed_history_delta
            == (context.input.amount,)
            and context.perturbed_final.state - context.perturbed_baseline.state
            == context.input.amount
            and context.run.subject_result == context.clean.returned_result
        )
        return EvaluationDecision(passed, "generated retry model matched")

    surface = "generated_effects"
    state_requirement = EvidenceRequirement(EvidenceKind.STATE, surface)
    history_requirement = EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, surface)
    result_requirement = EvidenceRequirement(EvidenceKind.SUBJECT_RESULT)
    case = CaseDefinition(
        subject_name="generated_keyed_subject" if keyed else "generated_unsafe_subject",
        input=command,
        operation_id=operation_id,
        operation_key_selector=lambda value: value.operation_key,
        world_factory=world_factory,
        coverage=SurfaceCoverage(
            surface=surface,
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="generated_test_model",
            limitations=("does not model a production provider",),
        ),
        canonicalize_state=lambda value: value,
        canonicalize_event=lambda value: value,
        clean_assertions=(
            CleanAssertion(
                "generated_clean_effect",
                (state_requirement, history_requirement, result_requirement),
                clean_matches,
            ),
        ),
        retry_invariants=(
            RetryInvariant(
                "generated_no_additional_effect",
                (state_requirement, history_requirement),
                retry_matches,
            ),
        ),
        scope_limitations=("private generated semantic model",),
    )
    return case, tracker


@given(
    keyed=st.booleans(),
    amount=st.integers(min_value=1, max_value=10),
    baseline_state=st.integers(min_value=-20, max_value=20),
    clean_prefix=_HISTORY,
    perturbed_prefix=_HISTORY,
    operation_body=_IDENTIFIER,
    key_body=_IDENTIFIER,
)
@_PROPERTY_SETTINGS
def test_generated_fresh_worlds_preserve_axes_schedule_history_and_cleanup(
    keyed: bool,
    amount: int,
    baseline_state: int,
    clean_prefix: tuple[int, ...],
    perturbed_prefix: tuple[int, ...],
    operation_body: str,
    key_body: str,
) -> None:
    operation_id = OperationId(f"operation/{operation_body}")
    command = _Command(amount=amount, operation_key=f"key/{key_body}")
    case, tracker = _build_generated_case(
        keyed=keyed,
        command=command,
        operation_id=operation_id,
        baseline_state=baseline_state,
        clean_prefix=clean_prefix,
        perturbed_prefix=perturbed_prefix,
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is (AxisStatus.PASS if keyed else AxisStatus.FAIL)
    assert len(result.confirmations) == (0 if keyed else 2)
    assert result.scope.operation_id == operation_id
    assert result.scope.operation_key == command.operation_key
    assert result.primary.retry_results[0].verdict is (
        InvariantVerdict.PASS if keyed else InvariantVerdict.INCONCLUSIVE
    )
    assert result.primary.retry_results[0].candidate is (not keyed)
    pairs = (result.primary, *result.confirmations)
    for pair in pairs:
        assert pair.clean is not None
        assert pair.perturbed is not None
        assert pair.clean.baseline.state == pair.perturbed.baseline.state == baseline_state
        assert pair.clean.baseline.history == clean_prefix
        assert pair.perturbed.baseline.history == perturbed_prefix
        assert pair.clean.history_delta == (amount,)
        assert tuple(pair.perturbed.attempts[-1].observation.history[len(perturbed_prefix) :]) == (
            (amount,) if keyed else (amount, amount)
        )
        assert len(pair.perturbed.attempts) == 2
        first, second = pair.perturbed.attempts
        assert pair.perturbed.harness.boundary_name == PROVIDER_RESULT_LOSS.boundary_name
        assert first.outcome == PROVIDER_RESULT_LOSS.lost_outcome
        assert second.outcome == "returned"
        assert first.returned_result is None
        assert first.identity.operation_id == second.identity.operation_id == operation_id
        assert first.identity.delivery_id != second.identity.delivery_id
        assert first.identity.attempt_id != second.identity.attempt_id
        assert pair.perturbed.harness.injected_attempt_id == first.identity.attempt_id
        assert pair.perturbed.harness.reached_attempt_ids == (
            first.identity.attempt_id,
            second.identity.attempt_id,
        )
        assert command.operation_key not in {
            first.identity.delivery_id.value,
            first.identity.attempt_id.value,
            second.identity.delivery_id.value,
            second.identity.attempt_id.value,
        }
    assert len(tracker.provisioned) == (2 if keyed else 6)
    assert tracker.cleanup_attempts == len(tracker.provisioned)


def test_empty_axis_contracts_remain_unverified_or_invalid() -> None:
    assert _aggregate((), allow_unverified=True, has_error=False).status is AxisStatus.UNVERIFIED
    assert _aggregate((), allow_unverified=False, has_error=False).status is AxisStatus.ERROR
    case: CaseDefinition[str, str, int, int, int, int] = CaseDefinition(
        subject_name="generated_subject",
        input="input",
        operation_id=OperationId("operation/generated"),
        operation_key_selector=lambda _value: None,
        world_factory=_unused_world_factory,
        coverage=SurfaceCoverage(
            surface="effects",
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="generated_test_model",
        ),
        canonicalize_state=lambda value: value,
        canonicalize_event=lambda value: value,
        clean_assertions=(),
        retry_invariants=(),
        scope_limitations=("generated private test model",),
    )

    with pytest.raises(CaseConfigurationError, match="retry invariant"):
        evaluate_case(case)
