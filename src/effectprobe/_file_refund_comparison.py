"""Private refund comparison observed through a controlled file journal."""

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from effectprobe._lost_result import TrialId
from effectprobe._refund_comparison import (
    RefundCase,
    RefundCaseResult,
    RefundCommand,
    RefundEvent,
    RefundObservation,
    RefundReceipt,
    RefundState,
    build_refund_case,
    validate_refund_fixture,
)
from effectprobe._semantic_kernel import (
    SurfaceCoverage,
    SurfaceObservation,
    World,
    WorldSession,
    evaluate_case,
)

_PAYMENT_ID = "payment/refund-001"
_PAYMENT_MINOR_UNITS = 10_000
_MAX_JOURNAL_BYTES = 64 * 1024
_EVENT_FIELDS = frozenset({"refund_id", "payment_id", "amount_minor_units", "operation_key"})


class FileRefundJournalError(ValueError):
    """The controlled journal cannot provide valid complete-history evidence."""


def initialize_file_refund_journal(path: Path) -> None:
    """Exclusively create one empty private refund journal."""

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise FileRefundJournalError(f"duplicate journal field: {key}")
        value[key] = item
    return value


def _decode_event(line: str, ordinal: int) -> RefundEvent:
    try:
        decoded = cast(
            "object",
            json.loads(line, object_pairs_hook=_strict_object),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise FileRefundJournalError("journal record is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise FileRefundJournalError("journal record is not an object")
    record = cast("dict[str, object]", decoded)
    if frozenset(record) != _EVENT_FIELDS:
        raise FileRefundJournalError("journal record has an unknown or missing field")

    refund_id = record["refund_id"]
    payment_id = record["payment_id"]
    amount = record["amount_minor_units"]
    operation_key = record["operation_key"]
    if not isinstance(refund_id, str) or refund_id != f"refund/{ordinal}":
        raise FileRefundJournalError("journal refund identity is invalid")
    if not isinstance(payment_id, str) or payment_id != _PAYMENT_ID:
        raise FileRefundJournalError("journal payment identity is invalid")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise FileRefundJournalError("journal refund amount is invalid")
    if operation_key is not None and not isinstance(operation_key, str):
        raise FileRefundJournalError("journal operation key is invalid")
    return RefundEvent(refund_id, payment_id, amount, operation_key)


def read_file_refund_journal(path: Path) -> tuple[RefundEvent, ...]:
    """Strictly read complete ordered history from one controlled journal."""

    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise FileRefundJournalError("refund journal is not a regular file")
    if metadata.st_size > _MAX_JOURNAL_BYTES:
        raise FileRefundJournalError("refund journal exceeds the controlled size bound")
    payload = path.read_bytes()
    if len(payload) > _MAX_JOURNAL_BYTES:
        raise FileRefundJournalError("refund journal grew beyond the controlled size bound")
    if not payload:
        return ()
    if not payload.endswith(b"\n"):
        raise FileRefundJournalError("refund journal has a truncated final record")
    try:
        lines = payload.decode("utf-8", errors="strict").removesuffix("\n").split("\n")
    except UnicodeDecodeError as error:
        raise FileRefundJournalError("refund journal is not valid UTF-8") from error

    events = tuple(_decode_event(line, ordinal) for ordinal, line in enumerate(lines, 1))
    if sum(event.amount_minor_units for event in events) > _PAYMENT_MINOR_UNITS:
        raise FileRefundJournalError("journal refunds exceed the controlled payment")
    keys = tuple(event.operation_key for event in events if event.operation_key is not None)
    if len(keys) != len(set(keys)):
        raise FileRefundJournalError("journal repeats a provider operation key")
    return events


def _encode_event(event: RefundEvent) -> bytes:
    return (
        json.dumps(
            {
                "refund_id": event.refund_id,
                "payment_id": event.payment_id,
                "amount_minor_units": event.amount_minor_units,
                "operation_key": event.operation_key,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _append_event(path: Path, event: RefundEvent) -> None:
    payload = _encode_event(event)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written == 0:
                raise OSError("journal append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class FileRefundObserver:
    """Read state and complete committed history from one owned journal."""

    journal_path: Path

    def observe(self) -> RefundObservation:
        history = read_file_refund_journal(self.journal_path)
        return SurfaceObservation(
            state=RefundState(
                payment_id=_PAYMENT_ID,
                payment_minor_units=_PAYMENT_MINOR_UNITS,
                refunded_minor_units=sum(event.amount_minor_units for event in history),
                key_index_size=len(
                    {event.operation_key for event in history if event.operation_key is not None}
                ),
            ),
            history=history,
        )


@dataclass(frozen=True, slots=True)
class FileRefundProvider:
    """Commit or deduplicate refunds using only the controlled journal."""

    journal_path: Path

    def refund(
        self,
        command: RefundCommand,
        *,
        operation_key: str | None,
        deliver_result: Callable[[RefundReceipt], RefundReceipt],
    ) -> RefundReceipt:
        history = read_file_refund_journal(self.journal_path)
        if command.payment_id != _PAYMENT_ID:
            raise ValueError(f"unknown payment: {command.payment_id}")

        if operation_key is not None:
            stored = next(
                (event for event in history if event.operation_key == operation_key),
                None,
            )
            if stored is not None:
                return deliver_result(
                    RefundReceipt(
                        stored.refund_id,
                        stored.payment_id,
                        stored.amount_minor_units,
                    )
                )

        committed = sum(event.amount_minor_units for event in history)
        if command.amount_minor_units <= 0 or committed + command.amount_minor_units > (
            _PAYMENT_MINOR_UNITS
        ):
            raise ValueError("refund amount exceeds the available payment balance")

        event = RefundEvent(
            refund_id=f"refund/{len(history) + 1}",
            payment_id=command.payment_id,
            amount_minor_units=command.amount_minor_units,
            operation_key=operation_key,
        )
        _append_event(self.journal_path, event)
        return deliver_result(
            RefundReceipt(event.refund_id, event.payment_id, event.amount_minor_units)
        )


@dataclass(slots=True)
class _UnsafeFileRefundSubject:
    provider: FileRefundProvider

    def invoke(
        self,
        command: RefundCommand,
        deliver_result: Callable[[RefundReceipt], RefundReceipt],
    ) -> RefundReceipt:
        return self.provider.refund(command, operation_key=None, deliver_result=deliver_result)


@dataclass(slots=True)
class _KeyedFileRefundSubject:
    provider: FileRefundProvider

    def invoke(
        self,
        command: RefundCommand,
        deliver_result: Callable[[RefundReceipt], RefundReceipt],
    ) -> RefundReceipt:
        return self.provider.refund(
            command,
            operation_key=command.operation_key,
            deliver_result=deliver_result,
        )


type _FileRefundSubject = _UnsafeFileRefundSubject | _KeyedFileRefundSubject


@dataclass(slots=True)
class FileRefundWorldRecord:
    """Test-visible lifecycle evidence for one fresh file-journal world."""

    trial_id: TrialId
    journal_path: Path
    cleaned: bool = False
    journal_removed: bool = False


@dataclass(slots=True)
class FileRefundWorldTracker:
    """Track provisioning and cleanup for the private file-journal case."""

    worlds: list[FileRefundWorldRecord] = field(
        default_factory=lambda: list[FileRefundWorldRecord]()
    )
    provision_attempts: int = 0
    cleanup_attempts: int = 0


def build_file_refund_case(
    *,
    keyed: bool,
    corrupt_journal_trials: frozenset[str] = frozenset(),
) -> tuple[RefundCase, FileRefundWorldTracker]:
    """Build one private file-journal refund case and lifecycle tracker."""

    base_case, _base_tracker = build_refund_case(keyed=keyed)
    tracker = FileRefundWorldTracker()

    def world_factory(
        trial_id: TrialId,
    ) -> WorldSession[RefundCommand, RefundReceipt, RefundState, RefundEvent]:
        directory: TemporaryDirectory[str] | None = None
        record: FileRefundWorldRecord | None = None
        observations = 0

        def provision() -> World[RefundCommand, RefundReceipt, RefundState, RefundEvent]:
            nonlocal directory, record
            tracker.provision_attempts += 1
            directory = TemporaryDirectory()
            journal_path = Path(directory.name) / "refund-events.jsonl"
            initialize_file_refund_journal(journal_path)
            record = FileRefundWorldRecord(trial_id=trial_id, journal_path=journal_path)
            tracker.worlds.append(record)
            provider = FileRefundProvider(journal_path)
            observer = FileRefundObserver(journal_path)
            subject: _FileRefundSubject = (
                _KeyedFileRefundSubject(provider) if keyed else _UnsafeFileRefundSubject(provider)
            )

            def observe() -> RefundObservation:
                nonlocal observations
                observations += 1
                if trial_id.value in corrupt_journal_trials and observations == 2:
                    with journal_path.open("ab") as journal:
                        journal.write(b"{")
                return observer.observe()

            return World(
                invoke=subject.invoke,
                observe=observe,
                validate_fixture=validate_refund_fixture,
            )

        def cleanup(
            _world: World[RefundCommand, RefundReceipt, RefundState, RefundEvent] | None,
        ) -> None:
            tracker.cleanup_attempts += 1
            if directory is not None:
                directory.cleanup()
            if record is not None:
                record.cleaned = True
                record.journal_removed = not record.journal_path.exists()

        return WorldSession(provision=provision, cleanup=cleanup)

    case = replace(
        base_case,
        subject_name=(
            "file_journal_keyed_refund_subject"
            if keyed
            else "file_journal_vulnerable_refund_subject"
        ),
        world_factory=world_factory,
        coverage=SurfaceCoverage(
            surface="refunds",
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="harness_controlled_file_journal_fixture",
            limitations=(
                "does not discover effects outside the owned journal",
                "does not establish filesystem crash durability",
                "does not validate production filesystem or provider semantics",
                "private observer implementation without a stable extension interface",
            ),
        ),
        scope_limitations=(
            "private provisional file-journal semantic outcome",
            "trusted local harness-controlled writer and observer",
            "code, dependency, runtime, and environment identity are not recorded",
            "not eligible for MCP evidence artifacts, reports, or replay",
        ),
    )
    return case, tracker


def evaluate_file_vulnerable_refund() -> RefundCaseResult:
    """Evaluate the vulnerable subject through the file-journal observer."""

    case, _tracker = build_file_refund_case(keyed=False)
    return evaluate_case(case)


def evaluate_file_keyed_refund() -> RefundCaseResult:
    """Evaluate the keyed subject through the file-journal observer."""

    case, _tracker = build_file_refund_case(keyed=True)
    return evaluate_case(case)
