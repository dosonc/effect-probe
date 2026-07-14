"""Private vulnerable and provider-keyed refund comparison tests."""

import pytest

from effectprobe._lost_result import OperationId
from effectprobe._refund_comparison import (
    FakeRefundProvider,
    RefundCommand,
    RefundEvent,
    RefundReceipt,
    RefundState,
    build_refund_case,
    evaluate_keyed_refund,
    evaluate_vulnerable_refund,
    validate_refund_fixture,
)
from effectprobe._semantic_kernel import (
    AxisStatus,
    InvariantVerdict,
    SurfaceObservation,
    evaluate_case,
)


def test_provider_stores_keyed_receipt_before_delivering_it() -> None:
    provider = FakeRefundProvider()
    command = RefundCommand("payment/refund-001", 2_500, "refund-key-001")
    delivery_attempts = 0

    def lose_first_delivery(receipt: RefundReceipt) -> RefundReceipt:
        nonlocal delivery_attempts
        delivery_attempts += 1
        if delivery_attempts == 1:
            raise RuntimeError("lost")
        return receipt

    try:
        provider.refund(
            command,
            operation_key=command.operation_key,
            deliver_result=lose_first_delivery,
        )
    except RuntimeError as error:
        assert str(error) == "lost"
    else:
        raise AssertionError("first delivery should be lost")

    after_commit = provider.observe()
    assert after_commit.state.refunded_minor_units == 2_500
    assert after_commit.state.key_index_size == 1
    assert after_commit.history == (
        RefundEvent(
            "refund/1",
            "payment/refund-001",
            2_500,
            "refund-key-001",
        ),
    )

    repeated = provider.refund(
        command,
        operation_key=command.operation_key,
        deliver_result=lose_first_delivery,
    )

    assert repeated == RefundReceipt("refund/1", "payment/refund-001", 2_500)
    assert provider.observe() == after_commit


def test_fixture_rejects_an_inconsistent_seeded_key_index() -> None:
    inconsistent = SurfaceObservation(
        state=RefundState(
            payment_id="payment/refund-001",
            payment_minor_units=10_000,
            refunded_minor_units=0,
            key_index_size=1,
        ),
        history=(),
    )

    with pytest.raises(ValueError, match="not fresh and empty"):
        validate_refund_fixture(inconsistent)


def test_vulnerable_refund_has_clean_pass_and_confirmed_retry_failure() -> None:
    case, tracker = build_refund_case(keyed=False)

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.FAIL
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.FAIL
    assert result.retry_safety.invariants[0].candidate is False
    assert len(result.confirmations) == 2
    assert len(tracker.worlds) == 6
    assert tracker.cleanup_attempts == 6
    assert all(world.cleaned for world in tracker.worlds)

    primary_clean = tracker.worlds[0].provider.observe()
    primary_perturbed = tracker.worlds[1].provider.observe()
    assert len(primary_clean.history) == 1
    assert primary_clean.state.refunded_minor_units == 2_500
    assert len(primary_perturbed.history) == 2
    assert primary_perturbed.state.refunded_minor_units == 5_000

    for pair in (result.primary, *result.confirmations):
        assert pair.clean is not None
        assert pair.perturbed is not None
        assert len(pair.clean.history_delta) == 1
        assert len(pair.perturbed.attempts[-1].observation.history) == 2


def test_keyed_refund_has_clean_and_retry_pass_without_confirmation() -> None:
    case, tracker = build_refund_case(keyed=True)

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.PASS
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.PASS
    assert result.confirmations == ()
    assert len(tracker.worlds) == 2
    assert tracker.cleanup_attempts == 2
    assert all(world.cleaned for world in tracker.worlds)

    clean_world, perturbed_world = tracker.worlds
    assert clean_world.provider.observe().state.refunded_minor_units == 2_500
    assert perturbed_world.provider.observe().state.refunded_minor_units == 2_500
    assert len(clean_world.provider.observe().history) == 1
    assert len(perturbed_world.provider.observe().history) == 1
    assert perturbed_world.subject.received_receipts == (
        RefundReceipt("refund/1", "payment/refund-001", 2_500),
    )

    assert result.primary.perturbed is not None
    run = result.primary.perturbed
    assert run.harness.undelivered_result == RefundReceipt("refund/1", "payment/refund-001", 2_500)
    assert run.subject_result == run.harness.undelivered_result
    assert run.attempts[0].returned_result is None
    assert run.attempts[1].returned_result == run.subject_result


def test_operation_key_is_recorded_without_replacing_harness_identities() -> None:
    case, _tracker = build_refund_case(keyed=True)

    result = evaluate_case(case)

    assert result.scope.operation_key == "refund-key-001"
    assert result.scope.operation_id == OperationId("operation/refund-001")
    assert result.scope.input.operation_key == result.scope.operation_key
    assert result.primary.clean is not None
    assert result.primary.perturbed is not None

    identities = (
        result.primary.clean.attempt.identity,
        *(attempt.identity for attempt in result.primary.perturbed.attempts),
    )
    assert len({identity.attempt_id for identity in identities}) == 3
    assert len({identity.delivery_id for identity in identities}) == 3
    assert {identity.operation_id for identity in identities} == {result.scope.operation_id}
    assert all("refund-key-001" not in identity.attempt_id.value for identity in identities)
    assert identities[0].trial_id.value == "primary/clean"
    assert identities[1].trial_id.value == "primary/perturbed"


def test_private_scope_records_why_result_is_not_reportable() -> None:
    result = evaluate_keyed_refund()

    assert result.scope.reportable is False
    assert result.scope.coverage.surface == "refunds"
    assert result.scope.coverage.complete_history is True
    assert result.scope.coverage.observation_interval == "baseline_to_final"
    assert result.scope.coverage.provenance == "harness_controlled_model"
    assert result.scope.limitations == (
        "private provisional semantic outcome",
        "code and dependency identity are not recorded",
        "runtime and environment fingerprints are not recorded",
        "runner and evidence-schema versions are not recorded",
    )


def test_refund_comparison_is_structurally_identical_in_100_fresh_evaluations() -> None:
    vulnerable = tuple(evaluate_vulnerable_refund() for _ in range(100))
    keyed = tuple(evaluate_keyed_refund() for _ in range(100))

    assert vulnerable == (vulnerable[0],) * 100
    assert keyed == (keyed[0],) * 100
