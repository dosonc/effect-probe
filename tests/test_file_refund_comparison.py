"""Evidence and lifecycle tests for the private file-journal observer."""

from pathlib import Path

import pytest

from effectprobe._file_refund_comparison import (
    FileRefundJournalError,
    FileRefundObserver,
    FileRefundProvider,
    build_file_refund_case,
    initialize_file_refund_journal,
    read_file_refund_journal,
)
from effectprobe._lost_result import OperationId
from effectprobe._refund_comparison import RefundCaseResult, RefundCommand
from effectprobe._semantic_kernel import AxisStatus, InvariantVerdict, evaluate_case


def _command(
    *,
    payment_id: str = "payment/refund-001",
    amount_minor_units: int = 2_500,
) -> RefundCommand:
    return RefundCommand(payment_id, amount_minor_units, "refund-key-001")


def test_vulnerable_file_refund_has_confirmed_retry_failure_and_cleanup() -> None:
    case, tracker = build_file_refund_case(keyed=False)

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.FAIL
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.FAIL
    assert result.scope.coverage.provenance == "harness_controlled_file_journal_fixture"
    assert len(result.confirmations) == 2
    assert len(tracker.worlds) == 6
    assert tracker.provision_attempts == tracker.cleanup_attempts == 6
    assert all(world.cleaned and world.journal_removed for world in tracker.worlds)

    for pair in (result.primary, *result.confirmations):
        assert pair.clean is not None
        assert pair.perturbed is not None
        assert len(pair.clean.history_delta) == 1
        assert len(pair.perturbed.attempts[0].observation.history) == 1
        history = pair.perturbed.attempts[1].observation.history
        assert len(history) == 2
        assert tuple(event.operation_key for event in history) == (None, None)


def test_keyed_file_refund_passes_without_an_additional_commit() -> None:
    case, tracker = build_file_refund_case(keyed=True)

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.PASS
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.PASS
    assert result.confirmations == ()
    assert len(tracker.worlds) == 2
    assert result.primary.clean is not None
    assert result.primary.perturbed is not None
    assert len(result.primary.clean.history_delta) == 1
    history = result.primary.perturbed.attempts[-1].observation.history
    assert len(history) == 1
    assert history[0].operation_key == "refund-key-001"
    assert result.primary.perturbed.subject_result.refund_id == history[0].refund_id
    assert all(world.cleaned and world.journal_removed for world in tracker.worlds)


def test_file_case_preserves_logical_delivery_attempt_and_key_boundaries() -> None:
    case, _tracker = build_file_refund_case(keyed=True)

    result = evaluate_case(case)

    assert result.scope.operation_id == OperationId("operation/refund-001")
    assert result.scope.operation_key == "refund-key-001"
    assert result.primary.clean is not None
    assert result.primary.perturbed is not None
    attempts = (result.primary.clean.attempt, *result.primary.perturbed.attempts)
    assert {attempt.identity.operation_id for attempt in attempts} == {result.scope.operation_id}
    assert len({attempt.identity.delivery_id for attempt in attempts}) == 3
    assert len({attempt.identity.attempt_id for attempt in attempts}) == 3
    assert all(
        event.operation_key == result.scope.operation_key
        for attempt in result.primary.perturbed.attempts
        for event in attempt.observation.history
    )
    assert all(
        attempt.identity.attempt_id.value != result.scope.operation_key
        and attempt.identity.delivery_id.value != result.scope.operation_key
        for attempt in attempts
    )


