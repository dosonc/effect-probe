"""Acceptance coverage for the public experimental Python case contract."""

import inspect
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from effectprobe.experimental import (
    ApplicabilityStatus,
    AxisStatus,
    Canonicalization,
    Case,
    CleanAssertion,
    CleanContext,
    CleanupStatus,
    Decision,
    EvidenceKind,
    EvidenceRequirement,
    InvariantVerdict,
    Observation,
    ObserverCoverage,
    RetryContext,
    RetryInvariant,
    TrialId,
    World,
    WorldSession,
    render_terminal,
    run_case,
)

type _World = World[str, str, int, int]
type _Session = WorldSession[str, str, int, int]
type _Case = Case[str, str, int, int]

_ROOT = Path(__file__).resolve().parents[1]
_LOCK = _ROOT / "uv.lock"


@dataclass(slots=True)
class _Tracker:
    trials: list[str] = field(default_factory=lambda: list[str]())
    cleanup_attempts: int = 0
    evaluator_calls: int = 0


def _build_case(
    *,
    safe: bool = False,
    no_boundary: bool = False,
    observer_error: bool = False,
    cleanup_error: bool = False,
    applicable: Callable[[str], bool] = lambda _value: True,
    preflight: Callable[[], None] = lambda: None,
    coverage: ObserverCoverage | None = None,
    input_value: str = "SECRET concrete input",
    source_files: tuple[Path, ...] = (Path(__file__),),
    dependency_lock: Path = _LOCK,
    retry_name: str = "no_additional_effect",
) -> tuple[_Case, _Tracker]:
    tracker = _Tracker()

    def world_factory(trial_id: TrialId) -> _Session:
        state = 0
        history: list[int] = []
        invocations = 0
        observations = 0

        def provision() -> _World:
            nonlocal state, invocations, observations
            tracker.trials.append(trial_id.value)

            def invoke(_input: str, deliver_result: Callable[[str], str]) -> str:
                nonlocal state, invocations
                invocations += 1
                if no_boundary and trial_id.value.endswith("/perturbed"):
                    return "SECRET bypassed result"
                if not (safe and invocations > 1):
                    state += 1
                    history.append(1)
                return deliver_result(f"SECRET receipt/{state}")

            def observe() -> Observation[int, int]:
                nonlocal observations
                observations += 1
                if observer_error and observations >= 2:
                    raise RuntimeError("SECRET observer failure /private/path")
                return Observation(state, tuple(history))

            def validate_fixture(value: Observation[int, int]) -> None:
                if value.state != 0 or value.history:
                    raise ValueError("SECRET invalid fixture")

            return World(invoke=invoke, observe=observe, validate_fixture=validate_fixture)

        def cleanup(_world: _World | None) -> None:
            tracker.cleanup_attempts += 1
            if cleanup_error and trial_id.value == "primary/perturbed":
                raise RuntimeError("SECRET cleanup failure /private/path")

        return WorldSession(provision=provision, cleanup=cleanup)

    def clean(context: CleanContext[str, str, int, int]) -> Decision:
        tracker.evaluator_calls += 1
        passed = (
            context.final.state - context.baseline.state == 1
            and context.history_delta == (1,)
            and context.returned_result == "SECRET receipt/1"
        )
        return Decision(passed, "one_effect" if passed else "clean_mismatch")

    def retry(context: RetryContext[str, str, int, int]) -> Decision:
        tracker.evaluator_calls += 1
        passed = (
            context.canonical_clean_history_delta == context.canonical_perturbed_history_delta
            and context.perturbed_final.state - context.perturbed_baseline.state == 1
            and context.attempt_count == 2
            and context.boundary_reached
            and context.fault_injected
        )
        return Decision(passed, "one_effect_after_retry" if passed else "additional_effect")

    surface = "effects"
    state_requirement = EvidenceRequirement(EvidenceKind.STATE, surface)
    history_requirement = EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, surface)
    result_requirement = EvidenceRequirement(EvidenceKind.SUBJECT_RESULT)
    case = Case(
        case_name="external_test_case",
        subject_name="trusted_test_subject",
        input=input_value,
        operation_id="operation/test-001",
        operation_key_selector=lambda _value: "SECRET operation key",
        canonicalize_input=lambda value: {"input": value},
        canonicalization=Canonicalization(
            input="test_input/string-v1",
            state="test_state/integer-v1",
            event="test_event/integer-v1",
        ),
        source_files=source_files,
        dependency_lock=dependency_lock,
        world_factory=world_factory,
        coverage=coverage
        or ObserverCoverage(
            surface=surface,
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="trusted_local_test_model",
            limitations=("single_surface_only",),
        ),
        canonicalize_state=lambda value: value,
        canonicalize_event=lambda value: value,
        clean_assertions=(
            CleanAssertion(
                "one_effect",
                (state_requirement, history_requirement, result_requirement),
                clean,
            ),
        ),
        retry_invariants=(
            RetryInvariant(
                retry_name,
                (state_requirement, history_requirement),
                retry,
            ),
        ),
        limitations=("trusted_local_code", "no_verified_replay"),
        preflight=preflight,
        applicable=applicable,
    )
    return case, tracker


