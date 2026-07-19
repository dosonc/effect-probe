"""Private evidence recording and exact replay for the controlled MCP refund case."""

import copy
import hashlib
import os
import platform
import re
import sqlite3
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Literal, cast

from mcp.types import LATEST_PROTOCOL_VERSION

from effectprobe._evidence_artifact import (
    ArtifactCompatibilityError,
    ArtifactFormatError,
    CompatibilityDifference,
    EvidenceArtifact,
    JsonValue,
    ReadEvidenceArtifact,
    artifact_payload,
    evidence_artifact_from_payload,
    read_evidence_artifact,
    require_bool,
    require_compatible,
    require_fields,
    require_int,
    require_list,
    require_nullable_string,
    require_object,
    require_string,
    write_evidence_artifact,
)
from effectprobe._lost_result import AttemptEvidence, RunEvidence
from effectprobe._mcp_refund_comparison import (
    McpRefundCaseResult,
    McpRefundWorldRecord,
    McpRefundWorldTracker,
    build_mcp_refund_case,
)
from effectprobe._mcp_refund_store import McpDelivery, McpRefundEvent, McpRefundObservation
from effectprobe._refund_comparison import RefundCommand, RefundReceipt, RefundState
from effectprobe._semantic_kernel import (
    AxisResult,
    CleanTrialEvidence,
    CleanupStatus,
    ErrorRecord,
    InvariantResult,
    PairResult,
    SurfaceObservation,
    evaluate_case,
)

type McpEvidenceMode = Literal["unsafe", "keyed"]
type McpRun = Callable[[McpEvidenceMode], tuple[McpRefundCaseResult, McpRefundWorldTracker]]

_SCHEMA_NAME = "effectprobe.private.evidence"
_SCHEMA_VERSION = 1
_RUNNER_ID = "effectprobe.private.mcp-evidence-replay"
_RUNNER_VERSION = 2
_REGISTRY_ID = "effectprobe.private.mcp-refund"
_CASE_VERSION = 1
_REDACTION_ID = "effectprobe.private.redaction/mcp-refund-v1"
_REDACTION_VERSION = 1
_CANONICAL_PROJECTION_VERSION = 1
_TOOL_NAME = "refund"
_PAYMENT_ID = "payment/refund-001"
_PAYMENT_MINOR_UNITS = 10_000
_REFUND_MINOR_UNITS = 2_500
_OPERATION_ID = "operation/refund-001"
_OPERATION_KEY = "refund-key-001"
_FIXTURE_VERSION = 1
_EXPECTED_LIMITATIONS = (
    "private provisional MCP stdio outcome",
    "trusted local harness-controlled server and SQLite provider",
    "MCP request identity is transport evidence, not a domain operation key",
    "private experimental evidence schema and replay registry",
    "compatibility covers only the enumerated runtime and environment dimensions",
    "unmodelled host state remains unknown",
)
_OMITTED_CATEGORIES = (
    "absolute_paths",
    "command_lines",
    "environment_names_and_values",
    "exception_messages_and_tracebacks",
    "process_identifiers",
    "subprocess_stderr",
)
_SUBJECT_FILES = (
    "src/effectprobe/_mcp_refund_server.py",
    "src/effectprobe/_mcp_refund_store.py",
)
_RUNNER_FILES = (
    "src/effectprobe/_lost_result.py",
    "src/effectprobe/_semantic_kernel.py",
    "src/effectprobe/_mcp_stdio.py",
    "src/effectprobe/_mcp_refund_comparison.py",
    "src/effectprobe/_refund_comparison.py",
    "src/effectprobe/_evidence_artifact.py",
    "src/effectprobe/_mcp_evidence_replay.py",
    "src/effectprobe/_mcp_reports.py",
)
_ALLOWED_EXPLANATIONS = frozenset(
    {
        "clean MCP refund committed and returned once",
        "clean MCP refund evidence differed from one requested commit",
        "MCP retry preserved the clean committed-effect and receipt semantics",
        "MCP retry produced an additional or inconsistent committed refund",
        (
            "MCP retry produced an additional or inconsistent committed refund; "
            "reproduced in two confirmations"
        ),
        "required clean evidence is unavailable",
        "required retry evidence is unavailable",
        "clean assertion evaluator malfunctioned",
        "retry invariant evaluator malfunctioned",
        "clean trial did not complete validly",
        "perturbed trial did not complete validly",
        "retry comparison requires completed clean evidence",
        "candidate did not receive exactly two confirmations",
        "confirmation infrastructure or evaluation failed",
        (
            "MCP retry produced an additional or inconsistent committed refund; "
            "a valid confirmation did not reproduce"
        ),
    }
)


class McpEvidenceCaptureError(RuntimeError):
    """Completed semantic and tracker evidence cannot form a valid artifact."""


@dataclass(frozen=True, slots=True)
class EvidenceInspection:
    """Safe data-only summary of one validated MCP evidence artifact."""

    sha256: str
    size_bytes: int
    schema_version: int
    lineage: str
    source_sha256: str | None
    mode: McpEvidenceMode
    subject_name: str
    schedule: str
    clean_validity: str
    retry_safety: str
    confirmation_count: int
    trial_count: int
    committed_effect_count: int
    transport_delivery_count: int
    cleanup_dispositions: tuple[str, ...]
    omitted_categories: tuple[str, ...]
    limitations: tuple[str, ...]
    reproduction_matched: bool | None


@dataclass(frozen=True, slots=True)
class ReplayEvidenceResult:
    """Source and newly written child metadata for a compatible replay."""

    source: ReadEvidenceArtifact
    child: ReadEvidenceArtifact
    matched: bool


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65_536):
            digest.update(chunk)
    return digest.hexdigest()


def _component_digests(paths: tuple[str, ...]) -> list[JsonValue]:
    root = _repository_root()
    result: list[JsonValue] = []
    for relative in paths:
        path = root / relative
        if not path.is_file():
            raise McpEvidenceCaptureError(f"required source component is unavailable: {relative}")
        result.append({"path": relative, "sha256": _sha256_file(path)})
    return result


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _locked_installed_distributions(lock_path: Path) -> list[JsonValue]:
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise McpEvidenceCaptureError("uv.lock cannot be fingerprinted") from error
    packages_value = lock.get("package")
    if not isinstance(packages_value, list):
        raise McpEvidenceCaptureError("uv.lock has no package list")
    locked_names: set[str] = set()
    for package_value in cast("list[object]", packages_value):
        if not isinstance(package_value, dict):
            raise McpEvidenceCaptureError("uv.lock contains an invalid package entry")
        package = cast("dict[object, object]", package_value)
        name = package.get("name")
        if not isinstance(name, str):
            raise McpEvidenceCaptureError("uv.lock package lacks a string name")
        locked_names.add(_normalize_distribution_name(name))

    installed: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name is None:
            continue
        normalized = _normalize_distribution_name(name)
        if normalized in locked_names:
            installed[normalized] = distribution.version
    if "effectprobe" not in installed:
        raise McpEvidenceCaptureError("the EffectProbe distribution is unavailable")
    return [{"name": name, "version": installed[name]} for name in sorted(installed)]