def test_file_journal_writer_appends_and_keyed_provider_reuses_committed_event(
    tmp_path: Path,
) -> None:
    unsafe_path = tmp_path / "unsafe.jsonl"
    initialize_file_refund_journal(unsafe_path)
    unsafe = FileRefundProvider(unsafe_path)

    unsafe.refund(_command(), operation_key=None, deliver_result=lambda receipt: receipt)
    first_bytes = unsafe_path.read_bytes()
    unsafe.refund(_command(), operation_key=None, deliver_result=lambda receipt: receipt)
    second_bytes = unsafe_path.read_bytes()

    assert first_bytes.endswith(b"\n")
    assert second_bytes.startswith(first_bytes)
    unsafe_observation = FileRefundObserver(unsafe_path).observe()
    assert unsafe_observation.state.refunded_minor_units == 5_000
    assert len(unsafe_observation.history) == 2

    keyed_path = tmp_path / "keyed.jsonl"
    initialize_file_refund_journal(keyed_path)
    keyed = FileRefundProvider(keyed_path)
    keyed_command = _command(amount_minor_units=6_000)
    first = keyed.refund(
        keyed_command,
        operation_key="refund-key-001",
        deliver_result=lambda receipt: receipt,
    )
    committed_bytes = keyed_path.read_bytes()
    second = keyed.refund(
        keyed_command,
        operation_key="refund-key-001",
        deliver_result=lambda receipt: receipt,
    )

    assert second == first
    assert keyed_path.read_bytes() == committed_bytes
    keyed_observation = FileRefundObserver(keyed_path).observe()
    assert keyed_observation.state.refunded_minor_units == 6_000
    assert keyed_observation.state.key_index_size == 1
    assert len(keyed_observation.history) == 1


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (_command(payment_id="payment/missing"), "unknown payment"),
        (_command(amount_minor_units=20_000), "exceeds"),
        (_command(amount_minor_units=0), "exceeds"),
    ],
)
def test_file_provider_rejects_invalid_requests_without_committing(
    tmp_path: Path,
    command: RefundCommand,
    message: str,
) -> None:
    journal = tmp_path / f"invalid-{command.amount_minor_units}.jsonl"
    initialize_file_refund_journal(journal)

    with pytest.raises(ValueError, match=message):
        FileRefundProvider(journal).refund(
            command,
            operation_key=None,
            deliver_result=lambda receipt: receipt,
        )

    assert read_file_refund_journal(journal) == ()


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b'{"refund_id":"refund/1"}\n',
        (
            b'{"amount_minor_units":2500,"operation_key":null,'
            b'"payment_id":"payment/refund-001","refund_id":"refund/1",'
            b'"refund_id":"refund/1"}\n'
        ),
        (
            b'{"amount_minor_units":true,"operation_key":null,'
            b'"payment_id":"payment/refund-001","refund_id":"refund/1"}\n'
        ),
        (
            b'{"amount_minor_units":2500,"operation_key":null,'
            b'"payment_id":"payment/refund-001","refund_id":"refund/1"}'
            b"\x0b"
            b'{"amount_minor_units":2500,"operation_key":null,'
            b'"payment_id":"payment/refund-001","refund_id":"refund/2"}\n'
        ),
        b"\xff\n",
    ],
)
def test_file_observer_rejects_incomplete_or_malformed_history(
    tmp_path: Path,
    payload: bytes,
) -> None:
    journal = tmp_path / "malformed.jsonl"
    initialize_file_refund_journal(journal)
    journal.write_bytes(payload)

    with pytest.raises(FileRefundJournalError):
        FileRefundObserver(journal).observe()


def test_malformed_file_history_maps_to_observer_error_and_cleans_up() -> None:
    case, tracker = build_file_refund_case(
        keyed=True,
        corrupt_journal_trials=frozenset({"primary/clean"}),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(
        error.phase == "final_observer" and error.error_type == "FileRefundJournalError"
        for error in result.primary.errors
    )
    assert tracker.provision_attempts == tracker.cleanup_attempts == 1
    assert all(world.cleaned and world.journal_removed for world in tracker.worlds)


def test_file_refund_comparisons_are_structurally_identical_in_ten_fresh_runs() -> None:
    vulnerable: list[RefundCaseResult] = []
    keyed: list[RefundCaseResult] = []
    for _ in range(10):
        vulnerable_case, _vulnerable_tracker = build_file_refund_case(keyed=False)
        keyed_case, _keyed_tracker = build_file_refund_case(keyed=True)
        vulnerable.append(evaluate_case(vulnerable_case))
        keyed.append(evaluate_case(keyed_case))

    assert tuple(vulnerable) == (vulnerable[0],) * 10
    assert tuple(keyed) == (keyed[0],) * 10