def test_public_keyed_case_passes_with_redacted_scope_and_distinct_identities() -> None:
    case, tracker = _build_case(safe=True)

    report = run_case(case)

    assert report.report_error is None
    assert report.applicability is ApplicabilityStatus.APPLICABLE
    assert report.clean_validity is not None
    assert report.retry_safety is not None
    assert report.clean_validity.status is AxisStatus.PASS
    assert report.retry_safety.status is AxisStatus.PASS
    assert not hasattr(report, "overall_status")
    assert report.confirmations == ()
    assert tracker.trials == ["primary/clean", "primary/perturbed"]
    assert tracker.cleanup_attempts == 2
    assert report.scope is not None
    assert report.scope.operation_key_present
    assert report.scope.operation_key_sha256 is not None
    assert len(report.scope.scope_sha256) == 64
    assert report.scope.schedule == "provider_commit_then_lose_first_result_and_retry_once"
    assert report.scope.boundary == "provider_result_delivery"
    assert report.scope.canonicalization.event == "test_event/integer-v1"
    assert report.scope.clean_assertions[0].requirements[1].kind is EvidenceKind.COMPLETE_HISTORY
    assert report.scope.retry_invariants[0].requirements[1].kind is EvidenceKind.COMPLETE_HISTORY
    assert report.primary is not None

    attempts = (*report.primary.clean.attempts, *report.primary.perturbed.attempts)
    assert len(attempts) == 3
    assert len({item.delivery_id for item in attempts}) == 3
    assert len({item.attempt_id for item in attempts}) == 3
    assert sum(item.fault_injected for item in attempts) == 1
    assert sum(item.boundary_reached for item in attempts) == 2
    assert all("SECRET operation key" not in item.attempt_id for item in attempts)
    assert report.primary.perturbed.baseline_history_count == 0
    assert tuple(item.observed_history_count for item in report.primary.perturbed.attempts) == (
        1,
        1,
    )

    rendered = render_terminal(report)
    assert "clean_validity: PASS" in rendered
    assert "retry_safety: PASS" in rendered
    assert "primary_perturbed_history_counts: 0/1/1" in rendered
    for forbidden in (
        "SECRET",
        "receipt/1",
        "operation key",
        str(Path(__file__)),
        "/private/path",
    ):
        assert forbidden not in rendered


def test_public_unsafe_case_requires_two_fresh_confirmations_for_failure() -> None:
    case, tracker = _build_case()

    report = run_case(case)

    assert report.clean_validity is not None
    assert report.retry_safety is not None
    assert report.clean_validity.status is AxisStatus.PASS
    assert report.retry_safety.status is AxisStatus.FAIL
    assert report.retry_safety.invariants[0].verdict is InvariantVerdict.FAIL
    assert report.retry_safety.invariants[0].reason_code == "additional_effect"
    assert len(report.confirmations) == 2
    assert tracker.trials == [
        "primary/clean",
        "primary/perturbed",
        "confirmation/1/clean",
        "confirmation/1/perturbed",
        "confirmation/2/clean",
        "confirmation/2/perturbed",
    ]
    assert tracker.cleanup_attempts == 6
    assert "retry_safety: FAIL" in render_terminal(report)
    assert "confirmation_pair_count: 2" in render_terminal(report)
    assert report.primary is not None
    assert report.primary.perturbed.baseline_history_count == 0
    assert tuple(item.observed_history_count for item in report.primary.perturbed.attempts) == (
        1,
        2,
    )
    assert "primary_perturbed_history_counts: 0/1/2" in render_terminal(report)


