"""Deterministic duplicate-refund scenario and private controller tests."""

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from effectprobe._lost_result import (
    AttemptId,
    BoundaryNotReached,
    DeliveryId,
    FaultNotPropagated,
    OperationId,
    RunEvidence,
    run_commit_then_lose_first_provider_result,
)

_PAYMENT_ID = "payment/refund-001"
_PAYMENT_MINOR_UNITS = 10_000
_REFUND_MINOR_UNITS = 2_500
_OPERATION_ID = OperationId("operation/refund-001")


@dataclass(frozen=True, slots=True)
class _RefundEvent:
    refund_id: str
    payment_id: str
    amount_minor_units: int


@dataclass(frozen=True, slots=True)
class _RefundReceipt:
    refund_id: str
    amount_minor_units: int


@dataclass(frozen=True, slots=True)
class _RefundObservation:
    payment_id: str
    payment_minor_units: int
    refunded_minor_units: int
    history: tuple[_RefundEvent, ...]


class _FakeRefundProvider:
    """Harness-controlled provider with state and append-only history."""

    def __init__(self) -> None:
        self._refunded_minor_units = 0
        self._history: list[_RefundEvent] = []

    def refund(
        self,
        amount_minor_units: int,
        deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        """Commit one refund before trying to deliver its receipt."""

        refund_id = f"refund/{len(self._history) + 1}"
        event = _RefundEvent(
            refund_id=refund_id,
            payment_id=_PAYMENT_ID,
            amount_minor_units=amount_minor_units,
        )
        self._refunded_minor_units += amount_minor_units
        self._history.append(event)
        receipt = _RefundReceipt(
            refund_id=refund_id,
            amount_minor_units=amount_minor_units,
        )
        return deliver_result(receipt)

    def observe(self) -> _RefundObservation:
        """Return immutable state and committed-history evidence."""

        return _RefundObservation(
            payment_id=_PAYMENT_ID,
            payment_minor_units=_PAYMENT_MINOR_UNITS,
            refunded_minor_units=self._refunded_minor_units,
            history=tuple(self._history),
        )


class _UnsafeRefundSubject:
    """Create one new refund for every concrete invocation."""

    def __init__(self, provider: _FakeRefundProvider) -> None:
        self._provider = provider
        self._received_receipts: list[_RefundReceipt] = []

    @property
    def received_receipts(self) -> tuple[_RefundReceipt, ...]:
        """Receipts that actually returned across the provider boundary."""

        return tuple(self._received_receipts)

    def __call__(
        self,
        deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        receipt = self._provider.refund(_REFUND_MINOR_UNITS, deliver_result)
        self._received_receipts.append(receipt)
        return receipt


@dataclass(frozen=True, slots=True)
class _DuplicateRefundFinding:
    invariant: str
    surface: str
    required_evidence: tuple[str, ...]
    expected_max_new_events: int
    expected_minor_units_per_event: int
    observed_new_events: int
    observed_minor_units: int
    state_delta_minor_units: int
    history_delta: tuple[_RefundEvent, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class _RefundScenarioResult:
    run: RunEvidence[_RefundReceipt, _RefundObservation]
    finding: _DuplicateRefundFinding
    subject_received_receipts: tuple[_RefundReceipt, ...]


def _evaluate_duplicate_refund(
    run: RunEvidence[_RefundReceipt, _RefundObservation],
) -> _DuplicateRefundFinding:
    baseline = run.baseline
    final = run.attempts[-1].observation
    history_delta = final.history[len(baseline.history) :]
    observed_minor_units = sum(event.amount_minor_units for event in history_delta)
    state_delta_minor_units = final.refunded_minor_units - baseline.refunded_minor_units
    return _DuplicateRefundFinding(
        invariant="at_most_one_committed_refund",
        surface="refunds",
        required_evidence=("state", "committed_history"),
        expected_max_new_events=1,
        expected_minor_units_per_event=_REFUND_MINOR_UNITS,
        observed_new_events=len(history_delta),
        observed_minor_units=observed_minor_units,
        state_delta_minor_units=state_delta_minor_units,
        history_delta=history_delta,
        explanation=(
            "Expected at most one new 2,500-unit refund commit; observed two new "
            "committed events totaling 5,000 units."
        ),
    )


def _run_refund_scenario() -> _RefundScenarioResult:
    provider = _FakeRefundProvider()
    subject = _UnsafeRefundSubject(provider)
    run = run_commit_then_lose_first_provider_result(
        operation_id=_OPERATION_ID,
        invoke=subject,
        observe=provider.observe,
    )
    return _RefundScenarioResult(
        run=run,
        finding=_evaluate_duplicate_refund(run),
        subject_received_receipts=subject.received_receipts,
    )


def test_provider_commits_before_delivering_result() -> None:
    provider = _FakeRefundProvider()

    def inspect_committed_state(receipt: _RefundReceipt) -> _RefundReceipt:
        observation = provider.observe()
        assert observation.refunded_minor_units == _REFUND_MINOR_UNITS
        assert observation.history == (_RefundEvent("refund/1", _PAYMENT_ID, _REFUND_MINOR_UNITS),)
        return receipt

    receipt = provider.refund(_REFUND_MINOR_UNITS, inspect_committed_state)

    assert receipt == _RefundReceipt("refund/1", _REFUND_MINOR_UNITS)


def test_lost_provider_result_causes_a_duplicate_refund() -> None:
    result = _run_refund_scenario()
    run = result.run

    assert run.operation_id == OperationId("operation/refund-001")
    assert run.baseline == _RefundObservation(
        payment_id=_PAYMENT_ID,
        payment_minor_units=_PAYMENT_MINOR_UNITS,
        refunded_minor_units=0,
        history=(),
    )
    assert len(run.attempts) == 2

    first, second = run.attempts
    assert first.identity.operation_id == run.operation_id
    assert first.identity.delivery_id == DeliveryId("delivery/operation/refund-001/perturbed/1")
    assert first.identity.attempt_id == AttemptId("attempt/operation/refund-001/perturbed/1")
    assert first.outcome == "provider_result_lost"
    assert first.returned_result is None
    assert first.observation.refunded_minor_units == 2_500
    assert first.observation.history == (
        _RefundEvent("refund/1", _PAYMENT_ID, _REFUND_MINOR_UNITS),
    )

    assert second.identity.operation_id == run.operation_id
    assert second.identity.delivery_id == DeliveryId("delivery/operation/refund-001/perturbed/2")
    assert second.identity.attempt_id == AttemptId("attempt/operation/refund-001/perturbed/2")
    assert second.outcome == "returned"
    assert second.returned_result == _RefundReceipt("refund/2", _REFUND_MINOR_UNITS)
    assert second.observation.refunded_minor_units == 5_000
    assert second.observation.history == (
        _RefundEvent("refund/1", _PAYMENT_ID, _REFUND_MINOR_UNITS),
        _RefundEvent("refund/2", _PAYMENT_ID, _REFUND_MINOR_UNITS),
    )

    assert run.harness.boundary_name == "provider_result_delivery"
    assert run.harness.reached_attempt_ids == (
        AttemptId("attempt/operation/refund-001/perturbed/1"),
        AttemptId("attempt/operation/refund-001/perturbed/2"),
    )
    assert run.harness.injected_attempt_id == first.identity.attempt_id
    assert run.harness.undelivered_result == _RefundReceipt("refund/1", _REFUND_MINOR_UNITS)
    assert run.subject_result == _RefundReceipt("refund/2", _REFUND_MINOR_UNITS)
    assert result.subject_received_receipts == (run.subject_result,)

    finding = result.finding
    assert finding.invariant == "at_most_one_committed_refund"
    assert finding.surface == "refunds"
    assert finding.required_evidence == ("state", "committed_history")
    assert finding.expected_max_new_events == 1
    assert finding.expected_minor_units_per_event == 2_500
    assert finding.observed_new_events == 2
    assert finding.observed_minor_units == 5_000
    assert finding.state_delta_minor_units == 5_000
    assert finding.history_delta == second.observation.history
    assert finding.explanation == (
        "Expected at most one new 2,500-unit refund commit; observed two new committed "
        "events totaling 5,000 units."
    )


def test_refund_scenario_is_structurally_identical_in_100_fresh_worlds() -> None:
    runs = tuple(_run_refund_scenario() for _ in range(100))

    assert runs == (runs[0],) * 100


def test_boundary_not_reached_stops_without_retry() -> None:
    invocations = 0
    observations = 0

    def invoke_without_boundary(
        _deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        return _RefundReceipt("not-delivered", _REFUND_MINOR_UNITS)

    def observe() -> int:
        nonlocal observations
        observations += 1
        return observations

    with pytest.raises(BoundaryNotReached) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=invoke_without_boundary,
            observe=observe,
        )

    assert error.value.identity.attempt_id == AttemptId("attempt/operation/refund-001/perturbed/1")
    assert invocations == 1
    assert observations == 1


def test_caught_fault_signal_stops_without_retry() -> None:
    invocations = 0

    def invoke_and_catch(
        deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        receipt = _RefundReceipt("refund/caught", _REFUND_MINOR_UNITS)
        try:
            return deliver_result(receipt)
        except Exception:
            return _RefundReceipt("fallback", 0)

    with pytest.raises(FaultNotPropagated) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=invoke_and_catch,
            observe=lambda: 0,
        )

    assert error.value.identity.attempt_id == AttemptId("attempt/operation/refund-001/perturbed/1")
    assert invocations == 1


def test_unrelated_subject_exception_propagates_without_retry() -> None:
    invocations = 0
    failure = ValueError("subject failed")

    def fail_before_boundary(
        _deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        raise failure

    with pytest.raises(ValueError) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=fail_before_boundary,
            observe=lambda: 0,
        )

    assert error.value is failure
    assert invocations == 1


def test_unrelated_provider_exception_propagates_without_retry() -> None:
    invocations = 0
    provider_calls = 0
    failure = ValueError("provider unavailable")

    def provider_refund(
        _deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal provider_calls
        provider_calls += 1
        raise failure

    def invoke(
        deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        return provider_refund(deliver_result)

    with pytest.raises(ValueError) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=invoke,
            observe=lambda: 0,
        )

    assert error.value is failure
    assert invocations == 1
    assert provider_calls == 1


def test_unrelated_second_invocation_exception_propagates_without_third_attempt() -> None:
    invocations = 0
    failure = ValueError("retry failed")

    def fail_on_retry(
        deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        if invocations == 2:
            raise failure
        return deliver_result(_RefundReceipt("refund/1", _REFUND_MINOR_UNITS))

    with pytest.raises(ValueError) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=fail_on_retry,
            observe=lambda: 0,
        )

    assert error.value is failure
    assert invocations == 2


def test_baseline_observer_exception_prevents_invocation() -> None:
    invocations = 0
    failure = RuntimeError("baseline observation failed")

    def invoke(
        _deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        return _RefundReceipt("unreachable", 0)

    def observe() -> int:
        raise failure

    with pytest.raises(RuntimeError) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=invoke,
            observe=observe,
        )

    assert error.value is failure
    assert invocations == 0


def test_post_loss_observer_exception_prevents_retry() -> None:
    invocations = 0
    observations = 0
    failure = RuntimeError("post-loss observation failed")

    def invoke(
        deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        return deliver_result(_RefundReceipt("refund/1", _REFUND_MINOR_UNITS))

    def observe() -> int:
        nonlocal observations
        observations += 1
        if observations == 2:
            raise failure
        return observations

    with pytest.raises(RuntimeError) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=invoke,
            observe=observe,
        )

    assert error.value is failure
    assert invocations == 1
    assert observations == 2


def test_final_observer_exception_propagates_without_third_attempt() -> None:
    invocations = 0
    observations = 0
    failure = RuntimeError("final observation failed")

    def invoke(
        deliver_result: Callable[[_RefundReceipt], _RefundReceipt],
    ) -> _RefundReceipt:
        nonlocal invocations
        invocations += 1
        return deliver_result(_RefundReceipt(f"refund/{invocations}", _REFUND_MINOR_UNITS))

    def observe() -> int:
        nonlocal observations
        observations += 1
        if observations == 3:
            raise failure
        return observations

    with pytest.raises(RuntimeError) as error:
        run_commit_then_lose_first_provider_result(
            operation_id=_OPERATION_ID,
            invoke=invoke,
            observe=observe,
        )

    assert error.value is failure
    assert invocations == 2
    assert observations == 3
