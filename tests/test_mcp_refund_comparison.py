"""End-to-end semantic evidence for MCP stdio client-result loss."""

import os
import sqlite3
from pathlib import Path

import pytest

from effectprobe._lost_result import MCP_CLIENT_RESULT_LOSS, OperationId
from effectprobe._mcp_refund_comparison import McpRefundCaseResult, build_mcp_refund_case
from effectprobe._mcp_refund_store import (
    commit_refund,
    initialize_refund_database,
    observe_refunds,
    read_mcp_deliveries,
)
from effectprobe._semantic_kernel import AxisStatus, InvariantVerdict, evaluate_case


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True


def test_vulnerable_mcp_refund_has_confirmed_retry_failure() -> None:
    case, tracker = build_mcp_refund_case(keyed=False)

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.FAIL
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.FAIL
    assert len(result.confirmations) == 2
    assert len(tracker.worlds) == 6
    assert tracker.preflight_attempts == 1
    assert tracker.provision_attempts == tracker.cleanup_attempts == 6
    assert all(world.cleaned and world.database_removed for world in tracker.worlds)

    for pair in (result.primary, *result.confirmations):
        assert pair.clean is not None
        assert pair.perturbed is not None
        assert len(pair.clean.history_delta) == 1
        assert len(pair.perturbed.attempts[0].observation.history) == 1
        assert len(pair.perturbed.attempts[1].observation.history) == 2


def test_keyed_mcp_refund_has_clean_and_retry_pass() -> None:
    case, tracker = build_mcp_refund_case(keyed=True)

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.PASS
    assert result.retry_safety.invariants[0].verdict is InvariantVerdict.PASS
    assert result.confirmations == ()
    assert len(tracker.worlds) == 2
    assert len(result.primary.clean.history_delta) == 1  # type: ignore[union-attr]
    assert result.primary.perturbed is not None
    assert len(result.primary.perturbed.attempts[-1].observation.history) == 1
    assert all(world.cleaned and world.database_removed for world in tracker.worlds)


def test_mcp_fault_occurs_after_validated_result_and_preserves_identity_boundaries() -> None:
    case, tracker = build_mcp_refund_case(keyed=True)

    result = evaluate_case(case)

    assert result.scope.operation_id == OperationId("operation/refund-001")
    assert result.scope.operation_key == "refund-key-001"
    assert result.scope.schedule == MCP_CLIENT_RESULT_LOSS.scope_name
    assert result.primary.clean is not None
    assert result.primary.perturbed is not None
    run = result.primary.perturbed
    assert run.harness.boundary_name == "mcp_client_result_delivery"
    assert run.attempts[0].outcome == "client_result_lost"
    assert run.harness.undelivered_result.refund_id == "refund/1"
    assert run.attempts[0].observation.history[0].refund_id == "refund/1"
    assert run.subject_result == run.harness.undelivered_result

    identities = (result.primary.clean.attempt.identity, *(item.identity for item in run.attempts))
    assert {identity.operation_id for identity in identities} == {result.scope.operation_id}
    assert len({identity.delivery_id for identity in identities}) == 3
    assert len({identity.attempt_id for identity in identities}) == 3

    perturbed_deliveries = tracker.worlds[1].deliveries
    assert len(perturbed_deliveries) == 2
    assert len({delivery.mcp_request_id for delivery in perturbed_deliveries}) == 2
    assert {delivery.operation_key for delivery in perturbed_deliveries} == {"refund-key-001"}
    assert len({delivery.process_id for delivery in perturbed_deliveries}) == 1
    assert all(
        delivery.mcp_request_id not in {result.scope.operation_id.value, result.scope.operation_key}
        for delivery in perturbed_deliveries
    )
    assert all(not _process_exists(delivery.process_id) for delivery in perturbed_deliveries)


def test_unsafe_tool_receives_but_does_not_use_the_operation_key() -> None:
    case, tracker = build_mcp_refund_case(keyed=False)

    result = evaluate_case(case)

    assert result.primary.perturbed is not None
    history = result.primary.perturbed.attempts[-1].observation.history
    assert tuple(event.operation_key for event in history) == (None, None)
    assert {item.operation_key for item in tracker.worlds[1].deliveries} == {"refund-key-001"}