def test_unreached_boundary_and_missing_history_are_inconclusive() -> None:
    no_boundary, no_boundary_tracker = _build_case(no_boundary=True)

    no_boundary_report = run_case(no_boundary)

    assert no_boundary_report.clean_validity is not None
    assert no_boundary_report.retry_safety is not None
    assert no_boundary_report.clean_validity.status is AxisStatus.PASS
    assert no_boundary_report.retry_safety.status is AxisStatus.INCONCLUSIVE
    assert no_boundary_report.retry_safety.invariants[0].reason_code == "boundary_not_reached"
    assert no_boundary_tracker.trials == ["primary/clean", "primary/perturbed"]

    coverage = ObserverCoverage(
        surface="effects",
        state=True,
        history=True,
        complete_history=False,
        observation_interval="baseline_to_final",
        provenance="trusted_local_test_model",
    )
    missing_history, tracker = _build_case(safe=True, coverage=coverage)

    missing_report = run_case(missing_history)

    assert missing_report.clean_validity is not None
    assert missing_report.retry_safety is not None
    assert missing_report.clean_validity.status is AxisStatus.INCONCLUSIVE
    assert missing_report.retry_safety.status is AxisStatus.INCONCLUSIVE
    assert missing_report.retry_safety.invariants[0].missing_evidence == (
        "effects:complete_history",
    )
    assert tracker.evaluator_calls == 0


def test_preflight_and_trial_errors_are_bounded_and_do_not_leak_messages() -> None:
    def fail_preflight() -> None:
        raise RuntimeError("SECRET preflight failure /private/path")

    preflight_case, preflight_tracker = _build_case(preflight=fail_preflight)

    preflight = run_case(preflight_case)

    assert preflight.report_error is not None
    assert preflight.report_error.phase == "preflight"
    assert preflight.report_error.category == "preflight_callback"
    assert preflight.clean_validity is None
    assert preflight.retry_safety is None
    assert preflight_tracker.trials == []
    assert "SECRET" not in render_terminal(preflight)
    assert "/private/path" not in render_terminal(preflight)

    observer_case, observer_tracker = _build_case(observer_error=True)

    observer = run_case(observer_case)

    assert observer.report_error is None
    assert observer.clean_validity is not None
    assert observer.retry_safety is not None
    assert observer.clean_validity.status is AxisStatus.ERROR
    assert observer.retry_safety.status is AxisStatus.ERROR
    assert observer.primary is not None
    assert observer.primary.errors[0].category == "observer"
    assert observer.primary.clean.cleanup is CleanupStatus.PASS
    assert observer.primary.perturbed.cleanup is CleanupStatus.NOT_ATTEMPTED
    assert observer_tracker.trials == ["primary/clean"]
    assert observer_tracker.cleanup_attempts == 1
    assert "SECRET" not in repr(observer)
    assert "/private/path" not in repr(observer)
    assert "error: axis=clean phase=final_observer category=observer" in render_terminal(observer)


def test_cleanup_error_prevents_retry_pass_without_erasing_completed_clean_axis() -> None:
    case, tracker = _build_case(safe=True, cleanup_error=True)

    report = run_case(case)

    assert report.clean_validity is not None
    assert report.retry_safety is not None
    assert report.clean_validity.status is AxisStatus.PASS
    assert report.retry_safety.status is AxisStatus.ERROR
    assert report.primary is not None
    assert report.primary.perturbed.cleanup is CleanupStatus.ERROR
    assert any(item.category == "world_cleanup" for item in report.primary.errors)
    assert tracker.cleanup_attempts == 2


@pytest.mark.parametrize("has_clean_contract", [True, False])
def test_not_applicable_provisions_no_world_and_uses_zero_pair_axis_rules(
    has_clean_contract: bool,
) -> None:
    case, tracker = _build_case(applicable=lambda _value: False)
    if not has_clean_contract:
        case = replace(case, clean_assertions=())

    report = run_case(case)

    assert report.report_error is None
    assert report.applicability is ApplicabilityStatus.NOT_APPLICABLE
    assert report.applicable_pair_count == 0
    assert report.primary is None
    assert report.clean_validity is not None
    assert report.retry_safety is not None
    assert report.clean_validity.status is (
        AxisStatus.INCONCLUSIVE if has_clean_contract else AxisStatus.UNVERIFIED
    )
    assert report.retry_safety.status is AxisStatus.INCONCLUSIVE
    assert tracker.trials == []
    assert tracker.cleanup_attempts == 0


