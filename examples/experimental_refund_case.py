"""Trusted-local external refund case using only EffectProbe's experimental API."""

import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from effectprobe.experimental import (
    Canonicalization,
    Case,
    CaseReport,
    CleanAssertion,
    CleanContext,
    Decision,
    EvidenceKind,
    EvidenceRequirement,
    JsonValue,
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

type Mode = Literal["unsafe", "keyed"]


@dataclass(frozen=True, slots=True)
class RefundInput:
    payment_id: str
    amount_minor_units: int
    operation_key: str


@dataclass(frozen=True, slots=True)
class RefundReceipt:
    refund_id: str
    payment_id: str
    amount_minor_units: int


@dataclass(frozen=True, slots=True)
class RefundEvent:
    refund_id: str
    payment_id: str
    amount_minor_units: int
    operation_key: str | None


@dataclass(frozen=True, slots=True)
class RefundState:
    refunded_minor_units: int
    committed_count: int


type RefundObservation = Observation[RefundState, RefundEvent]
type RefundWorld = World[RefundInput, RefundReceipt, RefundState, RefundEvent]
type RefundSession = WorldSession[RefundInput, RefundReceipt, RefundState, RefundEvent]
type RefundCase = Case[RefundInput, RefundReceipt, RefundState, RefundEvent]

_PAYMENT_ID = "payment/refund-001"
_AMOUNT = 2_500
_OPERATION_KEY = "refund-key-001"


def _event_payload(event: RefundEvent) -> dict[str, JsonValue]:
    return {
        "refund_id": event.refund_id,
        "payment_id": event.payment_id,
        "amount_minor_units": event.amount_minor_units,
        "operation_key": event.operation_key,
    }


def _read_history(journal: Path) -> tuple[RefundEvent, ...]:
    events: list[RefundEvent] = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        value = cast("dict[str, object]", json.loads(line))
        refund_id = value.get("refund_id")
        payment_id = value.get("payment_id")
        amount = value.get("amount_minor_units")
        operation_key = value.get("operation_key")
        if (
            not isinstance(refund_id, str)
            or not isinstance(payment_id, str)
            or not isinstance(amount, int)
            or isinstance(amount, bool)
            or (operation_key is not None and not isinstance(operation_key, str))
        ):
            raise ValueError("invalid journal event")
        events.append(RefundEvent(refund_id, payment_id, amount, operation_key))
    return tuple(events)


def _append_event(journal: Path, event: RefundEvent) -> None:
    with journal.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(_event_payload(event), ensure_ascii=True, separators=(",", ":")) + "\n"
        )
        stream.flush()


def _observe(journal: Path) -> RefundObservation:
    history = _read_history(journal)
    return Observation(
        state=RefundState(
            refunded_minor_units=sum(event.amount_minor_units for event in history),
            committed_count=len(history),
        ),
        history=history,
    )


def _clean_contract(
    context: CleanContext[RefundInput, RefundReceipt, RefundState, RefundEvent],
) -> Decision:
    event = context.history_delta[0] if len(context.history_delta) == 1 else None
    passed = (
        event is not None
        and context.returned_result.refund_id == event.refund_id
        and context.returned_result.payment_id == context.input.payment_id == event.payment_id
        and context.returned_result.amount_minor_units
        == context.input.amount_minor_units
        == event.amount_minor_units
        and context.final.state.refunded_minor_units - context.baseline.state.refunded_minor_units
        == context.input.amount_minor_units
    )
    return Decision(passed, "one_requested_refund" if passed else "clean_refund_mismatch")


def _retry_contract(
    context: RetryContext[RefundInput, RefundReceipt, RefundState, RefundEvent],
) -> Decision:
    event = (
        context.perturbed_history_delta[0] if len(context.perturbed_history_delta) == 1 else None
    )
    passed = (
        context.boundary_name == "provider_result_delivery"
        and context.boundary_reached
        and context.fault_injected
        and context.attempt_count == 2
        and context.canonical_clean_history_delta == context.canonical_perturbed_history_delta
        and event is not None
        and context.subject_result.refund_id == event.refund_id
    )
    return Decision(passed, "one_commit_after_retry" if passed else "additional_refund_commit")


def _canonical_input(value: RefundInput) -> JsonValue:
    return {
        "payment_id": value.payment_id,
        "amount_minor_units": value.amount_minor_units,
        "operation_key": value.operation_key,
    }