def _contract_descriptor() -> dict[str, JsonValue]:
    return {
        "fixture": {
            "id": "harness-controlled-sqlite-refund",
            "version": _FIXTURE_VERSION,
            "seed": {
                "payment_id": _PAYMENT_ID,
                "payment_minor_units": _PAYMENT_MINOR_UNITS,
                "refunded_minor_units": 0,
                "key_mapping_count": 0,
                "history": [],
            },
        },
        "clean_assertions": [
            {
                "name": "one_mcp_refund_matches_request",
                "requirements": [
                    {"kind": "state", "surface": "refunds"},
                    {"kind": "complete_history", "surface": "refunds"},
                    {"kind": "subject_result", "surface": None},
                ],
            }
        ],
        "retry_invariants": [
            {
                "name": "no_additional_mcp_refund_after_retry",
                "requirements": [
                    {"kind": "state", "surface": "refunds"},
                    {"kind": "complete_history", "surface": "refunds"},
                ],
            }
        ],
        "canonicalization": {
            "state": "mcp-refund-state/payment-total-refunded-key-count-v1",
            "history": "mcp-refund-event/payment-amount-operation-key-v1",
        },
        "mcp_tool": {
            "name": _TOOL_NAME,
            "input": [
                {"name": "amount_minor_units", "type": "integer"},
                {"name": "operation_key", "type": "string"},
                {"name": "payment_id", "type": "string"},
            ],
            "output": [
                {"name": "amount_minor_units", "type": "integer"},
                {"name": "payment_id", "type": "string"},
                {"name": "refund_id", "type": "string"},
            ],
        },
    }


def _observer_descriptor() -> dict[str, JsonValue]:
    return {
        "surface": "refunds",
        "state": True,
        "history": True,
        "complete_history": True,
        "observation_interval": "baseline_to_final",
        "provenance": "harness_controlled_sqlite_mcp_fixture",
        "limitations": ["does not validate production-provider semantics"],
    }


def _schedule_descriptor() -> dict[str, JsonValue]:
    return {
        "name": "mcp_tool_completion_then_lose_first_client_result_and_retry_once",
        "boundary": "mcp_client_result_delivery",
        "lost_outcome": "client_result_lost",
        "fault_count": 1,
        "attempt_count": 2,
        "recovery": "retry_same_logical_operation_once",
    }


def build_mcp_compatibility(mode: McpEvidenceMode) -> dict[str, JsonValue]:
    """Build the complete live descriptor before any MCP case is provisioned."""

    if mode not in ("unsafe", "keyed"):
        raise McpEvidenceCaptureError(f"unsupported MCP evidence mode: {mode}")
    root = _repository_root()
    lock_path = root / "uv.lock"
    if not lock_path.is_file():
        raise McpEvidenceCaptureError("uv.lock is unavailable")
    return {
        "schema": {"name": _SCHEMA_NAME, "version": _SCHEMA_VERSION},
        "redaction": {"id": _REDACTION_ID, "version": _REDACTION_VERSION},
        "case": {"registry_id": _REGISTRY_ID, "version": _CASE_VERSION},
        "subject": {
            "id": f"mcp_{mode}_refund_tool",
            "mode": mode,
            "components": _component_digests(_SUBJECT_FILES),
        },
        "runner": {
            "id": _RUNNER_ID,
            "version": _RUNNER_VERSION,
            "components": _component_digests(_RUNNER_FILES),
        },
        "dependencies": {
            "lock_sha256": _sha256_file(lock_path),
            "installed_locked_distributions": _locked_installed_distributions(lock_path),
        },
        "runtime": {
            "python_implementation": sys.implementation.name,
            "python_version": platform.python_version(),
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
            "architecture": platform.machine(),
            "sqlite_version": sqlite3.sqlite_version,
            "mcp_protocol_revision": str(LATEST_PROTOCOL_VERSION),
        },
        "input": {
            "payment_id": _PAYMENT_ID,
            "amount_minor_units": _REFUND_MINOR_UNITS,
            "operation_key": _OPERATION_KEY,
        },
        "contracts": _contract_descriptor(),
        "observer": _observer_descriptor(),
        "schedule": _schedule_descriptor(),
    }


def _receipt(value: RefundReceipt | None) -> JsonValue:
    if value is None:
        return None
    return {
        "refund_id": value.refund_id,
        "payment_id": value.payment_id,
        "amount_minor_units": value.amount_minor_units,
    }


def _state(value: RefundState) -> dict[str, JsonValue]:
    return {
        "payment_id": value.payment_id,
        "payment_minor_units": value.payment_minor_units,
        "refunded_minor_units": value.refunded_minor_units,
        "key_mapping_count": value.key_index_size,
    }


def _event(value: McpRefundEvent) -> dict[str, JsonValue]:
    return {
        "refund_id": value.refund_id,
        "payment_id": value.payment_id,
        "amount_minor_units": value.amount_minor_units,
        "operation_key": value.operation_key,
    }


def _observation(value: McpRefundObservation) -> dict[str, JsonValue]:
    return {"state": _state(value.state), "history": [_event(event) for event in value.history]}


def _identity(
    attempt: AttemptEvidence[RefundReceipt, McpRefundObservation],
) -> dict[str, JsonValue]:
    identity = attempt.identity
    return {
        "operation_id": identity.operation_id.value,
        "trial_id": identity.trial_id.value,
        "delivery_id": identity.delivery_id.value,
        "attempt_id": identity.attempt_id.value,
    }