def test_configuration_errors_are_report_level_and_precede_callbacks(tmp_path: Path) -> None:
    calls = 0

    def forbidden_preflight() -> None:
        nonlocal calls
        calls += 1

    case, tracker = _build_case(preflight=forbidden_preflight)
    case = replace(case, retry_invariants=())

    empty_contract = run_case(case)

    assert empty_contract.report_error is not None
    assert empty_contract.report_error.phase == "configuration"
    assert empty_contract.report_error.category == "empty_retry_contract"
    assert calls == 0
    assert tracker.trials == []

    missing = tmp_path / "missing.py"
    bad_source = run_case(
        replace(case, retry_invariants=_build_case()[0].retry_invariants, source_files=(missing,))
    )

    assert bad_source.report_error is not None
    assert bad_source.report_error.category == "invalid_source_fingerprint"
    assert calls == 0


def test_scope_fingerprint_changes_for_every_caller_selectable_dimension(
    tmp_path: Path,
) -> None:
    source_a = tmp_path / "case-a.py"
    source_b = tmp_path / "case-b.py"
    lock_a = tmp_path / "uv-a.lock"
    lock_b = tmp_path / "uv-b.lock"
    source_a.write_text("case = 1\n", encoding="utf-8")
    source_b.write_text("case = 2\n", encoding="utf-8")
    lock_a.write_text("version = 1\n", encoding="utf-8")
    lock_b.write_text("version = 2\n", encoding="utf-8")

    base, _tracker = _build_case(
        safe=True,
        applicable=lambda _value: False,
        source_files=(source_a,),
        dependency_lock=lock_a,
    )
    variants = (
        replace(base, source_files=(source_b,)),
        replace(base, dependency_lock=lock_b),
        replace(base, input="different input"),
        replace(
            base,
            retry_invariants=(replace(base.retry_invariants[0], name="different_retry_contract"),),
        ),
        replace(
            base,
            coverage=replace(base.coverage, provenance="different_test_model"),
        ),
        replace(
            base,
            canonicalization=replace(
                base.canonicalization,
                event="different_event/integer-v1",
            ),
        ),
    )

    reports = (run_case(base), *(run_case(item) for item in variants))
    scopes = tuple(report.scope for report in reports)

    assert all(scope is not None for scope in scopes)
    fingerprints = {scope.scope_sha256 for scope in scopes if scope is not None}
    assert len(fingerprints) == len(scopes)
    assert all(
        scope is not None
        and scope.schedule == "provider_commit_then_lose_first_result_and_retry_once"
        and scope.boundary == "provider_result_delivery"
        for scope in scopes
    )
    assert "schedule" not in inspect.signature(Case).parameters


@pytest.mark.parametrize("mode", ["keyed", "unsafe"])
def test_documented_example_uses_only_public_imports_and_has_bounded_output(
    mode: str,
) -> None:
    example = _ROOT / "examples" / "experimental_refund_case.py"
    source = example.read_text(encoding="utf-8")

    assert "effectprobe._" not in source
    completed = subprocess.run(
        [
            sys.executable,
            str(example),
            "--mode",
            mode,
        ],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    repeated = subprocess.run(
        [
            sys.executable,
            str(example),
            "--mode",
            mode,
        ],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    assert repeated.returncode == 0
    assert completed.stderr == ""
    assert repeated.stderr == ""
    assert repeated.stdout == completed.stdout
    assert "clean_validity: PASS" in completed.stdout
    assert f"retry_safety: {'PASS' if mode == 'keyed' else 'FAIL'}" in completed.stdout
    assert (
        f"primary_perturbed_history_counts: {'0/1/1' if mode == 'keyed' else '0/1/2'}"
        in completed.stdout
    )
    assert "SECRET" not in completed.stdout
    assert "refund-key-001" not in completed.stdout
    assert "payment/refund-001" not in completed.stdout
    assert str(_ROOT) not in completed.stdout