def _canonical_state(value: RefundState) -> JsonValue:
    return {
        "refunded_minor_units": value.refunded_minor_units,
        "committed_count": value.committed_count,
    }


def _canonical_event(value: RefundEvent) -> JsonValue:
    return {
        "payment_id": value.payment_id,
        "amount_minor_units": value.amount_minor_units,
        "operation_key": value.operation_key,
    }


def _world_factory(mode: Mode) -> Callable[[TrialId], RefundSession]:
    def create(_trial_id: TrialId) -> RefundSession:
        temporary: TemporaryDirectory[str] | None = None
        journal: Path | None = None

        def provision() -> RefundWorld:
            nonlocal temporary, journal
            temporary = TemporaryDirectory(prefix="effectprobe-experimental-")
            journal = Path(temporary.name) / "refunds.jsonl"
            journal.write_bytes(b"")

            def observe() -> RefundObservation:
                assert journal is not None
                return _observe(journal)

            def validate_fixture(observation: RefundObservation) -> None:
                if observation.state != RefundState(0, 0) or observation.history:
                    raise ValueError("refund fixture is not fresh")

            def invoke(
                command: RefundInput,
                deliver_result: Callable[[RefundReceipt], RefundReceipt],
            ) -> RefundReceipt:
                assert journal is not None
                history = _read_history(journal)
                if mode == "keyed":
                    existing = next(
                        (
                            event
                            for event in history
                            if event.operation_key == command.operation_key
                        ),
                        None,
                    )
                    if existing is not None:
                        return deliver_result(
                            RefundReceipt(
                                existing.refund_id,
                                existing.payment_id,
                                existing.amount_minor_units,
                            )
                        )
                event = RefundEvent(
                    refund_id=f"refund/{len(history) + 1}",
                    payment_id=command.payment_id,
                    amount_minor_units=command.amount_minor_units,
                    operation_key=(command.operation_key if mode == "keyed" else None),
                )
                _append_event(journal, event)
                return deliver_result(
                    RefundReceipt(event.refund_id, event.payment_id, event.amount_minor_units)
                )

            return World(
                invoke=invoke,
                observe=observe,
                validate_fixture=validate_fixture,
            )

        def cleanup(_world: RefundWorld | None) -> None:
            if temporary is not None:
                temporary.cleanup()

        return WorldSession(provision=provision, cleanup=cleanup)

    return create


def build_case(mode: Mode) -> RefundCase:
    """Build one unsafe or keyed case using public experimental types only."""

    surface = "refunds"
    state = EvidenceRequirement(EvidenceKind.STATE, surface)
    history = EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, surface)
    result = EvidenceRequirement(EvidenceKind.SUBJECT_RESULT)
    repository = Path(__file__).resolve().parents[1]
    return Case(
        case_name=f"external_refund_{mode}",
        subject_name=f"trusted_refund_{mode}",
        input=RefundInput(_PAYMENT_ID, _AMOUNT, _OPERATION_KEY),
        operation_id="operation/refund-001",
        operation_key_selector=lambda value: value.operation_key,
        canonicalize_input=_canonical_input,
        canonicalization=Canonicalization(
            input="refund_input/payment-amount-key-v1",
            state="refund_state/refunded-total-count-v1",
            event="refund_event/payment-amount-key-v1",
        ),
        source_files=(Path(__file__),),
        dependency_lock=repository / "uv.lock",
        world_factory=_world_factory(mode),
        coverage=ObserverCoverage(
            surface=surface,
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="trusted_local_file_journal",
            limitations=("production_provider_unvalidated", "single_surface_only"),
        ),
        canonicalize_state=_canonical_state,
        canonicalize_event=_canonical_event,
        clean_assertions=(
            CleanAssertion("one_requested_refund", (state, history, result), _clean_contract),
        ),
        retry_invariants=(
            RetryInvariant("no_additional_refund", (state, history, result), _retry_contract),
        ),
        limitations=("trusted_local_code", "no_verified_replay"),
    )


def evaluate(mode: Mode) -> CaseReport:
    """Evaluate one example mode without treating command status as an axis."""

    return run_case(build_case(mode))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("unsafe", "keyed"), required=True)
    values = parser.parse_args(argv)
    mode = cast("Mode", values.mode)
    print(render_terminal(evaluate(mode)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