def _attempt(
    value: AttemptEvidence[RefundReceipt, McpRefundObservation], command: RefundCommand
) -> dict[str, JsonValue]:
    delivered = value.outcome == "returned"
    return {
        "identity": _identity(value),
        "subject_view": {
            "input": {
                "payment_id": command.payment_id,
                "amount_minor_units": command.amount_minor_units,
                "operation_key": command.operation_key,
            },
            "outcome": value.outcome,
            "returned_result": _receipt(value.returned_result) if delivered else None,
        },
        "observer_truth": _observation(value.observation),
    }


def _transport(deliveries: tuple[McpDelivery, ...]) -> list[JsonValue]:
    return [
        {
            "ordinal": delivery.ordinal,
            "mcp_request_id": delivery.mcp_request_id,
            "operation_key": delivery.operation_key,
        }
        for delivery in deliveries
    ]


def _require_allowed_explanation(value: str) -> None:
    if value in _ALLOWED_EXPLANATIONS:
        return
    if value.startswith("mcp_client_result_delivery was not reached by attempt/"):
        return
    raise McpEvidenceCaptureError("result contains an unregistered evaluator explanation")


def _invariant(value: InvariantResult) -> dict[str, JsonValue]:
    _require_allowed_explanation(value.explanation)
    return {
        "name": value.name,
        "verdict": value.verdict.value,
        "explanation": value.explanation,
        "missing_evidence": list(value.missing_evidence),
        "candidate": value.candidate,
    }


def _axis(value: AxisResult) -> dict[str, JsonValue]:
    return {
        "status": value.status.value,
        "invariants": [_invariant(item) for item in value.invariants],
    }


def _error(value: ErrorRecord) -> dict[str, JsonValue]:
    return {
        "axis": value.axis,
        "phase": value.phase,
        "error_type": value.error_type,
        "message_redacted": True,
    }


def _cleanup_record(status: CleanupStatus, record: McpRefundWorldRecord) -> dict[str, JsonValue]:
    return {
        "status": status.value,
        "resource_closed": record.cleaned,
        "database_removed": record.database_removed,
        "protocol_version": record.protocol_version,
    }


def _clean_trial(
    value: CleanTrialEvidence[RefundReceipt, RefundState, McpRefundEvent],
    record: McpRefundWorldRecord,
    command: RefundCommand,
) -> dict[str, JsonValue]:
    return {
        "trial_id": value.trial_id.value,
        "baseline": _observation(value.baseline),
        "attempt": _attempt(value.attempt, command),
        "history_delta": [_event(event) for event in value.history_delta],
        "transport_truth": _transport(record.deliveries),
        "cleanup": _cleanup_record(value.cleanup, record),
    }


def _perturbed_trial(
    value: RunEvidence[RefundReceipt, SurfaceObservation[RefundState, McpRefundEvent]],
    cleanup: CleanupStatus,
    record: McpRefundWorldRecord,
    command: RefundCommand,
) -> dict[str, JsonValue]:
    return {
        "operation_id": value.operation_id.value,
        "trial_id": value.trial_id.value,
        "baseline": _observation(value.baseline),
        "attempts": [_attempt(attempt, command) for attempt in value.attempts],
        "subject_result": _receipt(value.subject_result),
        "harness_truth": {
            "boundary_name": value.harness.boundary_name,
            "reached_attempt_ids": [item.value for item in value.harness.reached_attempt_ids],
            "injected_attempt_id": value.harness.injected_attempt_id.value,
            "undelivered_result": _receipt(value.harness.undelivered_result),
        },
        "transport_truth": _transport(record.deliveries),
        "cleanup": _cleanup_record(cleanup, record),
    }


def _expected_trial_ids(result: McpRefundCaseResult) -> tuple[str, ...]:
    values: list[str] = []
    for pair in (result.primary, *result.confirmations):
        if pair.clean is not None:
            values.append(pair.clean.trial_id.value)
        if pair.perturbed is not None:
            values.append(pair.perturbed.trial_id.value)
    return tuple(values)


def _world_records(
    result: McpRefundCaseResult, tracker: McpRefundWorldTracker
) -> dict[str, McpRefundWorldRecord]:
    expected = _expected_trial_ids(result)
    records = {record.trial_id.value: record for record in tracker.worlds}
    if len(records) != len(tracker.worlds) or set(records) != set(expected):
        raise McpEvidenceCaptureError("semantic trials and MCP world records do not match")
    for record in records.values():
        if not record.cleaned or not record.database_removed or record.protocol_version is None:
            raise McpEvidenceCaptureError("artifact capture requires completed resource cleanup")
    if tracker.preflight_protocol_version is None:
        raise McpEvidenceCaptureError("artifact capture requires recorded MCP preflight evidence")
    return records


def _pair(
    value: PairResult[RefundReceipt, RefundState, McpRefundEvent],
    records: dict[str, McpRefundWorldRecord],
    command: RefundCommand,
) -> dict[str, JsonValue]:
    clean = (
        _clean_trial(value.clean, records[value.clean.trial_id.value], command)
        if value.clean is not None
        else None
    )
    perturbed = (
        _perturbed_trial(
            value.perturbed,
            value.perturbed_cleanup,
            records[value.perturbed.trial_id.value],
            command,
        )
        if value.perturbed is not None
        else None
    )
    return {
        "pair_id": value.pair_id,
        "clean_results": [_invariant(item) for item in value.clean_results],
        "retry_results": [_invariant(item) for item in value.retry_results],
        "errors": [_error(item) for item in value.errors],
        "clean": clean,
        "perturbed": perturbed,
    }


def _validate_result_scope(result: McpRefundCaseResult, mode: McpEvidenceMode) -> None:
    scope = result.scope
    expected_subject = f"mcp_{mode}_refund_tool"
    if (
        scope.subject_name != expected_subject
        or scope.input != RefundCommand(_PAYMENT_ID, _REFUND_MINOR_UNITS, _OPERATION_KEY)
        or scope.operation_id.value != _OPERATION_ID
        or scope.operation_key != _OPERATION_KEY
        or scope.schedule != _schedule_descriptor()["name"]
        or scope.reportable
        or scope.limitations != _EXPECTED_LIMITATIONS
    ):
        raise McpEvidenceCaptureError("semantic result does not match the registered MCP scope")
    coverage = scope.coverage
    if (
        coverage.surface != "refunds"
        or not coverage.state
        or not coverage.history
        or not coverage.complete_history
        or coverage.observation_interval != "baseline_to_final"
        or coverage.provenance != "harness_controlled_sqlite_mcp_fixture"
        or coverage.limitations != ("does not validate production-provider semantics",)
    ):
        raise McpEvidenceCaptureError("semantic result has unregistered observer coverage")