def test_sqlite_refund_history_rejects_update_and_delete(tmp_path: Path) -> None:
    database = tmp_path / "refunds.sqlite3"
    initialize_refund_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO refund_events(
                refund_id, payment_id, amount_minor_units, operation_key
            ) VALUES ('refund/1', 'payment/refund-001', 2500, NULL)
            """
        )

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE refund_events SET amount_minor_units = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM refund_events")


def test_sqlite_unsafe_and_keyed_commits_keep_effect_and_delivery_evidence_distinct(
    tmp_path: Path,
) -> None:
    unsafe_database = tmp_path / "unsafe.sqlite3"
    initialize_refund_database(unsafe_database)
    first = commit_refund(
        unsafe_database,
        mode="unsafe",
        payment_id="payment/refund-001",
        amount_minor_units=2_500,
        operation_key="refund-key-001",
        mcp_request_id="request/1",
    )
    second = commit_refund(
        unsafe_database,
        mode="unsafe",
        payment_id="payment/refund-001",
        amount_minor_units=2_500,
        operation_key="refund-key-001",
        mcp_request_id="request/2",
    )

    assert first.refund_id == "refund/1"
    assert second.refund_id == "refund/2"
    unsafe_observation = observe_refunds(unsafe_database)
    assert unsafe_observation.state.refunded_minor_units == 5_000
    assert tuple(event.operation_key for event in unsafe_observation.history) == (None, None)
    assert tuple(item.mcp_request_id for item in read_mcp_deliveries(unsafe_database)) == (
        "request/1",
        "request/2",
    )

    keyed_database = tmp_path / "keyed.sqlite3"
    initialize_refund_database(keyed_database)
    keyed_first = commit_refund(
        keyed_database,
        mode="keyed",
        payment_id="payment/refund-001",
        amount_minor_units=2_500,
        operation_key="refund-key-001",
        mcp_request_id="request/1",
    )
    keyed_second = commit_refund(
        keyed_database,
        mode="keyed",
        payment_id="payment/refund-001",
        amount_minor_units=2_500,
        operation_key="refund-key-001",
        mcp_request_id="request/2",
    )

    assert keyed_second == keyed_first
    keyed_observation = observe_refunds(keyed_database)
    assert keyed_observation.state.refunded_minor_units == 2_500
    assert keyed_observation.state.key_index_size == 1
    assert len(keyed_observation.history) == 1
    assert len(read_mcp_deliveries(keyed_database)) == 2


@pytest.mark.parametrize(
    ("payment_id", "amount", "message"),
    [
        ("payment/missing", 2_500, "unknown payment"),
        ("payment/refund-001", 20_000, "exceeds"),
    ],
)
def test_sqlite_refund_rejects_invalid_requests_without_committing(
    tmp_path: Path,
    payment_id: str,
    amount: int,
    message: str,
) -> None:
    database = tmp_path / f"invalid-{amount}.sqlite3"
    initialize_refund_database(database)

    with pytest.raises(ValueError, match=message):
        commit_refund(
            database,
            mode="unsafe",
            payment_id=payment_id,
            amount_minor_units=amount,
            operation_key="refund-key-001",
            mcp_request_id="request/1",
        )

    assert observe_refunds(database).history == ()
    assert read_mcp_deliveries(database) == ()


def test_mcp_refund_comparisons_are_structurally_identical_in_ten_fresh_runs() -> None:
    vulnerable: list[McpRefundCaseResult] = []
    keyed: list[McpRefundCaseResult] = []
    for _ in range(10):
        vulnerable_case, _vulnerable_tracker = build_mcp_refund_case(keyed=False)
        keyed_case, _keyed_tracker = build_mcp_refund_case(keyed=True)
        vulnerable.append(evaluate_case(vulnerable_case))
        keyed.append(evaluate_case(keyed_case))

    assert tuple(vulnerable) == (vulnerable[0],) * 10
    assert tuple(keyed) == (keyed[0],) * 10