def _scope(result: McpRefundCaseResult, mode: McpEvidenceMode) -> dict[str, JsonValue]:
    coverage = result.scope.coverage
    command = result.scope.input
    return {
        "subject_name": result.scope.subject_name,
        "mode": mode,
        "input": {
            "payment_id": command.payment_id,
            "amount_minor_units": command.amount_minor_units,
            "operation_key": command.operation_key,
        },
        "operation_id": result.scope.operation_id.value,
        "operation_key": result.scope.operation_key,
        "schedule": result.scope.schedule,
        "observer_coverage": {
            "surface": coverage.surface,
            "state": coverage.state,
            "history": coverage.history,
            "complete_history": coverage.complete_history,
            "observation_interval": coverage.observation_interval,
            "provenance": coverage.provenance,
            "limitations": list(coverage.limitations),
        },
        # Raw semantic results remain deliberately non-reportable. Successful
        # capture is the promotion boundary because it has now checked the full
        # compatibility descriptor, cleanup evidence, and redaction policy.
        "reportable": True,
        "limitations": list(result.scope.limitations),
    }


def capture_mcp_evidence(
    *,
    mode: McpEvidenceMode,
    result: McpRefundCaseResult,
    tracker: McpRefundWorldTracker,
    lineage: Literal["recording", "verified_replay"] = "recording",
    source_sha256: str | None = None,
    reproduction_matched: bool | None = None,
    forbidden_values: tuple[str, ...] = (),
) -> EvidenceArtifact:
    """Exhaustively capture one completed controlled MCP evaluation."""

    _validate_result_scope(result, mode)
    records = _world_records(result, tracker)
    compatibility = build_mcp_compatibility(mode)
    protocol = require_string(
        require_object(compatibility["runtime"], "compatibility.runtime")["mcp_protocol_revision"],
        "compatibility.runtime.mcp_protocol_revision",
    )
    if tracker.preflight_protocol_version != protocol or any(
        record.protocol_version != protocol for record in records.values()
    ):
        raise McpEvidenceCaptureError("negotiated MCP protocol differs from compatibility")
    if lineage == "recording" and (source_sha256 is not None or reproduction_matched is not None):
        raise McpEvidenceCaptureError("recording lineage cannot contain replay metadata")
    if lineage == "verified_replay":
        if source_sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise McpEvidenceCaptureError("replay lineage requires a source SHA-256")
        if reproduction_matched is None:
            raise McpEvidenceCaptureError("replay lineage requires a reproduction result")

    command = result.scope.input
    payload: dict[str, JsonValue] = {
        "schema": {"name": _SCHEMA_NAME, "version": _SCHEMA_VERSION},
        "producer": {"id": _RUNNER_ID, "version": _RUNNER_VERSION},
        "replay": {
            "registry_id": _REGISTRY_ID,
            "mode": mode,
            "replayable": True,
            "lineage": lineage,
            "source_sha256": source_sha256,
        },
        "compatibility": compatibility,
        "scope": _scope(result, mode),
        "contracts": _contract_descriptor(),
        "outcomes": {
            "clean_validity": _axis(result.clean_validity),
            "retry_safety": _axis(result.retry_safety),
        },
        "evidence": {
            "primary": _pair(result.primary, records, command),
            "confirmations": [_pair(pair, records, command) for pair in result.confirmations],
        },
        "provenance": {
            "provider": "harness-controlled SQLite MCP refund fixture",
            "runner": _RUNNER_ID,
            "known_limitations": list(_EXPECTED_LIMITATIONS),
        },
        "redaction": {
            "id": _REDACTION_ID,
            "version": _REDACTION_VERSION,
            "omitted_categories": list(_OMITTED_CATEGORIES),
        },
        "reproduction": (
            None
            if reproduction_matched is None
            else {
                "projection_version": _CANONICAL_PROJECTION_VERSION,
                "matched": reproduction_matched,
            }
        ),
    }
    artifact = evidence_artifact_from_payload(payload, validator=validate_mcp_artifact)
    # Report eligibility is narrower than artifact validity. Keep error or otherwise
    # inconsistent evidence inspectable, but do not promote it to a report source.
    from effectprobe._mcp_reports import McpReportInputError, validate_reportable_mcp_artifact

    try:
        validate_reportable_mcp_artifact(artifact)
    except McpReportInputError:
        require_object(payload["scope"], "artifact.scope")["reportable"] = False
        artifact = evidence_artifact_from_payload(payload, validator=validate_mcp_artifact)
    encoded = str(artifact_payload(artifact))
    forbidden = {
        *(str(record.database_path) for record in tracker.worlds),
        os.fspath(sys.executable),
        *forbidden_values,
    }
    leaked = tuple(value for value in forbidden if value and value in encoded)
    if leaked:
        raise McpEvidenceCaptureError("redaction defense detected a forbidden value")
    return artifact


def _run_mode(mode: McpEvidenceMode) -> tuple[McpRefundCaseResult, McpRefundWorldTracker]:
    case, tracker = build_mcp_refund_case(keyed=mode == "keyed")
    return evaluate_case(case), tracker


def record_mcp_evidence(mode: McpEvidenceMode, destination: Path) -> ReadEvidenceArtifact:
    """Run, capture, and exclusively write one registered private MCP case."""

    result, tracker = _run_mode(mode)
    artifact = capture_mcp_evidence(mode=mode, result=result, tracker=tracker)
    return write_evidence_artifact(destination, artifact)


def _required_mode(value: str) -> McpEvidenceMode:
    if value == "unsafe":
        return "unsafe"
    if value == "keyed":
        return "keyed"
    raise ArtifactFormatError("artifact replay mode is unknown")


def _validate_string_list(value: JsonValue, path: str) -> None:
    for index, item in enumerate(require_list(value, path)):
        require_string(item, f"{path}[{index}]")


def _validate_receipt(value: JsonValue, path: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    receipt = require_object(value, path)
    require_fields(receipt, frozenset({"refund_id", "payment_id", "amount_minor_units"}), path)
    require_string(receipt["refund_id"], f"{path}.refund_id")
    require_string(receipt["payment_id"], f"{path}.payment_id")
    require_int(receipt["amount_minor_units"], f"{path}.amount_minor_units")


def _validate_state(value: JsonValue, path: str) -> None:
    state = require_object(value, path)
    require_fields(
        state,
        frozenset(
            {"payment_id", "payment_minor_units", "refunded_minor_units", "key_mapping_count"}
        ),
        path,
    )
    require_string(state["payment_id"], f"{path}.payment_id")
    for field in ("payment_minor_units", "refunded_minor_units", "key_mapping_count"):
        require_int(state[field], f"{path}.{field}")


def _validate_event(value: JsonValue, path: str) -> None:
    event = require_object(value, path)
    require_fields(
        event,
        frozenset({"refund_id", "payment_id", "amount_minor_units", "operation_key"}),
        path,
    )
    require_string(event["refund_id"], f"{path}.refund_id")
    require_string(event["payment_id"], f"{path}.payment_id")
    require_int(event["amount_minor_units"], f"{path}.amount_minor_units")
    require_nullable_string(event["operation_key"], f"{path}.operation_key")


def _validate_observation(value: JsonValue, path: str) -> None:
    observation = require_object(value, path)
    require_fields(observation, frozenset({"state", "history"}), path)
    _validate_state(observation["state"], f"{path}.state")
    for index, event in enumerate(require_list(observation["history"], f"{path}.history")):
        _validate_event(event, f"{path}.history[{index}]")


def _validate_identity(value: JsonValue, path: str) -> None:
    identity = require_object(value, path)
    fields = frozenset({"operation_id", "trial_id", "delivery_id", "attempt_id"})
    require_fields(identity, fields, path)
    for field in fields:
        require_string(identity[field], f"{path}.{field}")


def _validate_attempt(value: JsonValue, path: str) -> None:
    attempt = require_object(value, path)
    require_fields(attempt, frozenset({"identity", "subject_view", "observer_truth"}), path)
    _validate_identity(attempt["identity"], f"{path}.identity")
    view = require_object(attempt["subject_view"], f"{path}.subject_view")
    require_fields(view, frozenset({"input", "outcome", "returned_result"}), f"{path}.subject_view")
    _validate_input(view["input"], f"{path}.subject_view.input")
    outcome = require_string(view["outcome"], f"{path}.subject_view.outcome")
    if outcome not in {"returned", "client_result_lost", "provider_result_lost"}:
        raise ArtifactFormatError(f"{path}.subject_view.outcome is unknown")
    _validate_receipt(
        view["returned_result"], f"{path}.subject_view.returned_result", nullable=True
    )
    if (outcome == "returned") != (view["returned_result"] is not None):
        raise ArtifactFormatError(f"{path}.subject_view result contradicts its outcome")
    _validate_observation(attempt["observer_truth"], f"{path}.observer_truth")


def _validate_input(value: JsonValue, path: str) -> None:
    item = require_object(value, path)
    require_fields(item, frozenset({"payment_id", "amount_minor_units", "operation_key"}), path)
    require_string(item["payment_id"], f"{path}.payment_id")
    require_int(item["amount_minor_units"], f"{path}.amount_minor_units")
    require_string(item["operation_key"], f"{path}.operation_key")


def _validate_transport(value: JsonValue, path: str) -> None:
    for index, item_value in enumerate(require_list(value, path)):
        item_path = f"{path}[{index}]"
        item = require_object(item_value, item_path)
        require_fields(item, frozenset({"ordinal", "mcp_request_id", "operation_key"}), item_path)
        require_int(item["ordinal"], f"{item_path}.ordinal")
        require_string(item["mcp_request_id"], f"{item_path}.mcp_request_id")
        require_string(item["operation_key"], f"{item_path}.operation_key")


def _validate_cleanup(value: JsonValue, path: str) -> None:
    cleanup = require_object(value, path)
    require_fields(
        cleanup,
        frozenset({"status", "resource_closed", "database_removed", "protocol_version"}),
        path,
    )
    if require_string(cleanup["status"], f"{path}.status") not in {
        "not_attempted",
        "pass",
        "error",
    }:
        raise ArtifactFormatError(f"{path}.status is unknown")
    require_bool(cleanup["resource_closed"], f"{path}.resource_closed")
    require_bool(cleanup["database_removed"], f"{path}.database_removed")
    require_nullable_string(cleanup["protocol_version"], f"{path}.protocol_version")


def _validate_invariant(value: JsonValue, path: str) -> None:
    item = require_object(value, path)
    require_fields(
        item,
        frozenset({"name", "verdict", "explanation", "missing_evidence", "candidate"}),
        path,
    )
    require_string(item["name"], f"{path}.name")
    if require_string(item["verdict"], f"{path}.verdict") not in {
        "PASS",
        "FAIL",
        "INCONCLUSIVE",
        "ERROR",
    }:
        raise ArtifactFormatError(f"{path}.verdict is unknown")
    require_string(item["explanation"], f"{path}.explanation")
    _validate_string_list(item["missing_evidence"], f"{path}.missing_evidence")
    require_bool(item["candidate"], f"{path}.candidate")


def _validate_invariants(value: JsonValue, path: str) -> None:
    for index, item in enumerate(require_list(value, path)):
        _validate_invariant(item, f"{path}[{index}]")


def _validate_axis(value: JsonValue, path: str) -> None:
    axis = require_object(value, path)
    require_fields(axis, frozenset({"status", "invariants"}), path)
    if require_string(axis["status"], f"{path}.status") not in {
        "PASS",
        "FAIL",
        "INCONCLUSIVE",
        "ERROR",
        "UNVERIFIED",
    }:
        raise ArtifactFormatError(f"{path}.status is unknown")
    _validate_invariants(axis["invariants"], f"{path}.invariants")


def _validate_error(value: JsonValue, path: str) -> None:
    item = require_object(value, path)
    require_fields(item, frozenset({"axis", "phase", "error_type", "message_redacted"}), path)
    if require_string(item["axis"], f"{path}.axis") not in {"clean", "retry"}:
        raise ArtifactFormatError(f"{path}.axis is unknown")
    require_string(item["phase"], f"{path}.phase")
    require_string(item["error_type"], f"{path}.error_type")
    if not require_bool(item["message_redacted"], f"{path}.message_redacted"):
        raise ArtifactFormatError(f"{path}.message_redacted must be true")


def _validate_clean(value: JsonValue, path: str) -> None:
    item = require_object(value, path)
    require_fields(
        item,
        frozenset(
            {"trial_id", "baseline", "attempt", "history_delta", "transport_truth", "cleanup"}
        ),
        path,
    )
    require_string(item["trial_id"], f"{path}.trial_id")
    _validate_observation(item["baseline"], f"{path}.baseline")
    _validate_attempt(item["attempt"], f"{path}.attempt")
    for index, event in enumerate(require_list(item["history_delta"], f"{path}.history_delta")):
        _validate_event(event, f"{path}.history_delta[{index}]")
    _validate_transport(item["transport_truth"], f"{path}.transport_truth")
    _validate_cleanup(item["cleanup"], f"{path}.cleanup")


def _validate_perturbed(value: JsonValue, path: str) -> None:
    item = require_object(value, path)
    require_fields(
        item,
        frozenset(
            {
                "operation_id",
                "trial_id",
                "baseline",
                "attempts",
                "subject_result",
                "harness_truth",
                "transport_truth",
                "cleanup",
            }
        ),
        path,
    )
    require_string(item["operation_id"], f"{path}.operation_id")
    require_string(item["trial_id"], f"{path}.trial_id")
    _validate_observation(item["baseline"], f"{path}.baseline")
    attempts = require_list(item["attempts"], f"{path}.attempts")
    if len(attempts) != 2:
        raise ArtifactFormatError(f"{path}.attempts must contain exactly two attempts")
    for index, attempt in enumerate(attempts):
        _validate_attempt(attempt, f"{path}.attempts[{index}]")
    _validate_receipt(item["subject_result"], f"{path}.subject_result")
    harness = require_object(item["harness_truth"], f"{path}.harness_truth")
    require_fields(
        harness,
        frozenset(
            {"boundary_name", "reached_attempt_ids", "injected_attempt_id", "undelivered_result"}
        ),
        f"{path}.harness_truth",
    )
    require_string(harness["boundary_name"], f"{path}.harness_truth.boundary_name")
    _validate_string_list(
        harness["reached_attempt_ids"], f"{path}.harness_truth.reached_attempt_ids"
    )
    require_string(harness["injected_attempt_id"], f"{path}.harness_truth.injected_attempt_id")
    _validate_receipt(harness["undelivered_result"], f"{path}.harness_truth.undelivered_result")
    _validate_transport(item["transport_truth"], f"{path}.transport_truth")
    _validate_cleanup(item["cleanup"], f"{path}.cleanup")


def _validate_pair(value: JsonValue, path: str) -> None:
    pair = require_object(value, path)
    require_fields(
        pair,
        frozenset({"pair_id", "clean_results", "retry_results", "errors", "clean", "perturbed"}),
        path,
    )
    require_string(pair["pair_id"], f"{path}.pair_id")
    _validate_invariants(pair["clean_results"], f"{path}.clean_results")
    _validate_invariants(pair["retry_results"], f"{path}.retry_results")
    for index, error in enumerate(require_list(pair["errors"], f"{path}.errors")):
        _validate_error(error, f"{path}.errors[{index}]")
    if pair["clean"] is not None:
        _validate_clean(pair["clean"], f"{path}.clean")
    if pair["perturbed"] is not None:
        _validate_perturbed(pair["perturbed"], f"{path}.perturbed")


def _validate_compatibility(value: JsonValue, path: str) -> None:
    item = require_object(value, path)
    expected = build_mcp_compatibility("unsafe")
    require_fields(item, frozenset(expected), path)

    # Full leaf equality is a replay concern. Reusing the live descriptor's shape
    # recursively makes the private decoder reject unknown or missing fields.
    def shape(actual_value: JsonValue, expected_value: JsonValue, current_path: str) -> None:
        if isinstance(expected_value, dict):
            actual_object = require_object(actual_value, current_path)
            require_fields(actual_object, frozenset(expected_value), current_path)
            for key, child in expected_value.items():
                shape(actual_object[key], child, f"{current_path}.{key}")
        elif isinstance(expected_value, list):
            actual_list = require_list(actual_value, current_path)
            if expected_value:
                for index, child in enumerate(actual_list):
                    template = (
                        expected_value[index] if index < len(expected_value) else expected_value[0]
                    )
                    shape(child, template, f"{current_path}[{index}]")
        elif isinstance(expected_value, bool):
            require_bool(actual_value, current_path)
        elif isinstance(expected_value, int):
            require_int(actual_value, current_path)
        elif isinstance(expected_value, str):
            require_string(actual_value, current_path)
        elif expected_value is None and actual_value is not None:
            raise ArtifactFormatError(f"{current_path} must be null")

    shape(value, expected, path)


def validate_mcp_artifact(payload: dict[str, JsonValue]) -> None:
    """Strictly validate every field in private MCP evidence schema v1."""

    producer = require_object(payload["producer"], "artifact.producer")
    require_fields(producer, frozenset({"id", "version"}), "artifact.producer")
    require_string(producer["id"], "artifact.producer.id")
    require_int(producer["version"], "artifact.producer.version")

    replay = require_object(payload["replay"], "artifact.replay")
    require_fields(
        replay,
        frozenset({"registry_id", "mode", "replayable", "lineage", "source_sha256"}),
        "artifact.replay",
    )
    registry_id = require_string(replay["registry_id"], "artifact.replay.registry_id")
    mode = _required_mode(require_string(replay["mode"], "artifact.replay.mode"))
    require_bool(replay["replayable"], "artifact.replay.replayable")
    lineage = require_string(replay["lineage"], "artifact.replay.lineage")
    source = require_nullable_string(replay["source_sha256"], "artifact.replay.source_sha256")
    if lineage == "recording":
        if source is not None:
            raise ArtifactFormatError("recording lineage cannot name a source artifact")
    elif lineage == "verified_replay":
        if source is None or re.fullmatch(r"[0-9a-f]{64}", source) is None:
            raise ArtifactFormatError("verified replay lineage requires a source SHA-256")
    else:
        raise ArtifactFormatError("artifact replay lineage is unknown")

    _validate_compatibility(payload["compatibility"], "artifact.compatibility")
    compatibility = require_object(payload["compatibility"], "artifact.compatibility")
    compatibility_case = require_object(compatibility["case"], "artifact.compatibility.case")
    compatibility_subject = require_object(
        compatibility["subject"], "artifact.compatibility.subject"
    )
    compatibility_runner = require_object(compatibility["runner"], "artifact.compatibility.runner")
    compatibility_redaction = require_object(
        compatibility["redaction"], "artifact.compatibility.redaction"
    )
    if registry_id != compatibility_case["registry_id"]:
        raise ArtifactFormatError("replay registry and compatibility case disagree")
    if mode != compatibility_subject["mode"]:
        raise ArtifactFormatError("replay mode and compatibility subject disagree")
    if (
        producer["id"] != compatibility_runner["id"]
        or producer["version"] != compatibility_runner["version"]
    ):
        raise ArtifactFormatError("producer and compatibility runner disagree")
    scope = require_object(payload["scope"], "artifact.scope")
    require_fields(
        scope,
        frozenset(
            {
                "subject_name",
                "mode",
                "input",
                "operation_id",
                "operation_key",
                "schedule",
                "observer_coverage",
                "reportable",
                "limitations",
            }
        ),
        "artifact.scope",
    )
    for field in ("subject_name", "mode", "operation_id", "schedule"):
        require_string(scope[field], f"artifact.scope.{field}")
    require_nullable_string(scope["operation_key"], "artifact.scope.operation_key")
    _validate_input(scope["input"], "artifact.scope.input")
    observer_coverage = require_object(
        scope["observer_coverage"], "artifact.scope.observer_coverage"
    )
    require_fields(
        observer_coverage,
        frozenset(_observer_descriptor()),
        "artifact.scope.observer_coverage",
    )
    require_bool(scope["reportable"], "artifact.scope.reportable")
    _validate_string_list(scope["limitations"], "artifact.scope.limitations")
    schedule = require_object(compatibility["schedule"], "artifact.compatibility.schedule")
    if (
        scope["mode"] != mode
        or scope["subject_name"] != compatibility_subject["id"]
        or scope["input"] != compatibility["input"]
        or scope["schedule"] != schedule["name"]
        or observer_coverage != compatibility["observer"]
    ):
        raise ArtifactFormatError("recorded scope and compatibility descriptor disagree")

    contracts = require_object(payload["contracts"], "artifact.contracts")
    require_fields(contracts, frozenset(_contract_descriptor()), "artifact.contracts")
    if contracts != compatibility["contracts"]:
        raise ArtifactFormatError("recorded contracts and compatibility descriptor disagree")

    outcomes = require_object(payload["outcomes"], "artifact.outcomes")
    require_fields(outcomes, frozenset({"clean_validity", "retry_safety"}), "artifact.outcomes")
    _validate_axis(outcomes["clean_validity"], "artifact.outcomes.clean_validity")
    _validate_axis(outcomes["retry_safety"], "artifact.outcomes.retry_safety")

    evidence = require_object(payload["evidence"], "artifact.evidence")
    require_fields(evidence, frozenset({"primary", "confirmations"}), "artifact.evidence")
    _validate_pair(evidence["primary"], "artifact.evidence.primary")
    for index, pair in enumerate(
        require_list(evidence["confirmations"], "artifact.evidence.confirmations")
    ):
        _validate_pair(pair, f"artifact.evidence.confirmations[{index}]")

    provenance = require_object(payload["provenance"], "artifact.provenance")
    require_fields(
        provenance, frozenset({"provider", "runner", "known_limitations"}), "artifact.provenance"
    )
    require_string(provenance["provider"], "artifact.provenance.provider")
    require_string(provenance["runner"], "artifact.provenance.runner")
    _validate_string_list(provenance["known_limitations"], "artifact.provenance.known_limitations")

    redaction = require_object(payload["redaction"], "artifact.redaction")
    require_fields(
        redaction, frozenset({"id", "version", "omitted_categories"}), "artifact.redaction"
    )
    require_string(redaction["id"], "artifact.redaction.id")
    require_int(redaction["version"], "artifact.redaction.version")
    _validate_string_list(redaction["omitted_categories"], "artifact.redaction.omitted_categories")
    if (
        redaction["id"] != compatibility_redaction["id"]
        or redaction["version"] != compatibility_redaction["version"]
    ):
        raise ArtifactFormatError("redaction policy and compatibility descriptor disagree")

    reproduction = payload["reproduction"]
    if reproduction is None:
        if lineage != "recording":
            raise ArtifactFormatError("verified replay requires a reproduction result")
    else:
        value = require_object(reproduction, "artifact.reproduction")
        require_fields(value, frozenset({"projection_version", "matched"}), "artifact.reproduction")
        if (
            require_int(value["projection_version"], "artifact.reproduction.projection_version")
            != _CANONICAL_PROJECTION_VERSION
        ):
            raise ArtifactFormatError("artifact reproduction projection version is unsupported")
        require_bool(value["matched"], "artifact.reproduction.matched")
        if lineage != "verified_replay":
            raise ArtifactFormatError("recording cannot contain a reproduction result")


def read_mcp_evidence(path: Path) -> ReadEvidenceArtifact:
    """Strictly read one private MCP evidence artifact."""

    return read_evidence_artifact(path, validator=validate_mcp_artifact)


def _payload_sections(read: ReadEvidenceArtifact) -> dict[str, JsonValue]:
    return artifact_payload(read.artifact)


def _count_trial(pair: dict[str, JsonValue]) -> tuple[int, int, int, tuple[str, ...]]:
    trials = 0
    effects = 0
    deliveries = 0
    cleanups: list[str] = []
    for field in ("clean", "perturbed"):
        value = pair[field]
        if value is None:
            continue
        trials += 1
        trial = require_object(value, f"pair.{field}")
        final_observation = (
            require_object(trial["attempt"], "pair.clean.attempt")["observer_truth"]
            if field == "clean"
            else require_list(trial["attempts"], "pair.perturbed.attempts")[-1]
        )
        if field == "perturbed":
            final_observation = require_object(final_observation, "pair.perturbed.attempt")[
                "observer_truth"
            ]
        observation = require_object(final_observation, f"pair.{field}.observer_truth")
        effects += len(require_list(observation["history"], f"pair.{field}.history"))
        deliveries += len(require_list(trial["transport_truth"], f"pair.{field}.transport_truth"))
        cleanup = require_object(trial["cleanup"], f"pair.{field}.cleanup")
        cleanups.append(require_string(cleanup["status"], f"pair.{field}.cleanup.status"))
    return trials, effects, deliveries, tuple(cleanups)


def inspect_mcp_evidence(path: Path) -> EvidenceInspection:
    """Return a safe summary without executing the recorded case."""

    read = read_mcp_evidence(path)
    payload = _payload_sections(read)
    replay = require_object(payload["replay"], "artifact.replay")
    scope = require_object(payload["scope"], "artifact.scope")
    outcomes = require_object(payload["outcomes"], "artifact.outcomes")
    evidence = require_object(payload["evidence"], "artifact.evidence")
    pairs = [evidence["primary"], *require_list(evidence["confirmations"], "confirmations")]
    trial_count = 0
    effects = 0
    deliveries = 0
    cleanups: list[str] = []
    for pair_value in pairs:
        pair = require_object(pair_value, "pair")
        pair_trials, pair_effects, pair_deliveries, pair_cleanups = _count_trial(pair)
        trial_count += pair_trials
        effects += pair_effects
        deliveries += pair_deliveries
        cleanups.extend(pair_cleanups)
    clean = require_object(outcomes["clean_validity"], "outcomes.clean_validity")
    retry = require_object(outcomes["retry_safety"], "outcomes.retry_safety")
    redaction = require_object(payload["redaction"], "artifact.redaction")
    reproduction = payload["reproduction"]
    return EvidenceInspection(
        sha256=read.sha256,
        size_bytes=read.size_bytes,
        schema_version=_SCHEMA_VERSION,
        lineage=require_string(replay["lineage"], "replay.lineage"),
        source_sha256=require_nullable_string(replay["source_sha256"], "replay.source_sha256"),
        mode=_required_mode(require_string(replay["mode"], "replay.mode")),
        subject_name=require_string(scope["subject_name"], "scope.subject_name"),
        schedule=require_string(scope["schedule"], "scope.schedule"),
        clean_validity=require_string(clean["status"], "clean.status"),
        retry_safety=require_string(retry["status"], "retry.status"),
        confirmation_count=len(require_list(evidence["confirmations"], "confirmations")),
        trial_count=trial_count,
        committed_effect_count=effects,
        transport_delivery_count=deliveries,
        cleanup_dispositions=tuple(cleanups),
        omitted_categories=tuple(
            require_string(item, "redaction.omitted_categories[]")
            for item in require_list(redaction["omitted_categories"], "omitted_categories")
        ),
        limitations=tuple(
            require_string(item, "scope.limitations[]")
            for item in require_list(scope["limitations"], "scope.limitations")
        ),
        reproduction_matched=(
            None
            if reproduction is None
            else require_bool(
                require_object(reproduction, "reproduction")["matched"],
                "reproduction.matched",
            )
        ),
    )


def _normalize_refund_ids(value: JsonValue) -> JsonValue:
    cloned = copy.deepcopy(value)
    identifiers: dict[str, str] = {}

    def visit(item: JsonValue) -> None:
        if isinstance(item, dict):
            typed = cast("dict[str, JsonValue]", item)
            refund_id = typed.get("refund_id")
            if isinstance(refund_id, str):
                normalized = identifiers.setdefault(
                    refund_id, f"generated-refund-identity/{len(identifiers) + 1}"
                )
                typed["refund_id"] = normalized
            for child in typed.values():
                visit(child)
        elif isinstance(item, list):
            for child in cast("list[JsonValue]", item):
                visit(child)

    visit(cloned)
    return cloned


def _canonical_pair(value: JsonValue) -> JsonValue:
    pair = copy.deepcopy(require_object(value, "pair"))
    for field in ("clean", "perturbed"):
        trial_value = pair[field]
        if trial_value is None:
            continue
        trial = require_object(trial_value, f"pair.{field}")
        transport = require_list(trial["transport_truth"], f"pair.{field}.transport_truth")
        request_ids: set[str] = set()
        for index, delivery_value in enumerate(transport):
            delivery = require_object(delivery_value, "delivery")
            request_ids.add(require_string(delivery["mcp_request_id"], "delivery.mcp_request_id"))
            delivery["mcp_request_id"] = f"transport-request/{index + 1}"
        trial["transport_request_ids_distinct"] = len(request_ids) == len(transport)
        pair[field] = _normalize_refund_ids(trial)
    return pair


def canonical_evaluative_projection(artifact: EvidenceArtifact) -> dict[str, JsonValue]:
    """Project generated identities away while preserving evaluative relations."""

    payload = artifact_payload(artifact)
    evidence = require_object(payload["evidence"], "artifact.evidence")
    return {
        "version": _CANONICAL_PROJECTION_VERSION,
        "scope": copy.deepcopy(payload["scope"]),
        "contracts": copy.deepcopy(payload["contracts"]),
        "outcomes": copy.deepcopy(payload["outcomes"]),
        "primary": _canonical_pair(evidence["primary"]),
        "confirmations": [
            _canonical_pair(pair)
            for pair in require_list(evidence["confirmations"], "evidence.confirmations")
        ],
    }


def replay_mcp_evidence(
    source: Path,
    destination: Path,
    *,
    run: McpRun | None = None,
) -> ReplayEvidenceResult:
    """Refuse drift, independently evaluate, and exclusively write a replay child."""

    if source.absolute() == destination.absolute():
        raise ArtifactFormatError("replay destination must differ from its source")
    source_read = read_mcp_evidence(source)
    source_payload = artifact_payload(source_read.artifact)
    replay = require_object(source_payload["replay"], "artifact.replay")
    registry_id = require_string(replay["registry_id"], "replay.registry_id")
    if registry_id != _REGISTRY_ID:
        raise ArtifactCompatibilityError(
            (CompatibilityDifference("compatibility.case.registry_id", registry_id, _REGISTRY_ID),)
        )
    if not require_bool(replay["replayable"], "replay.replayable"):
        raise ArtifactCompatibilityError(
            (CompatibilityDifference("compatibility.replayable", "false", "true"),)
        )
    mode = _required_mode(require_string(replay["mode"], "replay.mode"))
    recorded_compatibility = require_object(
        source_payload["compatibility"], "artifact.compatibility"
    )
    live_compatibility = build_mcp_compatibility(mode)
    require_compatible(recorded_compatibility, live_compatibility)

    execute = _run_mode if run is None else run
    result, tracker = execute(mode)
    preliminary = capture_mcp_evidence(
        mode=mode,
        result=result,
        tracker=tracker,
        lineage="verified_replay",
        source_sha256=source_read.sha256,
        reproduction_matched=False,
    )
    matched = canonical_evaluative_projection(
        source_read.artifact
    ) == canonical_evaluative_projection(preliminary)
    child = capture_mcp_evidence(
        mode=mode,
        result=result,
        tracker=tracker,
        lineage="verified_replay",
        source_sha256=source_read.sha256,
        reproduction_matched=matched,
    )
    child_read = write_evidence_artifact(destination, child)
    return ReplayEvidenceResult(source=source_read, child=child_read, matched=matched)
